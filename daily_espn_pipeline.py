"""LiveIntel ESPN current-date players for NBA, WNBA, NFL and CFB.

Current player context and validated completed boxscores share ESPN event identities.
Historical writes use the completed-game adapter; dates come only from ESPN.
No AI or dashboard code is invoked.
"""
import argparse
import datetime
import json
import math
import os
import re
import sqlite3
import tempfile
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo
from ingestor import normalize_game_lifecycle

DB_PATH = '/var/lib/liveintel/intel.db'
REPORT_PATH = '/var/lib/liveintel/daily_players_report.json'
UA = {'User-Agent': 'Mozilla/5.0'}
SLATE_ZONE = ZoneInfo('America/New_York')
LEAGUES = [
    {'sport': 'NBA', 'espn_path': 'basketball/nba'},
    {'sport': 'WNBA', 'espn_path': 'basketball/wnba'},
    {'sport': 'NFL', 'espn_path': 'football/nfl'},
    {'sport': 'CFB', 'espn_path': 'football/college-football'},
]
BASKETBALL = {'NBA', 'WNBA'}
FOOTBALL = {'NFL', 'CFB'}
STAT_FIELDS = ('points', 'rebounds', 'assists', 'fga', 'fgm', 'three_pa', 'three_pm',
               'pass_attempts', 'completions', 'pass_yards', 'rush_attempts', 'rush_yards',
               'targets', 'receptions', 'receiving_yards')
CONTEXT_COLUMNS = {'team_id': 'TEXT', 'slate_date': 'TEXT',
                   'player_identity_available': 'INTEGER', 'live_stats_available': 'INTEGER',
                   'identity_source': 'TEXT', 'starter': 'INTEGER', 'minutes': 'REAL'}


class RosterIntegrityError(ValueError):
    """A supplemental response contradicts the numeric team or season identity."""


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute('PRAGMA busy_timeout = 30000')
    conn.row_factory = sqlite3.Row
    return conn


def fetch_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read())


def parse_made_att(value):
    match = re.fullmatch(r'\s*(\d+)\s*[-/]\s*(\d+)\s*', str(value or ''))
    return tuple(map(int, match.groups())) if match else (None, None)


def _int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def event_date(value):
    """ESPN event instants, never database update times, establish slate dates."""
    stamp = datetime.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('event date has no timezone')
    return stamp.astimezone(SLATE_ZONE).date().isoformat()


def numeric_id(value):
    value = str(value).strip() if value is not None else ''
    return value if re.fullmatch(r'[0-9]+', value) else None


def teams(competition):
    sides = {}
    for competitor in competition.get('competitors') or []:
        side = competitor.get('homeAway')
        team = competitor.get('team') or {}
        abbreviation = str(team.get('abbreviation') or '').strip()
        if side not in ('home', 'away') or side in sides or not abbreviation:
            raise ValueError('invalid game/team metadata')
        sides[side] = {'abbr': abbreviation, 'id': str(team.get('id') or '')}
    if set(sides) != {'home', 'away'} or sides['home']['abbr'] == sides['away']['abbr']:
        raise ValueError('two distinct game teams required')
    return sides


def game_status(competition):
    status = competition.get('status') or {}
    kind = status.get('type') or {}
    if kind.get('completed') is True or str(kind.get('state') or '').lower() in ('post', 'final'):
        return 'post'
    name = str((status.get('type') or {}).get('name') or '').upper()
    for token, value in (('POSTPONED', 'postponed'), ('CANCEL', 'canceled'),
                         ('SUSPEND', 'suspended'), ('DELAY', 'delayed')):
        if token in name:
            return value
    lifecycle = normalize_game_lifecycle(status)[0]
    return 'post' if lifecycle == 'POST' else lifecycle


