import requests
import json
import sqlite3
import datetime
import time

DB_PATH = "/var/lib/liveintel/intel.db"

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = DELETE")
    return conn

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
    if not text: return None
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
        if not drv.get('plays'): continue
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
            
            possession = ''
            participants = []
            for tp in p.get('teamParticipants', []):
                if tp.get('type') == 'offense':
                    team_ref = tp.get('team', {})
                    team_id = team_ref.get('id') or team_ref.get('$ref', '').split('/')[-2] if team_ref.get('$ref') else ''
                    possession = team_map.get(team_id, '')
                participants.append({
                    'team_id': tp.get('team', {}).get('id') or tp.get('team', {}).get('$ref', '').split('/')[-2] if tp.get('team', {}).get('$ref') else '',
                    'order': tp.get('order'),
                    'type': tp.get('type')
                })
            
            plays.append({
                'game_id': game_id, 'drive_id': drive_id, 'play_id': play_id,
                'period': period, 'clock': clock, 'possession': possession,
                'description': description, 'play_type': play_type,
                'participants': json.dumps(participants),
                'yards': float(yards) if yards is not None else None,
                'score_after': score_after, 'event_timestamp': wallclock,
                'local_received_timestamp': modified
            })
    return plays

def calculate_passing_segments(plays, player_last):
    segs = {'Q1': 0, 'Q2': 0, 'Q3': 0, 'Q4': 0, 'OT': 0}
    for p in plays:
        name = parse_name_from_play_text(p.get('description', ''))
        if name and name.lower() == player_last.lower():
            if 'pass' in p.get('play_type', '').lower():
                per = p.get('period')
                yds = p.get('yards') or 0
                if per == '1': segs['Q1'] += yds
                elif per == '2': segs['Q2'] += yds
                elif per == '3': segs['Q3'] += yds
                elif per == '4': segs['Q4'] += yds
                elif per in ('5','6','7'): segs['OT'] += yds
    segs['1H'] = segs['Q1'] + segs['Q2']
    segs['2H'] = segs['Q3'] + segs['Q4']
    segs['FULL GAME'] = segs['Q1'] + segs['Q2'] + segs['Q3'] + segs['Q4'] + segs['OT']
    return segs

def calculate_rushing_segments(plays, player_last):
    segs = {'Q1': 0, 'Q2': 0, 'Q3': 0, 'Q4': 0, 'OT': 0}
    for p in plays:
        name = parse_name_from_play_text(p.get('description', ''))
        if name and name.lower() == player_last.lower():
            if 'rush' in p.get('play_type', '').lower():
                per = p.get('period')
                yds = p.get('yards') or 0
                if per == '1': segs['Q1'] += yds
                elif per == '2': segs['Q2'] += yds
                elif per == '3': segs['Q3'] += yds
                elif per == '4': segs['Q4'] += yds
                elif per in ('5','6','7'): segs['OT'] += yds
    segs['1H'] = segs['Q1'] + segs['Q2']
    segs['2H'] = segs['Q3'] + segs['Q4']
    segs['FULL GAME'] = segs['Q1'] + segs['Q2'] + segs['Q3'] + segs['Q4'] + segs['OT']
    return segs

def calculate_receiving_segments(plays, player_last):
    segs = {'Q1': 0, 'Q2': 0, 'Q3': 0, 'Q4': 0, 'OT': 0}
    for p in plays:
        desc = p.get('description', '')
        pt = p.get('play_type', '').lower()
        if 'to ' in desc and ('rec' in pt or 'pass' in pt):
            try:
                recv_part = desc.split('to ')[1].split(' for ')[0].strip()
                recv_name = recv_part.split()[0] if recv_part else ''
                recv_clean = parse_name_from_play_text(recv_name + ' dummy')
                if recv_clean and recv_clean.lower() == player_last.lower():
                    per = p.get('period')
                    yds = p.get('yards') or 0
                    if per == '1': segs['Q1'] += yds
                    elif per == '2': segs['Q2'] += yds
                    elif per == '3': segs['Q3'] += yds
                    elif per == '4': segs['Q4'] += yds
                    elif per in ('5','6','7'): segs['OT'] += yds
            except: pass
    segs['1H'] = segs['Q1'] + segs['Q2']
    segs['2H'] = segs['Q3'] + segs['Q4']
    segs['FULL GAME'] = segs['Q1'] + segs['Q2'] + segs['Q3'] + segs['Q4'] + segs['OT']
    return segs

def get_boxscore_kicking_stats(summary, player_id):
    for t in summary.get('boxscore', {}).get('players', []):
        for cat in t.get('statistics', []):
            if cat.get('name') == 'kicking':
                for a in cat.get('athletes', []):
                    if a.get('athlete', {}).get('id') == player_id:
                        stats = dict(zip(cat.get('labels', []), a.get('stats', [])))
                        return stats
    return None

