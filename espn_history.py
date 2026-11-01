"""Validated completed-game boxscores and historical identity repair. No play-by-play."""
import datetime
import copy
import hashlib
import json

from espn_stats import merge_stats, upsert_player_stats


def ensure_history_schema(conn):
    import daily_espn_pipeline as p
    p.ensure_context_columns(conn)
    columns = {r['name'] for r in conn.execute('PRAGMA table_info(games)')}
    for name in ('game_date', 'slate_date', 'scheduled_at'):
        if name not in columns:
            conn.execute(f'ALTER TABLE games ADD COLUMN {name} TEXT')
    conn.execute('''CREATE TABLE IF NOT EXISTS espn_team_game_totals(
        game_id TEXT,team_id TEXT,team TEXT,stats_json TEXT,last_updated TEXT,
        PRIMARY KEY(game_id,team_id))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS espn_player_quarantine(
        game_id TEXT REFERENCES games(id),sport TEXT,team_id TEXT,category TEXT,
        record_key TEXT,raw_player_id TEXT,player_name TEXT,reason TEXT,payload_json TEXT,last_updated TEXT,
        PRIMARY KEY(game_id,team_id,category,record_key))''')


def usable_groups(groups, game):
    """Keep unusable IDs out of athlete history without losing a valid game."""
    import daily_espn_pipeline as p
    groups = copy.deepcopy(groups)
    rejected = []
    for group in groups:
        team = group.get('team') or {}
        abbr = str(team.get('abbreviation') or '').strip()
        if abbr not in game['team_ids'] or str(team.get('id') or '') != game['team_ids'][abbr]:
            raise ValueError('player/game team mismatch')
        for category in group.get('statistics') or []:
            keep = []
            for row in category.get('athletes') or []:
                info = row.get('athlete') or {}
                if row.get('didNotPlay') or p.numeric_id(info.get('id')):
                    keep.append(row)
                    continue
                name = str(info.get('displayName') or info.get('fullName') or '').strip()
                payload = json.dumps(row, sort_keys=True, separators=(',', ':'))
                rejected.append(dict(team_id=str(team.get('id') or ''),category=str(category.get('name') or ''),
                    record_key=hashlib.sha256(payload.encode()).hexdigest(),raw_player_id=str(info.get('id') or ''),
                    player_name=name,reason='team_aggregate' if name.casefold()=='team' else 'invalid_athlete_id',
                    payload_json=payload))
            category['athletes'] = keep
    return groups, rejected


def quarantine_legacy_rows(conn, game):
    """Move existing unusable athlete keys to the audit table in this transaction."""
    import daily_espn_pipeline as p
    now = datetime.datetime.now(p.SLATE_ZONE).isoformat(' ', 'seconds')
    moved = 0
    for table in ('multi_sport_players','player_game_stats'):
        for row in conn.execute('SELECT rowid AS quarantine_rowid,* FROM '+table+' WHERE game_id=?',(game['id'],)).fetchall():
            record = dict(row)
            if p.numeric_id(record.get('player_id')):
                continue
            rowid = record.pop('quarantine_rowid')
            payload = json.dumps(record,sort_keys=True,separators=(',', ':'),default=str)
            conn.execute('''INSERT INTO espn_player_quarantine
                (game_id,sport,team_id,category,record_key,raw_player_id,player_name,reason,payload_json,last_updated)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(game_id,team_id,category,record_key) DO NOTHING''',
                (game['id'],game['sport'],game['team_ids'].get(record.get('team'),''),
                 'legacy:'+table+':'+str(record.get('segment') or ''),hashlib.sha256(payload.encode()).hexdigest(),
                 str(record.get('player_id') or ''),record.get('player_name') or '',
                 'invalid_legacy_athlete_id',payload,now))
            conn.execute('DELETE FROM '+table+' WHERE rowid=?',(rowid,))
            moved += 1
    return moved


def summary_game(summary, sport):
    import daily_espn_pipeline as p
    header = summary.get('header') or {}
    comp = (header.get('competitions') or [{}])[0]
    sides = p.teams(comp)
    game = dict(id=p.numeric_id(header.get('id')), sport=sport,
                game_date=p.event_date(comp.get('date') or header.get('date')),
                status=p.game_status(comp), home_team=sides['home']['abbr'],
                away_team=sides['away']['abbr'], team_ids={v['abbr']:v['id'] for v in sides.values()})
    if not game['id'] or sport not in {l['sport'] for l in p.LEAGUES}:
        raise ValueError('supported sport and numeric event ID required')
    p.validate_summary(summary, game)
    return game


