"""Explicit owner quote intake. Saves verified candidates only; never runs decisions."""
import json
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal, localcontext
from challenge_ledger import required, decimal, cents, validate_terms
from challenge_decisions import timestamp
from verified_offers import capture_terms, eligible_game_players, route, fresh, MAX_AGE


def supplied(value):
    return value is not None and value != ''


def eligible_contexts(c, clock=None):
    """Read only: expose exactly the player contexts accepted by the gateway."""
    clock = clock or datetime.now(timezone.utc)
    contexts = []
    for game in c.execute("SELECT * FROM games WHERE sport IN ('NBA','WNBA','CFB','NFL') AND lower(status) IN ('pre','in','scheduled','live') ORDER BY sport,id").fetchall():
        try:
            game, players, day = eligible_game_players(c, game['id'], game['sport'], clock)
        except ValueError:
            continue
        for player in players:
            contexts.append(dict(ai_id=route(game['sport']), sport=game['sport'], game_id=game['id'],
                game_status=game['status'], slate_date=day, home_team=game['home_team'], away_team=game['away_team'],
                player_id=str(player['player_id']), player=player['player_name'], player_team=player['team']))
    return contexts


def normalize_terms(data, n):
    """Convert only explicitly selected odds; never synthesize entry payout rules."""
    kind = required(data.get('ticket_type'))
    fmt = required(data.get('odds_format')).lower()
    structure = data.get('payout_structure')
    if isinstance(structure, str):
        try:
            structure = json.loads(structure)
        except ValueError:
            raise ValueError('Payout structure must be a JSON object')
    if structure is None:
        structure = {}
    if not isinstance(structure, dict):
        raise ValueError('Payout structure must be an object')
    odds = data.get('odds_decimal')
    american = data.get('american_odds')
    multiplier = data.get('payout_multiplier')
    allwin = '|'.join(['WIN'] * n)
    if kind == 'PICKEM':
        if fmt != 'entry' or supplied(odds) or supplied(american):
            raise ValueError('PICKEM requires entry odds format, without sportsbook odds')
        if not supplied(multiplier) or not structure or allwin not in structure:
            raise ValueError('PICKEM requires explicit multiplier AND entry payout structure including all-win rule')
        return dict(odds=None, payout_multiplier=str(decimal(multiplier)), payout_structure=structure,
                    odds_format=fmt, american_odds=None)
    if supplied(multiplier):
        raise ValueError('Sportsbook offers require combined ticket odds, without entry multiplier')
    if fmt == 'american':
        if not supplied(american) or supplied(odds):
            raise ValueError('Missing explicit American odds or conflicting decimal odds')
        a = decimal(american)
        if a != a.to_integral_value() or abs(a) < 100:
            raise ValueError('American odds must be an integer >= +100 or <= -100')
        with localcontext() as ctx:
            ctx.prec = 28
            odds = str(Decimal(1) + a / 100 if a > 0 else Decimal(1) + 100 / abs(a))
    elif fmt == 'decimal':
        if not supplied(odds) or supplied(american):
            raise ValueError('Missing explicit decimal odds or conflicting American odds')
        odds = str(decimal(odds))
    elif fmt == 'payout_rule' and kind == 'PARLAY':
        if supplied(odds) or supplied(american) or allwin not in structure:
            raise ValueError('PARLAY requires explicit combined all-win payout rule')
        odds = str(decimal(structure[allwin]))
    else:
        raise ValueError('Select a valid odds format for the ticket type')
    return dict(odds=odds, payout_multiplier=None, payout_structure=structure, odds_format=fmt,
                american_odds=str(american) if supplied(american) else None)