def discover_slate(sport, espn_path, day):
    params = {'dates': day.replace('-', ''), 'limit': 1000}
    if sport == 'CFB':
        params['groups'] = 80  # All FBS games, rather than the default ranked subset.
    data = fetch_json('https://site.api.espn.com/apis/site/v2/sports/' + espn_path +
                      '/scoreboard?' + urllib.parse.urlencode(params))
    if not isinstance(data.get('events'), list):
        raise ValueError('scoreboard event list missing')
    games = {}
    for event in data['events']:
        competitions = event.get('competitions') or []
        comp = competitions[0] if competitions else {}
        actual_date = event_date(event.get('date') or comp.get('date'))
        if actual_date != day:
            continue  # NFL weekly responses and date spillovers are not today's slate.
        gid = numeric_id(event.get('id'))
        if not gid:
            raise ValueError('invalid scoreboard game ID')
        if comp.get('id') is not None and numeric_id(comp['id']) != gid:
            raise ValueError('scoreboard competition ID mismatch')
        sides = teams(comp)
        game = {'id': gid, 'sport': sport, 'game_date': actual_date,
                'status': game_status(comp),
                'home_team': sides['home']['abbr'], 'away_team': sides['away']['abbr'],
                'team_ids': {v['abbr']: v['id'] for v in sides.values()}}
        season = event.get('season') or data.get('season') or next(
            (league.get('season') for league in data.get('leagues') or [] if league.get('season')), {})
        game['season_year'] = numeric_id(season.get('year'))
        if gid in games and games[gid] != game:
            raise ValueError('conflicting scoreboard game identity')
        games[gid] = game
    return list(games.values())


def validate_summary(summary, game):
    header = summary.get('header') or {}
    if numeric_id(header.get('id')) != game['id']:
        raise ValueError('summary game ID mismatch')
    league = str((header.get('league') or {}).get('abbreviation') or '').upper()
    allowed = {'CFB', 'NCAAF', 'NCAAFB'} if game['sport'] == 'CFB' else {game['sport']}
    if league and league not in allowed:
        raise ValueError('summary sport mismatch')
    comps = header.get('competitions') or []
    if not comps:
        raise ValueError('summary competition missing')
    comp = comps[0]
    if comp.get('id') is not None and numeric_id(comp['id']) != game['id']:
        raise ValueError('summary competition ID mismatch')
    if event_date(comp.get('date') or header.get('date')) != game['game_date']:
        raise ValueError('summary date mismatch')
    sides = teams(comp)
    for side in ('home', 'away'):
        expected = game[side + '_team']
        if sides[side]['abbr'] != expected:
            raise ValueError('summary team mismatch')
        expected_id = game['team_ids'][expected]
        if expected_id and sides[side]['id'] != expected_id:
            raise ValueError('summary team ID mismatch')


def summary_player_groups(summary, game):
    """Read event boxscores and any event-attached identity roster."""
    groups = list((summary.get('boxscore') or {}).get('players') or [])
    for roster in summary.get('rosters') or []:
        team = dict(roster.get('team') or {})
        # Some event rosters supply a team ID without its abbreviation.
        if not team.get('abbreviation'):
            matches = [abbr for abbr, tid in game['team_ids'].items()
                       if tid and str(team.get('id') or '') == tid]
            if len(matches) == 1:
                team['abbreviation'] = matches[0]
        athletes = []
        for entry in roster.get('roster') or roster.get('athletes') or []:
            if entry.get('active') is False or entry.get('didNotPlay'):
                continue
            athletes.append({'athlete': entry.get('athlete') or entry, 'stats': []})
        groups.append({'team': team, '_identity_source': 'ESPN_EVENT_ROSTER',
                       'statistics': [{'name': 'event_roster', 'athletes': athletes}]})
    return groups


def roster_athletes(entries):
    """ESPN basketball uses a flat list; football also groups athletes under items."""
    if not isinstance(entries, list):
        raise ValueError('ESPN roster athlete list missing')
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('invalid ESPN roster entry')
        if 'items' in entry:
            yield from roster_athletes(entry['items'])
        else:
            yield entry


def team_roster_group(data, game, abbreviation, source_url):
    """Join a season/team roster to an actual scoreboard opponent, never to guessed teams."""
    tid = numeric_id(game['team_ids'].get(abbreviation))
    team = data.get('team') or {}
    if not tid or numeric_id(team.get('id')) != tid:
        raise RosterIntegrityError('ESPN roster team ID mismatch')
    if numeric_id((data.get('season') or {}).get('year')) != game.get('season_year'):
        raise RosterIntegrityError('ESPN roster season mismatch')
    if team.get('abbreviation') and team['abbreviation'] != abbreviation:
        raise ValueError('ESPN roster team abbreviation mismatch')
    if data.get('status') not in (None, 'success'):
        raise ValueError('ESPN roster request unsuccessful')
    athletes = []
    for entry in roster_athletes(data.get('athletes')):
        info = entry.get('athlete') or entry
        status = info.get('status') or {}
        if entry.get('active') is False or info.get('active') is False or str(status.get('type') or '').lower() in (
                'inactive', 'retired', 'released', 'suspended'):
            continue
        # Never transfer season/career totals or placeholder zeroes to game stats.
        athletes.append({'athlete': info, 'stats': []})
    return {'team': {'id': tid, 'abbreviation': abbreviation}, '_identity_source': source_url,
            'statistics': [{'name': 'team_roster', 'athletes': athletes}]}


