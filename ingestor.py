import time
from espn_stats import upsert_player_stats
import requests
import json
import sqlite3
import datetime
import re
from zoneinfo import ZoneInfo
from situation_engine import GameState, get_team_abbr, determine_situation_alerts

DB_PATH = "/var/lib/liveintel/intel.db"

def get_db():
    for attempt in range(5):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30.0)
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.execute("PRAGMA journal_mode = DELETE")
            return conn
        except sqlite3.OperationalError as e:
            if "locked" in str(e) and attempt < 4:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS games (
            id TEXT PRIMARY KEY,
            sport TEXT,
            home_team TEXT,
            away_team TEXT,
            status TEXT,
            last_updated TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS plays (
            id TEXT PRIMARY KEY,
            game_id TEXT,
            drive_id TEXT,
            play_id TEXT,
            period TEXT,
            clock TEXT,
            possession TEXT,
            down TEXT,
            distance TEXT,
            yard_line TEXT,
            description TEXT,
            play_type TEXT,
            participants TEXT,
            yards REAL,
            score_after TEXT,
            event_timestamp TIMESTAMP,
            local_received_timestamp TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS player_game_stats (
            game_id TEXT,
            player_id TEXT,
            player_name TEXT,
            team TEXT,
            stats_json TEXT,
            segment TEXT,
            last_updated TIMESTAMP,
            PRIMARY KEY (game_id, player_id, segment)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS player_segment_stats (
            game_id TEXT,
            player_id TEXT,
            player_name TEXT,
            team TEXT,
            segment TEXT,
            stat_type TEXT,
            value REAL,
            last_updated TIMESTAMP,
            PRIMARY KEY (game_id, player_id, segment, stat_type)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prop_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT,
            player_name TEXT,
            market TEXT,
            line REAL,
            side TEXT,
            book TEXT,
            observed_at TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prop_projections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prop_id INTEGER,
            current_value REAL,
            remaining_needed REAL,
            projected_low REAL,
            projected_median REAL,
            projected_high REAL,
            signal TEXT,
            confidence TEXT,
            reasons TEXT,
            historical_median REAL,
            historical_75th REAL,
            historical_90th REAL,
            historical_sample INTEGER,
            calculated_at TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS historical_segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id TEXT,
            player_name TEXT,
            team TEXT,
            season INTEGER,
            segment TEXT,
            stat_type TEXT,
            value REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS game_state (
            game_id TEXT PRIMARY KEY,
            period TEXT,
            clock TEXT,
            away_score INTEGER,
            home_score INTEGER,
            score_diff INTEGER,
            possession TEXT,
            down TEXT,
            distance TEXT,
            yard_line TEXT,
            game_script TEXT,
            leading_team TEXT,
            trailing_team TEXT,
            score_diff_val INTEGER,
            red_zone INTEGER,
            goal_to_go INTEGER,
            fg_range INTEGER,
            drive_number INTEGER,
            drive_plays INTEGER,
            drive_yards INTEGER,
            away_team TEXT,
            home_team TEXT,
            last_updated TIMESTAMP,
            score_diff INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS team_situation_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT,
            team_id TEXT,
            period TEXT,
            rush_plays INTEGER,
            pass_plays INTEGER,
            rush_yards INTEGER,
            pass_yards INTEGER,
            rush_pct REAL,
            pass_pct REAL,
            red_zone_entries INTEGER,
            red_zone_plays INTEGER,
            red_zone_td INTEGER,
            red_zone_fg INTEGER,
            fg_attempts INTEGER,
            fg_makes INTEGER,
            pat_attempts INTEGER,
            pat_makes INTEGER,
            fg_range_entries INTEGER,
            drive_stalls INTEGER,
            two_minute_plays INTEGER,
            last_updated TIMESTAMP,
            UNIQUE(game_id, team_id, period)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS player_opportunity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT,
            player_id TEXT,
            player_name TEXT,
            team TEXT,
            period TEXT,
            targets INTEGER,
            carries INTEGER,
            pass_attempts INTEGER,
            rush_attempts INTEGER,
            red_zone_targets INTEGER,
            red_zone_carries INTEGER,
            red_zone_touch INTEGER,
            last_updated TIMESTAMP,
            UNIQUE(game_id, player_id, period)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS situation_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT,
            alert_type TEXT,
            team_id TEXT,
            detail TEXT,
            period TEXT,
            clock TEXT,
            created_at TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def add_manual_prop(game_id, player, market, line, side, book):
    conn = get_db()
    conn.execute("INSERT INTO prop_lines (game_id, player_name, market, line, side, book, observed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (game_id, player, market, line, side, book, datetime.datetime.now()))
    conn.commit()
    conn.close()

def get_team_id_to_abbr(summary):
    mapping = {}
    for t in summary.get('boxscore', {}).get('players', []):
        team_info = t.get('team', {})
        team_id = team_info.get('id')
        abbr = team_info.get('abbreviation')
        if team_id and abbr:
            mapping[team_id] = abbr
    return mapping

def parse_name_from_play_text(text):
    if not text:
        return None
    tokens = text.split()
    for tok in tokens:
        if "." in tok:
            parts = tok.split(".", 1)
            if len(parts) == 2 and parts[1]:
                return parts[1].strip(".")
    return None

def parse_plays_from_summary(summary, game_id):
    plays = []
    drives = summary.get('drives') or {}
    team_map = get_team_id_to_abbr(summary)
    
    current = drives.get('current') or {}
    previous = drives.get('previous') or []
    
    for drv in ([current] if current.get('plays') else []) + list(previous):
        if not drv.get('plays'):
            continue
        drive_id = str(drv.get('id') or '')
        for p in drv['plays']:
            play_id = str(p.get('id') or '')
            period_obj = p.get('period') or {}
            period = str(period_obj.get('number') or '')
            clock_obj = p.get('clock') or {}
            clock = clock_obj.get('displayValue') or ''
            play_type = (p.get('type') or {}).get('text') or ''
            description = p.get('text') or ''
            yards = p.get('statYardage')
            away_score = p.get('awayScore')
            home_score = p.get('homeScore')
            score_after = json.dumps({'away': away_score, 'home': home_score}) if away_score is not None and home_score is not None else ''
            wallclock = p.get('wallclock') or ''
            modified = p.get('modified') or ''
            
            # Parse start/end for down/distance/yardline/possession
            start = p.get('start', {})
            end = p.get('end', {})
            
            down = str(start.get('down') or '')
            distance = str(start.get('distance') or '')
            yard_line = str(start.get('yardLine') or '')
            
            # Use start.team.id for possession (most reliable)
            possession_team_id = start.get('team', {}).get('id')
            possession = str(possession_team_id) if possession_team_id else ''
            
            # Parse participants with athlete details
            participants = []
            for tp in p.get('participants', []):
                athlete = tp.get('athlete')
                if athlete:
                    athlete_id = str(athlete.get('id') or '')
                    athlete_name = athlete.get('displayName', '')
                    athlete_team = athlete.get('team', {}).get('abbreviation', '')
                    athlete_position = athlete.get('position', {}).get('abbreviation', '')
                    p_type = tp.get('type', '')
                    stats_ref = tp.get('playStatistics', {}).get('$ref', '')
                    participants.append({
                        'athlete_id': athlete_id,
                        'athlete_name': athlete_name,
                        'athlete_team': athlete_team,
                        'position': athlete_position,
                        'type': p_type,
                        'stats_ref': stats_ref
                    })
            
            # Get down/distance/yardline from start
            down = str(start.get('down') or '')
            distance = str(start.get('distance') or '')
            yard_line = str(start.get('yardLine') or '')
            
            # Use start.team.id for possession (most reliable)
            possession_team_id = start.get('team', {}).get('id')
            possession = str(possession_team_id) if possession_team_id else ''
            
            plays.append({
                'game_id': game_id,
                'drive_id': drive_id,
                'play_id': play_id,
                'period': period,
                'clock': clock,
                'possession': possession,
                'down': down,
                'distance': distance,
                'yard_line': yard_line,
                'description': description,
                'play_type': play_type,
                'participants': json.dumps(participants),
                'yards': float(yards) if yards is not None else None,
                'score_after': score_after,
                'event_timestamp': wallclock,
                'local_received_timestamp': modified,
                'start': start,
                'end': end
            })
    return plays

def store_plays(conn, plays):
    if not plays:
        return
    for p in plays:
        conn.execute("""
            INSERT OR REPLACE INTO plays 
            (id, game_id, drive_id, play_id, period, clock, possession, down, distance, yard_line, 
             description, play_type, participants, yards, score_after, event_timestamp, local_received_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (f"{p['game_id']}_{p['play_id']}", p['game_id'], p['drive_id'], p['play_id'], 
              p['period'], p['clock'], p['possession'], p['down'], p['distance'], p['yard_line'],
              p['description'], p['play_type'], p['participants'], p['yards'], p['score_after'],
              p['event_timestamp'], p['local_received_timestamp']))

def aggregate_qb_passing_segments(plays, player_name):
    segments = {'Q1': 0, 'Q2': 0, 'Q3': 0, 'Q4': 0, 'OT': 0}
    player_last = player_name.split()[-1].lower()
    for p in plays:
        desc = p.get('description', '')
        name = parse_name_from_play_text(desc)
        if name and name.lower() == player_last:
            play_type = p.get('play_type', '').lower()
            if 'pass' in play_type:
                period = p.get('period')
                yards = p.get('yards') or 0
                if period == '1':
                    segments['Q1'] += yards
                elif period == '2':
                    segments['Q2'] += yards
                elif period == '3':
                    segments['Q3'] += yards
                elif period == '4':
                    segments['Q4'] += yards
                elif period in ('5', '6', '7'):
                    segments['OT'] += yards
    segments['1H'] = segments['Q1'] + segments['Q2']
    segments['2H'] = segments['Q3'] + segments['Q4']
    segments['FULL GAME'] = segments['Q1'] + segments['Q2'] + segments['Q3'] + segments['Q4'] + segments['OT']
    return segments

def aggregate_rb_rushing_segments(plays, player_name):
    segments = {'Q1': 0, 'Q2': 0, 'Q3': 0, 'Q4': 0, 'OT': 0}
    player_last = player_name.split()[-1].lower()
    for p in plays:
        desc = p.get('description', '')
        name = parse_name_from_play_text(desc)
        if name and name.lower() == player_last:
            play_type = p.get('play_type', '').lower()
            if 'rush' in play_type:
                period = p.get('period')
                yards = p.get('yards') or 0
                if period == '1':
                    segments['Q1'] += yards
                elif period == '2':
                    segments['Q2'] += yards
                elif period == '3':
                    segments['Q3'] += yards
                elif period == '4':
                    segments['Q4'] += yards
                elif period in ('5', '6', '7'):
                    segments['OT'] += yards
    segments['1H'] = segments['Q1'] + segments['Q2']
    segments['2H'] = segments['Q3'] + segments['Q4']
    segments['FULL GAME'] = segments['Q1'] + segments['Q2'] + segments['Q3'] + segments['Q4'] + segments['OT']
    return segments

def aggregate_wr_receiving_segments(plays, player_name):
    segments = {'Q1': 0, 'Q2': 0, 'Q3': 0, 'Q4': 0, 'OT': 0}
    player_last = player_name.split()[-1].lower()
    for p in plays:
        desc = p.get('description', '')
        play_type = p.get('play_type', '').lower()
        if 'to ' in desc and ('rec' in play_type or 'pass' in play_type):
            try:
                receiver_part = desc.split('to ')[1].split(' for ')[0].strip()
                receiver_name = receiver_part.split()[0] if receiver_part else ''
                receiver_clean = parse_name_from_play_text(receiver_name + ' dummy')
                if receiver_clean and receiver_clean.lower() == player_last:
                    period = p.get('period')
                    yards = p.get('yards') or 0
                    if period == '1':
                        segments['Q1'] += yards
                    elif period == '2':
                        segments['Q2'] += yards
                    elif period == '3':
                        segments['Q3'] += yards
                    elif period == '4':
                        segments['Q4'] += yards
                    elif period in ('5', '6', '7'):
                        segments['OT'] += yards
            except Exception:
                pass
    segments['1H'] = segments['Q1'] + segments['Q2']
    segments['2H'] = segments['Q3'] + segments['Q4']
    segments['FULL GAME'] = segments['Q1'] + segments['Q2'] + segments['Q3'] + segments['Q4'] + segments['OT']
    return segments

def store_player_game_stats(conn, summary, game_id, plays, now):
    team_map = get_team_id_to_abbr(summary)
    
    for t in summary.get('boxscore', {}).get('players', []):
        team_info = t.get('team', {})
        team_abbr = team_info.get('abbreviation', '')
        team_id = team_info.get('id', '')
        
        for cat in t.get('statistics', []):
            cat_name = cat.get('name', '')
            labels = cat.get('labels', [])
            for a in cat.get('athletes', []):
                athlete = a.get('athlete', {})
                player_id = str(athlete.get('id') or '')
                name = athlete.get('displayName', '')
                if not player_id or not name:
                    continue
                stats = dict(zip(labels, a.get('stats', [])))
                
                upsert_player_stats(conn, game_id, player_id, name, team_abbr,
                                    {cat_name: stats}, "FULL GAME", now)

                if cat_name == 'passing':
                    segs = aggregate_qb_passing_segments(plays, name)
                    for seg, val in segs.items():
                        conn.execute(""""
                            INSERT OR REPLACE INTO player_segment_stats
                            (game_id, player_id, player_name, team, segment, stat_type, value, last_updated)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (game_id, player_id, name, team_abbr, seg, 'passing_yards', float(val) if val else 0.0, now))
                
                if cat_name == 'rushing':
                    segs = aggregate_rb_rushing_segments(plays, name)
                    for seg, val in segs.items():
                        conn.execute(""""
                            INSERT OR REPLACE INTO player_segment_stats
                            (game_id, player_id, player_name, team, segment, stat_type, value, last_updated)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (game_id, player_id, name, team_abbr, seg, 'rushing_yards', float(val) if val else 0.0, now))
                
                if cat_name == 'receiving':
                    segs = aggregate_wr_receiving_segments(plays, name)
                    for seg, val in segs.items():
                        conn.execute(""""
                            INSERT OR REPLACE INTO player_segment_stats
                            (game_id, player_id, player_name, team, segment, stat_type, value, last_updated)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (game_id, player_id, name, team_abbr, seg, 'receiving_yards', float(val) if val else 0.0, now))
                
                if cat_name == 'kicking':
                    fg = stats.get('FG', '0/0')
                    xp = stats.get('XP', '0/0')
                    pts = stats.get('PTS', '0')
                    conn.execute("""
                        INSERT OR REPLACE INTO player_segment_stats
                        (game_id, player_id, player_name, team, segment, stat_type, value, last_updated)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (game_id, player_id, name, team_abbr, 'FULL GAME', 'fg_made', float(fg.split('/')[0]) if '/' in fg else 0.0, now))
                    conn.execute("""
                        INSERT OR REPLACE INTO player_segment_stats
                        (game_id, player_id, player_name, team, segment, stat_type, value, last_updated)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (game_id, player_id, name, team_abbr, 'FULL GAME', 'fg_att', float(fg.split('/')[1]) if '/' in fg else 0.0, now))
                    conn.execute("""
                        INSERT OR REPLACE INTO player_segment_stats
                        (game_id, player_id, player_name, team, segment, stat_type, value, last_updated)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (game_id, player_id, name, team_abbr, 'FULL GAME', 'xp_made', float(xp.split('/')[0]) if '/' in xp else 0.0, now))
                    conn.execute("""
                        INSERT OR REPLACE INTO player_segment_stats
                        (game_id, player_id, player_name, team, segment, stat_type, value, last_updated)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (game_id, player_id, name, team_abbr, 'FULL GAME', 'kicking_points', float(pts) if pts else 0.0, now))

def parse_name_from_play_text(text):
    if not text:
        return None
    tokens = text.split()
    for tok in tokens:
        if "." in tok:
            parts = tok.split(".", 1)
            if len(parts) == 2 and parts[1]:
                return parts[1].strip(".")
    return None

def get_historical_remaining(player_id, player_name, team, stat_type, segment, season=2026):
    conn = get_db()
    rows = conn.execute("""
        SELECT value FROM historical_segments 
        WHERE player_id = ? AND stat_type = ? AND segment = ? AND season = ?
    """, (player_id, stat_type, segment, season)).fetchall()
    conn.close()
    
    if rows and len(rows) >= 3:
        values = sorted([r[0] for r in rows])
        n = len(values)
        p25 = values[int(n * 0.25)]
        return {
            'sample': n,
            'avg': sum(values) / n,
            'median': values[n // 2],
            'p25': p25,
            'p75': values[int(n * 0.75)],
            'p90': values[int(n * 0.9)]
        }
    return None

def get_elapsed_fraction(period, clock):
    try:
        if period in ('1', '2', '3', '4'):
            parts = clock.split(':')
            if len(parts) == 2:
                minutes = int(parts[0])
                seconds = int(parts[1])
                total_seconds = minutes * 60 + seconds
                quarter_remaining = total_seconds / 900.0
                return (int(period) - 1) * 0.25 + (1 - quarter_remaining) * 0.25
    except:
        pass
    return 0.5

def calculate_projections(prop_id, current_value, line, side, game_id, player_name, player_id, team, stat_type, period, clock):
    remaining_needed = line - current_value if side == 'OVER' else current_value - line
    
    hist = get_historical_remaining(player_id, player_name, team, stat_type, '2H', 2026)
    
    if hist:
        hist_median = hist['median']
        hist_p75 = hist['p75']
        hist_p90 = hist['p90']
        projected_median = current_value + hist_median if side == 'OVER' else current_value - hist_median
        projected_low = current_value + hist['p25'] if side == 'OVER' else current_value - hist_p75
        projected_high = current_value + hist_p90 if side == 'OVER' else current_value - hist['p25']
        historical_label = f"AVAILABLE HISTORY: {hist['sample']} games"
    else:
        elapsed = get_elapsed_fraction(period, clock)
        remaining_fraction = max(0, 1 - elapsed)
        rate = current_value / max(0.01, elapsed)
        projected_additional = rate * remaining_fraction
        projected_median = current_value + projected_additional
        projected_low = projected_median * 0.8
        projected_high = projected_median * 1.2
        hist_median = hist_p75 = hist_p90 = None
        historical_label = "NO HISTORICAL DATA - USING CURRENT RATE PROJECTION"
    
    signal, confidence, reasons = determine_signal(
        current_value, line, side, remaining_needed, 
        hist_median, hist_p75, hist_p90, hist,
        period, clock, projected_median
    )
    
    return {
        'current_value': current_value,
        'remaining_needed': remaining_needed,
        'projected_low': round(projected_low, 1),
        'projected_median': round(projected_median, 1),
        'projected_high': round(projected_high, 1),
        'signal': signal,
        'confidence': confidence,
        'reasons': reasons,
        'historical_median': hist_median,
        'historical_75th': hist_p75,
        'historical_90th': hist_p90,
        'historical_sample': hist['sample'] if hist else 0,
        'historical_label': historical_label
    }

def determine_signal(current, line, side, needed, hist_med, hist_p75, hist_p90, hist, period, clock, projected):
    reasons = []
    
    if not hist:
        reasons.append("No historical 2H data available")
        return "DATA INCOMPLETE", "LOW", "; ".join(reasons)
    
    if side == 'UNDER':
        if needed <= 0:
            return "STRONG UNDER", "HIGH", ["Already under line"]
        if hist_p75 is not None and needed > hist_p75:
            reasons.append(f"Needs {needed:.1f} remaining; historical 2H 75th pct is {hist_p75:.1f}")
            return "UNDER WATCH", "MEDIUM", "; ".join(reasons)
        if hist_p90 is not None and needed > hist_p90:
            reasons.append(f"Needs {needed:.1f} remaining; historical 2H 90th pct is {hist_p90:.1f}")
            return "STRONG UNDER", "HIGH", "; ".join(reasons)
        if hist_med is not None and needed > hist_med:
            reasons.append(f"Needs {needed:.1f} remaining; historical 2H median is {hist_med:.1f}")
            return "LEAN UNDER", "MEDIUM", "; ".join(reasons)
        reasons.append(f"Needs {needed:.1f} remaining; below historical median")
        return "PASS", "LOW", "; ".join(reasons)
    else:
        if needed <= 0:
            return "STRONG OVER", "HIGH", ["Already over line"]
        if hist_p75 is not None and needed < hist_p75:
            reasons.append(f"Needs {needed:.1f} remaining; historical 2H 75th pct is {hist_p75:.1f}")
            return "OVER WATCH", "MEDIUM", "; ".join(reasons)
        if hist_p90 is not None and needed < hist_p90:
            reasons.append(f"Needs {needed:.1f} remaining; historical 2H 90th pct is {hist_p90:.1f}")
            return "STRONG OVER", "HIGH", "; ".join(reasons)
        if hist_med is not None and needed < hist_med:
            reasons.append(f"Needs {needed:.1f} remaining; historical 2H median is {hist_med:.1f}")
            return "LEAN OVER", "MEDIUM", "; ".join(reasons)
        reasons.append(f"Needs {needed:.1f} remaining; above historical median")
        return "PASS", "LOW", "; ".join(reasons)

def store_projection(prop_id, proj):
    conn = get_db()
    conn.execute("""
        INSERT INTO prop_projections
        (prop_id, current_value, remaining_needed, projected_low, projected_median, projected_high,
         signal, confidence, reasons, historical_median, historical_75th, historical_90th, 
         historical_sample, calculated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (prop_id, proj['current_value'], proj['remaining_needed'], proj['projected_low'],
          proj['projected_median'], proj['projected_high'], proj['signal'], proj['confidence'],
          proj['reasons'], proj['historical_median'], proj['historical_75th'], proj['historical_90th'],
          proj['historical_sample'], datetime.datetime.now()))
    conn.commit()
    conn.close()

def fetch_game_summary(game_id):
    url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={game_id}"
    response = requests.get(url, timeout=10)
    if response.status_code == 200:
        return response.json()
    return None

def normalize_game_lifecycle(status):
    """Map ESPN lifecycle to the games and game_state conventions together."""
    status_type = status.get('type') or {}
    state = str(status_type.get('state') or '').lower()
    if status_type.get('completed') is True or state in ('post', 'final'):
        # No active quarter/clock is meaningful once the event is completed.
        return 'POST', 'FINAL', None, None
    if state in ('pre', 'scheduled'):
        return 'PRE', 'PRE', None, None
    if state in ('in', 'live', 'halftime'):
        return 'LIVE', 'LIVE', status.get('period'), status.get('displayClock')
    raise ValueError(f'Unknown ESPN lifecycle: {state!r}')


def store_game_lifecycle(conn, game_id, comp, sport=None, live_script=None):
    """Publish both lifecycle columns atomically from one competition payload."""
    if str(comp.get('id')) != str(game_id):
        raise ValueError(f'Competition game_id does not match {game_id}')
    lifecycle, script, period, clock = normalize_game_lifecycle(comp.get('status') or {})
    previous = conn.execute('SELECT status FROM games WHERE id=?', (game_id,)).fetchone()
    if previous and str(previous[0]).lower() in ('post', 'final') and lifecycle != 'POST':
        raise ValueError('Refusing to reactivate a completed game')
    competitors = {c.get('homeAway'): c for c in comp.get('competitors', [])}
    home, away = competitors['home'], competitors['away']
    home_team = home['team']['abbreviation']
    away_team = away['team']['abbreviation']
    if lifecycle == 'LIVE' and live_script:
        script = f'LIVE + {live_script}'
    now = datetime.datetime.now()
    conn.execute('SAVEPOINT game_lifecycle')
    try:
        conn.execute("""
            INSERT INTO games (id, sport, home_team, away_team, status, last_updated)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                sport = COALESCE(excluded.sport, games.sport),
                home_team = excluded.home_team, away_team = excluded.away_team,
                status = excluded.status, last_updated = excluded.last_updated
        """, (game_id, sport, home_team, away_team,
              'post' if lifecycle == 'POST' else lifecycle, now))
        conn.execute("""
            INSERT INTO game_state
                (game_id, game_script, away_team, home_team, period, clock,
                 away_score, home_score, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(game_id) DO UPDATE SET
                game_script = excluded.game_script,
                away_team = excluded.away_team, home_team = excluded.home_team,
                period = excluded.period, clock = excluded.clock,
                away_score = COALESCE(excluded.away_score, game_state.away_score),
                home_score = COALESCE(excluded.home_score, game_state.home_score),
                last_updated = excluded.last_updated
        """, (game_id, script, away_team, home_team, period, clock,
              away.get('score'), home.get('score'), now))
    except Exception:
        conn.execute('ROLLBACK TO game_lifecycle')
        conn.execute('RELEASE game_lifecycle')
        raise
    conn.execute('RELEASE game_lifecycle')


def process_game_situation(conn, game_id, summary, plays, home_team, away_team, home_team_id, away_team_id):
    """Process plays through the situation engine and store game state"""
    try:
        # A requested event ID alone does not establish the payload's identity.
        header = summary.get('header') or {}
        competitions = header.get('competitions') or []
        comp = next((c for c in competitions if str(c.get('id')) == str(game_id)), None)
        if str(header.get('id')) != str(game_id) or comp is None:
            raise ValueError(f'Summary game_id does not match {game_id}')
        competitors = comp.get('competitors') or []
        expected = {'home': (str(home_team_id), home_team),
                    'away': (str(away_team_id), away_team)}
        normalize = lambda team: {'WSH': 'WAS', 'JAX': 'JAC'}.get(team, team)
        if len(competitors) != 2:
            raise ValueError(f'Invalid competitors for game {game_id}')
        for competitor in competitors:
            team = competitor.get('team') or {}
            identity = expected.get(competitor.get('homeAway'))
            if (identity is None or str(team.get('id')) != identity[0]
                    or normalize(team.get('abbreviation')) != normalize(identity[1])):
                raise ValueError(f'Summary teams do not match game {game_id}')
        team_ids = {str(home_team_id), str(away_team_id)}
        for play in plays:
            possession = str((play.get('start') or {}).get('team', {}).get('id')
                             or play.get('possession') or '')
            if str(play.get('game_id')) != str(game_id) or (possession and possession not in team_ids):
                raise ValueError(f'Foreign play/drive in game {game_id}')

        # Initialize game state
        gs = GameState(game_id)
        gs.away_team = away_team
        gs.home_team = home_team
        gs.away_team_id = away_team_id
        gs.home_team_id = home_team_id
        
        # Get team IDs from summary boxscore
        team_map = get_team_id_to_abbr(summary)
        
        # ESPN supplies the current drive before previous drives. Replay in game order.
        def play_order(play):
            period = int(play.get('period') or 0)
            minutes, seconds = (play.get('clock') or '0:00').split(':')
            return period, -(int(minutes) * 60 + int(seconds)), int(play.get('play_id') or 0)

        status = comp.get('status') or {}
        lifecycle, _, _, _ = normalize_game_lifecycle(status)
        for play in sorted(plays, key=play_order) if lifecycle != 'PRE' else []:
            gs.update_from_play(play)

        # Use the same event's authoritative status, never an earlier play's clock.
        if lifecycle == 'PRE':
            gs.script, gs.period, gs.clock = 'PRE', None, None
        elif lifecycle == 'POST':
            gs.script, gs.period, gs.clock = 'FINAL', None, None
        elif lifecycle == 'LIVE':
            gs.period = status.get('period', gs.period)
            gs.clock = status.get('displayClock', gs.clock)
        for competitor in competitors:
            score = competitor.get('score')
            if score is not None:
                setattr(gs, competitor['homeAway'] + '_score', int(score))
        gs.update_score(gs.away_score, gs.home_score)
        if lifecycle == 'PRE':
            gs.script = 'PRE'
        elif lifecycle == 'POST':
            gs.script = 'FINAL'
        store_game_lifecycle(conn, game_id, comp, live_script=gs.script)
        if lifecycle == 'LIVE':
            gs.script = f'LIVE + {gs.script}'
            
        # Get situation alerts
        alerts = determine_situation_alerts(gs)
        
        # Store game state
        conn.execute("""
            INSERT OR REPLACE INTO game_state
            (game_id, period, clock, away_score, home_score, score_diff, possession, down, distance, yard_line,
             game_script, leading_team, trailing_team, score_diff_val, red_zone, goal_to_go, fg_range,
             drive_number, drive_plays, drive_yards, away_team, home_team, last_updated, score_diff)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (gs.game_id, gs.period, gs.clock, gs.away_score, gs.home_score, gs.score_diff,
              gs.possession, gs.down, gs.distance, gs.yard_line,
              gs.script, gs.leading_team or '', gs.trailing_team or '', gs.score_diff,
              1 if gs.red_zone else 0, 1 if gs.goal_to_go else 0, 1 if gs.fg_range else 0,
              gs.drive_number, gs.drive_plays, gs.drive_yards,
              gs.away_team, gs.home_team, datetime.datetime.now(), gs.score_diff))
        
        # Store team situation stats per period
        for team_id, team_abbr in [(away_team_id, away_team), (home_team_id, home_team)]:
            if not team_id:
                continue
            for period in range(1, 5):
                rush = gs.quarter_rush[period].get(team_id, 0)
                pas = gs.quarter_pass[period].get(team_id, 0)
                total = rush + pas
                if total > 0:
                    vals = (game_id, team_abbr, str(period),
                          gs.quarter_rush[period].get(team_id, 0),
                          gs.quarter_pass[period].get(team_id, 0),
                          gs.team_rush_yards.get(team_id, 0),
                          gs.team_pass_yards.get(team_id, 0),
                          100 * rush / max(1, rush + pas) if (rush + pas) > 0 else 0,
                          100 * pas / max(1, rush + pas) if (rush + pas) > 0 else 0,
                          0, 0, 0, 0, 0,  # red zone stats not fully tracked yet
                          0, 0, 0, 0, 0, 0, datetime.datetime.now())
                    conn.execute(""""
                        INSERT OR REPLACE INTO team_situation_stats
                        (game_id, team_id, period, rush_plays, pass_plays, rush_yards, pass_yards,
                         rush_pct, pass_pct, red_zone_entries, red_zone_plays, red_zone_td, red_zone_fg,
                         fg_attempts, fg_makes, pat_attempts, pat_makes, fg_range_entries,
                         drive_stalls, two_minute_plays, last_updated)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, vals)
        
        # Store player opportunity
        for player_id, carries in gs.player_carries.items():
            # Get player name from plays
            player_name = ''
            team = ''
            for p in plays:
                participants = p.get('participants', [])
                for part in participants:
                    if part.get('athlete_id') == player_id:
                        player_name = part.get('athlete_name', '')
                        team = part.get('athlete_team', '')
                        break
                if player_name:
                    break
            if player_name:
                for period in range(1, 5):
                    conn.execute("""
                        INSERT OR REPLACE INTO player_opportunity
                        (game_id, player_id, player_name, team, period, targets, carries,
                         pass_attempts, rush_attempts, red_zone_targets, red_zone_carries,
                         red_zone_touch, last_updated)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (game_id, player_id, player_name, team, str(period),
                          0, carries, 0, carries, 0, 0, 0, datetime.datetime.now()))
        
        # Store alerts
        alerts = determine_situation_alerts(gs)
        for alert in alerts:
            conn.execute("""
                INSERT INTO situation_alerts (game_id, alert_type, team_id, detail, period, clock, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (game_id, alert['type'], alert['team'], alert['detail'],
                  gs.period, gs.clock, datetime.datetime.now()))
        
        conn.commit()
    except Exception as e:
        print(f"Situation processing error for game {game_id}: {e}", flush=True)

def ingest():
    url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
    slate_zone = ZoneInfo('America/New_York')
    today = datetime.datetime.now(slate_zone).date()
    conn = get_db()
    try:
        response = requests.get(url, params={'dates': today.strftime('%Y%m%d'), 'limit': 1000}, timeout=10)
        if response.status_code == 200:
            data = response.json()
            events = data.get('events', [])
            
            for event in events:
                game_id = event['id']
                competitions = event.get('competitions', [])
                if not competitions:
                    continue
                comp = competitions[0]
                # NFL scoreboards may return an entire week despite dates=.
                stamp = datetime.datetime.fromisoformat(str(event.get('date') or comp.get('date')).replace('Z', '+00:00'))
                if stamp.tzinfo is None or stamp.astimezone(slate_zone).date() != today:
                    continue
                competitors = comp.get('competitors', [])
                if len(competitors) != 2:
                    continue
                home = competitors[1] if competitors[1].get('homeAway') == 'home' else competitors[0]
                away = competitors[0] if competitors[0].get('homeAway') == 'away' else competitors[1]
                home_team = home.get('team', {}).get('abbreviation', '')
                away_team = away.get('team', {}).get('abbreviation', '')
                home_team_id = home.get('team', {}).get('id', '')
                away_team_id = away.get('team', {}).get('id', '')
                status, _, _, _ = normalize_game_lifecycle(comp.get('status') or {})
                store_game_lifecycle(conn, game_id, comp, sport='NFL')
                
                # Only process detailed data if game is in progress
                if status == 'LIVE':
                    summary = fetch_game_summary(game_id)
                    if summary and 'boxscore' in summary:
                        plays = parse_plays_from_summary(summary, game_id)
                        store_plays(conn, plays)
                        store_player_game_stats(conn, summary, game_id, plays, datetime.datetime.now())
                        
                        # Process plays through situation engine
                        process_game_situation(conn, game_id, summary, plays, home_team, away_team, home_team_id, away_team_id)
            
            conn.commit()
            print(f"Ingested {len(events)} games", flush=True)
    except Exception as e:
        print(f"Ingest error: {e}", flush=True)
    finally:
        conn.close()

def fetch_game_summary(game_id):
    url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={game_id}"
    response = requests.get(url, timeout=10)
    if response.status_code == 200:
        return response.json()
    return None

def add_manual_prop(game_id, player, market, line, side, book):
    conn = get_db()
    conn.execute("INSERT INTO prop_lines (game_id, player_name, market, line, side, book, observed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (game_id, player, market, line, side, book, datetime.datetime.now()))
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    while True:
        ingest()
        time.sleep(30)
