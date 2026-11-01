#!/usr/bin/env python3
import sqlite3
import json
import datetime
import urllib.parse
import re
import math
import time

DB_PATH = "/var/lib/liveintel/intel.db"

# Live game freshness window: 30 minutes
LIVE_DATA_TTL_SECONDS = 1800

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

def get_player_stats_for_segment(player_name, team, sport, segment='2H'):
    conn = get_db()
    stats = {}
    
    try:
        rows = conn.execute(
            """SELECT stat_type, value FROM player_segment_stats 
               WHERE player_name = ? AND team = ? AND segment = ?""",
            (player_name, team, segment)
        ).fetchall()
        
        for row in rows:
            stat_type = row['stat_type']
            value = row['value']
            
            if stat_type == 'passing_yards':
                stats['PTS'] = value
                stats['AST'] = 0
                stats['PRA'] = value
            elif stat_type == 'rushing_yards':
                stats['REB'] = value
                stats['PRA'] = stats.get('PRA', 0) + value
            elif stat_type == 'receiving_yards':
                stats['REC'] = value
                stats['PRA'] = stats.get('PRA', 0) + value
                
        # Calculate totals if not already calculated
        if 'PTS' in stats:
            stats['PTS'] = round(stats['PTS'], 1)
        if 'REB' in stats:
            stats['REB'] = round(stats['REB'], 1)
        if 'AST' in stats:
            stats['AST'] = round(stats['AST'], 1)
        if 'PRA' in stats:
            stats['PRA'] = round(stats['PRA'], 1)
            
        return stats
    finally:
        conn.close()

def get_player_info_from_multi_sport(player_name):
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT sport, team, position, player_id FROM multi_sport_players 
               WHERE player_name = ? LIMIT 1""",
            (player_name,)
        ).fetchone()
        
        if row:
            return {
                'sport': row['sport'],
                'team': row['team'],
                'position': row['position'] or 'UNKNOWN',
                'player_id': row['player_id']
            }
        return None
    finally:
        conn.close()

def get_latest_game_stats_for_player(player_name, team, sport):
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT game_id, segment, stat_type, value 
               FROM player_segment_stats 
               WHERE player_name = ? AND team = ? AND segment = 'FULL GAME' 
               ORDER BY last_updated DESC LIMIT 10""",
            (player_name, team)
        ).fetchall()
        
        stats_history = {}
        for row in rows:
            segment = row['segment']
            stat_type = row['stat_type']
            value = row['value']
            
            if segment not in stats_history:
                stats_history[segment] = {}
                
            if stat_type == 'passing_yards':
                stats_history[segment]['pts'] = value
                stats_history[segment]['pra'] = value
            elif stat_type == 'rushing_yards':
                stats_history[segment]['reb'] = value
                stats_history[segment]['pra'] = stats_history[segment].get('pra', 0) + value
            elif stat_type == 'receiving_yards':
                stats_history[segment]['rec'] = value
                stats_history[segment]['pra'] = stats_history[segment].get('pra', 0) + value
                
        return stats_history
    finally:
        conn.close()

def get_season_stats_for_player(player_name, team):
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT stat_type, value FROM player_segment_stats 
               WHERE player_name = ? AND team = ? AND segment = 'FULL GAME'""",
            (player_name, team)
        ).fetchall()
        
        season_stats = {}
        for row in rows:
            stat_type = row['stat_type']
            value = row['value']
            
            if stat_type == 'passing_yards':
                season_stats['pts'] = value
                season_stats['pra'] = value
            elif stat_type == 'rushing_yards':
                season_stats['reb'] = value
                season_stats['pra'] = season_stats.get('pra', 0) + value
            elif stat_type == 'receiving_yards':
                season_stats['rec'] = value
                season_stats['pra'] = season_stats.get('pra', 0) + value
                
        return season_stats
    finally:
        conn.close()

def get_late_game_signals_for_player(player_name, team):
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT * FROM nba_late_game_signals 
               WHERE player_name = ? AND team = ? 
               ORDER BY game_id DESC LIMIT 5""",
            (player_name, team)
        ).fetchall()
        
        return [dict(row) for row in rows]
    finally:
        conn.close()

def get_tickets_for_user(owner_name=None):
    conn = get_db()
    try:
        if owner_name:
            rows = conn.execute(
                """SELECT * FROM tickets 
                   WHERE owner_name = ? 
                   ORDER BY created_at DESC""",
                (owner_name,)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM tickets 
                   ORDER BY created_at DESC LIMIT 20"""
            ).fetchall()
            
        return [dict(row) for row in rows]
    finally:
        conn.close()

def get_bankroll_accounts_for_user(owner_name=None):
    conn = get_db()
    try:
        if owner_name:
            rows = conn.execute(
                """SELECT *, current_bankroll as current_balance FROM bankroll_accounts 
                   WHERE owner_name = ? 
                   ORDER BY updated_at DESC""",
                (owner_name,)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT *, current_bankroll as current_balance FROM bankroll_accounts 
                   ORDER BY updated_at DESC LIMIT 10"""
            ).fetchall()
            
        return [dict(row) for row in rows]
    finally:
        conn.close()