def player_groups(summary, game, espn_path, cache, source_errors, integrity_errors=None):
    groups = summary_player_groups(summary, game)
    if game['status'] != 'PRE' or game['sport'] not in ('NBA', 'WNBA', 'CFB'):
        return groups
    for abbreviation, team_id in game['team_ids'].items():
        present = any((group.get('team') or {}).get('abbreviation') == abbreviation and
                      any(numeric_id((athlete.get('athlete') or {}).get('id'))
                          for category in group.get('statistics') or []
                          for athlete in category.get('athletes') or []) for group in groups)
        if present:
            continue
        try:
            tid = numeric_id(team_id)
            season = game.get('season_year')
            if not tid or not numeric_id(season):
                raise ValueError('scoreboard team ID and season required for roster fallback')
            url = ('https://site.api.espn.com/apis/site/v2/sports/' + espn_path + '/teams/' + tid +
                   '/roster?' + urllib.parse.urlencode({'season': season}))
            if url not in cache:
                cache[url] = fetch_json(url)
            groups.append(team_roster_group(cache[url], game, abbreviation, url))
        except RosterIntegrityError as exc:
            target = integrity_errors if integrity_errors is not None else source_errors
            target.append(f"{game['id']} {abbreviation}: {exc}")
        except Exception as exc:
            source_errors.append(f"{game['id']} {abbreviation}: {exc}")
    return groups


def ensure_context_columns(conn):
    existing = {r['name'] for r in conn.execute('PRAGMA table_info(multi_sport_players)')}
    for name, kind in CONTEXT_COLUMNS.items():
        if name not in existing:
            conn.execute(f'ALTER TABLE multi_sport_players ADD COLUMN {name} {kind}')


def persist_game(conn, game):
    existing = conn.execute('SELECT * FROM games WHERE id=?', (game['id'],)).fetchone()
    if existing:
        if existing['sport'] != game['sport']:
            raise ValueError('stored game sport mismatch')
        if (existing['home_team'], existing['away_team']) != (game['home_team'], game['away_team']):
            raise ValueError('stored game team mismatch')
        if str(existing['status']).lower() in ('post', 'final') and game['status'] != 'post':
            raise ValueError('refusing to reactivate a completed game')
        if str(existing['status']).lower() in ('post', 'final'):
            return
        if str(existing['status']).lower() in ('live', 'in', 'halftime') and game['status'] == 'PRE':
            raise ValueError('refusing to regress a live game to PRE')
    conn.execute('INSERT INTO games(id,sport,home_team,away_team,status,last_updated) VALUES(?,?,?,?,?,?) '
                 'ON CONFLICT(id) DO UPDATE SET status=excluded.status,last_updated=excluded.last_updated',
                 (game['id'], game['sport'], game['home_team'], game['away_team'], game['status'],
                  datetime.datetime.now(SLATE_ZONE).isoformat(' ', 'seconds')))