def save_manual_offer(c, data, clock=None):
    """Validate and atomically save source rows plus their exact terms attachment."""
    clock = clock or datetime.now(timezone.utc)
    ai = None
    try:
        if not isinstance(data, dict):
            raise ValueError('Expected an offer object')
        declared = {route(leg.get('sport')) for leg in data.get('legs', []) if isinstance(leg, dict)} if isinstance(data.get('legs'), list) else set()
        ai = next(iter(declared)) if len(declared) == 1 else None
        book = required(data.get('book'))
        kind = required(data.get('ticket_type'))
        source = required(data.get('source'))
        captured = timestamp(data.get('source_captured_at'))
        fresh(captured, clock)
        legs = data.get('legs')
        if not isinstance(legs, list) or not 1 <= len(legs) <= 12:
            raise ValueError('Supply 1 to 12 complete legs')
        n = data.get('number_of_legs')
        if isinstance(n, bool) or decimal(n) != len(legs):
            raise ValueError('NUMBER_OF_LEGS must match the complete leg list')
        stake = cents(data.get('stake'))
        potential = cents(data.get('potential_payout'))
        if stake <= 0 or potential <= 0:
            raise ValueError('Quoted stake and potential gross payout must be positive')
        if supplied(data.get('offer_expires_at')):
            expiry = timestamp(data['offer_expires_at'])
            if supplied(data.get('freshness_ttl')):
                raise ValueError('Supply expiry OR freshness TTL')
        elif supplied(data.get('freshness_ttl')):
            ttl = decimal(data['freshness_ttl'])
            if ttl != ttl.to_integral_value() or not 1 <= ttl <= MAX_AGE:
                raise ValueError('Freshness TTL must be 1–120 seconds')
            expiry = captured + timedelta(seconds=int(ttl))
        else:
            raise ValueError('Explicit offer expiry or freshness TTL is required')
        if expiry <= clock or expiry <= captured:
            raise ValueError('STALE OFFER: expiry must follow capture and be in the future')
        normalized = normalize_terms(data, len(legs))
        validate_terms(kind, len(legs), stake, normalized['odds'], normalized['payout_multiplier'],
                       normalized['payout_structure'], potential)
        # Hold the write lock across eligibility checks and inserts to prevent context races.
        c.execute('BEGIN IMMEDIATE')
        for leg in legs:
            if not isinstance(leg, dict):
                raise ValueError('Incomplete leg')
            for key in ('game_id', 'sport', 'player', 'player_team', 'market', 'side'):
                required(leg.get(key))
            if not str(leg.get('player_id', '')).isdigit():
                raise ValueError('Explicit numeric PLAYER_ID is required')
            if not supplied(leg.get('line')):
                raise ValueError('Missing market line')
            decimal(leg['line'])
            _, players, _ = eligible_game_players(c, leg['game_id'], leg['sport'], clock)
            if not any(str(p['player_id']) == str(leg['player_id']) and p['player_name'] == leg['player']
                       and p['team'] == leg['player_team'] for p in players):
                raise ValueError('Player identity missing/ambiguous or mismatches selected game/team')
            specialist = route(leg['sport'])
            if ai is not None and ai != specialist:
                raise ValueError('Mixed specialist ticket')
            ai = specialist
            if data.get('ai_id', ai) != ai or leg.get('ai_id', ai) != ai:
                raise ValueError('Wrong specialist for source sport')
        prop_ids = []
        player_ids = {}
        for leg in legs:
            cur = c.execute('INSERT INTO prop_lines(game_id,player_name,market,line,side,book,observed_at) VALUES(?,?,?,?,?,?,?)',
                (leg['game_id'], leg['player'], leg['market'], str(decimal(leg['line'])), leg['side'], book, captured.isoformat()))
            prop_ids.append(cur.lastrowid)
            player_ids[str(cur.lastrowid)] = str(leg['player_id'])
        terms = dict(source=source, ticket_type=kind, book=book, ai_id=ai, stake=stake/100,
            eligible_stakes=[stake/100], potential_payout=potential/100, observed_at=captured.isoformat(),
            source_captured_at=captured.isoformat(), expires_at=expiry.isoformat(), created_at=clock.isoformat(),
            player_ids=player_ids, declared_legs=legs, **normalized)
        attachment_id = 'owner-manual:' + str(uuid.uuid4())
        public = capture_terms(c, attachment_id, prop_ids, terms, clock)
        return dict(status='VALID', saved=True, offer=public, offer_id=public['OFFER_ID'],
            attachment_id=attachment_id, ai_id=ai, prop_ids=prop_ids, source=source, created_at=clock.isoformat(),
            source_captured_at=captured.isoformat(), offer_expires_at=expiry.isoformat(), normalized_terms=normalized,
            decision_status='NOT RUN — specialist still chooses BET or PASS', tickets_created=0)
    except (ValueError, TypeError, KeyError) as exc:
        if c.in_transaction:
            c.rollback()
        return dict(status='REJECTED', reason=str(exc), saved=False, offer_id=None, ai_id=ai, tickets_created=0)
    except Exception:
        if c.in_transaction:
            c.rollback()
        raise


