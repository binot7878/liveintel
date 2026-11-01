"""Read-only operational projections for the LiveIntel board.

No migrations, decisions, settlements, bankroll writes or quote capture occur here.
Legacy AI tables and manual prop sources are deliberately never projected.
"""
import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from challenge_ledger import SPORTS, snapshot
from challenge_decisions import latest_decision, ticket_views
from verified_offers import scan_offers
from survival_awards import awards

ZONE = ZoneInfo('America/New_York')
ACTIVE = {'pre', 'scheduled', 'live', 'in', 'in progress', 'halftime', 'started'}
LIVE = {'live', 'in', 'in progress', 'halftime', 'started'}
FINAL = {'post', 'final', 'completed', 'ended'}
STATS = {'points':'Points','rebounds':'Rebounds','assists':'Assists','pass_yards':'Passing yards',
         'rush_yards':'Rushing yards','receiving_yards':'Receiving yards','targets':'Targets',
         'pass_attempts':'Pass attempts','rush_attempts':'Rush attempts','fga':'Shot attempts','minutes':'Minutes','three_pa':'3PA','receptions':'Receptions','goals':'Goals','shots':'Shots','hits':'Hits','runs':'Runs','rbis':'RBIs','strikeouts':'Strikeouts'}
TEST_OWNERS = {'early test','test','test2','test owner','parlay owner','test_user'}


def instant(value):
    try:
        d = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        # Existing naive ingestion/database stamps are America/New_York local time.
        return d.replace(tzinfo=ZONE) if d.tzinfo is None else d
    except (ValueError, TypeError):
        return None


def fresh(value, clock, seconds=1800):
    stamp = instant(value)
    return stamp is not None and 0 <= (clock-stamp).total_seconds() <= seconds


def text(value):
    if value is None or str(value).strip().lower() in ('none','null','undefined','n/a','unknown','?', 'noneq','#none'):
        return ''
    result = str(value).strip()
    return '' if re.search(r'\b(?:NoneQ|None|null|undefined)\b|#None', result, re.I) else result


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def rows(c, table, where='', args=()):
    # All table names and predicates are internal constants.
    if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return []
    return [dict(r) for r in c.execute('SELECT * FROM '+table+(' WHERE '+where if where else ''), args)]


def current_games(c, clock):
    day = clock.astimezone(ZONE).date().isoformat()
    # Group once instead of scanning the enlarged history table twice per game.
    # Every linked row must still have today's non-null date, as before.
    games = c.execute('''SELECT g.* FROM games g JOIN
        (SELECT game_id,sport FROM multi_sport_players GROUP BY game_id,sport
         HAVING min(CASE WHEN substr(game_date,1,10)=? THEN 1 ELSE 0 END)=1) p
        ON p.game_id=g.id AND p.sport=g.sport ORDER BY g.sport,g.id''', (day,)).fetchall()
    output = []
    for row in games:
        g = dict(row)
        status = str(g['status']).lower()
        if status not in ACTIVE | FINAL:
            continue
        state = rows(c, 'game_state', 'game_id=?', (g['id'],))
        state = state[0] if state else {}
        # Status belongs to games; an old state row can never revive a finished game.
        live_fresh = status in LIVE and fresh(g.get('last_updated'), clock)
        category = 'LIVE' if live_fresh else 'TODAY' if status in ('pre','scheduled') else 'FINAL' if status in FINAL else 'UNAVAILABLE'
        item = dict(id=g['id'],sport=g['sport'],home=text(g['home_team']),away=text(g['away_team']),
            status='LIVE' if live_fresh else 'PRE' if category=='TODAY' else 'FINAL' if category=='FINAL' else 'DATA NOT READY',
            category=category,slate_date=day)
        path = {'NBA':'nba','WNBA':'wnba','NCAAB':'mens-college-basketball','NFL':'nfl','CFB':'college-football','NHL':'nhl','MLB':'mlb'}.get(g['sport'],'sports')
        item['gamecast'] = 'https://www.espn.com/'+path+'/game/_/gameId/'+str(g['id'])
        if category in ('LIVE','FINAL') and fresh(state.get('last_updated'),clock):
            for key in ('away_score','home_score','period','clock','possession','down','distance','game_script'):
                val = state.get(key)
                if text(val):
                    # Possession must be a readable same-game team, never an unmapped numeric ID.
                    if key=='possession' and val not in (item['home'],item['away']):
                        continue
                    if key in ('period','away_score','home_score','down','distance'):
                        numeric = number(val)
                        if numeric is None:
                            continue
                        item[key] = numeric
                    else:
                        item[key] = text(val)
            item['red_zone'] = state.get('red_zone') == 1
        if category=='LIVE' and not fresh(state.get('last_updated'),clock):
            item['detail_status'] = 'Live details updating'
        from sport_math import timing
        item['period_label']=timing(item['sport'],item.get('period'),item.get('clock'))['label']
        output.append(item)
    return output