def persist_players(conn, game, rows):
    """Refresh verified identities without deleting history or changing an old slate date."""
    if not conn.in_transaction:
        conn.execute('BEGIN IMMEDIATE')
    existing_game = conn.execute('SELECT status FROM games WHERE id=?', (game['id'],)).fetchone()
    if game['status'] == 'post' or (existing_game and str(existing_game['status']).lower() in ('post', 'final')):
        raise ValueError('completed game player rows are immutable to this pipeline')
    if not rows:
        return 0
    ensure_context_columns(conn)
    stored = conn.execute('SELECT * FROM multi_sport_players WHERE sport=? AND game_id=?',
                          (game['sport'], game['id'])).fetchall()
    if any(r['game_date'] is not None and str(r['game_date'])[:10] != game['game_date'] for r in stored):
        raise ValueError('stored slate date mismatch; historical rows preserved')
    created, seen = 0, set()
    for row in rows:
        if row['player_id'] in seen:
            raise ValueError('duplicate player identity in incoming rows')
        seen.add(row['player_id'])
        matches = [r for r in stored if str(r['player_id']) == row['player_id'] and r['segment'] == 'FULL GAME']
        if len(matches) > 1:
            raise ValueError('existing duplicate player identities; historical rows preserved')
        if any((r['player_name'], r['team']) != (row['player_name'], row['team']) for r in matches):
            raise ValueError('stored player/team identity mismatch')
        if matches:
            updates = {k: v for k, v in row.items() if v is not None and (k != 'position' or v)}
            for match in matches:
                # A transient empty boxscore must not erase already observed live production.
                updates['live_stats_available'] = int(bool(match['live_stats_available'] or row['live_stats_available']))
                conn.execute('UPDATE multi_sport_players SET ' + ','.join(k+'=?' for k in updates) + ' WHERE id=?',
                             (*updates.values(), match['id']))
        else:
            cols = ','.join(row)
            conn.execute(f"INSERT INTO multi_sport_players ({cols}) VALUES ({','.join('?' for _ in row)})", list(row.values()))
            created += 1
    return created


def reconcile_previous_games(conn, sport, espn_path, current_ids, fatal_errors=None):
    """Refresh old active statuses only from each game's own current ESPN summary."""
    errors = []
    old = conn.execute("SELECT * FROM games WHERE sport=? AND lower(status) IN "
                       "('pre','scheduled','in','live','halftime')", (sport,)).fetchall()
    for existing in old:
        if existing['id'] in current_ids:
            continue
        try:
            if not numeric_id(existing['id']):
                raise ValueError('invalid stored game ID')
            summary = fetch_json('https://site.api.espn.com/apis/site/v2/sports/' + espn_path +
                                 '/summary?event=' + existing['id'])
            comp = summary['header']['competitions'][0]
            sides = teams(comp)
            game = dict(id=existing['id'], sport=sport, game_date=event_date(comp.get('date')),
                        home_team=sides['home']['abbr'], away_team=sides['away']['abbr'],
                        team_ids={v['abbr']: v['id'] for v in sides.values()}, status=game_status(comp))
            validate_summary(summary, game)
            with conn:
                if game['status'] == 'post':
                    from espn_history import persist_completed
                    persist_completed(conn, summary, game)
                else:
                    persist_game(conn, game)
        except json.JSONDecodeError as exc:
            errors.append(f"{existing['id']}: {exc}")
        except (sqlite3.Error, ValueError, TypeError, KeyError) as exc:
            if fatal_errors is None:
                errors.append(f"{existing['id']}: {exc}")
            else:
                category = 'database' if isinstance(exc, sqlite3.Error) else 'identity_integrity'
                fatal_errors.append({'category': category, 'reason': f"{existing['id']}: {exc}"})
        except Exception as exc:
            errors.append(f"{existing['id']}: {exc}")
    return errors