def save_offer_entry(c, data, clock=None):
    """Apply UI conveniences before the unchanged strict offer validator.

    Legacy explicit API requests retain their existing required-field behavior.
    No odds, markets or books are supplied by this adapter.
    """
    clock = clock or datetime.now(timezone.utc)
    if not isinstance(data, dict) or 'entry_mode' not in data:
        return save_manual_offer(c, data, clock)
    try:
        if data['entry_mode'] not in ('simple', 'advanced'):
            raise ValueError('Unknown entry mode')
        simple = data['entry_mode'] == 'simple'
        prepared = dict(data)
        legs = data.get('legs')
        if not isinstance(legs, list) or not 1 <= len(legs) <= 12 or (simple and len(legs) != 1):
            raise ValueError('Supply one leg in simple mode or 1 to 12 complete advanced legs')
        if simple and data.get('ticket_type', 'STRAIGHT') != 'STRAIGHT':
            raise ValueError('Simple mode requires STRAIGHT')
        if 'number_of_legs' in data and (isinstance(data['number_of_legs'], bool) or decimal(data['number_of_legs']) != len(legs)):
            raise ValueError('NUMBER_OF_LEGS must match the complete leg list')
        if 'source' in data and data['source'] != 'OWNER_MANUAL':
            raise ValueError('Manual intake source must be OWNER_MANUAL')
        resolved = []
        for leg in legs:
            if not isinstance(leg, dict):
                raise ValueError('Incomplete leg')
            _, players, _ = eligible_game_players(c, leg.get('game_id'), leg.get('sport'), clock)
            matches = [p for p in players if str(p['player_id']) == str(leg.get('player_id'))]
            if len(matches) != 1:
                raise ValueError('Player identity missing/ambiguous for selected game/team')
            player = matches[0]
            automatic = dict(player=player['player_name'], player_team=player['team'], ai_id=route(leg['sport']))
            if any(key in leg and leg[key] != value for key, value in automatic.items()):
                raise ValueError('Supplied player/team/specialist conflicts with verified context')
            resolved.append(dict(leg, **automatic))
        prepared.update(legs=resolved, source='OWNER_MANUAL',
            ticket_type='STRAIGHT' if simple else data.get('ticket_type', 'STRAIGHT'),
            number_of_legs=len(resolved))
        if simple and any(supplied(data.get(k)) for k in ('source_captured_at','offer_expires_at','freshness_ttl','payout_multiplier','payout_structure')):
            raise ValueError('Use advanced mode for capture, expiry or payout structure overrides')
        prepared['source_captured_at'] = data.get('source_captured_at') or clock.isoformat()
        if not supplied(data.get('offer_expires_at')) and not supplied(data.get('freshness_ttl')):
            prepared['freshness_ttl'] = MAX_AGE
        normalized = normalize_terms(prepared, len(resolved))
        if simple or not supplied(prepared.get('potential_payout')):
            from challenge_ledger import payout
            multiplier = normalized['odds'] if normalized['odds'] is not None else normalized['payout_multiplier']
            gross = payout(cents(prepared.get('stake')), multiplier)
            calculated = str(Decimal(gross) / 100)
            if supplied(data.get('potential_payout')) and cents(data['potential_payout']) != gross:
                raise ValueError('Potential payout conflicts with entered stake and odds')
            prepared['potential_payout'] = calculated
        return save_manual_offer(c, prepared, clock)
    except (ValueError, TypeError, KeyError) as exc:
        return dict(status='REJECTED', reason=str(exc), saved=False, offer_id=None, ai_id=None, tickets_created=0)