def player_stats(row, game, clock):
    if game['status']!='LIVE' or not row.get('live_stats_available') or not fresh(row.get('last_updated'),clock):
        return []
    return [dict(label=label,value=row[key]) for key,label in STATS.items() if number(row.get(key)) is not None]


def players(c, games, clock, filters):
    sport, gid = filters.get('sport',''), filters.get('game','')
    lookup = {g['id']:g for g in games}
    if gid not in lookup or lookup[gid]['sport']!=sport:
        return dict(players=[],total=0)
    game = lookup[gid]
    found = {}
    for p in rows(c,'multi_sport_players','game_id=? AND sport=?',(gid,sport)):
        if p['team'] not in (game['home'],game['away']) or not str(p['player_id']).isdigit() or not text(p['player_name']):
            continue
        if filters.get('team') and p['team']!=filters['team']:
            continue
        if filters.get('q') and filters['q'].casefold() not in p['player_name'].casefold():
            continue
        found[str(p['player_id'])] = dict(id=str(p['player_id']),name=p['player_name'],team=p['team'],sport=sport,
            position=text(p.get('position')),game_id=gid,game=game['away']+' @ '+game['home'],status=game['status'],
            stats=player_stats(p,game,clock),availability='Roster confirmed',
            performance='Season / recent performance not available',role='Starter' if p.get('starter')==1 else 'Bench / rotation' if p.get('starter')==0 else 'Role / usage not available',ai_interest='No current signal')
    ordered = sorted(found.values(),key=lambda p:p['name'])
    try:
        offset = max(0,min(10000,int(filters.get('offset') or 0)))
    except (TypeError,ValueError):
        offset = 0
    return dict(players=ordered[offset:offset+30],total=len(ordered),offset=offset)


def owner_tickets(c):
    output = []
    for t in rows(c,'tickets'):
        owner = text(t.get('owner_name'))
        if not owner or owner.casefold() in TEST_OWNERS or owner.upper() in SPORTS or str(t.get('environment','')).upper()!='REAL':
            continue
        legs = rows(c,'ticket_legs','ticket_id=?',(t['id'],))
        try:
            metadata=json.loads(t.get('notes') or '{}')
            metadata=metadata if isinstance(metadata,dict) and metadata.get('entry_source')=='LIVEINTEL_WORKSPACE' else {}
        except (ValueError,TypeError):metadata={}
        for i,leg in enumerate(legs):
            game=rows(c,'games','id=? AND sport=?',(leg.get('game_id'),leg.get('sport')))
            if game:leg['game']=text(game[0]['away_team'])+' @ '+text(game[0]['home_team'])
            else:
                extra=metadata.get('legs',[])
                item=extra[i] if isinstance(extra,list) and len(extra)>i and isinstance(extra[i],dict) else {}
                leg['game']=text(item.get('game_label'))

        status = str(t.get('status','')).upper()
        if status in ('PENDING','OPEN'): group = 'OPEN'
        elif status in ('WIN','WON'): group = 'WON'
        elif status in ('LOSS','LOST'): group = 'LOST'
        elif status in ('VOID','VOIDED','PUSH','PUSHED'): group = 'VOID'
        elif status=='SETTLED' and number(t.get('actual_payout')) is not None and number(t.get('wager_amount')) is not None:
            group = 'WON' if t['actual_payout']>t['wager_amount'] else 'LOST' if t['actual_payout']<t['wager_amount'] else 'VOID'
        else: group = 'HISTORY'
        output.append(dict(id=t['id'],owner=owner,book=text(t.get('sportsbook_platform')),stake=number(t.get('wager_amount')),
            payout=number(t.get('potential_payout')),actual_payout=number(t.get('actual_payout')),group=group,
            placed=t.get('date_time_placed'),settled=t.get('settled_at'),
            legs=[{k:l.get(k) for k in ('sport','game_id','player_id','player','team','market','line','side','odds','game')} for l in legs]))
    return sorted(output,key=lambda t:t['id'],reverse=True)