def participants(side, game, errors):
    """Validate identities once per game/player, merging categories without guessing positions."""
    records, invalid = {}, set()
    expected_teams = (game['home_team'], game['away_team'])
    for group in side:
        team = group.get('team') or {}
        abbreviation = str(team.get('abbreviation') or '').strip()
        expected_id = game['team_ids'].get(abbreviation)
        group_valid = abbreviation in expected_teams and (
            not expected_id or str(team.get('id') or '') == expected_id)
        for category in group.get('statistics') or []:
            labels = category.get('labels') or category.get('names') or []
            for athlete in category.get('athletes') or []:
                if athlete.get('didNotPlay'):
                    continue
                info = athlete.get('athlete') or {}
                pid = numeric_id(info.get('id'))
                name = str(info.get('displayName') or info.get('fullName') or info.get('shortName') or '').strip()
                athlete_team = info.get('team') or {}
                athlete_team_valid = (
                    not athlete_team.get('abbreviation') or athlete_team['abbreviation'] == abbreviation) and (
                    not athlete_team.get('id') or not expected_id or str(athlete_team['id']) == expected_id)
                reason = None
                if not pid:
                    reason = 'numeric player ID required'
                elif not name:
                    reason = 'player name required'
                elif not group_valid or not athlete_team_valid:
                    reason = 'player/game team mismatch'
                elif pid in records and (records[pid]['team'] != abbreviation or
                                         records[pid]['player_name'].casefold() != name.casefold()):
                    reason = 'conflicting player identity'
                if reason:
                    message = f"{game['sport']} {game['id']} {pid or info.get('id')} {reason}"
                    if message not in errors:
                        errors.append(message)
                    if pid:
                        invalid.add(pid); records.pop(pid, None)
                    continue
                if pid in invalid:
                    continue
                position = info.get('position') or ''
                if isinstance(position, dict):
                    position = position.get('abbreviation') or ''
                position = str(position).strip()
                if position.upper() in ('POS', 'UNKNOWN', 'N/A', 'NULL'):
                    position = ''
                record = records.setdefault(pid, {
                    'sport': game['sport'], 'player_id': pid, 'player_name': name,
                    'team': abbreviation, 'position': position, 'game_id': game['id'],
                    'game_date': game['game_date'], 'segment': 'FULL GAME',
                    'team_id': numeric_id(expected_id), 'slate_date': game['game_date'],
                    'player_identity_available': 1,
                    'identity_source': group.get('_identity_source') or 'ESPN_EVENT_BOXSCORE',
                    'last_updated': datetime.datetime.now(SLATE_ZONE).isoformat(' ', 'seconds'),
                    '_categories': [],
                })
                if position and not record['position']:
                    record['position'] = position
                if isinstance(athlete.get('starter'), bool):
                    record['starter'] = int(athlete['starter'])
                raw = dict(zip([str(label).upper() for label in labels], athlete.get('stats') or []))
                if game.get('status') == 'PRE':
                    raw = {}  # Pregame zeroes/season totals are not game production.
                record['_categories'].append((str(category.get('name') or category.get('displayName') or '').lower(), raw))
    return records


def mark_stats_available(row, game):
    row['live_stats_available'] = int(game.get('status') == 'LIVE' and
                                      any(row.get(field) is not None for field in STAT_FIELDS))


def extract_basketball(side, game, errors, warnings):
    if game['sport'] not in BASKETBALL:
        raise ValueError('basketball extractor received wrong sport')
    records = participants(side, game, errors)
    for row in records.values():
        row.update(points=None, rebounds=None, assists=None, fga=None, fgm=None, three_pa=None, three_pm=None, minutes=None)
        for _, raw in row.pop('_categories'):
            fgm, fga = parse_made_att(raw.get('FG'))
            tpm, tpa = parse_made_att(raw.get('3PT'))
            minutes = raw.get('MIN')
            try:
                if ':' in str(minutes):
                    mm, ss = str(minutes).split(':'); minutes = int(mm) + int(ss)/60 if 0<=int(ss)<60 else None
                else: minutes = float(minutes) if minutes is not None else None
                if minutes is not None and (not math.isfinite(minutes) or minutes<0): minutes=None
            except (TypeError,ValueError): minutes=None
            values = dict(minutes=minutes, points=_int(raw.get('PTS')), rebounds=_int(raw.get('REB')),
                          assists=_int(raw.get('AST')), fgm=fgm, fga=fga, three_pm=tpm, three_pa=tpa)
            row.update({key: value for key, value in values.items() if value is not None})
        mark_stats_available(row, game)
        if not row['position']:
            warnings.append(f"{game['sport']} {game['id']} {row['player_id']} missing position")
    return list(records.values())


def extract_football(side, game, errors, warnings):
    """Adapt the category merge and C/ATT slash parsing from enrich_cfb_history.py.

    Only parsing is reused: no backfill, historical writes or inferred zero stats.
    """
    if game['sport'] not in FOOTBALL:
        raise ValueError('football extractor received wrong sport')
    records = participants(side, game, errors)
    for row in records.values():
        row.update(pass_attempts=None, completions=None, pass_yards=None,
                   rush_attempts=None, rush_yards=None, targets=None, receptions=None, receiving_yards=None)
        for category, raw in row.pop('_categories'):
            if 'pass' in category:
                cm, ca = parse_made_att(raw.get('C/ATT') or raw.get('C/A'))
                row.update({k:v for k,v in dict(completions=cm, pass_attempts=ca, pass_yards=_int(raw.get('YDS'))).items() if v is not None})
            elif 'rush' in category:
                row.update({k:v for k,v in dict(rush_attempts=_int(raw.get('CAR')), rush_yards=_int(raw.get('YDS'))).items() if v is not None})
            elif 'receiv' in category:
                target = next((raw[k] for k in ('TGT', 'TGTS', 'TARGETS') if raw.get(k) is not None), None)
                row.update({k:v for k,v in dict(targets=_int(target), receptions=_int(raw.get('REC')), receiving_yards=_int(raw.get('YDS'))).items() if v is not None})
        mark_stats_available(row, game)
        if not row['position']:
            warnings.append(f"{game['sport']} {game['id']} {row['player_id']} missing position")
    return list(records.values())


