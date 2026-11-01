"""Non-destructive ESPN category upserts shared by all boxscore writers."""
import json


def merge_stats(previous, incoming):
    result = dict(previous)
    for key, value in incoming.items():
        if value is None or (isinstance(value, str) and value.strip() in ('', '--', 'N/A')):
            continue
        if isinstance(value, dict):
            result[key] = merge_stats(result.get(key, {}) if isinstance(result.get(key), dict) else {}, value)
        else:
            result[key] = value
    return result


def upsert_player_stats(conn, game_id, player_id, name, team, payload, segment, now):
    if not conn.in_transaction:
        conn.execute('BEGIN IMMEDIATE')
    existing = conn.execute('SELECT stats_json FROM player_game_stats WHERE game_id=? AND player_id=? AND segment=?',
                            (game_id, player_id, segment)).fetchone()
    previous = json.loads(existing[0]) if existing and existing[0] else {}
    if not isinstance(previous, dict):
        raise ValueError('stored player statistics must be an object')
    merged = merge_stats(previous, payload)
    conn.execute('''INSERT INTO player_game_stats
        (game_id,player_id,player_name,team,stats_json,segment,last_updated) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(game_id,player_id,segment) DO UPDATE SET
        player_name=excluded.player_name,team=excluded.team,stats_json=excluded.stats_json,last_updated=excluded.last_updated''',
        (game_id, player_id, name, team, json.dumps(merged), segment, now))