def bobby(c, tickets, clock):
    own = [t for t in tickets if t['owner'].casefold()=='bobby']
    opened = [t for t in own if t['group']=='OPEN']
    accounts = [a for a in rows(c,'bankroll_accounts') if str(a.get('owner_name','')).casefold()=='bobby' and a.get('environment')=='REAL']
    today = clock.astimezone(ZONE).date()
    settled = [t for t in own if t['group'] in ('WON','LOST','VOID')]
    today_pl = sum(t['actual_payout']-t['stake'] for t in settled if instant(t.get('settled')) and instant(t['settled']).astimezone(ZONE).date()==today and t['actual_payout'] is not None and t['stake'] is not None)
    return dict(name='BOBBY',bankroll=number(accounts[-1].get('current_bankroll')) if accounts else None,
        exposure=sum(t['stake'] or 0 for t in opened),today_pl=today_pl,
        wins=sum(t['group']=='WON' for t in settled),losses=sum(t['group']=='LOST' for t in settled),
        open_count=len(opened),tickets=opened)



def player_team(c, leg):
    teams = {p['team'] for p in rows(c,'multi_sport_players','game_id=? AND sport=? AND player_id=?',
        (leg['game_id'],leg['sport'],str(leg.get('player_id') or ''))) if p['player_name']==leg.get('player')}
    return next(iter(teams)) if len(teams)==1 else ''


def picks(c, games, clock):
    proposals, _ = scan_offers(c,clock)
    by_offer = {p['offer_id']:p for p in proposals}
    lookup = {g['id']:g for g in games if g['status'] in ('PRE','LIVE')}
    specialists = []
    for ai in ('BASKETBALL_AI','NFL_AI','CFB_AI'):
        try:
            state = snapshot(c,ai)
            decision = latest_decision(c,ai)
            ledger = ticket_views(c,ai)
            survival = awards(c,ai)
        except (ValueError,sqlite3.Error):
            specialists.append(dict(id=ai,name=ai.replace('_',' '),available=False))
            continue
        settled = [t for t in ledger if t['ticket_result'] in ('WIN','LOSS','PUSH')]
        recent = sorted(settled,key=lambda t:t.get('settled_at') or '',reverse=True)[:5]
        current = None
        try:
            proposal = json.loads(decision.get('proposal_json') or 'null') or {}
            ticket = next((t for t in ledger if t['id']==decision.get('ticket_id') and t['ticket_result']=='PENDING'),None)
            candidate = by_offer.get(proposal.get('offer_id'))
            if ticket and candidate and decision.get('action','').startswith('BET $') and fresh(decision.get('created_at'),clock,120):
                legs = ticket['legs']
                if all(l['game_id'] in lookup and l['sport']==lookup[l['game_id']]['sport'] for l in legs):
                    current = dict(ticket_id=ticket['id'],book=ticket['book'],stake=ticket['stake_cents']/100,
                        payout=ticket['potential_payout_cents']/100,confidence=ticket['confidence'],
                        odds=ticket.get('odds_decimal'),multiplier=ticket.get('payout_multiplier'),
                        legs=[dict(sport=l['sport'],game_id=l['game_id'],player_id=l['player_id'],player=l['player'],team=player_team(c,l),
                            market=l['market'],line=l['line'],side=l['side'],game=lookup[l['game_id']]['away']+' @ '+lookup[l['game_id']]['home'],
                            status=lookup[l['game_id']]['status']) for l in legs])
        except (ValueError,TypeError,KeyError):
            pass
        specialists.append(dict(id=ai,name=ai.replace('_',' '),available=True,bankroll=state['current_bankroll'],goal=10000,
            progress=round((state['current_bankroll']-100)/9900*100,2),wins=state['wins'],losses=state['losses'],
            recent=[t['ticket_result'] for t in recent],survival_score=survival['SURVIVAL_SCORE'],
            award=(survival.get('CURRENT_BADGES') or ['No award yet'])[-1],exposure=state['pending_exposure'],pick=current,
            offer_available=any(p['ai_id']==ai for p in proposals)))
    return specialists