def get_player_team_abbr(summary, player_id):
    for t in summary.get('boxscore', {}).get('players', []):
        for cat in t.get('statistics', []):
            for a in cat.get('athletes', []):
                if a.get('athlete', {}).get('id') == player_id:
                    return t.get('team', {}).get('abbreviation', '')
    return ''

def process_game(game_id, season=2026, conn=None):
    print(f"  Processing game {game_id}...")
    summary = None
    for attempt in range(3):
        try:
            summary = fetch_summary(game_id)
            if summary: break
        except: pass
        time.sleep(1)
    if not summary:
        print(f"    Failed to fetch summary for {game_id}")
        return 0
    
    # Check if game is completed
    status = 'unknown'
    for c in summary.get('header', {}).get('competitions', []):
        status = c.get('status', {}).get('type', {}).get('state', '?')
    if status != 'post':
        print(f"    Game {game_id} not completed (status: {status})")
        return 0
    
    plays = parse_plays_from_summary(summary, game_id)
    if not plays:
        print(f"    No plays found for {game_id}")
        return 0
    
    processed = 0
    for t in summary.get('boxscore', {}).get('players', []):
        team_abbr = t.get('team', {}).get('abbreviation', '')
        for cat in t.get('statistics', []):
            cat_name = cat.get('name', '')
            for a in cat.get('athletes', []):
                athlete = a.get('athlete', {})
                player_id = str(athlete.get('id') or '')
                name = athlete.get('displayName', '')
                if not player_id or not name: continue
                player_last = name.split()[-1]
                
                segments_data = {}
                
                if cat_name == 'passing':
                    segs = calculate_passing_segments(plays, player_last)
                    segments_data['passing_yards'] = segs
                elif cat_name == 'rushing':
                    segs = calculate_rushing_segments(plays, player_last)
                    segments_data['rushing_yards'] = segs
                elif cat_name == 'receiving':
                    segs = calculate_receiving_segments(plays, player_last)
                    segments_data['receiving_yards'] = segs
                elif cat_name == 'kicking':
                    stats = get_boxscore_kicking_stats(summary, player_id)
                    if stats:
                        fg = stats.get('FG', '0/0')
                        xp = stats.get('XP', '0/0')
                        pts = stats.get('PTS', '0')
                        fg_made = float(fg.split('/')[0]) if '/' in fg else 0
                        fg_att = float(fg.split('/')[1]) if '/' in fg else 0
                        xp_made = float(xp.split('/')[0]) if '/' in xp else 0
                        kick_pts = float(pts) if pts else 0
                        segments_data['fg_made'] = {'FULL GAME': fg_made}
                        segments_data['fg_att'] = {'FULL GAME': fg_att}
                        segments_data['xp_made'] = {'FULL GAME': xp_made}
                        segments_data['kicking_points'] = {'FULL GAME': kick_pts}
                
                if not segments_data:
                    continue
                
                for stat_type, segs in segments_data.items():
                    for segment, value in segs.items():
                        conn.execute("""
                            INSERT OR REPLACE INTO historical_segments 
                            (player_id, player_name, team, season, segment, stat_type, value)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (player_id, name, team_abbr, season, segment, stat_type, value))
                        processed += 1
    
    return processed

def fetch_summary(game_id):
    url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={game_id}"
    response = requests.get(url, timeout=15)
    if response.status_code == 200:
        return response.json()
    return None

def get_completed_games():
    teams = ['sf', 'ten', 'bal', 'min', 'atl', 'tb', 'cle', 'cin', 'hou', 'nyg', 'mia', 'nyj', 'ind', 'car', 'ari', 'det', 'lv', 'sea', 'phi', 'kc', 'buf', 'lac', 'den', 'pit', 'wsh', 'dal', 'gb', 'chi', 'jac', 'no', 'la']
    completed = set()
    for team in teams:
        try:
            url = f'https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{team}/schedule?season=2026&seasontype=2'
            r = requests.get(url, timeout=12)
            d = r.json()
            for e in d.get('events', []):
                comps = e.get('competitions', [])
                if comps:
                    status = comps[0].get('status', {}).get('type', {}).get('state', '?')
                    if status == 'post':
                        completed.add(e['id'])
            time.sleep(0.1)
        except Exception as e:
            print(f"Error fetching {team} schedule: {e}")
    return sorted(completed)

def main():
    print("Fetching completed games...")
    completed_games = get_completed_games()
    print(f"Found {len(completed_games)} completed games")
    
    conn = get_db()
    total_processed = 0
    
    for i, game_id in enumerate(completed_games):
        try:
            count = process_game(game_id, 2026, conn)
            total_processed += count
            conn.commit()
            if (i + 1) % 10 == 0:
                print(f"Progress: {i+1}/{len(completed_games)} games, {total_processed} segments")
        except Exception as e:
            print(f"Error processing {game_id}: {e}")
        time.sleep(0.3)  # Rate limit
    
    conn.close()
    print(f"\nTotal historical segments inserted: {total_processed}")

if __name__ == "__main__":
    main()