def get_live_games_with_players():
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT game_id, game_script, period, clock, away_score, home_score, 
                      away_team, home_team, leading_team, trailing_team
               FROM game_state 
               WHERE period IS NOT NULL AND clock IS NOT NULL 
               ORDER BY last_updated DESC LIMIT 10"""
        ).fetchall()
        
        games = []
        for row in rows:
            game_id = row['game_id']
            
            # Get sport from games table or multi_sport_players
            sport_row = conn.execute(
                "SELECT sport FROM games WHERE id = ?", (game_id,)
            ).fetchone()
            
            if sport_row:
                sport = sport_row['sport']
            else:
                # Fallback: check multi_sport_players
                sport_row = conn.execute(
                    "SELECT DISTINCT sport FROM multi_sport_players WHERE game_id = ?", (game_id,)
                ).fetchone()
                sport = sport_row['sport'] if sport_row else 'NBA'
            
            # Get key players for this game
            players = conn.execute(
                """SELECT DISTINCT player_name, team, position 
                   FROM multi_sport_players 
                   WHERE game_id = ? AND (sport = 'NBA' OR sport = 'NFL') 
                   LIMIT 5""",
                (game_id,)
            ).fetchall()
            
            game = dict(row)
            game['sport'] = sport
            game['key_players'] = [dict(p) for p in players]
            games.append(game)
            
        return games
    finally:
        conn.close()

def get_players_by_sport(sport='NBA'):
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT DISTINCT player_name, team, position, sport 
               FROM multi_sport_players 
               WHERE sport = ? 
               ORDER BY player_name ASC 
               LIMIT 50""",
            (sport,)
        ).fetchall()
        
        return [dict(row) for row in rows]
    finally:
        conn.close()

def get_historical_data_for_player(player_id, stat_type, segment='2H'):
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT value FROM historical_segments 
               WHERE player_id = ? AND stat_type = ? AND segment = ?
               ORDER BY id DESC LIMIT 10""",
            (player_id, stat_type, segment)
        ).fetchall()
        
        values = [row['value'] for row in rows]
        values.sort(reverse=True)
        
        return values
    finally:
        conn.close()

def get_nba_late_game_summary():
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT * FROM nba_late_game_signals 
               WHERE confidence = 'HIGH' 
               ORDER BY game_id DESC LIMIT 10"""
        ).fetchall()
        
        return [dict(row) for row in rows]
    finally:
        conn.close()