def watch(c, games, clock):
    lookup = {g['id']:g for g in games}
    output = []
    pinned = sorted(rows(c,'owner_watchlist','is_pinned=1'),key=lambda r:r.get('added_at') or '',reverse=True)[:5]
    for pin in pinned:
        matches = [p for p in rows(c,'multi_sport_players','player_id=? AND sport=?',(str(pin['player_id']),pin['sport']))
            if p['game_id'] in lookup and p['team'] in (lookup[p['game_id']]['home'],lookup[p['game_id']]['away'])]
        player = matches[0] if matches else None
        game = lookup[player['game_id']] if player else next((g for g in games if str(pin['player_id']).startswith('team:') and g['sport']==pin['sport'] and pin['team'] in (g['home'],g['away'])),None)
        output.append(dict(id=str(pin['player_id']),name=pin['player_name'],team=player['team'] if player else pin['team'],sport=pin['sport'],
            status=game['status'] if game else 'NO GAME TODAY',game_id=game['id'] if game else None,
            game=game['away']+' @ '+game['home'] if game else '',stats=player_stats(player,game,clock) if player else []))
    return output


def intelligence(c, games, clock):
    live = [g for g in games if g['category']=='LIVE']
    alerts, impacts, opportunities = [], [], []
    for g in live:
        name = g['away']+' @ '+g['home']
        period = number(g.get('period'))
        clock_text = text(g.get('clock'))
        seconds = None
        try:
            m,s = clock_text.split(':')
            seconds = int(m)*60+int(s)
        except (ValueError,TypeError):
            pass
        labels = []
        if g.get('red_zone'): labels.append('Red zone')
        if period==4: labels.append('4th quarter')
        if period and seconds is not None and seconds<=120: labels.append('Final 2 minutes')
        if 'half' in clock_text.lower(): labels.append('Halftime')
        if g['sport'] in ('NFL','CFB') and number(g.get('away_score')) is not None and number(g.get('home_score')) is not None and abs(g['away_score']-g['home_score'])<=8: labels.append('One-score game')
        types = {'RED_ZONE':'Red zone','TWO_MINUTE':'Final 2 minutes','TWO_MINUTE_WARNING':'Final 2 minutes',
            'HALFTIME':'Halftime','FOURTH_QUARTER':'4th quarter','ONE_SCORE_GAME':'One-score game',
            'POSSESSION_CHANGE':'Possession change','INJURY':'Injury update','ROLE_CHANGE':'Role change'}
        for alert in rows(c,'situation_alerts','game_id=?',(g['id'],)):
            label = types.get(str(alert.get('alert_type','')).upper())
            if label and fresh(alert.get('created_at'),clock) and label not in labels:
                labels.append(label)
        alerts.extend(dict(game=name,label=label) for label in labels)
        if period and period>=3:
            opportunities.append(dict(game=name,label='4Q WATCH' if period>=4 else '2H WATCH'))
        for p in rows(c,'multi_sport_players','game_id=? AND sport=?',(g['id'],g['sport'])):
            stats = player_stats(p,g,clock)
            if stats and p['team'] in (g['home'],g['away']):
                impacts.append(dict(player=p['player_name'],team=p['team'],game=name,stats=stats))
    return dict(alerts=alerts,situations=live,impacts=impacts[:12],opportunities=opportunities)



