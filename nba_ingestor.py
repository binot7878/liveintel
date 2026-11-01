import sqlite3
from espn_stats import upsert_player_stats
import json
import datetime
import urllib.request
import urllib.error

DB_PATH = "/var/lib/liveintel/intel.db"


def get_db():
    for attempt in range(5):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30.0)
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.execute("PRAGMA journal_mode = DELETE")
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.OperationalError as e:
            if "locked" in str(e) and attempt < 4:
                import time
                time.sleep(0.5 * (attempt + 1))
                continue
            raise


def fetch_nba_summary(game_id):
    """Fetch NBA game summary from ESPN API"""
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={game_id}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            data = response.read()
            return json.loads(data)
    except urllib.error.URLError as e:
        print(f"Failed to fetch NBA summary for {game_id}: {e}")
        return None


def fetch_nba_play_by_play_fallback(game_id):
    """Fallback method to get play-by-play data using a different source"""
    # Try to fetch game data from a different ESPN endpoint
    # This might not have detailed play-by-play but we can try
    
    # First, try to get the basic game info
    info_url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
    try:
        req = urllib.request.Request(info_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            data = response.read()
            games = json.loads(data)
            
            # Look for our game in the scoreboard
            for event in games.get('events', []):
                if event.get('id') == game_id:
                    return {
                        'game_info': event,
                        'competitors': event.get('competitions', [])[0].get('competitors', []),
                        'boxscore': fetch_nba_summary(game_id).get('boxscore', {}) if fetch_nba_summary(game_id) else {}
                    }
    except Exception as e:
        print(f"Failed in fallback: {e}")
        
    return None


def parse_name_from_play_text(text):
    """Extract player name from play description text"""
    if not text:
        return None
    tokens = text.split()
    for tok in tokens:
        if "." in tok:
            parts = tok.split(".", 1)
            if len(parts) == 2 and parts[1]:
                return parts[1].strip(".")
    return None


def parse_nba_play_from_detailed(play_data, game_id):
    """Parse a play from detailed NBA play-by-play data"""
    period = play_data.get('period', {})
    clock = play_data.get('clock', {})
    
    # Parse team information
    team_data = play_data.get('team', {})
    team_id = team_data.get('id', '')
    
    # Parse participants for player information
    participants = []
    if 'participants' in play_data:
        for tp in play_data['participants']:
            athlete = tp.get('athlete', {})
            if athlete:
                participant = {
                    'athlete_id': str(athlete.get('id', '')),
                    'athlete_name': athlete.get('displayName', ''),
                    'athlete_team': athlete.get('team', {}).get('abbreviation', ''),
                    'position': athlete.get('position', {}).get('abbreviation', ''),
                    'type': tp.get('type', ''),
                    'playStatistics': tp.get('playStatistics', {}).get('$ref', '')
                }
                participants.append(participant)
    
    # Extract score information
    home_score = play_data.get('homeScore', 0)
    away_score = play_data.get('awayScore', 0)
    score_after = json.dumps({'home': home_score, 'away': away_score})
    
    # Build the play record
    play = {
        'game_id': game_id,
        'drive_id': '',
        'play_id': str(play_data.get('id', '')),
        'period': str(period.get('number', '')),
        'clock': clock.get('displayValue', '') if clock else '',
        'possession': team_id,
        'down': '',
        'distance': '',
        'yard_line': '',
        'description': play_data.get('text', ''),
        'play_type': play_data.get('type', {}).get('text', ''),
        'participants': json.dumps(participants),
        'yards': play_data.get('statYardage'),
        'score_after': score_after,
        'event_timestamp': play_data.get('wallclock', ''),
        'local_received_timestamp': play_data.get('modified', '')
    }
    
    return play


def ingest_nba_game(game_id):
    """Ingest a single NBA game into the database"""
    print(f"Ingesting NBA game {game_id}...")
    
    # Fetch game summary
    summary = fetch_nba_summary(game_id)
    if not summary:
        print(f"  Failed to fetch summary for {game_id}")
        return False
    
    # Get game info from header
    header = summary.get('header', {})
    competitions = header.get('competitions', [])
    
    if not competitions:
        print(f"  No competition data for {game_id}")
        return False
    
    comp = competitions[0]
    competitors = comp.get('competitors', [])
    
    # Determine home and away teams
    home_team = None
    away_team = None
    home_score = 0
    away_score = 0
    
    for c in competitors:
        team_info = c.get('team', {})
        team_abbr = team_info.get('abbreviation', '')
        is_home = c.get('homeAway', '').lower() == 'home'
        score = c.get('score', 0)
        
        if is_home:
            home_team = team_abbr
            home_score = score
        else:
            away_team = team_abbr
            away_score = score
    
    # Get game status
    status = comp.get('status', {}).get('type', {}).get('state', 'unknown')
    
    # Insert game into games table
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO games (id, sport, home_team, away_team, status, last_updated) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET home_team=COALESCE(NULLIF(excluded.home_team,''),games.home_team), "
            "away_team=COALESCE(NULLIF(excluded.away_team,''),games.away_team), "
            "status=CASE WHEN lower(games.status) IN ('post','final','completed','ended') THEN games.status ELSE excluded.status END, "
            "last_updated=excluded.last_updated",
            (game_id, 'NBA', home_team, away_team, status, datetime.datetime.now())
        )
        
        print(f"  Game inserted: {away_team} @ {home_team} - {away_score} - {home_score} ({status})")
        
        # Try to get play-by-play data
        # For now, we'll use the summary data to create some basic plays
        # This is a fallback since the ESPN API doesn't provide detailed play-by-play
        
        # Create a few basic plays based on available data
        plays_to_insert = []
        
        # Try to get play-by-play from a different source
        fallback_data = fetch_nba_play_by_play_fallback(game_id)
        if fallback_data:
            print(f"  Retrieved fallback data for {game_id}")
            
            # In a real implementation, we would parse the play-by-play data here
            # For now, we'll just note that we have the data
            # plays_to_insert = parse_nba_plays_from_data(fallback_data, game_id)
        
        # Parse and store NBA play-by-play data
        plays_to_insert = []
        if 'plays' in summary:
            print(f"  Found {len(summary['plays'])} plays in NBA game summary")
            
            for play_data in summary['plays']:
                # Only process plays with period and clock
                period = play_data.get('period', {})
                clock = play_data.get('clock', {})
                
                if period and clock:
                    play = parse_nba_play_from_detailed(play_data, game_id)
                    plays_to_insert.append(play)
            
            print(f"  Parsed {len(plays_to_insert)} valid plays with period/clock")
        else:
            print(f"  No 'plays' field in NBA game summary")
        
        # Store plays if we have them
        if plays_to_insert:
            for play in plays_to_insert:
                conn.execute(
                    """INSERT INTO plays 
                       (id, game_id, drive_id, play_id, period, clock, possession, down, distance, yard_line, 
                        description, play_type, participants, yards, score_after, event_timestamp, local_received_timestamp)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        f"{game_id}_{play['play_id']}", play['game_id'], play['drive_id'], play['play_id'],
                        play['period'], play['clock'], play['possession'], play['down'], play['distance'],
                        play['yard_line'], play['description'], play['play_type'], play['participants'],
                        play.get('yards'), play.get('score_after'), play.get('event_timestamp'),
                        play.get('local_received_timestamp')
                    )
                )
        
        # Insert player stats from boxscore
        boxscore = summary.get('boxscore', {})
        players = boxscore.get('players', [])
        
        inserted_players = 0
        for player_group in players:
            team_info = player_group.get('team', {})
            team_abbr = team_info.get('abbreviation', '')
            
            for stat_category in player_group.get('statistics', []):
                category_name = stat_category.get('name', '')
                athletes = stat_category.get('athletes', [])
                
                for athlete in athletes:
                    player_info = athlete.get('athlete', {})
                    player_id = str(player_info.get('id', ''))
                    player_name = player_info.get('displayName', '')
                    
                    if not player_id or not player_name:
                        continue
                    
                    # Store basic stats in player_game_stats
                    stats = dict(zip(stat_category.get('labels', []), athlete.get('stats', [])))
                    
                    upsert_player_stats(conn, game_id, player_id, player_name, team_abbr,
                                        {category_name: stats}, "FULL GAME", datetime.datetime.now())
                    
                    inserted_players += 1
        
        conn.commit()
        print(f"  Game {game_id} ingested successfully: {inserted_players} players, {len(plays_to_insert)} plays")
        return True
        
    except Exception as e:
        conn.rollback()
        print(f"  Error ingesting {game_id}: {e}")
        return False
    finally:
        conn.close()


def fetch_nba_schedule(date_str):
    """Fetch today's NBA schedule from ESPN"""
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={date_str}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            data = response.read()
            return json.loads(data)
    except urllib.error.URLError as e:
        print(f"Failed to fetch NBA schedule for {date_str}: {e}")
        return None

def ingest_nba_game(game_id):
    """Ingest a single NBA game into the database"""
    print(f"Ingesting NBA game {game_id}...")
    
    # Fetch game summary
    summary = fetch_nba_summary(game_id)
    if not summary:
        print(f"  Failed to fetch summary for {game_id}")
        return False
    
    # Get game info from header
    header = summary.get('header', {})
    competitions = header.get('competitions', [])
    
    if not competitions:
        print(f"  No competition data for {game_id}")
        return False
    
    comp = competitions[0]
    competitors = comp.get('competitors', [])
    
    # Determine home and away teams
    home_team = None
    away_team = None
    home_score = 0
    away_score = 0
    
    for c in competitors:
        team_info = c.get('team', {})
        team_abbr = team_info.get('abbreviation', '')
        is_home = c.get('homeAway', '').lower() == 'home'
        score = c.get('score', 0)
        
        if is_home:
            home_team = team_abbr
            home_score = score
        else:
            away_team = team_abbr
            away_score = score
    
    # Get game status
    status = comp.get('status', {}).get('type', {}).get('state', 'unknown')
    
    # Insert game into games table
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO games (id, sport, home_team, away_team, status, last_updated) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET home_team=COALESCE(NULLIF(excluded.home_team,''),games.home_team), "
            "away_team=COALESCE(NULLIF(excluded.away_team,''),games.away_team), "
            "status=CASE WHEN lower(games.status) IN ('post','final','completed','ended') THEN games.status ELSE excluded.status END, "
            "last_updated=excluded.last_updated",
            (game_id, 'NBA', home_team, away_team, status, datetime.datetime.now())
        )
        
        print(f"  Game inserted: {away_team} @ {home_team} - {away_score} - {home_score} ({status})")
        
        # Try to get play-by-play data
        # For now, we'll use the summary data to create some basic plays
        # This is a fallback since the ESPN API doesn't always provide detailed play-by-play
        
        # Create a few basic plays based on available data
        plays_to_insert = []
        
        # Try to get play-by-play from a different source
        fallback_data = fetch_nba_play_by_play_fallback(game_id)
        if fallback_data:
            print(f"  Retrieved fallback data for {game_id}")
            
            # In a real implementation, we would parse the play-by-play data here
            # For now, we'll just note that we have the data
            # plays_to_insert = parse_nba_plays_from_data(fallback_data, game_id)
        
        # Parse and store NBA play-by-play data
        plays_to_insert = []
        if 'plays' in summary:
            print(f"  Found {len(summary['plays'])} plays in NBA game summary")
            
            for play_data in summary['plays']:
                # Only process plays with period and clock
                period = play_data.get('period', {})
                clock = play_data.get('clock', {})
                
                if period and clock:
                    play = parse_nba_play_from_detailed(play_data, game_id)
                    plays_to_insert.append(play)
            
            print(f"  Parsed {len(plays_to_insert)} valid plays with period/clock")
        else:
            print(f"  No 'plays' field in NBA game summary")
        
        # Store plays if we have them
        if plays_to_insert:
            for play in plays_to_insert:
                conn.execute(
                    """INSERT INTO plays 
                       (id, game_id, drive_id, play_id, period, clock, possession, down, distance, yard_line, 
                        description, play_type, participants, yards, score_after, event_timestamp, local_received_timestamp)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        f"{game_id}_{play['play_id']}", play['game_id'], play['drive_id'], play['play_id'],
                        play['period'], play['clock'], play['possession'], play['down'], play['distance'],
                        play['yard_line'], play['description'], play['play_type'], play['participants'],
                        play.get('yards'), play.get('score_after'), play.get('event_timestamp'),
                        play.get('local_received_timestamp')
                    )
                )
        
        # Insert player stats from boxscore
        boxscore = summary.get('boxscore', {})
        players = boxscore.get('players', [])
        
        inserted_players = 0
        for player_group in players:
            team_info = player_group.get('team', {})
            team_abbr = team_info.get('abbreviation', '')
            
            for stat_category in player_group.get('statistics', []):
                category_name = stat_category.get('name', '')
                athletes = stat_category.get('athletes', [])
                
                for athlete in athletes:
                    player_info = athlete.get('athlete', {})
                    player_id = str(player_info.get('id', ''))
                    player_name = player_info.get('displayName', '')
                    
                    if not player_id or not player_name:
                        continue
                    
                    # Store basic stats in player_game_stats
                    stats = dict(zip(stat_category.get('labels', []), athlete.get('stats', [])))
                    
                    upsert_player_stats(conn, game_id, player_id, player_name, team_abbr,
                                        {category_name: stats}, "FULL GAME", datetime.datetime.now())
                    
                    inserted_players += 1
        
        conn.commit()
        print(f"  Game {game_id} ingested successfully: {inserted_players} players, {len(plays_to_insert)} plays")
        return True
        
    except Exception as e:
        conn.rollback()
        print(f"  Error ingesting {game_id}: {e}")
        return False
    finally:
        conn.close()

def ingest_today_nba_games():
    """Ingest all today's NBA games - both scheduled and active"""
    today_str = datetime.datetime.now().strftime('%Y%m%d')
    
    print(f"Fetching today's NBA schedule for {today_str}...")
    data = fetch_nba_schedule(today_str)
    
    if not data or 'events' not in data:
        print("  No events found in schedule")
        return 0, 0
    
    events = data.get('events', [])
    print(f"  Found {len(events)} games in schedule")
    
    total_games = 0
    successful_games = 0
    
    for event in events:
        game_id = event.get('id', '')
        if not game_id:
            continue
        
        # Get competitors
        competitions = event.get('competitions', [])
        if not competitions:
            continue
        
        comp = competitions[0]
        competitors = comp.get('competitors', [])
        if len(competitors) != 2:
            continue
        
        # Determine home and away
        home_team = None
        away_team = None
        for c in competitors:
            team_info = c.get('team', {})
            team_abbr = team_info.get('abbreviation', '')
            is_home = c.get('homeAway', '').lower() == 'home'
            
            if is_home:
                home_team = team_abbr
            else:
                away_team = team_abbr
        
        # Keep the completed lifecycle distinct from the situation display label.
        status_type = comp.get('status', {}).get('type', {})
        status = status_type.get('state', 'pre')
        completed = status_type.get('completed') is True or str(status).lower() in ('post', 'final')
        if completed:
            status = 'post'
        game_script = 'FINAL' if completed else status.upper()
        
        # Insert into games table
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO games (id, sport, home_team, away_team, status, last_updated) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET home_team=COALESCE(NULLIF(excluded.home_team,''),games.home_team), "
                "away_team=COALESCE(NULLIF(excluded.away_team,''),games.away_team), "
                "status=CASE WHEN lower(games.status) IN ('post','final','completed','ended') THEN games.status ELSE excluded.status END, "
                "last_updated=excluded.last_updated",
                (game_id, 'NBA', home_team, away_team, status, datetime.datetime.now())
            )
            
            # Insert into game_state with status information
            conn.execute(
                """INSERT OR REPLACE INTO game_state 
                   (game_id, game_script, away_team, home_team, period, clock, away_score, home_score, last_updated)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (game_id, game_script, away_team, home_team, None, None, 0, 0, datetime.datetime.now())
            )
            
            conn.commit()
            print(f"  Scheduled game inserted: {away_team} @ {home_team} ({status})")
            successful_games += 1
        except Exception as e:
            print(f"  Error inserting {game_id}: {e}")
            conn.rollback()
        finally:
            conn.close()
        
        total_games += 1
    
    print(f"\nNBA Schedule Ingestion Complete:")
    print(f"Total games in schedule: {total_games}")
    print(f"Successfully inserted: {successful_games}")
    print(f"Failed: {total_games - successful_games}")
    
    return total_games, successful_games


if __name__ == "__main__":
    # First ingest today's schedule
    ingest_today_nba_games()
    
    # Then try to get detailed data for any active games
    nba_game_ids = [
        '401909868', '401909869', '401909870',
        '401909871', '401909872', '401909873',
        '401909874', '401909875', '401909876'
    ]
    
    print(f"\nStarting NBA game ingestion for {len(nba_game_ids)} games...")
    
    total_games = 0
    successful_games = 0
    
    for game_id in nba_game_ids:
        if ingest_nba_game(game_id):
            successful_games += 1
        total_games += 1
    
    print(f"\nNBA Game Ingestion Complete:")
    print(f"Total games processed: {total_games}")
    print(f"Successfully ingested: {successful_games}")
    print(f"Failed: {total_games - successful_games}")
    
    # Return summary for verification
    conn = get_db()
    
    try:
        # Get NBA game count
        nba_games = conn.execute("SELECT COUNT(*) FROM games WHERE sport = 'NBA'").fetchone()[0]
        
        # Get NBA play count
        nba_plays = conn.execute("SELECT COUNT(*) FROM plays WHERE game_id LIKE '%401909868%' OR game_id LIKE '%401909869%' OR game_id LIKE '%401909870%'").fetchone()[0]
        
        # Get NBA player stats count
        nba_stats = conn.execute("SELECT COUNT(*) FROM player_game_stats WHERE game_id LIKE '%401909868%' OR game_id LIKE '%401909869%' OR game_id LIKE '%401909870%'").fetchone()[0]
        
        # Get game_state counts by status
        live_state = conn.execute("SELECT COUNT(*) FROM game_state WHERE game_script LIKE '%LEADING%' OR game_script LIKE '%TIED%' OR game_script LIKE '%NORMAL%'").fetchone()[0]
        
        print(f"\nDatabase Counts:")
        print(f"NBA Games: {nba_games}")
        print(f"NBA Plays: {nba_plays}")
        print(f"NBA Player Stats: {nba_stats}")
        print(f"Live Game State Rows: {live_state}")
        
    finally:
        conn.close()