def persist_completed(conn, summary, game):
    """Caller owns transaction; any contradiction rolls back this entire event."""
    import daily_espn_pipeline as p
    p.validate_summary(summary, game)
    validated = summary_game(summary, game['sport'])
    if validated['status'] != 'post':
        raise ValueError('completed ESPN summary required')
    game = validated
    comp = summary['header']['competitions'][0]
    existing = conn.execute('SELECT * FROM games WHERE id=?', (game['id'],)).fetchone()
    if existing and existing['sport'] not in (None, '', game['sport']):
        raise ValueError('stored game sport mismatch')
    errors, warnings = [], []
    groups, rejected = usable_groups((summary.get('boxscore') or {}).get('players') or [], game)
    extractor = p.extract_basketball if game['sport'] in p.BASKETBALL else p.extract_football
    players = extractor(groups, game, errors, warnings)
    if errors:
        raise ValueError('; '.join(errors))
    raw_players = p.participants(groups, game, errors)
    scores = {}
    for competitor in comp.get('competitors') or []:
        score = competitor.get('score')
        if isinstance(score, dict):
            score = score.get('value', score.get('displayValue'))
        score = p._int(score)
        if score is not None and score >= 0:
            scores[competitor['homeAway'] + '_score'] = score
    ensure_history_schema(conn)
    now = datetime.datetime.now(p.SLATE_ZONE).isoformat(' ', 'seconds')
    game_updated = now
    if existing and str(existing['status']).lower() in ('post','final','completed','ended') and len(scores)!=2:
        game_updated = existing['last_updated'] or now
    stamp = comp.get('date') or summary['header'].get('date')
    conn.execute('''INSERT INTO games(id,sport,home_team,away_team,status,last_updated,game_date,slate_date,scheduled_at)
        VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
        sport=excluded.sport,home_team=excluded.home_team,away_team=excluded.away_team,
        status=excluded.status,last_updated=excluded.last_updated,game_date=excluded.game_date,
        slate_date=excluded.slate_date,scheduled_at=excluded.scheduled_at''',
        (game['id'],game['sport'],game['home_team'],game['away_team'],'post',game_updated,
         game['game_date'],game['game_date'],stamp))
    for rejected_row in rejected:
        values = dict(game_id=game['id'],sport=game['sport'],**rejected_row,last_updated=now)
        conn.execute('INSERT INTO espn_player_quarantine('+','.join(values)+') VALUES('+','.join('?' for _ in values)+') '
                     'ON CONFLICT(game_id,team_id,category,record_key) DO UPDATE SET last_updated=excluded.last_updated',
                     list(values.values()))
    quarantine_legacy_rows(conn,game)
    # Only final summary scores are written; absent scores never erase observations.
    if len(scores)==2:
        state = dict(game_id=game['id'], **scores, home_team=game['home_team'],
                     away_team=game['away_team'], last_updated=now)
        cols = ','.join(state)
        conn.execute(f"INSERT INTO game_state({cols}) VALUES({','.join('?' for _ in state)}) "
                     'ON CONFLICT(game_id) DO UPDATE SET '+','.join(k+'=excluded.'+k for k in state if k!='game_id'),
                     list(state.values()))
    for row in players:
        # ESPN event/player IDs are authoritative. Repair legacy labels and dates only
        # for athletes explicitly present in this validated event's boxscore.
        matches = conn.execute("SELECT * FROM multi_sport_players WHERE game_id=? AND player_id=? AND segment='FULL GAME'",
                               (game['id'],row['player_id'])).fetchall()
        if len(matches)>1:
            raise ValueError('duplicate legacy player/game identities require explicit reconciliation')
        updates = {k:v for k,v in row.items() if v is not None and v!=''}
        if matches:
            conn.execute('UPDATE multi_sport_players SET '+','.join(k+'=?' for k in updates)+' WHERE id=?',
                         (*updates.values(),matches[0]['id']))
        else:
            conn.execute('INSERT INTO multi_sport_players('+','.join(updates)+') VALUES('+','.join('?' for _ in updates)+')',
                         list(updates.values()))
        # Repair dates and linkage for existing segments without manufacturing stats.
        conn.execute('''UPDATE multi_sport_players SET sport=?,player_name=?,team=?,team_id=COALESCE(?,team_id),game_date=?,slate_date=?
            WHERE game_id=? AND player_id=?''',
            (row['sport'],row['player_name'],row['team'],row.get('team_id'),game['game_date'],game['game_date'],game['id'],row['player_id']))
        conn.execute('UPDATE player_game_stats SET player_name=?,team=? WHERE game_id=? AND player_id=?',
                     (row['player_name'],row['team'],game['id'],row['player_id']))
        payload = {}
        for category, stats in raw_players[row['player_id']]['_categories']:
            payload = merge_stats(payload, {category:stats})
        # The research adapters consume these explicit flat metrics, including MIN
        # parsed as fractional minutes, without using starter status for eligibility.
        totals = {k:row[k] for k in (*p.STAT_FIELDS,'minutes','starter') if row.get(k) is not None}
        payload = merge_stats(payload, {'totals':totals})
        upsert_player_stats(conn,game['id'],row['player_id'],row['player_name'],row['team'],payload,'FULL GAME',now)
    for group in (summary.get('boxscore') or {}).get('teams') or []:
        team = group.get('team') or {}
        tid = p.numeric_id(team.get('id'))
        abbr = team.get('abbreviation') or next((a for a,t in game['team_ids'].items() if t==tid),None)
        if not tid or game['team_ids'].get(abbr)!=tid:
            raise ValueError('team totals identity mismatch')
        stats = {s['name']:s.get('displayValue',s.get('value')) for s in group.get('statistics') or [] if s.get('name')}
        prior = conn.execute('SELECT stats_json FROM espn_team_game_totals WHERE game_id=? AND team_id=?',
                             (game['id'],tid)).fetchone()
        merged = merge_stats(json.loads(prior[0]) if prior else {},stats)
        conn.execute('''INSERT INTO espn_team_game_totals VALUES(?,?,?,?,?)
            ON CONFLICT(game_id,team_id) DO UPDATE SET team=excluded.team,
            stats_json=excluded.stats_json,last_updated=excluded.last_updated''',
            (game['id'],tid,abbr,json.dumps(merged),now))
    return len(players)