_SCHEDULE_CACHE = {'day': '', 'at': None, 'times': {}, 'details': {}}


def schedule_times(games, clock):
    """Read-only start-time hydration for known current database games only.

    This does not discover, insert, or change any game or player. ESPN's date and
    both team identities must match the already-linked production context.
    """
    from concurrent.futures import ThreadPoolExecutor
    from urllib.request import Request, urlopen
    from daily_espn_pipeline import event_date, teams
    day = clock.astimezone(ZONE).date().isoformat()
    cache = _SCHEDULE_CACHE
    if cache['day']==day and cache['at'] and 0 <= (clock-cache['at']).total_seconds()<300:
        return {g['id']:cache['times'][g['id']] for g in games if g['id'] in cache['times']}
    from sport_math import PATHS
    paths = PATHS
    known = {(g['sport'],g['id']):g for g in games}
    details = {}
    def fetch(sport):
        result = {}
        if sport not in paths: return result
        url = 'https://site.api.espn.com/apis/site/v2/sports/'+paths[sport]+'/scoreboard?dates='+day.replace('-','')+'&limit=1000'+('&groups=80' if sport=='CFB' else '')
        try:
            with urlopen(Request(url,headers={'User-Agent':'LiveIntel/1.0'}),timeout=3) as response:
                payload = json.loads(response.read())
            for event in payload.get('events',[]):
                gid = str(event.get('id',''))
                g = known.get((sport,gid))
                if not g or event_date(event.get('date'))!=day:
                    continue
                sides = teams((event.get('competitions') or [{}])[0])
                if sides['home']['abbr']!=g['home'] or sides['away']['abbr']!=g['away']:
                    continue
                stamp = instant(event.get('date'))
                if stamp:
                    result[gid] = stamp.astimezone(ZONE).strftime('%-I:%M %p ET')
                    details[gid] = dict(start_time=result[gid],start_at=stamp.isoformat(),
                        home_name=text(sides['home'].get('name') or (next((x.get('team',{}).get('displayName') for x in (event.get('competitions') or [{}])[0].get('competitors',[]) if x.get('homeAway')=='home'),''))),
                        away_name=text(sides['away'].get('name') or (next((x.get('team',{}).get('displayName') for x in (event.get('competitions') or [{}])[0].get('competitors',[]) if x.get('homeAway')=='away'),''))))
        except (OSError,ValueError,TypeError,KeyError):
            pass
        return result
    found = {}
    available_sports = sorted({g['sport'] for g in games})
    if available_sports:
        with ThreadPoolExecutor(max_workers=4) as pool:
            for result in pool.map(fetch,available_sports):
                found.update(result)
    cache.update(day=day,at=clock,times=found,details=details)
    return found


def board_data(c, view='overview', filters=None, clock=None):
    clock = clock or datetime.now(timezone.utc)
    if view in ('terminal', 'terminal-player', 'terminal-heat'):
        from terminal_view import project
        return project(c, view, filters or {}, clock)
    if view=='research':
        from math_lab import research
        return research(c,clock)
    if view=='heat':
        from heat_view import heat_data
        return heat_data(c,filters,clock)
    games = current_games(c,clock)
    if view=='schedule':
        times=schedule_times(games,clock)
        return dict(times=times,details=_SCHEDULE_CACHE.get('details',{}))
    if view=='players':
        return players(c,games,clock,filters or {})
    tickets = owner_tickets(c)
    return dict(slate_date=clock.astimezone(ZONE).date().isoformat(),games=games,
        bobby=bobby(c,tickets,clock),specialists=picks(c,games,clock),tickets=tickets,
        watch=watch(c,games,clock),intelligence=intelligence(c,games,clock),
        offers=[dict(book=p['book'],players=[l['player'] for l in p['legs']],ai_id=p['ai_id']) for p in scan_offers(c,clock)[0]])