def get_tickets_by_type(is_real=None):
    conn = get_db()
    try:
        if is_real is None:
            rows = conn.execute(
                """SELECT * FROM tickets 
                   ORDER BY created_at DESC LIMIT 20"""
            ).fetchall()
        elif is_real:
            rows = conn.execute(
                """SELECT * FROM tickets 
                   WHERE environment != 'TEST' 
                   ORDER BY created_at DESC LIMIT 20"""
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM tickets 
                   WHERE environment = 'TEST' 
                   ORDER BY created_at DESC LIMIT 20"""
            ).fetchall()
            
        return [dict(row) for row in rows]
    finally:
        conn.close()

def is_fresh(timestamp_str, now=None):
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    
    if not timestamp_str:
        return False
    
    try:
        # Ensure now is timezone-naive for comparison
        if hasattr(now, 'tzinfo') and now.tzinfo is not None:
            now_naive = now.replace(tzinfo=None)
        else:
            now_naive = now
        
        # Handle timestamps with or without microseconds
        ts_str = timestamp_str
        if '.' in ts_str:
            ts_str = ts_str.split('.')[0]
        timestamp = datetime.datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')
        age_seconds = (now_naive - timestamp).total_seconds()
        return age_seconds <= LIVE_DATA_TTL_SECONDS
    except:
        return False

def categorize_game_status(status, last_updated, now=None):
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    
    if not status or not last_updated:
        return 'STALE'
    
    try:
        # Ensure now is timezone-naive for comparison with stored timestamps
        if hasattr(now, 'tzinfo') and now.tzinfo is not None:
            now_naive = now.replace(tzinfo=None)
        else:
            now_naive = now
        
        # Handle timestamps with or without microseconds
        ts_str = last_updated
        if '.' in ts_str:
            ts_str = ts_str.split('.')[0]
        last_updated_dt = datetime.datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')
        age_seconds = (now_naive - last_updated_dt).total_seconds()
        
        status_upper = status.upper()
        
        # For scheduled games (PRE), they're fresh if scheduled for today
        # regardless of when they were inserted into the database
        if any(s in status_upper for s in ['PRE', 'PRE-GAME', 'PREGAME', 'SCHEDULED']):
            # Check if game is scheduled for today
            game_date = last_updated_dt.strftime('%Y-%m-%d')
            today = now_naive.strftime('%Y-%m-%d')
            if game_date == today:
                return 'UPCOMING TODAY'
        
        # LIVE games: keep visible regardless of data freshness
        # ESPN statuses: LIVE, IN PROGRESS, HALFTIME, STARTED
        if any(s in status_upper for s in ['IN PROGRESS', 'LIVE', 'HALFTIME', 'STARTED']):
            return 'LIVE NOW'
        
        # For other statuses (POST, FINAL, etc.), use the freshness check
        if age_seconds > LIVE_DATA_TTL_SECONDS:
            return 'STALE'
        
        if any(s in status_upper for s in ['POST', 'FINAL', 'COMPLETED', 'ENDED']):
            return 'FINAL TODAY'
        else:
            return 'STALE'
    except:
        return 'STALE'

def get_games_by_status_category(status_category, now=None):
    conn = get_db()
    try:
        if now is None:
            now = datetime.datetime.utcnow()
        
        today_str = now.strftime('%Y-%m-%d')
        
        if status_category == 'LIVE NOW':
            rows = conn.execute(
                """SELECT game_id, game_script, away_team, home_team, 
                          period, clock, away_score, home_score,
                          last_updated
                    FROM game_state 
                    WHERE last_updated >= date('now', '-1 days')
                    ORDER BY last_updated DESC"""
            ).fetchall()
            
            filtered_rows = []
            for row in rows:
                status = categorize_game_status(row['game_script'], row['last_updated'], now)
                if status == 'LIVE NOW':
                    filtered_rows.append(row)
            
            return [dict(row) for row in filtered_rows]
            
        elif status_category == 'UPCOMING TODAY':
            rows = conn.execute(
                """SELECT game_id, game_script, away_team, home_team, 
                          period, clock, away_score, home_score,
                          last_updated
                    FROM game_state 
                    WHERE last_updated >= date('now', '-1 days')
                    ORDER BY last_updated DESC"""
            ).fetchall()
            
            filtered_rows = []
            for row in rows:
                status = categorize_game_status(row['game_script'], row['last_updated'], now)
                if status == 'UPCOMING TODAY':
                    filtered_rows.append(row)
            
            return [dict(row) for row in filtered_rows]
            
        elif status_category == 'FINAL TODAY':
            rows = conn.execute(
                """SELECT game_id, game_script, away_team, home_team, 
                          period, clock, away_score, home_score,
                          last_updated
                    FROM game_state 
                    WHERE last_updated >= date('now', '-1 days')
                    ORDER BY last_updated DESC"""
            ).fetchall()
            
            filtered_rows = []
            for row in rows:
                if is_fresh(row['last_updated'], now):
                    status = categorize_game_status(row['game_script'], row['last_updated'], now)
                    if status == 'FINAL TODAY':
                        filtered_rows.append(row)
            
            return [dict(row) for row in filtered_rows]
            
        elif status_category == 'STALE':
            all_rows = conn.execute(
                """SELECT game_id, game_script, away_team, home_team, 
                          period, clock, away_score, home_score,
                          last_updated
                    FROM game_state 
                    ORDER BY last_updated DESC"""
            ).fetchall()
            
            stale_rows = []
            for row in all_rows:
                if not is_fresh(row['last_updated'], now):
                    stale_rows.append(row)
            
            return [dict(row) for row in stale_rows]
        
        return []
    finally:
        conn.close()

if __name__ == '__main__':
    # Test data extraction
    print("Testing player stats extraction:")
    player_stats = get_player_stats_for_segment("Joe Burrow", "CIN", "NFL")
    print(f"Joe Burrow stats: {player_stats}")
    
    print("\nTesting player info extraction:")
    player_info = get_player_info_from_multi_sport("Joe Burrow")
    print(f"Joe Burrow info: {player_info}")
    
    print("\nTesting historical data:")
    hist_data = get_historical_data_for_player("4632266", "passing_yards")
    print(f"Joe Burrow historical passing yards (2H): {hist_data}")
    
    print("\nTesting live games:")
    live_games = get_live_games_with_players()
    print(f"Live games with players: {len(live_games)} games")
    
    print("\nTesting NBA late game signals:")
    late_game_signals = get_nba_late_game_summary()
    print(f"NBA late game signals: {len(late_game_signals)} signals")
    
    print("\nTesting tickets:")
    real_tickets = get_tickets_by_type(is_real=True)
    test_tickets = get_tickets_by_type(is_real=False)
    print(f"Real tickets: {len(real_tickets)}, Test tickets: {len(test_tickets)}")
    
    print("\nDone!")