def link_to_history(conn, sport, game_ids, player_ids):
    linked = 0
    marks = ','.join('?' for _ in game_ids)
    for pid in player_ids:
        match = conn.execute(
            f'SELECT 1 FROM player_game_stats p JOIN games g ON g.id=p.game_id '
            f'WHERE g.sport=? AND p.player_id=? AND p.game_id NOT IN ({marks}) LIMIT 1',
            (sport, pid, *game_ids)).fetchone()
        linked += match is not None
    return linked, len(player_ids) - linked


def check_usable_context(conn, game):
    """Assess actual persisted game/player linkage, not aggregate player counts."""
    rows = conn.execute("SELECT * FROM multi_sport_players WHERE sport=? AND game_id=? AND segment='FULL GAME'",
                        (game['sport'], game['id'])).fetchall()
    players, covered = set(), set()
    for row in rows:
        pid = numeric_id(row['player_id'])
        name = str(row['player_name'] or '').strip()
        team = row['team']
        if not pid or not name or team not in (game['home_team'], game['away_team']):
            raise ValueError('invalid stored player/game/team linkage')
        if str(row['game_date'])[:10] != game['game_date']:
            raise ValueError('stored player slate date mismatch')
        if 'slate_date' in row.keys() and row['slate_date'] and row['slate_date'] != game['game_date']:
            raise ValueError('stored player context slate date mismatch')
        if 'team_id' in row.keys() and row['team_id'] and game['team_ids'].get(team) and str(row['team_id']) != game['team_ids'][team]:
            raise ValueError('stored player numeric team ID mismatch')
        if pid in players:
            raise ValueError('duplicate stored player identity')
        players.add(pid)
        covered.add(team)
    return bool(players), sorted(set(game['team_ids']) - covered)


def classify_result(result):
    """error_count remains the fatal-only exit contract used by both entry points."""
    result['fatal_error_count'] = result['error_count'] = len(result['fatal_errors'])
    result['warning_count'] = len(result['warning_details'])
    if result['fatal_error_count']:
        result['status'] = 'source_fetch_failed' if result.get('source_error') else 'partial_failure'
    else:
        result['status'] = 'ok_with_warnings' if result['warning_count'] else 'ok'


def process_league(conn, sport, espn_path, today):
    if (sport, espn_path) not in {(league['sport'], league['espn_path']) for league in LEAGUES}:
        raise ValueError('unsupported sport/ESPN route')
    datetime.date.fromisoformat(today)
    result = {'sport': sport, 'slate_date': today, 'timezone': str(SLATE_ZONE),
              'status': 'ok', 'games': 0, 'pre': 0, 'live': 0, 'final': 0, 'players': 0,
              'pregame_identities_created': 0, 'player_identity_available': 0, 'live_stats_available': 0,
              'roster_source_errors': [],
              'roster_integrity_errors': [], 'fatal_errors': [], 'warning_details': [],
              'fatal_error_count': 0, 'warning_count': 0, 'usable_games': 0,
              'linked': 0, 'unlinked': 0, 'identity_errors': 0, 'position_warnings': 0,
              'error_count': 0, 'identity_error_details': [], 'position_warning_details': []}
    errors, warnings, pids = result['identity_error_details'], result['position_warning_details'], set()
    try:
        games = discover_slate(sport, espn_path, today)
    except Exception as exc:
        games = []
        result.update(status='source_fetch_failed', error_count=1, source_error=str(exc))
        result['fatal_errors'].append({'category': 'discovery', 'reason': str(exc)})
    result['games'] = len(games)
    result['game_ids'] = [g['id'] for g in games]
    for field, status in (('pre', 'PRE'), ('live', 'LIVE'), ('final', 'post')):
        result[field] = sum(g['status'] == status for g in games)
    extractor = extract_basketball if sport in BASKETBALL else extract_football
    linked, observed_players, live_players, roster_cache = set(), set(), set(), {}
    for game in games:
        gid = game['id']
        previous = conn.execute('SELECT status FROM games WHERE id=?', (gid,)).fetchone()
        if game['status'] == 'post' or (previous and str(previous['status']).lower() in ('post', 'final')):
            try:
                from espn_history import persist_completed
                summary = fetch_json('https://site.api.espn.com/apis/site/v2/sports/' + espn_path + '/summary?event=' + gid)
                with conn:
                    count = persist_completed(conn, summary, game)
                result['completed_players'] = result.get('completed_players', 0) + count
            except Exception as exc:
                result['fatal_errors'].append({'category': 'completed_ingestion', 'reason': f'{gid}: {exc}'})
            continue
        try:
            with conn:
                persist_game(conn, game)
        except Exception as exc:
            result['error_count'] += 1
            result['status'] = 'partial_failure'
            result.setdefault('source_errors', []).append(f'{gid}: {exc}')
            result['fatal_errors'].append({'category': 'database' if isinstance(exc, sqlite3.Error) else 'identity_integrity',
                                           'reason': f'{gid}: {exc}'})
            continue
        try:
            summary = fetch_json('https://site.api.espn.com/apis/site/v2/sports/' + espn_path + '/summary?event=' + gid)
        except Exception as exc:
            result.setdefault('summary_warnings', []).append(f'{gid}: {exc}')
            if game['status'] == 'PRE' and sport in ('NBA', 'WNBA', 'CFB'):
                summary = {}
            else:
                continue
        try:
            if summary:
                validate_summary(summary, game)
                if game_status(summary['header']['competitions'][0]) == 'post':
                    from espn_history import persist_completed
                    with conn:
                        count = persist_completed(conn, summary, game)
                    result['completed_players'] = result.get('completed_players', 0) + count
                    continue
            existing = conn.execute('SELECT sport FROM games WHERE id=?', (gid,)).fetchone()
            if existing and existing['sport'] != sport:
                raise ValueError('stored game sport mismatch')
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(f'{sport} {gid}: {exc}')
            continue
        try:
            groups = player_groups(summary, game, espn_path, roster_cache, result['roster_source_errors'],
                                   result['roster_integrity_errors'])
            for group in groups:
                for category in group.get('statistics') or []:
                    for athlete in category.get('athletes') or []:
                        if not athlete.get('didNotPlay'):
                            info = athlete.get('athlete') or {}
                            observed_players.add((gid, numeric_id(info.get('id')) or json.dumps(info, sort_keys=True)))
            rows = extractor(groups, game, errors, warnings)
            with conn:
                created = persist_players(conn, game, rows)
            if game['status'] == 'PRE':
                result['pregame_identities_created'] += created
        except sqlite3.Error as exc:
            result['fatal_errors'].append({'category': 'database', 'reason': f'{gid}: {exc}'})
            continue
        except Exception as exc:
            errors.append(f'{sport} {gid}: {exc}')
            continue
        if not rows:
            result.setdefault('games_awaiting_player_context', []).append(gid)
        pids.update(row['player_id'] for row in rows)
        linked.update((gid, row['player_id']) for row in rows)
        live_players.update((gid, row['player_id']) for row in rows if row['live_stats_available'])
    result['players'] = len(linked)
    result['player_identity_available'] = len(linked)
    result['live_stats_available'] = len(live_players)
    result['identity_errors'] = len(errors)
    result['position_warnings'] = len(warnings)
    for reason in errors + result['roster_integrity_errors']:
        result['fatal_errors'].append({'category': 'identity_integrity', 'reason': reason})
    result['linked'] = len(linked)
    result['unlinked'] = len(observed_players - linked)
    if games:
        try:
            result['history_linked'], result['history_unlinked'] = link_to_history(conn, sport, [g['id'] for g in games], pids)
        except sqlite3.Error as exc:
            result['fatal_errors'].append({'category': 'database', 'reason': str(exc)})
    # A failed discovery is not evidence for a zero-game league or a status change.
    if result['status'] != 'source_fetch_failed':
        result['reconciliation_errors'] = reconcile_previous_games(conn, sport, espn_path, set(result['game_ids']),
                                                                  result['fatal_errors'])
    for game in games:
        if game['status'] not in ('PRE', 'LIVE'):
            continue
        try:
            usable, missing_teams = check_usable_context(conn, game)
            if not usable:
                if game['status'] == 'LIVE':
                    result['fatal_errors'].append({
                        'category': 'unusable_game_context',
                        'reason': f"{game['id']}: no usable player identities"
                    })
                else:
                    result['warning_details'].append({
                        'category': 'awaiting_player_context',
                        'reason': f"{game['id']}: pregame player identities not yet available"
                    })
            else:
                result['usable_games'] += 1
                if missing_teams:
                    result['warning_details'].append({'category': 'incomplete_team_coverage',
                        'reason': f"{game['id']}: no usable player context for {', '.join(missing_teams)}"})
        except (ValueError, sqlite3.Error) as exc:
            result['fatal_errors'].append({'category': 'database' if isinstance(exc, sqlite3.Error) else 'identity_integrity',
                                           'reason': f"{game['id']}: {exc}"})
    for category, details in (('optional_position', warnings), ('supplemental_roster', result['roster_source_errors']),
                              ('supplemental_summary', result.get('summary_warnings', [])),
                              ('supplemental_reconciliation', result.get('reconciliation_errors', []))):
        result['warning_details'].extend({'category': category, 'reason': reason} for reason in details)
    classify_result(result)
    result['identity_error_details'] = [e['reason'] for e in result['fatal_errors'] if e['category'] == 'identity_integrity']
    result['identity_errors'] = len(result['identity_error_details'])
    if result['status'] == 'source_fetch_failed':
        # Unknown counts must not look like a verified zero-game slate.
        for field in ('games', 'pre', 'live', 'final', 'players', 'linked', 'unlinked',
                      'player_identity_available', 'live_stats_available'):
            result[field] = None
    print(f"{sport}: GAMES={result['games']} PLAYERS={result['players']} "
          f"LINKED={result['linked']} UNLINKED={result['unlinked']} "
          f"IDENTITY_ERRORS={result['identity_errors']} POSITION_WARNINGS={result['position_warnings']} "
          f"FATAL_ERRORS={result['fatal_error_count']} WARNINGS={result['warning_count']} "
          f"SLATE_DATE={today} STATUS={result['status']}")
    for severity, details in (('FATAL', result['fatal_errors']), ('WARNING', result['warning_details'])):
        for detail in details:
            print(f"{sport}: {severity} [{detail['category']}] {detail['reason']}", flush=True)
    try:
        save_report(result)
    except OSError as exc:
        result['report_error'] = str(exc)
        result['fatal_errors'].append({'category': 'report_write', 'reason': str(exc)})
        classify_result(result)
        print(f"{sport}: FATAL [report_write] {exc}", flush=True)
        print(json.dumps(result), flush=True)
    return result


def save_report(result):
    data = {}
    try:
        with open(REPORT_PATH) as source:
            data = json.load(source)
    except (OSError, ValueError):
        pass
    data[result['sport']] = result
    data['generated_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    directory = os.path.dirname(os.path.abspath(REPORT_PATH))
    os.makedirs(directory, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory, delete=False) as output:
            temp_path = output.name
            json.dump(data, output, indent=1)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_path, REPORT_PATH)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', type=datetime.date.fromisoformat,
                        default=datetime.datetime.now(SLATE_ZONE).date(), help='ESPN slate date (America/New_York)')
    args = parser.parse_args()
    conn = None
    try:
        conn = get_db()
        results = [process_league(conn, league['sport'], league['espn_path'], args.date.isoformat()) for league in LEAGUES]
    except sqlite3.Error as exc:
        print(f'FATAL [database] {exc}', flush=True)
        return 1
    except (ValueError, TypeError, KeyError) as exc:
        print(f'FATAL [corrupt_core_response] {exc}', flush=True)
        return 1
    finally:
        if conn is not None:
            conn.close()
    return 1 if any(result['error_count'] for result in results) else 0


if __name__ == '__main__':
    raise SystemExit(main())
