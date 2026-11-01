"""Read-only presentation metrics; no pick/learning decisions or writes."""
import json
import math

SPECIALISTS = {'BASKETBALL_AI': ('basketball', ('NBA', 'WNBA')), 'NFL_AI': ('nfl', ('NFL',)), 'CFB_AI': ('cfb', ('CFB',))}

def number(v):
    return float(v) if isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v) else None

def metrics(picks, bankroll, memory, performance):
    settled = sorted([p for p in picks if p.get('result') in ('WIN','LOSS','PUSH') and p.get('settled_at')], key=lambda p:(p['settled_at'],p.get('id',0)), reverse=True)
    recent=settled[:10]
    decisive=[p for p in recent if p['result'] != 'PUSH']
    wins=sum(p['result']=='WIN' for p in decisive)
    losses=len(decisive)-wins
    pnl=sum(p['profit_loss'] for p in recent) if recent and all(number(p.get('profit_loss')) is not None for p in recent) else None
    streak=0
    if settled:
        for p in settled:
            if p['result'] != settled[0]['result']: break
            streak+=1
    score=round(100*(wins+2)/(len(decisive)+4)) if decisive else 50
    label='NEUTRAL'
    if len(decisive)>=5:
        label='HOT' if score>=70 and pnl is not None and pnl>0 else 'COLD' if score<=30 and pnl is not None and pnl<0 else 'WARM' if score>=58 else 'COOL' if score<=42 else 'NEUTRAL'
    current=number(bankroll.get('current_bankroll')); start=number(bankroll.get('starting_bankroll')); high=number(bankroll.get('high_water_mark'))
    high=high if high is not None and current is not None and high>=current and high>0 else None
    overall=next((m for m in memory if m['namespace']=='confidence_calibration' and m['key']=='overall'),None)
    learned=None
    if overall:
        try: learned=number(json.loads(overall['value']).get('total_picks'))
        except (ValueError,TypeError,AttributeError): pass
    perf=max((sum(number(p.get(k)) or 0 for k in ('wins','losses','pushes')) for p in performance),default=0)
    # Aggregate learning count is corroborated conservatively, never sum overlapping memory samples.
    learned=int(min(learned,len(settled),perf)) if learned is not None and learned>=0 else None
    level='NEW' if not learned else 'BUILDING' if learned<20 else 'LEARNING' if learned<100 else 'ESTABLISHED'
    return dict(HEAT_SCORE=score,HEAT_LABEL=label,RECENT_RECORD=f'{wins}-{losses}',RECENT_SAMPLE_SIZE=len(recent),RECENT_PL=pnl,CURRENT_STREAK=(f'{streak} {settled[0]["result"]}' if settled else 'NONE'),HEAT_SAMPLE_STATUS='INSUFFICIENT SAMPLE' if len(decisive)<5 else 'SUFFICIENT SAMPLE',CURRENT_BANKROLL=current,STARTING_BANKROLL=start,GOAL=10000,PROGRESS_PCT=((current-start)/(10000-start)*100 if current is not None and start is not None and start<10000 else None),HIGH_WATER_MARK=high,CURRENT_DRAWDOWN=(high-current if high is not None else None),MEMORY_LEVEL=level,SETTLED_PICKS_LEARNED=learned,MEMORY_FRESHNESS=(overall.get('updated_at') if overall else None),ADAPTATION_ACTIVE='UNVERIFIED',CONFIDENCE_SAMPLE_STATUS='INSUFFICIENT SAMPLE' if learned is None or learned<20 else 'EVIDENCE AVAILABLE — ADAPTATION UNVERIFIED',WAGERED=number(bankroll.get('total_wagered')),PL=number(bankroll.get('total_profit_loss')),ROI=number(bankroll.get('roi')),RECORD=f'{sum(p["result"]=="WIN" for p in settled)}-{sum(p["result"]=="LOSS" for p in settled)}',LAST_RESULT=(settled[0] if settled else None),ACTIVE_PICKS=[p for p in picks if p.get('result') in (None,'','PENDING')])

def read_monitoring(conn):
    from challenge_ledger import snapshot
    from verified_offers import scan_offers
    _, offer_audit = scan_offers(conn)
    output = {}
    for ai in SPECIALISTS:
        state = snapshot(conn, ai)
        picks = []
        for t in state['tickets']:
            p = dict(t)
            p.update(result=t['ticket_result'], profit_loss=t['profit_loss_cents']/100 if t['profit_loss_cents'] is not None else None,
                     player_name=t['book'] + ' ' + t['ticket_type'], player=t['book'], market=t['ticket_type'], side='', line=None)
            picks.append(p)
        output[ai] = metrics(picks, state, [], [])
        output[ai]['SETTLED_PICKS_LEARNED'] = 0
        output[ai]['CHALLENGE_RUN_ID'] = state['run']['challenge_run_id']
        from challenge_decisions import latest_decision, ticket_views
        output[ai]['CURRENT_DECISION'] = latest_decision(conn, ai)
        output[ai]['TICKETS'] = ticket_views(conn, ai)
        from risk_policy import policy, cash
        from survival_awards import awards
        output[ai]['RISK_POLICY'] = policy(cash(state['current_bankroll']),cash(state['high_water_mark']),cash(state['pending_exposure']))
        output[ai]['SURVIVAL'] = awards(conn,ai)
        output[ai]['OFFERS'] = [o for o in offer_audit if o.get('AI_ID') == ai]
    return output

from http.server import HTTPServer, BaseHTTPRequestHandler
import sqlite3
import json
import urllib.parse
import urllib.request
import datetime
import time
from situation_engine import get_team_abbr, determine_situation_alerts, get_prop_situation_effect

DB_PATH = "/var/lib/liveintel/intel.db"

LIVE_DATA_TTL_SECONDS = 1800

ESPN_START_TIME_CACHE = {'ts': 0, 'data': {}}
ESPN_FINAL_STATS_CACHE = {'ts': 0, 'data': {}}

SETTLEMENT_SIDE_MAP = {
    'OVER': 'MORE',
    'UNDER': 'LESS',
    'MORE': 'OVER',
    'LESS': 'UNDER'
}

MARKET_TO_STAT_TYPE = {
    'Passing Yards': 'passing_yards',
    'Rushing Yards': 'rushing_yards',
    'Receiving Yards': 'receiving_yards',
    'Receptions': 'receptions',
    'Passing Touchdowns': 'passing_tds',
    'Rushing Touchdowns': 'rushing_tds',
    'Receiving Touchdowns': 'receiving_tds',
    'Interceptions': 'interceptions',
    'Sacks': 'sacks',
    'Tackles': 'tackles',
    'Points': 'points',
    'Rebounds': 'rebounds',
    'Assists': 'assists',
    'Three Pointers Made': 'three_pointers_made',
    'Field Goals Made': 'fg_made',
    'Free Throws Made': 'ft_made',
    'Turnovers': 'turnovers',
    'Steals': 'steals',
    'Blocks': 'blocks',
    'Kicking Points': 'kicking_points',
    'Field Goals Made': 'fg_made',
    'Extra Points Made': 'xp_made',
    'Spread': 'spread',
    'Moneyline': 'moneyline',
    'Total Points': 'total_points',
}

def get_espn_start_time_map():
    """Fetch ESPN scoreboards (cached 5 min) and return {game_id: '7:00 PM ET'}."""
    now_ts = time.time()
    if now_ts - ESPN_START_TIME_CACHE['ts'] < 300 and ESPN_START_TIME_CACHE['data']:
        return ESPN_START_TIME_CACHE['data']
    result = {}
    from zoneinfo import ZoneInfo
    league_paths = [
        ('Football/NFL', 'football/nfl'),
        ('Basketball/NBA', 'basketball/nba'),
        ('Football/CFB', 'college-football'),
    ]
    et = ZoneInfo('America/New_York')
    dates = [(datetime.datetime.now() + datetime.timedelta(days=offset)).strftime('%Y%m%d')
             for offset in range(5)]
    for _, path in league_paths:
        for date_str in dates:
            try:
                url = f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard?dates={date_str}"
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=10) as response:
                    data = json.loads(response.read())
                for event in data.get('events', []):
                    gid = str(event.get('id', ''))
                    iso = event.get('date', '')
                    if not gid or not iso:
                        continue
                    try:
                        dt = datetime.datetime.fromisoformat(iso.replace('Z', '+00:00'))
                        result[gid] = dt.astimezone(et).strftime('%a %m/%d %I:%M %p ET')
                    except Exception:
                        result[gid] = iso
            except Exception:
                continue
    if result:
        ESPN_START_TIME_CACHE['ts'] = now_ts
        ESPN_START_TIME_CACHE['data'] = result
    return result

def is_fresh(timestamp_str, now=None):
    if now is None:
        now = datetime.datetime.utcnow()
    
    if not timestamp_str:
        return False
    
    try:
        timestamp = datetime.datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
        age_seconds = (now - timestamp).total_seconds()
        return age_seconds <= LIVE_DATA_TTL_SECONDS
    except:
        return False

def categorize_game_status(status, last_updated, now=None):
    if now is None:
        now = datetime.datetime.utcnow()
    
    if not status or not last_updated:
        return 'STALE'
    
    try:
        last_updated_dt = datetime.datetime.strptime(last_updated, '%Y-%m-%d %H:%M:%S')
        age_seconds = (now - last_updated_dt).total_seconds()
        
        status_upper = status.upper()
        
        # LIVE games: keep visible regardless of data freshness
        # ESPN statuses: LIVE, IN PROGRESS, HALFTIME, STARTED
        if any(s in status_upper for s in ['IN PROGRESS', 'LIVE', 'HALFTIME', 'STARTED']):
            return 'LIVE NOW'
        
        # For other statuses, use the freshness check
        if age_seconds > LIVE_DATA_TTL_SECONDS:
            return 'STALE'
        
        if any(s in status_upper for s in ['PRE', 'PRE-GAME', 'PREGAME', 'SCHEDULED']):
            return 'UPCOMING TODAY'
        elif any(s in status_upper for s in ['POST', 'FINAL', 'COMPLETED', 'ENDED']):
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
                if is_fresh(row['last_updated'], now):
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
                time.sleep(0.5 * (attempt + 1))
                continue
            raise

def find_prop_segment_stat(conn, game_id, player_name, stat_type):
    """Resolve a unique same-game identity before reading its full-game stat."""
    normalize_name = lambda name: ' '.join(str(name or '').casefold().split())
    game = conn.execute(
        'SELECT sport, away_team, home_team FROM games WHERE id = ?', (game_id,)
    ).fetchone()
    if not game:
        return None
    candidates = conn.execute("""
        SELECT player_id, player_name, team FROM player_segment_stats
        WHERE game_id = ? AND segment = 'FULL GAME'
        UNION
        SELECT player_id, player_name, team FROM multi_sport_players
        WHERE game_id = ? AND segment = 'FULL GAME' AND sport = ?
    """, (game_id, game_id, game['sport'])).fetchall()
    identities = {(r['player_id'], r['team']) for r in candidates
                  if normalize_name(r['player_name']) == normalize_name(player_name)
                  and r['player_id'] and r['team'] in (game['away_team'], game['home_team'])}
    if len(identities) != 1:
        return None
    player_id, team = identities.pop()
    rows = conn.execute("""
        SELECT value FROM player_segment_stats
        WHERE game_id = ? AND player_id = ? AND team = ?
          AND segment = 'FULL GAME' AND stat_type = ?
    """, (game_id, player_id, team, stat_type)).fetchall()
    # The NFL full-game feed can exist before segment stats are populated.
    if not rows and stat_type == 'passing_yards' and game['sport'] == 'NFL':
        rows = conn.execute("""
            SELECT pass_yards AS value FROM multi_sport_players
            WHERE game_id = ? AND player_id = ? AND team = ? AND sport = 'NFL'
              AND segment = 'FULL GAME'
        """, (game_id, player_id, team)).fetchall()
    values = {r['value'] for r in rows if r['value'] is not None}
    if len(values) != 1:
        return None
    return {'player_id': player_id, 'team': team, 'value': values.pop()}


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
        p75 = values[int(n * 0.75)]
        p90 = values[int(n * 0.9)]
        return {
            'sample': n,
            'avg': sum(values) / n,
            'median': values[n // 2],
            'p25': p25,
            'p75': p75,
            'p90': p90,
            'max': max(values),
            'values': values
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

def get_prop_state(current, line, side, game_status='in'):
    """Determine the current state of a prop based on current value and side."""
    diff = current - line
    
    if side == 'OVER':
        if diff > 0:
            return 'OVER HIT', 'WON'
        elif diff < 0:
            return 'LIVE — OVER BELOW LINE', 'LIVE'
        else:
            return 'PUSH', 'PUSH'
    else:  # UNDER
        if diff < 0:
            return 'LIVE — UNDER BELOW LINE', 'LIVE'
        elif diff > 0:
            return 'UNDER LOST', 'LOST'
        else:
            return 'PUSH', 'PUSH'

def get_display_language(current, line, side):
    """Generate human-readable display text for the prop."""
    diff = current - line
    abs_diff = abs(diff)
    
    if side == 'OVER':
        if diff > 0:
            return f"OVER HIT — ABOVE LINE BY {abs_diff:.1f}"
        elif diff < 0:
            return f"NEEDS {abs_diff:.1f} MORE TO CLEAR OVER"
        else:
            return "PUSH — EXACTLY ON LINE"
    else:  # UNDER
        if diff < 0:
            return f"UNDER HAS {abs_diff:.1f} YARDS OF CUSHION"
        elif diff > 0:
            return f"UNDER LOST — LINE EXCEEDED BY {abs_diff:.1f}"
        else:
            return "PUSH — EXACTLY ON LINE"

def calculate_projections(prop_id, current_value, line, side, game_id, player_name, player_id, team, stat_type, period, clock):
    # Compute difference and state
    diff = current_value - line
    state, outcome = get_prop_state(current_value, line, side)
    display_text = get_display_language(current_value, line, side)
    
    # Only compute projections if still live (not settled)
    is_live = outcome == 'LIVE'
    
    if is_live:
        # Map segment for historical lookup
        if stat_type in ('kicking_points', 'fg_made', 'fg_att', 'xp_made'):
            hist_segment = 'FULL GAME'
        else:
            hist_segment = '2H'
        
        hist = get_historical_remaining(player_id, player_name, team, stat_type, hist_segment, 2026)
        
        if hist:
            hist_median = hist['median']
            hist_p75 = hist['p75']
            hist_p90 = hist['p90']
            projected_median = current_value + hist_median if side == 'OVER' else current_value - hist_median
            projected_low = current_value + hist['p25'] if side == 'OVER' else current_value - hist_p75
            projected_high = current_value + hist_p90 if side == 'OVER' else current_value - hist['p25']
            historical_label = f"AVAILABLE HISTORY: {hist['sample']} games (2026 season)"
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
    else:
        # Settled prop - no projection needed
        projected_low = projected_median = projected_high = current_value
        hist_median = hist_p75 = hist_p90 = None
        hist = None
        historical_label = f"SETTLED — {state}"
    
    signal, confidence, reasons = determine_signal(
        current_value, line, side, diff, outcome,
        hist_median if hist else None, 
        hist_p75 if hist else None, 
        hist_p90 if hist else None, 
        hist,
        period, clock, projected_median
    )
    
    return {
        'current_value': current_value,
        'line': line,
        'side': side,
        'diff': diff,
        'abs_diff': abs(diff),
        'state': state,
        'outcome': outcome,
        'display_text': display_text,
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
        'historical_label': historical_label,
        'is_live': is_live
    }

def determine_signal(current, line, side, diff, outcome, hist_med, hist_p75, hist_p90, hist, period, clock, projected):
    reasons = []
    
    if outcome != 'LIVE':
        return state_label(outcome, side), 'FINAL', [f"Prop settled: {get_display_language(current, line, side)}"]
    
    if not hist:
        reasons.append("No historical data available")
        return "DATA INCOMPLETE", "LOW", "; ".join(reasons)
    
    reasons.append(f"Historical 2H median: {hist_med:.1f}, 75th: {hist_p75:.1f}, 90th: {hist_p90:.1f} (n={hist['sample']})")
    
    if side == 'OVER':
        # OVER: need current > line to win
        needed = -diff  # positive = how much more needed
        if needed <= 0:
            return "OVER HIT", "FINAL", ["Already over line"]
        
        if hist_p75 is not None and needed > hist_p75:
            reasons.append(f"Needs {needed:.1f} more; above historical 75th percentile ({hist_p75:.1f})")
            return "OVER WATCH", "MEDIUM", "; ".join(reasons)
        if hist_p90 is not None and needed > hist_p90:
            reasons.append(f"Needs {needed:.1f} more; above historical 90th percentile ({hist_p90:.1f})")
            return "STRONG OVER", "HIGH", "; ".join(reasons)
        if hist_med is not None and needed > hist_med:
            reasons.append(f"Needs {needed:.1f} more; between median ({hist_med:.1f}) and 75th ({hist_p75:.1f})")
            return "LEAN OVER", "MEDIUM", "; ".join(reasons)
        reasons.append(f"Needs {needed:.1f} more; below historical median ({hist_med:.1f})")
        return "PASS", "LOW", "; ".join(reasons)
    else:  # UNDER
        # UNDER: need current < line to win
        cushion = diff  # positive = how much cushion left
        if cushion >= 0:
            return "UNDER LOST", "FINAL", [f"Line exceeded by {cushion:.1f}"]
        
        # cushion is negative, abs is how much room left
        room = -cushion
        if hist_p75 is not None and room < hist_p75:
            reasons.append(f"Has {room:.1f} cushion; below historical 75th ({hist_p75:.1f})")
            return "UNDER WATCH", "MEDIUM", "; ".join(reasons)
        if hist_p90 is not None and room < hist_p90:
            reasons.append(f"Has {room:.1f} cushion; below historical 90th ({hist_p90:.1f})")
            return "STRONG UNDER", "HIGH", "; ".join(reasons)
        if hist_med is not None and room < hist_med:
            reasons.append(f"Has {room:.1f} cushion; between median ({hist_med:.1f}) and 75th ({hist_p75:.1f})")
            return "LEAN UNDER", "MEDIUM", "; ".join(reasons)
        reasons.append(f"Has {room:.1f} cushion; above historical median ({hist_med:.1f})")
        return "PASS", "LOW", "; ".join(reasons)

def state_label(outcome, side):
    """Map outcome to signal label for display."""
    labels = {
        'WON': 'OVER HIT' if side == 'OVER' else 'UNDER WON',
        'LOST': 'OVER LOST' if side == 'OVER' else 'UNDER LOST',
        'LIVE': 'LIVE',
        'PUSH': 'PUSH',
        'FINAL': 'SETTLED'
    }
    return labels.get(outcome, 'DATA INCOMPLETE')

def store_projection(prop_id, proj):
    conn = get_db()
    conn.execute("""
        INSERT INTO prop_projections 
        (prop_id, current_value, remaining_needed, projected_low, projected_median, projected_high,
         signal, confidence, reasons, historical_median, historical_75th, historical_90th, 
         historical_sample, calculated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (proj['prop_id'] if 'prop_id' in proj else proj_id, proj['current_value'], proj.get('remaining_needed', 0), 
          proj['projected_low'], proj['projected_median'], proj['projected_high'], 
          proj['signal'], proj['confidence'], proj['reasons'], 
          proj['historical_median'], proj['historical_75th'], proj['historical_90th'], 
          proj['historical_sample'], datetime.datetime.now()))
    conn.commit()
    conn.close()

def get_historical_count():
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) FROM historical_segments").fetchone()[0]
    conn.close()
    return n


def get_espn_final_stats(game_id, sport):
    """Fetch final player stats from ESPN Gamecast for a completed game."""
    now_ts = time.time()
    cache_key = f"{game_id}_{sport}"
    
    # Check cache (30 min TTL for final stats)
    if now_ts - ESPN_FINAL_STATS_CACHE['ts'] < 1800 and cache_key in ESPN_FINAL_STATS_CACHE['data']:
        return ESPN_FINAL_STATS_CACHE['data'][cache_key]
    
    sport_slug_map = {
        'NFL': 'football/nfl',
        'NBA': 'basketball/nba',
        'CFB': 'football/college-football'
    }
    sport_slug = sport_slug_map.get(sport.upper(), 'football/nfl')
    
    try:
        # ESPN game summary endpoint for final stats
        url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_slug}/summary?event={game_id}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read())
        
        result = {}
        
        # Parse player stats from boxscore
        for competitor in data.get('boxscore', {}).get('players', []):
            team_abbr = competitor.get('team', {}).get('abbreviation', '')
            team_id = competitor.get('team', {}).get('id', '')
            
            for stat_category in competitor.get('statistics', []):
                stat_name = stat_category.get('name', '').lower()
                labels = [l.lower() for l in stat_category.get('labels', [])]
                
                for athlete in stat_category.get('athletes', []):
                    player_name = athlete.get('athlete', {}).get('displayName', '')
                    player_id = athlete.get('athlete', {}).get('id', '')
                    
                    if not player_name or not player_id:
                        continue
                    
                    stats_values = athlete.get('stats', [])
                    if not stats_values:
                        continue
                    
                    # Map ESPN stat names to our stat types using labels
                    for i, stat_value in enumerate(stats_values):
                        if i >= len(labels):
                            continue
                        label = labels[i]
                        
                        # Map labels to our stat types
                        stat_type = None
                        if label in ['yds'] and stat_name == 'passing':
                            stat_type = 'passing_yards'
                        elif label in ['yds'] and stat_name == 'rushing':
                            stat_type = 'rushing_yards'
                        elif label in ['yds'] and stat_name == 'receiving':
                            stat_type = 'receiving_yards'
                        elif label in ['td'] and stat_name == 'passing':
                            stat_type = 'passing_tds'
                        elif label in ['td'] and stat_name == 'rushing':
                            stat_type = 'rushing_tds'
                        elif label in ['td'] and stat_name == 'receiving':
                            stat_type = 'receiving_tds'
                        elif label in ['int']:
                            stat_type = 'interceptions'
                        elif label in ['sacks']:
                            stat_type = 'sacks'
                        elif label in ['tot'] and stat_name == 'defensive':
                            stat_type = 'tackles'
                        elif label in ['rec']:
                            stat_type = 'receptions'
                        elif label in ['pts'] and stat_name == 'kicking':
                            stat_type = 'kicking_points'
                        elif label in ['fg'] and stat_name == 'kicking':
                            stat_type = 'fg_made'
                        elif label in ['xp'] and stat_name == 'kicking':
                            stat_type = 'xp_made'
                        elif stat_name == 'points':
                            stat_type = 'points'
                        elif stat_name == 'rebounds':
                            stat_type = 'rebounds'
                        elif stat_name == 'assists':
                            stat_type = 'assists'
                        elif 'three' in label and 'made' in label:
                            stat_type = 'three_pointers_made'
                        elif 'field goals' in label and 'made' in label:
                            stat_type = 'fg_made'
                        elif 'free throws' in label and 'made' in label:
                            stat_type = 'ft_made'
                        elif label in ['turnovers', 'to']:
                            stat_type = 'turnovers'
                        elif label in ['steals', 'stl']:
                            stat_type = 'steals'
                        elif label in ['blocks', 'blk']:
                            stat_type = 'blocks'
                        
                        if stat_type:
                            try:
                                value = float(stat_value) if stat_value else 0
                                # Handle fractions like "3/3" for FG
                                if '/' in str(stat_value):
                                    parts = str(stat_value).split('/')
                                    value = float(parts[0])
                            except:
                                value = 0
                            
                            key = f"{player_id}_{stat_type}"
                            result[key] = {
                                'player_id': player_id,
                                'player_name': player_name,
                                'team': team_abbr,
                                'stat_type': stat_type,
                                'value': value
                            }
        
        ESPN_FINAL_STATS_CACHE['ts'] = now_ts
        ESPN_FINAL_STATS_CACHE['data'][cache_key] = result
        return result
        
    except Exception as e:
        print(f"ESPN final stats error for {game_id}: {e}")
        return {}


def settle_ticket_legs(ticket_id, conn):
    """Settle all legs of a ticket using ESPN final stats. Returns (ticket_status, actual_payout)."""
    legs = conn.execute("""
        SELECT * FROM ticket_legs WHERE ticket_id = ?
    """, (ticket_id,)).fetchall()
    
    if not legs:
        return 'PENDING', 0
    
    # Get ticket info
    ticket = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if not ticket:
        return 'PENDING', 0
    
    wager = ticket['wager_amount']
    potential_payout = ticket['potential_payout']
    environment = ticket['environment']
    ticket_type = ticket['ticket_type']
    
    leg_results = []
    all_final = True
    
    for leg in legs:
        game_id = leg['game_id']
        sport = leg['sport']
        player_name = leg['player']
        market = leg['market']
        line = leg['line']
        side = leg['side']
        odds = leg['odds']
        
        # Check if game is final
        game = conn.execute("""
            SELECT game_script, away_score, home_score FROM game_state WHERE game_id = ?
        """, (game_id,)).fetchone()
        
        if not game:
            all_final = False
            leg_results.append({
                'leg_id': leg['id'],
                'status': 'PENDING',
                'reason': 'Game not found'
            })
            continue
        
        game_status = game['game_script'].upper() if game['game_script'] else ''
        is_final = any(s in game_status for s in ['FINAL', 'COMPLETED', 'ENDED', 'POST'])
        
        if not is_final:
            all_final = False
            leg_results.append({
                'leg_id': leg['id'],
                'status': 'PENDING',
                'reason': f'Game not final: {game_status}'
            })
            continue
        
        # Get ESPN final stats
        final_stats = get_espn_final_stats(game_id, sport)
        
        # Map market to stat type
        stat_type = MARKET_TO_STAT_TYPE.get(market)
        if not stat_type:
            # Try fuzzy match
            for m, st in MARKET_TO_STAT_TYPE.items():
                if m.lower() in market.lower() or market.lower() in m.lower():
                    stat_type = st
                    break
        
        if not stat_type:
            leg_results.append({
                'leg_id': leg['id'],
                'status': 'VOID',
                'reason': f'Unknown market: {market}'
            })
            continue
        
        # Find player stat
        player_stat = None
        for key, stat in final_stats.items():
            if stat['stat_type'] == stat_type and stat['player_name'].lower() == player_name.lower():
                player_stat = stat
                break
        
        if not player_stat:
            leg_results.append({
                'leg_id': leg['id'],
                'status': 'VOID',
                'reason': f'Player stat not found: {player_name} {stat_type}'
            })
            continue
        
        actual_value = player_stat['value']
        
        # Determine leg result
        if side == 'MORE' or side == 'OVER':
            if actual_value > line:
                leg_status = 'WIN'
            elif actual_value < line:
                leg_status = 'LOSS'
            else:
                leg_status = 'PUSH'
        elif side == 'LESS' or side == 'UNDER':
            if actual_value < line:
                leg_status = 'WIN'
            elif actual_value > line:
                leg_status = 'LOSS'
            else:
                leg_status = 'PUSH'
        elif side == 'MONEYLINE':
            # For moneyline, need team result
            away_score = game['away_score']
            home_score = game['home_score']
            team = player_stat['team']  # This is actually the team abbr
            
            # Determine if team won
            is_home = team == (conn.execute("SELECT home_team FROM game_state WHERE game_id = ?", (game_id,)).fetchone() or {}).get('home_team', '')
            
            if is_home:
                team_won = home_score > away_score
            else:
                team_won = away_score > home_score
            
            if team_won:
                leg_status = 'WIN'
            elif home_score == away_score:
                leg_status = 'PUSH'
            else:
                leg_status = 'LOSS'
        else:
            leg_status = 'VOID'
        
        # Update leg
        conn.execute("""
            UPDATE ticket_legs 
            SET leg_status = ?, current_value = ?, settled_at = datetime('now')
            WHERE id = ?
        """, (leg_status, actual_value, leg['id']))
        
        leg_results.append({
            'leg_id': leg['id'],
            'status': leg_status,
            'actual_value': actual_value,
            'line': line,
            'side': side
        })
    
    # Determine ticket status
    if not all_final:
        return 'PENDING', 0
    
    leg_statuses = [r['status'] for r in leg_results]
    
    if all(s == 'WIN' for s in leg_statuses):
        ticket_status = 'WON'
        actual_payout = potential_payout
    elif any(s == 'LOSS' for s in leg_statuses):
        ticket_status = 'LOST'
        actual_payout = 0
    elif any(s == 'PUSH' for s in leg_statuses):
        ticket_status = 'PUSH'
        actual_payout = wager  # Push returns stake
    elif any(s == 'VOID' for s in leg_statuses):
        ticket_status = 'VOID'
        actual_payout = wager  # Void returns stake
    else:
        ticket_status = 'PENDING'
        actual_payout = 0
    
    return ticket_status, actual_payout


def update_owner_bankroll(owner_name, environment, wager, actual_payout, ticket_status, conn):
    """Update owner bankroll after ticket settlement."""
    row = conn.execute("""
        SELECT * FROM bankroll_accounts WHERE owner_name = ? AND environment = ?
    """, (owner_name, environment)).fetchone()
    
    if not row:
        return
    
    new_total_wagered = row['total_wagered'] + wager
    new_total_returned = row['total_returned'] + actual_payout
    new_current = row['starting_bankroll'] + new_total_returned - new_total_wagered
    
    new_wins = row['tickets_won']
    new_losses = row['tickets_lost']
    new_pushes = row['tickets_pushed']
    new_voids = row['tickets_voided']
    
    if ticket_status == 'WON':
        new_wins += 1
    elif ticket_status == 'LOST':
        new_losses += 1
    elif ticket_status == 'PUSH':
        new_pushes += 1
    elif ticket_status == 'VOID':
        new_voids += 1
    
    conn.execute("""
        UPDATE bankroll_accounts 
        SET current_bankroll = ?, total_wagered = ?, total_returned = ?,
            tickets_won = ?, tickets_lost = ?, tickets_pushed = ?, tickets_voided = ?,
            updated_at = datetime('now')
        WHERE id = ?
    """, (new_current, new_total_wagered, new_total_returned, 
          new_wins, new_losses, new_pushes, new_voids, row['id']))


def update_ai_bankroll(ai_id, ai_name, wager, actual_payout, ticket_status, conn):
    # Historical specialist tables are frozen; new challenges use challenge_ledger.
    return


def update_ai_pick_result(ai_name, game_id, player_name, market, line, side, leg_status, actual_value, wager, conn):
    # Historical specialist tables are frozen; new challenges use challenge_ledger.
    return


def auto_settle_all_tickets():
    """Main settlement function - call periodically or via cron."""
    conn = get_db()
    settled_count = 0
    
    try:
        # Get all PENDING tickets
        pending_tickets = conn.execute("""
            SELECT id, owner_name, sportsbook_platform, wager_amount, potential_payout, 
                   ticket_type, environment, status
            FROM tickets 
            WHERE status = 'PENDING'
        """).fetchall()
        
        for ticket in pending_tickets:
            ticket_id = ticket['id']
            ticket_status, actual_payout = settle_ticket_legs(ticket_id, conn)
            
            if ticket_status != 'PENDING':
                # Update ticket
                conn.execute("""
                    UPDATE tickets 
                    SET status = ?, actual_payout = ?, settled_at = datetime('now')
                    WHERE id = ?
                """, (ticket_status, actual_payout, ticket_id))
                
                # Update bankroll
                update_owner_bankroll(
                    ticket['owner_name'], 
                    ticket['environment'], 
                    ticket['wager_amount'], 
                    actual_payout, 
                    ticket_status, 
                    conn
                )
                
                # Update AI bankrolls for AI tickets
                if ticket['owner_name'] in ['BASKETBALL_AI', 'NFL_AI', 'CFB_AI']:
                    update_ai_bankroll(
                        ticket['owner_name'],
                        ticket['owner_name'],
                        ticket['wager_amount'],
                        actual_payout,
                        ticket_status,
                        conn
                    )
                    
                    # Update AI pick results
                    legs = conn.execute("SELECT * FROM ticket_legs WHERE ticket_id = ?", (ticket_id,)).fetchall()
                    for leg in legs:
                        wager_per_leg = ticket['wager_amount'] / len(legs) if legs else ticket['wager_amount']
                        update_ai_pick_result(
                            ticket['owner_name'],
                            leg['game_id'],
                            leg['player'],
                            leg['market'],
                            leg['line'],
                            leg['side'],
                            leg['leg_status'],
                            leg['current_value'],
                            wager_per_leg,
                            conn
                        )
                
                settled_count += 1
        
        # Also settle AI picks that aren't linked to tickets
        # Legacy picks lack provenance and are excluded from settlement.
        
        conn.commit()
        return settled_count
    except Exception as e:
        conn.rollback()
        print(f"Auto-settle error: {e}")
        return 0
    finally:
        conn.close()


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split('?')[0] in ('/board.css','/board.js'):
            from pathlib import Path
            filename = self.path.split('?')[0][1:]
            payload = Path('/opt/liveintel', filename).read_bytes()
            self.send_response(200)
            self.send_header('Content-type', 'text/css' if filename.endswith('.css') else 'application/javascript')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(payload)
            return
        # Handle API routes
        if self.path.startswith('/api/'):
            self.handle_api()
            return
        
        # Serve the new HTML dashboard if requested
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            with open('/opt/liveintel/liveintel_v2.html', 'rb') as f:
                self.wfile.write(f.read())
            return
        
        # Handle other API endpoints for backward compatibility
        if self.path.startswith('/liveintel/'):
            # Map old dashboard routes to new liveintel_service
            tab_id = self.path.replace('/liveintel/', '')
            self.serve_tab_data(tab_id)
            return
            
        # Default to old dashboard for other paths
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        
        conn = get_db()
        
        props = conn.execute("""
            SELECT pl.*, pp.current_value, pp.remaining_needed, pp.projected_low, 
                   pp.projected_median, pp.projected_high, pp.signal, pp.confidence, 
                   pp.reasons, pp.historical_median, pp.historical_75th, pp.historical_90th,
                   pp.historical_sample, pp.calculated_at
            FROM prop_lines pl
            LEFT JOIN prop_projections pp ON pl.id = pp.prop_id
            ORDER BY pl.observed_at DESC
        """).fetchall()
        
        player_stats = conn.execute("""
            SELECT game_id, player_name, team, segment, stat_type, value, last_updated
            FROM player_segment_stats
        """).fetchall()
        
        plays_count = conn.execute("SELECT COUNT(*) FROM plays").fetchone()[0]
        
        # Get game state data
        game_states = conn.execute("""
            SELECT * FROM game_state
        """).fetchall()
        
        situation_alerts = conn.execute("""
            SELECT * FROM situation_alerts ORDER BY created_at DESC
        """).fetchall()
        
        team_situation = conn.execute("""
            SELECT * FROM team_situation_stats
        """).fetchall()
        
        player_opportunity = conn.execute("""
            SELECT * FROM player_opportunity
        """).fetchall()
        
        plays_count = conn.execute("SELECT COUNT(*) FROM plays").fetchone()[0]
        
        conn.close()
        
        stats_lookup = {}
        for s in player_stats:
            key = (s['game_id'], s['player_name'], s['segment'], s['stat_type'])
            stats_lookup[key] = s
        
        # Get latest play for each game
        conn2 = get_db()
        latest_plays = {}
        if props:
            for p in props:
                game_id = p['game_id']
                if game_id not in latest_plays:
                    play_row = conn2.execute(
                        "SELECT period, clock FROM plays WHERE game_id = ? ORDER BY event_timestamp DESC LIMIT 1",
                        (game_id,)
                    ).fetchone()
                    latest_plays[game_id] = play_row
        conn2.close()
        
        html = """<html><head><style>
        body { font-family: monospace; margin: 20px; background: #1a1a1a; color: #e0e0e0; }
        h1 { color: #00ff88; border-bottom: 1px solid #333; padding-bottom: 10px; }
        h2 { color: #00cc66; }
        table { border-collapse: collapse; width: 100%; margin: 10px 0; }
        th, td { border: 1px solid #333; padding: 8px; text-align: left; }
        th { background: #222; color: #00ff88; }
        tr:nth-child(even) { background: #222; }
        .signal-OVER_HIT { background: #004400; color: #00ff00; font-weight: bold; }
        .signal-OVER_WATCH { background: #004400; color: #aaff00; }
        .signal-LEAN_OVER { background: #003300; color: #88ff00; }
        .signal-PASS { background: #444400; color: #ffff00; }
        .signal-LEAN_UNDER { background: #330000; color: #ff8800; }
        .signal-UNDER_WATCH { background: #440000; color: #ff8800; }
        .signal-STRONG_UNDER { background: #440000; color: #ff4400; font-weight: bold; }
        .signal-UNDER_LOST { background: #440000; color: #ff4400; font-weight: bold; }
        .signal-OVER_LOST { background: #330000; color: #ff6600; }
        .signal-DATA_INCOMPLETE { background: #333300; color: #ffaa00; }
        .signal-SETTLED { background: #222; color: #888; }
        .card { border: 1px solid #333; margin: 10px 0; padding: 15px; background: #1e1e1e; }
        .card-header { font-size: 1.2em; font-weight: bold; color: #00ff88; margin-bottom: 10px; }
        .card-row { display: flex; gap: 20px; flex-wrap: wrap; margin: 5px 0; }
        .card-field { min-width: 180px; }
        .label { color: #888; font-size: 0.85em; }
        .value { color: #fff; font-weight: bold; }
        .value-lost { color: #ff4444; font-weight: bold; }
        .value-won { color: #00ff00; font-weight: bold; }
        .value-live { color: #ffff00; font-weight: bold; }
        form { background: #222; padding: 15px; border: 1px solid #333; margin: 10px 0; }
        input { background: #333; border: 1px solid #444; color: #fff; padding: 5px; margin: 2px; }
        input[type=submit] { background: #006600; cursor: pointer; }
        .test-badge { background: #ff6600; color: #000; padding: 2px 6px; font-size: 0.7em; margin-left: 5px; }
        .historical { color: #aaa; font-size: 0.85em; }
        .reasons { color: #ccc; font-size: 0.8em; line-height: 1.4; }
        .btn { background: #006600; color: #fff; padding: 8px 16px; border: none; cursor: pointer; margin: 5px; }
        .btn:hover { background: #008800; }
        .display-text { font-size: 1.1em; font-weight: bold; padding: 10px; background: #222; border-radius: 4px; margin: 10px 0; }
        </style></head><body>"""
        
        html += "<h1>LIVE GAME INTELLIGENCE COMMAND CENTER</h1>"
        
        html += f"<div class='card'><div class='card-row'>"
        html += f"<div class='card-field'><div class='label'>PLAYS INGESTED</div><div class='value'>{plays_count}</div></div>"
        html += f"<div class='card-field'><div class='label'>LAST INGEST</div><div class='value'>{datetime.datetime.now().strftime('%H:%M:%S')}</div></div>"
        html += f"<div class='card-field'><div class='label'>DB UPDATED</div><div class='value'>{datetime.datetime.now().strftime('%H:%M:%S')}</div></div>"
        html += f"<div class='card-field'><div class='label'>HISTORICAL ROWS</div><div class='value'>{get_historical_count()}</div></div>"
        html += "</div></div>"
        
        html += "<h2>Add Manual Prop (TEST MANUAL)</h2>"
        html += "<form method='POST' action='/add_prop'>"
        html += "Game ID: <input name='game_id' placeholder='401873017'><br>"
        html += "Player: <input name='player' placeholder='Brock Purdy'><br>"
        html += "Market: <input name='market' placeholder='Passing Yards'><br>"
        html += "Line: <input name='line' placeholder='254.5'><br>"
        html += "Side (OVER/UNDER): <input name='side' placeholder='UNDER'><br>"
        html += "Book: <input name='book' placeholder='TEST MANUAL'><br>"
        html += "<input type='submit' value='Add Prop'>"
        html += "</form>"
        
        html += "<form method='POST' action='/recalc' style='display:inline;'><button class='btn' type='submit'>Recalculate All Projections</button></form>"
        
        html += "<h2>LIVE PROP WATCH</h2>"
        
        if props:
            for p in props:
                prop_id = p['id']
                game_id = p['game_id']
                player = p['player_name']
                market = p['market']
                line = p['line']
                side = p['side']
                book = p['book']
                
                stat_type_map = {
                    'Passing Yards': 'passing_yards',
                    'PASS_YDS': 'passing_yards',
                    'Rushing Yards': 'rushing_yards',
                    'Receiving Yards': 'receiving_yards',
                    'Kicking Points': 'kicking_points',
                }
                stat_type = stat_type_map.get(market, 'passing_yards')
                
                current_val = p['current_value'] if p['current_value'] is not None else 0
                stat_key = (game_id, player, 'FULL GAME', stat_type)
                if stat_key in stats_lookup:
                    current_val = stats_lookup[stat_key]['value']
                
                # Recalculate projection on the fly for live accuracy
                conn3 = get_db()
                play_row = conn3.execute(
                    "SELECT period, clock FROM plays WHERE game_id = ? ORDER BY event_timestamp DESC LIMIT 1",
                    (game_id,)
                ).fetchone()
                conn3.close()
                
                period = play_row['period'] if play_row else '?'
                clock = play_row['clock'] if play_row else '?'
                
                # Find player_id
                conn4 = get_db()
                player_row = find_prop_segment_stat(conn4, game_id, player, stat_type)
                conn4.close()
                
                if player_row:
                    current_val = player_row['value']
                    player_id = player_row['player_id']
                    team = player_row['team']
                    proj = calculate_projections(prop_id, current_val, line, side, game_id, player, player_id, team, stat_type, period, clock)
                else:
                    proj = {
                        'current_value': current_val,
                        'line': line,
                        'side': side,
                        'diff': current_val - line,
                        'abs_diff': abs(current_val - line),
                        'state': 'UNKNOWN',
                        'outcome': 'UNKNOWN',
                        'display_text': 'NO PLAYER DATA',
                        'projected_low': 0,
                        'projected_median': 0,
                        'projected_high': 0,
                        'signal': 'DATA INCOMPLETE',
                        'confidence': 'LOW',
                        'reasons': 'Could not find player in segment stats',
                        'historical_median': None,
                        'historical_75th': None,
                        'historical_90th': None,
                        'historical_sample': 0,
                        'historical_label': '',
                        'is_live': False
                    }
                
                # State display
                outcome_class = 'value-live'
                if proj['outcome'] == 'WON':
                    outcome_class = 'value-won'
                elif proj['outcome'] == 'LOST':
                    outcome_class = 'value-lost'
                elif proj['outcome'] == 'FINAL':
                    outcome_class = 'value-lost'
                
                # Signal class
                signal_class = proj['signal'].replace(' ', '_').replace('—', '').replace('-', '_').upper()
                
                play_row = latest_plays.get(game_id)
                period = play_row['period'] if play_row else '?'
                clock = play_row['clock'] if play_row else '?'
                
                feed_age = "LIVE"
                prop_age = "NEW"
                
                html += f"<div class='card'>"
                html += f"<div class='card-header'>{player} <span class='test-badge'>{book}</span></div>"
                html += f"<div class='card-row'>"
                html += f"<div class='card-field'><div class='label'>GAME</div><div class='value'>{game_id}</div></div>"
                html += f"<div class='card-field'><div class='label'>MARKET</div><div class='value'>{market}</div></div>"
                html += f"<div class='card-field'><div class='label'>LINE</div><div class='value'>{line}</div></div>"
                html += f"<div class='card-field'><div class='label'>SIDE</div><div class='value'>{side}</div></div>"
                html += f"</div>"
                html += f"<div class='card-row'>"
                html += f"<div class='card-field'><div class='label'>CURRENT</div><div class='value'>{current_val:.1f}</div></div>"
                html += f"<div class='card-field'><div class='label'>DIFFERENCE</div><div class='value'>{proj['diff']:.1f}</div></div>"
                html += f"<div class='card-field'><div class='label'>STATE</div><div class='value {outcome_class}'>{proj['state']}</div></div>"
                html += f"<div class='card-field'><div class='label'>PERIOD/CLOCK</div><div class='value'>{period} / {clock}</div></div>"
                html += f"<div class='card-field'><div class='label'>FEED AGE</div><div class='value'>{feed_age}</div></div>"
                html += f"<div class='card-field'><div class='label'>PROP AGE</div><div class='value'>{prop_age}</div></div>"
                html += f"</div>"
                html += f"<div class='display-text'>{proj['display_text']}</div>"
                
                if proj['is_live']:
                    html += f"<div class='card-row'>"
                    html += f"<div class='card-field'><div class='label'>PROJECTED LOW</div><div class='value'>{proj['projected_low']:.1f}</div></div>"
                    html += f"<div class='card-field'><div class='label'>PROJECTED MEDIAN</div><div class='value'>{proj['projected_median']:.1f}</div></div>"
                    html += f"<div class='card-field'><div class='label'>PROJECTED HIGH</div><div class='value'>{proj['projected_high']:.1f}</div></div>"
                    html += f"</div>"
                    html += f"<div class='card-row'>"
                    html += f"<div class='card-field'><div class='label'>SIGNAL</div><div class='value signal-{signal_class}'>{proj['signal']}</div></div>"
                    html += f"<div class='card-field'><div class='label'>CONFIDENCE</div><div class='value'>{proj['confidence']}</div></div>"
                    html += f"</div>"
                    hist_med = proj['historical_median']
                    hist_p75 = proj['historical_75th']
                    hist_p90 = proj['historical_90th']
                    hist_sample = proj['historical_sample']
                    if hist_med is not None:
                        html += f"<div class='card-row'>"
                        html += f"<div class='card-field'><div class='label'>HIST 2H MEDIAN</div><div class='value'>{hist_med:.1f}</div></div>"
                        html += f"<div class='card-field'><div class='label'>HIST 2H 75TH</div><div class='value'>{hist_p75:.1f}</div></div>"
                        html += f"<div class='card-field'><div class='label'>HIST 2H 90TH</div><div class='value'>{hist_p90:.1f}</div></div>"
                        html += f"<div class='card-field'><div class='label'>SAMPLE</div><div class='value'>{hist_sample}</div></div>"
                        html += f"</div>"
                else:
                    html += f"<div class='card-row'>"
                    html += f"<div class='card-field'><div class='label'>FINAL VALUE</div><div class='value'>{proj['projected_low']:.1f}</div></div>"
                    html += f"</div>"
                
                hist_med = proj['historical_median']
                hist_p75 = proj['historical_75th']
                hist_p90 = proj['historical_90th']
                hist_sample = proj['historical_sample']
                if hist_med is not None:
                    html += f"<div class='card-row'><div class='card-field historical'>HISTORICAL: {proj['historical_label']}</div></div>"
                
                html += f"<div class='card-row'><div class='card-field'><div class='label reasons'>REASONS</div><div class='value reasons' style='color:#ccc;font-size:0.8em;'>{proj['reasons']}</div></div>"
                html += f"</div>"
        else:
            html += "<p>No props added yet. Use the form above to add TEST MANUAL props.</p>"
        
        # LIVE SITUATION ENGINE
        html += "<h2>LIVE SITUATION ENGINE</h2>"
        
        if game_states:
            for gs in game_states:
                leading = gs['leading_team']
                trailing = gs['trailing_team']
                script = gs['game_script']
                poss = gs['possession']
                poss_abbr = get_team_abbr(poss) if poss else '?'
                
                # Game state card
                html += f"<div class='card'>"
                html += f"<div class='card-header'>{gs['away_team']} @ {gs['home_team']}  <span class='test-badge'>{script}</span></div>"
                html += f"<div class='card-row'>"
                html += f"<div class='card-field'><div class='label'>PERIOD/CLOCK</div><div class='value'>{gs['period']}Q / {gs['clock']}</div></div>"
                html += f"<div class='card-field'><div class='label'>SCORE</div><div class='value'>{gs['away_team']} {gs['away_score']} - {gs['home_score']} {gs['home_team']}</div></div>"
                html += f"<div class='card-field'><div class='label'>POSSESSION</div><div class='value'>{poss_abbr}</div></div>"
                html += f"<div class='card-field'><div class='label'>DOWN/DIST</div><div class='value'>{gs['down']} / {gs['distance']}</div></div>"
                html += f"<div class='card-field'><div class='label'>YARD LINE</div><div class='value'>{gs['yard_line']}</div></div>"
                html += f"<div class='card-field'><div class='label'>DIFF</div><div class='value'>{gs['score_diff']}</div></div>"
                html += f"</div>"
                
                # Game script detail
                html += f"<div class='card-row'><div class='card-field'><div class='label'>GAME SCRIPT</div><div class='value'>{script}</div></div></div>"
                
                # Run/Pass rates
                html += f"<div class='card-row'>"
                for team_id in [gs['away_team'], gs['home_team']]:
                    if team_id:
                        team_stats = [t for t in team_situation if t['game_id'] == gs['game_id'] and t['team_id'] == team_id]
                        if team_stats:
                            ts = team_stats[0]
                            html += f"<div class='card-field'><div class='label'>{get_team_abbr(team_id)} RUSH/PASS</div><div class='value'>Rush {ts['rush_pct']:.1f}% / Pass {ts['pass_pct']:.1f}%</div></div>"
                html += f"</div>"
                
                # Red zone / FG range
                flags = []
                if gs['red_zone']: flags.append('RED ZONE')
                if gs['goal_to_go']: flags.append('GOAL TO GO')
                if gs['fg_range']: flags.append('FG RANGE')
                if flags:
                    html += f"<div class='card-row'><div class='card-field'><div class='label'>FLAGS</div><div class='value'>{', '.join(flags)}</div></div>"
                
                # Drive info
                html += f"<div class='card-row'>"
                html += f"<div class='card-field'><div class='label'>DRIVE</div><div class='value'>#{gs['drive_number']} ({gs['drive_plays']} plays, {gs['drive_yards']} yds)</div></div>"
                html += f"</div>"
                
                html += f"</div>"
        else:
            html += "<p>No live games currently.</p>"
        
        # Situation alerts
        html += "<h3>SITUATION ALERTS</h3>"
        if situation_alerts:
            for alert in situation_alerts:
                html += f"<div class='card' style='border-left: 4px solid #ff6600;'><div class='card-row'>"
                html += f"<div class='card-field'><div class='label'>TYPE</div><div class='value'>{alert['alert_type']}</div></div>"
                html += f"<div class='card-field'><div class='label'>TEAM</div><div class='value'>{get_team_abbr(alert['team_id'])}</div></div>"
                html += f"<div class='card-field'><div class='label'>DETAIL</div><div class='value'>{alert['detail']}</div></div>"
                html += f"<div class='card-field'><div class='label'>PERIOD/CLOCK</div><div class='value'>{alert['period']}Q {alert['clock']}</div></div>"
                html += f"<div class='card-field'><div class='label'>TIME</div><div class='value'>{alert['created_at']}</div></div>"
                html += f"</div></div>"
        else:
            html += "<p>No active situation alerts.</p>"
        
        # Team run/pass trends
        html += "<h3>TEAM RUN/PASS TRENDS</h3>"
        if team_situation:
            html += "<table><tr><th>Game</th><th>Team</th><th>Period</th><th>Rush%</th><th>Pass%</th><th>Rush Yds</th><th>Pass Yds</th></tr>"
            for ts in team_situation:
                html += f"<tr><td>{ts['game_id']}</td><td>{get_team_abbr(ts['team_id'])}</td><td>Q{ts['period']}</td><td>{ts['rush_pct']:.1f}%</td><td>{ts['pass_pct']:.1f}%</td><td>{ts['rush_yards']}</td><td>{ts['pass_yards']}</td></tr>"
            html += "</table>"
        else:
            html += "<p>No team situation data yet.</p>"
        
        # Player opportunity
        html += "<h3>PLAYER OPPORTUNITY</h3>"
        if player_opportunity:
            html += "<table><tr><th>Game</th><th>Player</th><th>Team</th><th>Period</th><th>Targets</th><th>Carries</th><th>Pass Att</th><th>Rush Att</th><th>RZ Targets</th><th>RZ Carries</th><th>RZ Touch</th></tr>"
            for po in player_opportunity[:50]:
                html += f"<tr><td>{po['game_id']}</td><td>{po['player_name']}</td><td>{po['team']}</td><td>{po['period']}</td><td>{po['targets']}</td><td>{po['carries']}</td><td>{po['pass_attempts']}</td><td>{po['rush_attempts']}</td><td>{po['red_zone_targets']}</td><td>{po['red_zone_carries']}</td><td>{po['red_zone_touch']}</td></tr>"
            html += "</table>"
        else:
            html += "<p>No player opportunity data yet.</p>"
        
        html += "<h2>PLAYER SEGMENT STATS (Sample)</h2>"
        html += "<table><tr><th>Game</th><th>Player</th><th>Team</th><th>Segment</th><th>Stat</th><th>Value</th><th>Updated</th></tr>"
        for s in player_stats[:30]:
            html += f"<tr><td>{s['game_id']}</td><td>{s['player_name']}</td><td>{s['team']}</td><td>{s['segment']}</td><td>{s['stat_type']}</td><td>{s['value']:.1f}</td><td>{s['last_updated']}</td></tr>"
        html += "</table>"
        
        html += "</body></html>"
        self.wfile.write(html.encode())
    
    def serve_tab_data(self, tab_id):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        
        try:
            from liveintel_service import (
                get_players_by_sport,
                get_player_info_from_multi_sport,
                get_season_stats_for_player,
                get_latest_game_stats_for_player,
                get_live_games_with_players,
                get_tickets_by_type,
                get_bankroll_accounts_for_user,
                get_nba_late_game_summary
            )
            
            # Get relevant data based on tab
            if tab_id == 'key-players':
                players = get_players_by_sport('NFL')
                html = "<h2>KEY PLAYERS</h2><div class='card'>"
                for player in players[:20]:  # Show first 20 players
                    html += f"<div class='card-row'><div class='card-field'><div class='label'>PLAYER NAME</div><div class='value'>{player['player_name']}</div></div>"
                    html += f"<div class='card-field'><div class='label'>TEAM</div><div class='value'>{player['team']}</div></div>"
                    html += f"<div class='card-field'><div class='label'>SPORT</div><div class='value'>{player['sport']}</div></div>"
                    html += f"<div class='card-field'><div class='label'>POSITION</div><div class='value'>{player['position']}</div></div></div>"
                html += "</div>"
                
            elif tab_id == 'nba':
                players = get_players_by_sport('NBA')
                html = "<h2>NBA PLAYERS</h2><div class='card'>"
                for player in players[:20]:
                    html += f"<div class='card-row'><div class='card-field'><div class='label'>PLAYER NAME</div><div class='value'>{player['player_name']}</div></div>"
                    html += f"<div class='card-field'><div class='label'>TEAM</div><div class='value'>{player['team']}</div></div>"
                    html += f"<div class='card-field'><div class='label'>SPORT</div><div class='value'>{player['sport']}</div></div>"
                    html += f"<div class='card-field'><div class='label'>POSITION</div><div class='value'>{player['position']}</div></div></div>"
                html += "</div>"
                
            elif tab_id == 'nfl':
                players = get_players_by_sport('NFL')
                html = "<h2>NFL PLAYERS</h2><div class='card'>"
                for player in players[:20]:
                    html += f"<div class='card-row'><div class='card-field'><div class='label'>PLAYER NAME</div><div class='value'>{player['player_name']}</div></div>"
                    html += f"<div class='card-field'><div class='label'>TEAM</div><div class='value'>{player['team']}</div></div>"
                    html += f"<div class='card-field'><div class='label'>SPORT</div><div class='value'>{player['sport']}</div></div>"
                    html += f"<div class='card-field'><div class='label'>POSITION</div><div class='value'>{player['position']}</div></div></div>"
                html += "</div>"
                
            elif tab_id == 'cfb':
                players = get_players_by_sport('CFB')
                html = "<h2>CFB PLAYERS</h2><div class='card'>"
                for player in players[:20]:
                    html += f"<div class='card-row'><div class='card-field'><div class='label'>PLAYER NAME</div><div class='value'>{player['player_name']}</div></div>"
                    html += f"<div class='card-field'><div class='label'>TEAM</div><div class='value'>{player['team']}</div></div>"
                    html += f"<div class='card-field'><div class='label'>SPORT</div><div class='value'>{player['sport']}</div></div>"
                    html += f"<div class='card-field'><div class='label'>POSITION</div><div class='value'>{player['position']}</div></div></div>"
                html += "</div>"
                
            elif tab_id == 'live-games':
                games = get_live_games_with_players()
                html = "<h2>LIVE GAMES</h2><div class='card'>"
                if games:
                    for game in games:
                        sport = game.get('sport', 'NBA')
                        sport_slug = 'nba' if sport == 'NBA' else ('nfl' if sport == 'NFL' else 'college-football')
                        espn_gamecast_url = f"https://www.espn.com/{sport_slug}/game/_/gameId/{game['game_id']}"
                        html += f"<div class='live-game-card card'><div class='live-game-header'><h3>{game['away_team']} @ {game['home_team']}</h3><div class='live-badge'>LIVE</div></div>"
                        html += f"<div class='card-row'><div class='card-field'><div class='label'>SCORE</div><div class='value game-score'>{game['away_score']} - {game['home_score']}</div></div>"
                        html += f"<div class='card-field'><div class='label'>PERIOD/CLOCK</div><div class='value game-clock'>{game['period']}Q / {game['clock']}</div></div>"
                        html += f"<div class='card-field'><div class='label'>STATE</div><div class='value'>{game['game_script']}</div></div></div>"
                        html += f"<div class='card-row'><div class='card-field'><a href='{espn_gamecast_url}' target='_blank' class='gamecast-button'>GAMECAST</a></div></div>"
                        if game.get('key_players'):
                            html += "<div class='card-row'><div class='label'>KEY PLAYERS</div><div class='value'>"
                            for p in game['key_players']:
                                html += f"{p['player_name']} ({p['team']}), "
                            html = html.rstrip(', ') + "</div></div>"
                        html += "</div>"
                else:
                    html += "<div class='empty-state'><p>No live games currently.</p></div>"
                html += "</div>"
                
            elif tab_id == 'nba-late-game':
                late_signals = get_nba_late_game_summary()
                html = "<h2>NBA LATE GAME INTELLIGENCE</h2><div class='card'>"
                if late_signals:
                    html += "<table><tr><th>GAME ID</th><th>WINDOW</th><th>PLAYER</th><th>TEAM</th><th>SIGNAL</th><th>CONFIDENCE</th><th>CURRENT</th><th>LINE</th></tr>"
                    for sig in late_signals:
                        html += f"<tr><td>{sig['game_id']}</td><td>{sig['window']}</td><td>{sig.get('player_name', 'N/A')}</td><td>{sig.get('team', 'N/A')}</td><td><span class='badge badge-{sig['signal'].lower().replace(' ', '-')}'>{sig['signal']}</span></td><td>{sig['confidence']}</td><td>{sig['current_value']}</td><td>{sig.get('line', 'N/A')}</td></tr>"
                    html += "</table>"
                else:
                    html += "<div class='empty-state'><p>No late game signals available.</p></div>"
                html += "</div>"
                
            elif tab_id == 'tickets':
                real_tickets = get_tickets_by_type(is_real=True)
                test_tickets = get_tickets_by_type(is_real=False)
                html = "<h2>TICKETS</h2>"
                
                # NEW TICKET ENTRY FORM
                html += "<div class='card'><h3>NEW TICKET ENTRY</h3>"
                html += "<form id='ticket-form' method='POST' action='/api/save-ticket'>"
                html += "<div class='card-row'><div class='card-field'><div class='label'>OWNER</div><input name='owner_name' required placeholder='Owner Name'></div>"
                html += "<div class='card-field'><div class='label'>SPORTSBOOK</div><input name='sportsbook_platform' required placeholder='e.g. DraftKings, FanDuel'></div>"
                html += "<div class='card-field'><div class='label'>TYPE</div><select name='environment' id='ticket-env'><option value='REAL' selected>REAL</option><option value='TEST'>TEST</option></select></div>"
                html += "<div class='card-field'><div class='label'>WAGER $</div><input name='wager_amount' type='number' step='0.01' required placeholder='100.00'></div>"
                html += "<div class='card-field'><div class='label'>POTENTIAL PAYOUT $</div><input name='potential_payout' type='number' step='0.01' required placeholder='250.00'></div></div>"
                
                html += "<div class='card-row'><div class='card-field'><div class='label'>GAME ID</div><input name='game_id' placeholder='e.g. 401873017 (ESPN Game ID)'></div><div class='card-field'><div class='label'>SPORT</div><select name='sport' id='ticket-sport'><option value='NFL'>NFL</option><option value='CFB'>CFB</option><option value='NBA'>NBA</option></select></div></div>"
                
                html += "<div id='ticket-legs-container'>"
                html += "<div class='ticket-leg' data-leg='1'>"
                html += "<div class='leg-header'><span class='leg-number'>LEG 1</span><button type='button' class='btn remove-leg' style='background:#cc0000; font-size:12px; padding:4px 8px;'>REMOVE LEG</button></div>"
                html += "<div class='card-row'><div class='card-field'><div class='label'>PLAYER/TEAM</div><input name='leg_1_player' placeholder='Player or Team'></div>"
                html += "<div class='card-field'><div class='label'>MARKET</div><input name='leg_1_market' placeholder='e.g. Passing Yards, Spread, Moneyline'></div>"
                html += "<div class='card-field'><div class='label'>LINE</div><input name='leg_1_line' type='number' step='0.5' placeholder='254.5'></div>"
                html += "<div class='card-field'><div class='label'>SIDE</div><select name='leg_1_side'><option value='MORE'>MORE</option><option value='LESS'>LESS</option><option value='MONEYLINE'>MONEYLINE</option></select></div>"
                html += "<div class='card-field'><div class='label'>ODDS</div><input name='leg_1_odds' type='number' step='1' placeholder='-110'></div></div>"
                html += "</div>"
                html += "</div>"
                
                html += "<div class='card-row'><button type='button' id='add-leg-btn' class='btn' style='background:#006600;'>ADD LEG</button>"
                html += "<button type='submit' class='btn' style='background:#008800;'>SAVE TICKET</button></div>"
                html += "</form></div>"
                
                html += "<div class='card'><h3>OWNER TICKET</h3>"
                if real_tickets:
                    html += "<table><tr><th>OWNER</th><th>SPORTSBOOK</th><th>AMOUNT</th><th>PAYOUT</th><th>STATUS</th></tr>"
                    for t in real_tickets:
                        html += f"<tr><td>{t['owner_name']}</td><td>{t['sportsbook_platform']}</td><td>{t['wager_amount']}</td><td>{t['potential_payout']}</td><td><span class='ticket-status status-{t['status'].lower()}'>{t['status']}</span></td></tr>"
                    html += "</table>"
                else:
                    html += "<div class='empty-state'><p>No owner tickets found.</p></div>"
                html += "</div>"
                
                # AI TICKETS SECTIONS
                for ai_name, ai_picks_table, ai_bankroll_table in [
                    ('BASKETBALL AI', 'basketball_ai_picks', 'basketball_ai_bankroll'),
                    ('NFL AI', 'nfl_ai_picks', 'nfl_ai_bankroll'),
                    ('CFB AI', 'cfb_ai_picks', 'cfb_ai_bankroll'),
                ]:
                    conn = get_db()
                    bankroll_row = conn.execute(f"SELECT * FROM {ai_bankroll_table} ORDER BY updated_at DESC LIMIT 1").fetchone()
                    picks_rows = conn.execute(f"SELECT * FROM {ai_picks_table} ORDER BY pick_time DESC LIMIT 10").fetchall()
                    conn.close()
                    
                    bankroll = dict(bankroll_row) if bankroll_row else None
                    picks = [dict(p) for p in picks_rows]
                    
                    current_bankroll = bankroll['current_bankroll'] if bankroll else 10000
                    
                    html += f"<div class='card'><h3>{ai_name} (TEST ONLY)</h3>"
                    html += f"<div class='card-row'><div class='card-field'><div class='label'>CURRENT BANKROLL</div><div class='value'>${current_bankroll:.2f}</div></div></div>"
                    
                    if picks:
                        html += "<table style='width:100%; border-collapse:collapse; margin-top:10px; font-size:12px;'>"
                        html += "<tr style='background:#222;'><th>GAME</th><th>PLAYER/TEAM</th><th>MARKET</th><th>LINE</th><th>SIDE</th><th>BOOK</th><th>WAGER</th><th>CONFIDENCE</th><th>STATUS</th><th>PAYOUT</th><th>P/L</th></tr>"
                        for p in picks:
                            status = p['result'] or 'PENDING'
                            pl = p['profit_loss'] if p['profit_loss'] is not None else 0
                            status_class = 'status-win' if status == 'WIN' else ('status-loss' if status == 'LOSS' else 'status-pending')
                            html += f"<tr><td>{p['game_id'] or 'N/A'}</td><td>{p['player_name'] or p['team'] or 'N/A'}</td><td>{p['market'] or 'N/A'}</td><td>{p['line'] or 'N/A'}</td><td>{p['side'] or 'N/A'}</td><td>{p['book'] or 'N/A'}</td><td>${p.get('wager_amount', 100):.2f}</td><td>{p.get('confidence', 'N/A')}</td><td><span class='ticket-status {status_class}'>{status}</span></td><td>${p.get('potential_payout', 0):.2f}</td><td class='{'positive' if pl > 0 else 'negative' if pl < 0 else ''}'>${pl:.2f}</td></tr>"
                        html += "</table>"
                    else:
                        html += "<div class='empty-state'><p>No AI picks found.</p></div>"
                    html += "</div>"
                
                html += "</div>"
                
            elif tab_id == 'bankroll':
                conn = get_db()
                
                # Get OWNER bankroll accounts (both REAL and TEST)
                owner_rows = [dict(r) for r in conn.execute("""SELECT owner_name, starting_bankroll, current_bankroll, total_wagered, total_returned, environment
                                     FROM bankroll_accounts 
                                     ORDER BY updated_at DESC LIMIT 20""").fetchall()]
                
                # Get AI bankrolls
                basketball_ai = [dict(r) for r in conn.execute("""SELECT 'BASKETBALL_AI' as owner_name, starting_bankroll, current_bankroll, total_wagered, total_returned, roi, 'AI_TEST' as environment
                                        FROM basketball_ai_bankroll ORDER BY updated_at DESC LIMIT 1""").fetchall()]
        
                nfl_ai = [dict(r) for r in conn.execute("""SELECT 'NFL_AI' as owner_name, starting_bankroll, current_bankroll, total_wagered, total_returned, roi, 'AI_TEST' as environment
                                FROM nfl_ai_bankroll ORDER BY updated_at DESC LIMIT 1""").fetchall()]
        
                cfb_ai = [dict(r) for r in conn.execute("""SELECT 'CFB_AI' as owner_name, starting_bankroll, current_bankroll, total_wagered, total_returned, roi, 'AI_TEST' as environment
                                FROM cfb_ai_bankroll ORDER BY updated_at DESC LIMIT 1""").fetchall()]
                
                conn.close()
                
                all_rows = owner_rows + basketball_ai + nfl_ai + cfb_ai
                
                real_bankroll = [r for r in all_rows if r.get('environment') == 'REAL']
                test_bankroll = [r for r in all_rows if r.get('environment') == 'TEST']
                ai_bankroll = [r for r in all_rows if r.get('environment') == 'AI_TEST']
                
                html = "<h2>BANKROLL</h2><div class='card'><h3>REAL BANKROLL</h3>"
                if real_bankroll:
                    for acc in real_bankroll:
                        current_balance = acc.get('current_bankroll', acc.get('current_balance', 0)) or 0
                        total_wagered = acc.get('total_wagered', 0) or 0
                        roi = acc.get('roi', 0) or 0
                        html += f"<div class='bankroll-item'><div class='bankroll-info'><div class='section-label'>ACCOUNT</div><div class='value'>{acc['owner_name']}</div></div>"
                        html += f"<div class='bankroll-info'><div class='section-label'>CURRENT</div><div class='value bankroll-value'>{current_balance:.2f}</div></div>"
                        html += f"<div class='bankroll-info'><div class='section-label'>WAGERED</div><div class='value'>{total_wagered:.2f}</div></div>"
                        html += f"<div class='bankroll-info'><div class='section-label'>ROI</div><div class='value roi-{'positive' if roi > 0 else 'negative'}'>{roi:.2f}%</div></div></div>"
                else:
                    html += "<div class='empty-state'><p>No real bankroll accounts found.</p></div>"
                html += "</div><div class='card'><h3>TEST BANKROLL</h3>"
                if test_bankroll:
                    for acc in test_bankroll:
                        current_balance = acc.get('current_bankroll', acc.get('current_balance', 0)) or 0
                        total_wagered = acc.get('total_wagered', 0) or 0
                        roi = acc.get('roi', 0) or 0
                        html += f"<div class='bankroll-item'><div class='bankroll-info'><div class='section-label'>ACCOUNT</div><div class='value'>{acc['owner_name']}</div></div>"
                        html += f"<div class='bankroll-info'><div class='section-label'>CURRENT</div><div class='value bankroll-value'>{current_balance:.2f}</div></div>"
                        html += f"<div class='bankroll-info'><div class='section-label'>WAGERED</div><div class='value'>{total_wagered:.2f}</div></div>"
                        html += f"<div class='bankroll-info'><div class='section-label'>ROI</div><div class='value roi-{'positive' if roi > 0 else 'negative'}'>{roi:.2f}%</div></div></div>"
                else:
                    html += "<div class='empty-state'><p>No test bankroll accounts found.</p></div>"
                html += "</div>"
                
                # AI BANKROLLS
                if ai_bankroll:
                    html += "<div class='card'><h3>AI BANKROLLS (TEST ONLY)</h3>"
                    for acc in ai_bankroll:
                        current_balance = acc.get('current_bankroll', acc.get('current_balance', 0)) or 0
                        total_wagered = acc.get('total_wagered', 0) or 0
                        roi = acc.get('roi', 0) or 0
                        html += f"<div class='bankroll-item'><div class='bankroll-info'><div class='section-label'>ACCOUNT</div><div class='value'>{acc['owner_name']}</div></div>"
                        html += f"<div class='bankroll-info'><div class='section-label'>CURRENT</div><div class='value bankroll-value'>{current_balance:.2f}</div></div>"
                        html += f"<div class='bankroll-info'><div class='section-label'>WAGERED</div><div class='value'>{total_wagered:.2f}</div></div>"
                        html += f"<div class='bankroll-info'><div class='section-label'>ROI</div><div class='value roi-{'positive' if roi > 0 else 'negative'}'>{roi:.2f}%</div></div></div>"
                    html += "</div>"
                
            elif tab_id == 'search':
                html = "<h2>PLAYER SEARCH</h2><div class='card'><div class='search-box'>"
                html += "<input type='text' class='search-input' id='search-term' placeholder='Enter player name...'><button class='search-button' onclick='searchPlayer()'>SEARCH</button>"
                html += "</div><div id='search-results'><div class='empty-state'><p>Enter a player name to search for their stored history.</p></div></div></div>"
                
            else:
                html = f"<h2>{tab_id.replace('-', ' ').title()}</h2><div class='card'><p>Content for {tab_id}</p></div>"
            
            self.wfile.write(html.encode())
            
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f"Error serving {tab_id}: {str(e)}".encode())

    def handle_api(self):
        path = self.path.replace('/api/', '').split('?')[0]
        
        if path == 'board':
            from board_view import board_data
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            filters = {k:v[0] for k,v in params.items()}
            conn = sqlite3.connect('file:' + DB_PATH + '?mode=ro', uri=True, timeout=30)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute('PRAGMA query_only=ON')
                conn.execute('BEGIN')
                data = board_data(conn, filters.get('view','overview'), filters)
            finally:
                conn.close()
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(json.dumps(data, allow_nan=False).encode())
        elif path == 'manual-offer-contexts':
            from manual_offers import eligible_contexts
            conn = sqlite3.connect('file:' + DB_PATH + '?mode=ro', uri=True, timeout=30)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute('PRAGMA query_only=ON')
                conn.execute('BEGIN')
                contexts = eligible_contexts(conn)
            finally:
                conn.close()
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(json.dumps({'contexts': contexts}).encode())
        elif path == 'ai-monitoring':
            conn = sqlite3.connect('file:/var/lib/liveintel/intel.db?mode=ro', uri=True)
            conn.row_factory = sqlite3.Row
            try:
                data = read_monitoring(conn)
            finally:
                conn.close()
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
        elif path == 'live-games':
            self.serve_live_games()
        elif path == 'key-players':
            self.serve_key_players()
        elif path == 'totals':
            self.serve_totals()
        elif path == 'props':
            self.serve_props()
        elif path == 'raw-data':
            self.serve_raw_data()
        elif path == 'tickets':
            self.serve_tickets()
        elif path == 'bankroll':
            self.serve_bankroll()
        elif path == 'search-player':
            self.serve_search_player()
        elif path == 'watchlist':
            self.serve_watchlist()
        elif path == 'add-to-watchlist':
            self.add_to_watchlist()
        elif path == 'remove-from-watchlist':
            self.remove_from_watchlist()
        elif path == 'update-watchlist':
            self.update_watchlist()
        elif path == 'suggested-watchers':
            self.get_suggested_watchers()
        elif path == 'analyze-player-market':
            self.analyze_player_market()
        elif path == 'player-best-market':
            self.get_player_best_market()
        elif path == 'today-players':
            self.serve_today_players_count()
        elif path == 'save-ticket':
            self.save_ticket()
        elif path == 'auto-settle':
            self.auto_settle()
        else:
            self.send_response(404)
            self.end_headers()

    def serve_live_games(self):
        from liveintel_service import categorize_game_status, is_fresh, get_games_by_status_category
        
        now = datetime.datetime.utcnow()
        
        live_games = get_games_by_status_category('LIVE NOW', now)
        upcoming_games = get_games_by_status_category('UPCOMING TODAY', now)
        final_games = get_games_by_status_category('FINAL TODAY', now)
        stale_games = get_games_by_status_category('STALE', now)

        start_times = get_espn_start_time_map()
        conn = get_db()
        for game in upcoming_games:
            game['start_time'] = start_times.get(str(game.get('game_id', '')), 'TODAY (TIME TBD)')
        
        for game in live_games:
            last_updated = game.get('last_updated', '')
            if last_updated:
                try:
                    last_updated_dt = datetime.datetime.strptime(last_updated, '%Y-%m-%d %H:%M:%S')
                    age_minutes = (now - last_updated_dt).total_seconds() / 60
                except:
                    age_minutes = 999
            else:
                age_minutes = 999
            
            game['data_age_minutes'] = round(age_minutes, 1)
            game['is_stale'] = False
            game['freshness_label'] = 'LIVE'
            game['game_script'] = game.get('game_script', 'LIVE')
            game['period'] = game.get('period', 'PRE')
            game['clock'] = game.get('clock', 'NOT STARTED')
            
            # Add sport for GAMECAST link
            gid = str(game.get('game_id', ''))
            sport_row = conn.execute("SELECT sport FROM games WHERE id = ?", (gid,)).fetchone()
            if sport_row:
                game['sport'] = sport_row['sport']
            else:
                # Fallback to multi_sport_players
                sport_row = conn.execute("SELECT DISTINCT sport FROM multi_sport_players WHERE game_id = ?", (gid,)).fetchone()
                game['sport'] = sport_row['sport'] if sport_row else 'NBA'
        
        # Add sport to all game categories
        for games_list in [upcoming_games, final_games, stale_games]:
            for game in games_list:
                gid = str(game.get('game_id', ''))
                sport_row = conn.execute("SELECT sport FROM games WHERE id = ?", (gid,)).fetchone()
                if sport_row:
                    game['sport'] = sport_row['sport']
                else:
                    sport_row = conn.execute("SELECT DISTINCT sport FROM multi_sport_players WHERE game_id = ?", (gid,)).fetchone()
                    game['sport'] = sport_row['sport'] if sport_row else 'NBA'
        
        conn.close()
        
        for game in upcoming_games:
            last_updated = game.get('last_updated', '')
            if last_updated:
                try:
                    last_updated_dt = datetime.datetime.strptime(last_updated, '%Y-%m-%d %H:%M:%S')
                    age_minutes = (now - last_updated_dt).total_seconds() / 60
                except:
                    age_minutes = 999
            else:
                age_minutes = 999
            
            game['data_age_minutes'] = round(age_minutes, 1)
            game['is_stale'] = False
            game['freshness_label'] = 'UPCOMING'
            game['game_script'] = game.get('game_script', 'PRE-GAME')
            game['period'] = game.get('period', 'PRE')
            game['clock'] = game.get('clock', 'NOT STARTED')
        
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({
            'live_games': live_games,
            'upcoming_games': upcoming_games,
            'final_games': final_games,
            'stale_games': stale_games,
            'live_count': len(live_games),
            'upcoming_count': len(upcoming_games),
            'final_count': len(final_games),
            'stale_count': len(stale_games)
        }).encode())
    
    def serve_key_players(self):
        import json
        
        conn = get_db()
        
        # Get players with actual game stats from player_game_stats
        # This gives us NBA, NFL, and CFB players with real data
        rows = conn.execute("""
            SELECT DISTINCT player_name, team, player_id
            FROM player_game_stats
            WHERE player_name IS NOT NULL
            GROUP BY player_name
            ORDER BY MAX(game_id) DESC
            LIMIT 100
        """).fetchall()
        conn.close()
        
        known_nba_players = {
            'NIKOLA JOKIC', 'LUKA DONCIC', 'GIANNIS ANTEKOUNMPO', 'JALEN BRUNSON', 
            'KARL-ANTHONY TOWNS', 'STEPHEN CURRY', 'TYUS JONES', 'JAMAL MURRAY',
            'JAYSON TATOM', 'JAYLEN BROWN', 'LONZO BALL', 'ZION WILLIAMSON',
            'LAMAR JACKSON', 'CAIDEN JONES', 'DEVON BUCHKAN', 'BRADLEY BEAL',
            'KEVIN DURANT', 'LEBRON JAMES', 'LUDWIG AKBAR', 'DEVIN BOOKER',
            'KAWHI LEONARD', 'PAUL GEORGE', 'Nikola Jokic', 'Luka Doncic',
            'Giannis Antetokounmpo', 'Jalen Brunson', 'Karl-Anthony Towns',
            'Stephen Curry', 'Jayson Tatum', 'Jaylen Brown', 'Zion Williamson',
            'Devon Bucket', 'Bradley Beal', 'Kevin Durant', 'LeBron James',
            'Luka Samaniego', 'Luka Garza', 'Nikola Topic', 'Nikola Vucevic'
        }
        
        # Load watchlist once
        watchlist = {}
        watchlist_file = "/opt/liveintel/data/watchlist.json"
        try:
            with open(watchlist_file) as f:
                watchlist_data = json.load(f)
                for pid, pdata in watchlist_data.get("players", {}).items():
                    watchlist[pdata.get("player_name", "")] = pdata.get("watch_reason", "WATCH")
        except:
            pass
        
        players = []
        for row in rows:
            player_name = row[0]
            player = {'player_name': player_name, 'team': row[1], 'player_id': row[2]}
            
            # Determine sport based on player name and team
            team = row[1]
            if player_name.upper() in known_nba_players:
                sport = 'NBA'
            elif team.upper() in ['FSU', 'UF', 'USC', 'OSU', 'MSU', 'LSU', 'ALABAMA', 'GEORGIA', 'TEXAS', 'OKLAHOMA', 'CLEMSON', 'NC STATE', 'MICHIGAN', 'OHIO STATE', 'NOTRE DAME', 'PENN STATE', 'WISCONSIN', 'IOWA', 'PACIFIC', 'BYU', 'HOUSTON', 'UCF', 'SMU', 'TCU', 'BOISE STATE', 'FRESNO STATE', 'SAN DIEGO STATE', 'UTAH STATE', 'NEVADA', 'NEW MEXICO', 'COLORADO STATE', 'ARIZONA STATE', 'TEXAS TECH', 'BAYLOR', 'OKLAHOMA STATE', 'WEST VIRGINIA', 'KANSAS STATE', 'IOWA STATE', 'KANSAS', 'DENVER', 'FLORIDA', 'VANDERBILT', 'MISSISSIPPI', 'MISSISSIPPI STATE', 'ALABAMA STATE', 'MISSOURI', 'ARKANSAS', 'LOUISIANA STATE', 'TEXAS A&M', 'SOUTH CAROLINA', 'GEORGIA TECH', 'FLORIDA STATE', 'DUKE', 'MARYLAND', 'VIRGINIA', 'NORTH CAROLINA', 'SYRACUSE', 'STONY BROOK', 'UCLA', 'CALIFORNIA', 'OREGON', 'WASHINGTON', 'ARIZONA', 'COLORADO', 'UTAH', 'STANFORD', 'NORTHWESTERN', 'RUTGERS', 'ILLINOIS', 'INDIANA', 'PURDUE', 'MICHIGAN STATE']:
                sport = 'CFB'
            else:
                sport = 'NFL'
            
            player['sport'] = sport
            # Try to get classification from watchlist first
            classification = watchlist.get(player_name, 'WATCH')
            if classification == 'WATCH':
                # Use player_game_stats for classification
                classification = self.classify_player_from_db(player_name, '', sport)
            
            player['dependency'] = classification
            
            # Get player stats from player_game_stats
            stats = self.get_player_stats(player['player_id'], '')
            player['last_game_stats'] = stats.get('last_game', 'N/A')
            player['last_5'] = stats.get('last_5', 'N/A')
            player['last_10'] = stats.get('last_10', 'N/A')
            player['season_stats'] = stats.get('season', 'N/A')
            player['current_role'] = stats.get('role', 'N/A')
            players.append(player)
        
        # Sort by classification priority: CORE, HIGH DEPENDENCY, IMPORTANT, WATCH
        classification_order = {'CORE': 0, 'HIGH DEPENDENCY': 1, 'IMPORTANT': 2, 'WATCH': 3}
        players.sort(key=lambda x: classification_order.get(x['dependency'], 4))
        
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'players': players}).encode())

    def get_player_classification(self, player_name, position, sport):
        """Get classification for a player based on their stats and role"""
        import json
        
        # Try to get from volume watchlist first
        watchlist_file = "/opt/liveintel/data/watchlist.json"
        try:
            with open(watchlist_file) as f:
                watchlist_data = json.load(f)
                players = watchlist_data.get("players", {})
                for pid, pdata in players.items():
                    if pdata.get("player_name") == player_name:
                        return pdata.get("watch_reason", "WATCH")
        except:
            pass
        
        # Use database stats for classification
        classification = self.classify_player_from_db(player_name, position, sport)
        return classification

    def classify_player_from_db(self, player_name, position, sport):
        """Classify player based on their volume stats from player_game_stats table"""
        import sqlite3
        
        conn = sqlite3.connect('/var/lib/liveintel/intel.db', timeout=30.0)
        conn.execute('PRAGMA busy_timeout = 30000')
        conn.row_factory = sqlite3.Row
        
        try:
            # Determine sport if not provided
            if not sport:
                sport = 'NFL'  # Default to NFL
            
            # Get player_id
            row = conn.execute("""
                SELECT player_id FROM player_game_stats WHERE player_name = ? LIMIT 1
            """, (player_name,)).fetchone()
            
            if not row:
                conn.close()
                return 'WATCH'
            
            player_id = row['player_id']
            rows = conn.execute("""
                SELECT game_id, stats_json FROM player_game_stats 
                WHERE player_id = ? AND stats_json IS NOT NULL
            """, (player_id,)).fetchall()
            
            if not rows:
                conn.close()
                return 'WATCH'
            
            # Classify based on sport and position
            if sport.upper() in ['NFL', 'CFB']:
                # Check pass attempts for QBs
                attempts = []
                for r in rows:
                    try:
                        stats_json = json.loads(r['stats_json'])
                        for category, data in stats_json.items():
                            if isinstance(data, dict):
                                for k, v in data.items():
                                    if v and 'att' in k.lower():
                                        if '/' in str(v):
                                            try:
                                                parts = str(v).split('/')
                                                att = int(parts[1])
                                                attempts.append(att)
                                            except:
                                                pass
                                        elif str(v).isdigit():
                                            attempts.append(int(v))
                    except:
                        pass
                
                if attempts:
                    avg_att = sum(attempts) / len(attempts)
                    if avg_att >= 40:
                        return 'CORE'
                    elif avg_att >= 35:
                        return 'HIGH DEPENDENCY'
                    elif avg_att >= 25:
                        return 'IMPORTANT'
                    else:
                        return 'WATCH'
                
                # Check rush attempts for RBs
                if position.upper() == 'RB':
                    attempts = []
                    for r in rows:
                        try:
                            stats_json = json.loads(r['stats_json'])
                            for category, data in stats_json.items():
                                if isinstance(data, dict):
                                    for k, v in data.items():
                                        if v and 'car' in k.lower() and str(v).isdigit():
                                            attempts.append(int(v))
                        except:
                            pass
                    
                    if attempts:
                        avg_att = sum(attempts) / len(attempts)
                        if avg_att >= 20:
                            return 'CORE'
                        elif avg_att >= 15:
                            return 'HIGH DEPENDENCY'
                        elif avg_att >= 10:
                            return 'IMPORTANT'
                        else:
                            return 'WATCH'
                
                # Check targets for WR/TE
                if position.upper() in ['WR', 'TE']:
                    targets = []
                    for r in rows:
                        try:
                            stats_json = json.loads(r['stats_json'])
                            for category, data in stats_json.items():
                                if isinstance(data, dict):
                                    for k, v in data.items():
                                        if v and 'tgt' in k.lower() and str(v).isdigit():
                                            targets.append(int(v))
                        except:
                            pass
                    
                    if targets:
                        avg_targets = sum(targets) / len(targets)
                        if avg_targets >= 12:
                            return 'CORE'
                        elif avg_targets >= 10:
                            return 'HIGH DEPENDENCY'
                        elif avg_targets >= 5:
                            return 'IMPORTANT'
                        else:
                            return 'WATCH'
            
            # NBA classification
            elif sport.upper() == 'NBA':
                fga_values = []
                fta_values = []
                points_values = []
                rebounds_values = []
                assists_values = []
                
                for r in rows:
                    try:
                        stats_json = json.loads(r['stats_json'])
                        for category, data in stats_json.items():
                            if isinstance(data, dict):
                                if 'totals' in category.lower() or not category:
                                    # NBA stats: FGA, FTA, PTS, REB, AST
                                    if 'FGA' in data:
                                        try:
                                            val = int(data['FGA'])
                                            fga_values.append(val)
                                        except:
                                            pass
                                    if 'FTA' in data:
                                        try:
                                            val = int(data['FTA'])
                                            fta_values.append(val)
                                        except:
                                            pass
                                    if 'PTS' in data:
                                        try:
                                            val = int(data['PTS'])
                                            points_values.append(val)
                                        except:
                                            pass
                                    if 'REB' in data:
                                        try:
                                            val = int(data['REB'])
                                            rebounds_values.append(val)
                                        except:
                                            pass
                                    if 'AST' in data:
                                        try:
                                            val = int(data['AST'])
                                            assists_values.append(val)
                                        except:
                                            pass
                    except:
                        pass
                
                # Classify based on FGA (field goal attempts) - primary indicator of usage
                if fga_values:
                    avg_fga = sum(fga_values) / len(fga_values)
                    if avg_fga >= 20:
                        return 'CORE'
                    elif avg_fga >= 15:
                        return 'HIGH DEPENDENCY'
                    elif avg_fga >= 10:
                        return 'IMPORTANT'
                    else:
                        return 'WATCH'
            
            conn.close()
            return 'WATCH'
                
        except Exception:
            conn.close()
            return 'WATCH'

    def get_player_stats(self, player_id, position):
        """Get player stats from player_game_stats table (NBA/NFL/CFB)"""
        import sqlite3
        conn = sqlite3.connect('/var/lib/liveintel/intel.db', timeout=30.0)
        conn.execute('PRAGMA busy_timeout = 30000')
        conn.row_factory = sqlite3.Row
        
        stats = {'last_game': 'N/A', 'last_5': 'N/A', 'last_10': 'N/A', 'season': 'N/A', 'role': 'N/A'}
        
        # Get stats from player_game_stats table
        rows = conn.execute("""
            SELECT game_id, stats_json, last_updated FROM player_game_stats 
            WHERE player_id = ?
            ORDER BY game_id DESC LIMIT 20
        """, (player_id,)).fetchall()
        
        if not rows:
            conn.close()
            return stats
        
        # Parse stats from JSON
        last_game_stats = []
        last_5_pts = []
        last_5_reb = []
        last_5_ast = []
        last_5_min = []
        last_5_fga = []
        last_10_pts = []
        last_10_reb = []
        last_10_ast = []
        last_10_min = []
        last_10_fga = []
        
        for i, row in enumerate(rows):
            try:
                stats_json = json.loads(row['stats_json']) if row['stats_json'] else {}
                for category, data in stats_json.items():
                    if isinstance(data, dict):
                        # NBA stats - check for empty key or totals
                        if not category or 'totals' in category.lower():
                            if 'PTS' in data:
                                try:
                                    val = int(data['PTS'])
                                    if i == 0:
                                        last_game_stats.append(f"PTS: {val}")
                                    if i < 5:
                                        last_5_pts.append(val)
                                    if i < 10:
                                        last_10_pts.append(val)
                                except:
                                    pass
                            if 'REB' in data:
                                try:
                                    val = int(data['REB'])
                                    if i < 5:
                                        last_5_reb.append(val)
                                    if i < 10:
                                        last_10_reb.append(val)
                                except:
                                    pass
                            if 'AST' in data:
                                try:
                                    val = int(data['AST'])
                                    if i < 5:
                                        last_5_ast.append(val)
                                    if i < 10:
                                        last_10_ast.append(val)
                                except:
                                    pass
                            if 'MIN' in data:
                                try:
                                    val = int(data['MIN'])
                                    if i == 0:
                                        last_game_stats.append(f"MIN: {val}")
                                    if i < 5:
                                        last_5_min.append(val)
                                    if i < 10:
                                        last_10_min.append(val)
                                except:
                                    pass
                            if 'FGA' in data:
                                try:
                                    val = int(data['FGA'])
                                    if i < 5:
                                        last_5_fga.append(val)
                                    if i < 10:
                                        last_10_fga.append(val)
                                except:
                                    pass
            except:
                pass
        
        # Build last_game display
        if last_game_stats:
            stats['last_game'] = ', '.join(last_game_stats)
        
        # Calculate averages for last 5
        if last_5_pts:
            stats['last_5'] = f"PTS: {last_5_pts[0] if len(last_5_pts) == 1 else sum(last_5_pts)/len(last_5_pts):.1f}"
        elif last_5_fga:
            stats['last_5'] = f"FGA: {last_5_fga[0] if len(last_5_fga) == 1 else sum(last_5_fga)/len(last_5_fga):.1f}"
        if last_5_reb:
            if stats['last_5'] == 'N/A':
                stats['last_5'] = f"REB: {last_5_reb[0] if len(last_5_reb) == 1 else sum(last_5_reb)/len(last_5_reb):.1f}"
            else:
                stats['last_5'] += f", REB: {last_5_reb[0] if len(last_5_reb) == 1 else sum(last_5_reb)/len(last_5_reb):.1f}"
        if last_5_ast:
            if stats['last_5'] == 'N/A':
                stats['last_5'] = f"AST: {last_5_ast[0] if len(last_5_ast) == 1 else sum(last_5_ast)/len(last_5_ast):.1f}"
            else:
                stats['last_5'] += f", AST: {last_5_ast[0] if len(last_5_ast) == 1 else sum(last_5_ast)/len(last_5_ast):.1f}"
        
        # Calculate averages for last 10
        if last_10_pts:
            stats['last_10'] = f"PTS: {sum(last_10_pts)/len(last_10_pts):.1f}"
        elif last_10_fga:
            stats['last_10'] = f"FGA: {sum(last_10_fga)/len(last_10_fga):.1f}"
        if last_10_reb:
            if stats['last_10'] == 'N/A':
                stats['last_10'] = f"REB: {sum(last_10_reb)/len(last_10_reb):.1f}"
            else:
                stats['last_10'] += f", REB: {sum(last_10_reb)/len(last_10_reb):.1f}"
        if last_10_ast:
            if stats['last_10'] == 'N/A':
                stats['last_10'] = f"AST: {sum(last_10_ast)/len(last_10_ast):.1f}"
            else:
                stats['last_10'] += f", AST: {sum(last_10_ast)/len(last_10_ast):.1f}"
        
        # Build season stats
        if last_5_pts or last_10_pts:
            all_pts = last_5_pts if len(last_5_pts) >= len(last_10_pts) else last_10_pts
            all_reb = last_5_reb if len(last_5_reb) >= len(last_10_reb) else last_10_reb
            all_ast = last_5_ast if len(last_5_ast) >= len(last_10_ast) else last_10_ast
            
            season_parts = []
            if all_pts:
                season_parts.append(f"PTS: {sum(all_pts)/len(all_pts):.1f}")
            if all_reb:
                season_parts.append(f"REB: {sum(all_reb)/len(all_reb):.1f}")
            if all_ast:
                season_parts.append(f"AST: {sum(all_ast)/len(all_ast):.1f}")
            
            if season_parts:
                stats['season'] = ', '.join(season_parts)
        
        # Determine role based on stats
        if last_5_pts:
            if len(last_5_pts) >= 5:
                avg_pts = sum(last_5_pts) / len(last_5_pts)
                avg_fga = sum(last_5_fga) / len(last_5_fga) if last_5_fga else 0
                if avg_fga >= 15:
                    stats['role'] = 'PRIMARY SCORER'
                elif avg_fga >= 10:
                    stats['role'] = 'SECONDARY SCORER'
                elif avg_fga >= 5:
                    stats['role'] = 'ROLE SCORER'
            if last_5_reb and len(last_5_reb) >= 5:
                avg_reb = sum(last_5_reb) / len(last_5_reb)
                if avg_reb >= 8:
                    stats['role'] = 'PRIMARY REBOUNDER'
                elif avg_reb >= 5:
                    stats['role'] = 'SECONDARY REBOUNDER'
            if last_5_ast and len(last_5_ast) >= 5:
                avg_ast = sum(last_5_ast) / len(last_5_ast)
                if avg_ast >= 8:
                    stats['role'] = 'PRIMARY CREATOR'
                elif avg_ast >= 5:
                    stats['role'] = 'SECONDARY CREATOR'
        
        conn.close()
        return stats

    def serve_totals(self):
        import sqlite3
        conn = sqlite3.connect('/var/lib/liveintel/intel.db', timeout=30.0)
        conn.execute('PRAGMA busy_timeout = 30000')
        conn.row_factory = sqlite3.Row
        
        today = datetime.datetime.now().strftime('%Y-%m-%d')
        
        # Get upcoming today games, prefer NBA
        games = conn.execute("""
            SELECT game_id, away_team, home_team, away_score, home_score, period, clock, last_updated, game_script
            FROM game_state 
            WHERE game_script LIKE 'UPCOMING%' OR game_script LIKE 'TODAY%' OR game_script LIKE '%UPCOMING%' OR game_script = 'PRE' 
            ORDER BY last_updated DESC 
            LIMIT 10
        """).fetchall()
        
        totals = []
        for game in games:
            last_updated = game['last_updated'] if 'last_updated' in game.keys() else ''
            book_total = 'UNAVAILABLE'
            baseline = 'N/A'
            gap = 'N/A'
            lean = 'N/A'
            
            # Calculate team baselines from history
            away_scorers = 'N/A'
            home_scorers = 'N/A'
            h1_baselines = 'N/A'
            h2_baselines = 'N/A'
            
            try:
                # Get team scoring averages from completed games
                team_away_avg = self.get_team_average(game['away_team'], 'NBA', 'full')
                team_home_avg = self.get_team_average(game['home_team'], 'NBA', 'full')
                
                if team_away_avg and team_home_avg:
                    baseline = team_away_avg + team_home_avg
                    gap = round(baseline - 240.5, 1) if baseline != 'N/A' else 'N/A'
                    lean = 'UNDER' if gap < 0 else 'OVER'
                    h1_avg = self.get_team_average(game['away_team'], 'NBA', 'h1') + self.get_team_average(game['home_team'], 'NBA', 'h1')
                    h2_avg = self.get_team_average(game['away_team'], 'NBA', 'h2') + self.get_team_average(game['home_team'], 'NBA', 'h2')
                    h1_baselines = f"{h1_avg:.1f}" if h1_avg != 'N/A' else 'N/A'
                    h2_baselines = f"{h2_avg:.1f}" if h2_avg != 'N/A' else 'N/A'
                    
                    # Get key scorers
                    away_scorers = self.get_key_scorers(game['away_team'], 'NBA')
                    home_scorers = self.get_key_scorers(game['home_team'], 'NBA')
            except:
                pass
            
            total = {
                'game_id': game['game_id'],
                'away_team': game['away_team'],
                'home_team': game['home_team'],
                'baseline': f"{baseline:.1f}" if isinstance(baseline, (int, float)) else baseline,
                'book_line': book_total,
                'gap': f"{gap:.1f}" if isinstance(gap, (int, float)) else gap,
                'gap_class': 'positive' if isinstance(gap, (int, float)) and gap > 0 else 'negative' if isinstance(gap, (int, float)) and gap < 0 else '',
                'lean': lean,
                'h1_baselines': h1_baselines,
                'h2_baselines': h2_baselines,
                'home_scorers': home_scorers,
                'away_scorers': away_scorers
            }
            totals.append(total)
        
        conn.close()
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'totals': totals}).encode())
    
    def get_team_average(self, team, sport, period='full'):
        import sqlite3
        conn = sqlite3.connect('/var/lib/liveintel/intel.db', timeout=30.0)
        conn.execute('PRAGMA busy_timeout = 30000')
        
        try:
            if period == 'full':
                result = conn.execute("""
                    SELECT AVG(away_score + home_score) as avg
                    FROM game_state 
                    WHERE (away_team = ? OR home_team = ?)
                    AND game_script LIKE '%FINAL%'
                    LIMIT 5
                """, (team, team)).fetchone()
            elif period == 'h1':
                result = conn.execute("""
                    SELECT AVG(away_score_first_half + home_score_first_half) as avg
                    FROM game_state 
                    WHERE (away_team = ? OR home_team = ?)
                    AND game_script LIKE '%FINAL%'
                    LIMIT 5
                """, (team, team)).fetchone()
            elif period == 'h2':
                result = conn.execute("""
                    SELECT AVG(away_score_second_half + home_score_second_half) as avg
                    FROM game_state 
                    WHERE (away_team = ? OR home_team = ?)
                    AND game_script LIKE '%FINAL%'
                    LIMIT 5
                """, (team, team)).fetchone()
            
            conn.close()
            return result['avg'] if result and result['avg'] else 'N/A'
        except:
            conn.close()
            return 'N/A'
    
    def get_key_scorers(self, team, sport):
        import sqlite3
        conn = sqlite3.connect('/var/lib/liveintel/intel.db', timeout=30.0)
        conn.execute('PRAGMA busy_timeout = 30000')
        conn.row_factory = sqlite3.Row
        
        try:
            players = conn.execute("""
                SELECT player_name, team, SUM(CAST(json_extract(stats_json, '$.totals.PTS') AS INTEGER)) as total_pts, COUNT(*) as games
                FROM player_game_stats 
                WHERE team = ?
                AND stats_json LIKE '%PTS%'
                GROUP BY player_name
                ORDER BY total_pts DESC
                LIMIT 5
            """, (team,)).fetchall()
            
            conn.close()
            if players:
                return ', '.join([f"{p['player_name']} ({p['total_pts']//p['games']:.1f} PPG)" for p in players])
            return 'N/A'
        except:
            conn.close()
            return 'N/A'
    
    def serve_props(self):
        conn = get_db()
        rows = conn.execute("""SELECT pl.*, pp.current_value, pp.projected_low, pp.projected_median, 
                              pp.projected_high, pp.signal, pp.confidence, pp.reasons, pp.historical_median
                              FROM prop_lines pl
                              LEFT JOIN prop_projections pp ON pl.id = pp.prop_id
                              ORDER BY pl.observed_at DESC LIMIT 20""").fetchall()
        conn.close()
        
        props = []
        for row in rows:
            prop = dict(row)
            current_val = prop['current_value'] if prop['current_value'] is not None else 0
            line = prop['line'] if prop['line'] else 0
            diff = current_val - line
            prop['current_value'] = round(current_val, 1) if current_val != 0 else 'N/A'
            prop['diff_text'] = round(diff, 1) if diff != 0 else 'N/A'
            prop['diff_class'] = 'positive' if diff > 0 else 'negative'
            prop['signal_class'] = prop['signal'].lower().replace(' ', '-') if prop['signal'] else 'unsettled'
            props.append(prop)
        
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'props': props}).encode())
    
    def serve_watchlist(self):
        """Get owner's watchlist"""
        import sqlite3
        conn = sqlite3.connect('/var/lib/liveintel/intel.db', timeout=30.0)
        conn.execute('PRAGMA busy_timeout = 30000')
        conn.row_factory = sqlite3.Row
        
        rows = conn.execute("""
            SELECT player_name, team, sport, player_id, is_pinned, added_at, source
            FROM owner_watchlist
            ORDER BY is_pinned DESC, added_at DESC
        """).fetchall()
        conn.close()
        
        watchlist = [dict(row) for row in rows]
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'watchlist': watchlist}).encode())
    
    def add_to_watchlist(self):
        """Add player to watchlist"""
        import json
        import sqlite3
        
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        data = json.loads(body.decode())
        
        player_name = data.get('player_name', '')
        team = data.get('team', '')
        sport = data.get('sport', '')
        player_id = data.get('player_id', '')
        is_pinned = data.get('is_pinned', 0)
        source = data.get('source', 'system')
        
        if not player_id or not player_name:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'player_id and player_name required'}).encode())
            return
        
        conn = sqlite3.connect('/var/lib/liveintel/intel.db', timeout=30.0)
        conn.execute('PRAGMA busy_timeout = 30000')
        
        existing = conn.execute("SELECT id FROM owner_watchlist WHERE player_id = ?", (player_id,)).fetchone()
        
        if existing:
            conn.execute("""
                UPDATE owner_watchlist 
                SET team = ?, sport = ?, is_pinned = ?, source = ?
                WHERE player_id = ?
            """, (team, sport, is_pinned, source, player_id))
        else:
            conn.execute("""
                INSERT INTO owner_watchlist (player_name, team, sport, player_id, is_pinned, source)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (player_name, team, sport, player_id, is_pinned, source))
        
        conn.commit()
        conn.close()
        
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'success': True, 'message': 'Added to watchlist'}).encode())

    def remove_from_watchlist(self):
        """Remove player from watchlist"""
        import json
        import sqlite3
        
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        data = json.loads(body.decode())
        
        player_id = data.get('player_id', '')
        
        if not player_id:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'player_id required'}).encode())
            return
        
        conn = sqlite3.connect('/var/lib/liveintel/intel.db', timeout=30.0)
        conn.execute('PRAGMA busy_timeout = 30000')
        conn.execute("DELETE FROM owner_watchlist WHERE player_id = ?", (player_id,))
        conn.commit()
        conn.close()
        
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'success': True, 'message': 'Removed from watchlist'}).encode())
    
    def update_watchlist(self):
        """Update watchlist items (pin, remove, replace)"""
        import json
        import sqlite3
        
        content_length = int(self.headers.get('Content-Length', 0))
        sys.stderr.write(f"DEBUG: Content-Length={content_length}\n")
        try:
            body = self.rfile.read(content_length)
            sys.stderr.write(f"DEBUG: Body read successfully, length={len(body)}\n")
        except Exception as e:
            sys.stderr.write(f"DEBUG: Error reading body: {e}\n")
            raise
        data = json.loads(body.decode())
        
        action = data.get('action', '')
        player_id = data.get('player_id', '')
        is_pinned = data.get('is_pinned', None)
        
        conn = sqlite3.connect('/var/lib/liveintel/intel.db', timeout=30.0)
        conn.execute('PRAGMA busy_timeout = 30000')
        
        if action == 'pin':
            conn.execute("UPDATE owner_watchlist SET is_pinned = ? WHERE player_id = ?", (is_pinned, player_id))
        elif action == 'remove':
            conn.execute("DELETE FROM owner_watchlist WHERE player_id = ?", (player_id,))
        elif action == 'replace':
            conn.execute("DELETE FROM owner_watchlist WHERE player_id = ?", (player_id,))
            if data.get('new_player_id'):
                conn.execute("""
                    INSERT INTO owner_watchlist (player_name, team, sport, player_id, is_pinned, source)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (data.get('new_player_name'), data.get('new_team'), data.get('new_sport'), 
                      data.get('new_player_id'), is_pinned or 0, 'owner'))
        
        conn.commit()
        conn.close()
        
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'success': True, 'message': f'Action {action} completed'}).encode())
    
    def get_suggested_watchers(self):
        """Get suggested players for watchlist based on classification"""
        import sqlite3
        
        conn = sqlite3.connect('/var/lib/liveintel/intel.db', timeout=30.0)
        conn.execute('PRAGMA busy_timeout = 30000')
        conn.row_factory = sqlite3.Row
        
        # Get players not in watchlist from multi_sport_players table
        rows = conn.execute("""
            SELECT DISTINCT player_name, team, sport, player_id
            FROM multi_sport_players
            WHERE player_id NOT IN (
                SELECT player_id FROM owner_watchlist WHERE is_pinned = 1
            )
            GROUP BY player_id
            ORDER BY player_name ASC
            LIMIT 5
        """).fetchall()
        conn.close()
        
        suggestions = [dict(row) for row in rows]
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'suggestions': suggestions}).encode())
    
    def analyze_player_market(self):
        """Analyze a player's best market based on their stats"""
        import json
        import sqlite3
        
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        data = json.loads(body.decode())
        
        player_id = data.get('player_id', '')
        player_name = data.get('player_name', '')
        team = data.get('team', '')
        sport = data.get('sport', '')
        
        if not player_id:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'player_id required'}).encode())
            return
        
        conn = sqlite3.connect('/var/lib/liveintel/intel.db', timeout=30.0)
        conn.execute('PRAGMA busy_timeout = 30000')
        conn.row_factory = sqlite3.Row
        
        game_stats = conn.execute("""
            SELECT game_id, stats_json, segment, last_updated 
            FROM player_game_stats 
            WHERE player_id = ?
            ORDER BY last_updated DESC
            LIMIT 50
        """, (player_id,)).fetchall()
        conn.close()
        
        if not game_stats:
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                'player_id': player_id,
                'best_market': None,
                'alternatives': [],
                'reason': 'No game stats found'
            }).encode())
            return
        
        if sport == 'NBA':
            markets = ['PTS', 'REB', 'AST', 'PRA', 'PTS+REB', 'PTS+AST', 'REB+AST', 
                      'FGM', 'FGA', '3PM', 'FTM', 'STL', 'BLK', 'TO', 'FANTASY']
            segments = ['FULL GAME', '1Q', '1H', '2H', 'Q4']
        elif sport in ['NFL', 'CFB']:
            position = self._get_player_position(game_stats)
            if position == 'QB':
                markets = ['PASS_ATT', 'PASS_COMP', 'PASS_YDS', 'PASS_TD', 'RUSH_ATT', 'RUSH_YDS', 'FANTASY']
            elif position == 'RB':
                markets = ['RUSH_ATT', 'RUSH_YDS', 'TOTAL_TOUCHES', 'RECV', 'RECV_YDS', 'FANTASY']
            elif position in ['WR', 'TE']:
                markets = ['RECV', 'RECV_YDS', 'TGT', 'FANTASY']
            elif position == 'K':
                markets = ['FGM', 'XP', 'KICKING_POINTS']
            else:
                markets = ['FANTASY']
            segments = ['FULL GAME']
        else:
            markets = []
            segments = []
        
        market_results = []
        for market in markets:
            for segment in segments:
                result = self._analyze_market(player_id, player_name, team, sport, market, segment, game_stats)
                if result:
                    market_results.append(result)
        
        if not market_results:
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                'player_id': player_id,
                'best_market': None,
                'alternatives': []
            }).encode())
            return
        
        market_results.sort(key=lambda x: (x.get('hit_rate', 0), x.get('sample_size', 0)), reverse=True)
        best_markets = market_results[:3]
        
        self._save_market_stats(player_id, player_name, team, sport, market_results)
        
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({
            'player_id': player_id,
            'player_name': player_name,
            'team': team,
            'sport': sport,
            'best_market': best_markets[0] if best_markets else None,
            'alternatives': best_markets[1:3] if len(best_markets) > 1 else [],
            'all_markets': market_results
        }).encode())
    
    def _get_player_position(self, game_stats):
        import json
        for stat in game_stats:
            stats_json = json.loads(stat['stats_json'])
            if isinstance(stats_json, dict):
                if 'pass_att' in stats_json:
                    return 'QB'
                elif 'rush_car' in stats_json or 'rush_att' in stats_json:
                    return 'RB'
                elif 'rec_tgt' in stats_json or 'rec' in stats_json:
                    return 'WR'
                elif 'fgm' in stats_json:
                    return 'K'
        return None
    
    def _analyze_market(self, player_id, player_name, team, sport, market, segment, game_stats):
        import json
        
        segment_stats = []
        for stat in game_stats:
            if stat['segment'] == segment:
                stats_json = json.loads(stat['stats_json'])
                value = self._get_stat_value(stats_json, market, sport)
                if value is not None:
                    segment_stats.append(value)
        
        if len(segment_stats) < 1:
            return None
        
        line = self._get_current_line(player_name, market, sport, team)
        if line is None:
            return None
        
        hits = sum(1 for s in segment_stats if self._is_hit(s, line, market))
        sample_size = len(segment_stats)
        hit_rate = hits / sample_size if sample_size > 0 else 0
        
        margins = [s - line for s in segment_stats]
        avg_margin = sum(margins) / len(margins) if margins else 0
        median_margin = sorted(margins)[len(margins)//2] if margins else 0
        
        avg = sum(segment_stats) / len(segment_stats)
        median = sorted(segment_stats)[len(segment_stats)//2]
        
        side = 'MORE' if line >= 0 else 'LESS'
        consistency = self._calculate_consistency(segment_stats, avg, median, avg_margin)
        
        return {
            'player_id': player_id, 'player_name': player_name, 'team': team, 'sport': sport,
            'market': market, 'segment': segment, 'line': round(line, 1), 'side': side,
            'sample_size': sample_size, 'hits': hits, 'hit_rate': round(hit_rate, 3),
            'avg': round(avg, 1), 'median': round(median, 1),
            'avg_margin': round(avg_margin, 2), 'median_margin': round(median_margin, 2),
            'recent_results': segment_stats[-5:], 'game_log': segment_stats,
            'consistency_score': round(consistency, 2)
        }
    
    def _get_stat_value(self, stats_json, market, sport):
        import json
        
        if not isinstance(stats_json, dict):
            return None
        
        # Stats might be under 'totals' key or directly
        if 'totals' in stats_json:
            main_stats = stats_json['totals']
        elif '' in stats_json:
            main_stats = stats_json['']
        else:
            main_stats = stats_json
        
        if not isinstance(main_stats, dict):
            return None
        
        if sport == 'NBA':
            if market == 'PTS': return main_stats.get('PTS') or main_stats.get('pts') or main_stats.get('total_points')
            elif market == 'REB': return main_stats.get('REB') or main_stats.get('reb') or main_stats.get('total_rebounds')
            elif market == 'AST': return main_stats.get('AST') or main_stats.get('ast') or main_stats.get('assists')
            elif market == 'PRA': return (main_stats.get('PTS', 0) + main_stats.get('REB', 0) + main_stats.get('AST', 0))
            elif market == 'PTS+REB': return (main_stats.get('PTS', 0) + main_stats.get('REB', 0))
            elif market == 'PTS+AST': return (main_stats.get('PTS', 0) + main_stats.get('AST', 0))
            elif market == 'REB+AST': return (main_stats.get('REB', 0) + main_stats.get('AST', 0))
            elif market == 'FGM': 
                fg = main_stats.get('FG') or main_stats.get('fg')
                if fg and '-' in fg: return int(fg.split('-')[0])
                return main_stats.get('FGM') or main_stats.get('FGM')
            elif market == 'FGA': 
                fg = main_stats.get('FG') or main_stats.get('fg')
                if fg and '-' in fg: return int(fg.split('-')[1])
                return main_stats.get('FGA') or main_stats.get('FGA')
            elif market == '3PM': 
                tpm = main_stats.get('3PT') or main_stats.get('3pt') or main_stats.get('3PM')
                if tpm and '-' in str(tpm): return int(str(tpm).split('-')[0])
                return main_stats.get('3PM') or main_stats.get('three_pt_made')
            elif market == 'FTM': 
                ft = main_stats.get('FT') or main_stats.get('ft')
                if ft and '-' in str(ft): return int(str(ft).split('-')[0])
                return main_stats.get('FTM') or main_stats.get('free_throws_made')
            elif market == 'STL': return main_stats.get('STL') or main_stats.get('stl') or main_stats.get('steals')
            elif market == 'BLK': return main_stats.get('BLK') or main_stats.get('blk') or main_stats.get('blocks')
            elif market == 'TO': return main_stats.get('TO') or main_stats.get('to') or main_stats.get('turnovers')
            elif market == 'FANTASY': return main_stats.get('fantasy_points') or main_stats.get('fantasy')
        elif sport in ['NFL', 'CFB']:
            if market == 'PASS_ATT': return main_stats.get('PASS_att') or main_stats.get('pass_att') or main_stats.get('pass_attempts')
            elif market == 'PASS_COMP': return main_stats.get('PASS_cmp') or main_stats.get('pass_cmp') or main_stats.get('completions')
            elif market == 'PASS_YDS': return main_stats.get('PASS_yds') or main_stats.get('pass_yds') or main_stats.get('pass_yards')
            elif market == 'PASS_TD': return main_stats.get('PASS_td') or main_stats.get('pass_td') or main_stats.get('passing_tds')
            elif market == 'RUSH_ATT': return main_stats.get('rush_att') or main_stats.get('rush_attempts')
            elif market == 'RUSH_YDS': return main_stats.get('rush_yds') or main_stats.get('rush_yards')
            elif market == 'RECV': return main_stats.get('rec') or main_stats.get('receptions')
            elif market == 'RECV_YDS': return main_stats.get('rec_yds') or main_stats.get('receiving_yards')
            elif market == 'TGT': return main_stats.get('rec_tgt') or main_stats.get('targets')
            elif market == 'TOTAL_TOUCHES': return (main_stats.get('rush_att', 0) + main_stats.get('rec', 0))
            elif market == 'FANTASY': return main_stats.get('fantasy_points') or main_stats.get('fantasy')
        return None
    
    def _is_hit(self, stat_value, line, market):
        if market in ['TO', 'ALLOWED']:
            return stat_value <= line
        else:
            return stat_value >= line
    
    def _get_current_line(self, player_name, market, sport, team):
        import sqlite3
        market_map = {
            'PTS': 'points', 'REB': 'rebounds', 'AST': 'assists', 'PRA': 'pts+reb+ast',
            'PTS+REB': 'pts+reb', 'PTS+AST': 'pts+ast', 'REB+AST': 'reb+ast',
            'FGM': 'field_goals_made', 'FGA': 'field_goals_att', '3PM': 'three_pt_made',
            'FTM': 'free_throws_made', 'STL': 'steals', 'BLK': 'blocks', 'TO': 'turnovers',
            'PASS_ATT': 'pass_attempts', 'PASS_COMP': 'completions', 'PASS_YDS': 'pass_yards',
            'PASS_TD': 'passing_tds', 'RUSH_ATT': 'rush_attempts', 'RUSH_YDS': 'rush_yards',
            'RECV': 'receptions', 'RECV_YDS': 'receiving_yards', 'TGT': 'targets',
            'TOTAL_TOUCHES': 'total_touches', 'KICKING_POINTS': 'kicking_points', 'FGM': 'field_goals_made'
        }
        prop_market = market_map.get(market, market.lower())
        conn = sqlite3.connect('/var/lib/liveintel/intel.db', timeout=30.0)
        conn.execute('PRAGMA busy_timeout = 30000')
        row = conn.execute("SELECT line, side FROM prop_lines WHERE player_name = ? AND LOWER(market) = ? ORDER BY observed_at DESC LIMIT 1", (player_name, prop_market)).fetchone()
        conn.close()
        return row[0] if row else None
    
    def _calculate_consistency(self, stats, avg, median, avg_margin):
        if not stats: return 0
        hit_rate = sum(1 for s in stats if abs(s - abs(avg)) <= abs(avg_margin)) / len(stats)
        hit_score = hit_rate * 40
        median_dev = abs(median - avg) / (avg if avg else 1)
        median_score = max(0, 20 - median_dev * 20)
        margins = [abs(s - avg) for s in stats]
        margin_std = (sum((m - sum(margins)/len(margins))**2 for m in margins) / len(margins))**0.5 if margins else 0
        margin_score = max(0, 20 - margin_std * 2)
        sample_score = min(20, len(stats) * 2)
        return hit_score + median_score + margin_score + sample_score
    
    def _save_market_stats(self, player_id, player_name, team, sport, market_results):
        import sqlite3
        import json
        conn = sqlite3.connect('/var/lib/liveintel/intel.db', timeout=30.0)
        conn.execute('PRAGMA busy_timeout = 30000')
        for result in market_results:
            existing = conn.execute("SELECT id FROM player_market_stats WHERE player_id = ? AND market = ? AND segment = ?", (player_id, result['market'], result['segment'])).fetchone()
            if existing:
                conn.execute("UPDATE player_market_stats SET hits = ?, hit_rate = ?, avg = ?, median = ?, avg_margin = ?, median_margin = ?, sample_size = ?, recent_results = ?, game_log = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (result['hits'], result['hit_rate'], result['avg'], result['median'], result['avg_margin'], result['median_margin'], result['sample_size'], json.dumps(result['recent_results']), json.dumps(result['game_log']), existing[0]))
            else:
                conn.execute("INSERT INTO player_market_stats (player_id, player_name, team, sport, market, segment, line, side, sample_size, hits, hit_rate, avg, median, avg_margin, median_margin, recent_results, game_log) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (result['player_id'], result['player_name'], result['team'], result['sport'], result['market'], result['segment'], result['line'], result['side'], result['sample_size'], result['hits'], result['hit_rate'], result['avg'], result['median'], result['avg_margin'], result['median_margin'], json.dumps(result['recent_results']), json.dumps(result['game_log'])))
        conn.commit()
        conn.close()
    
    def serve_today_players_count(self):
        from urllib.parse import urlparse, parse_qs
        league = (parse_qs(urlparse(self.path).query).get('league', ['NBA'])[0] or 'NBA').upper()
        if league not in ('NBA', 'NFL'):
            league = 'NBA'
        try:
            with open('/var/lib/liveintel/daily_players_report.json') as f:
                rep = json.load(f)
        except Exception:
            rep = {}
        r = rep.get(league, {})
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({
            'league': league,
            'today_games': r.get('games', 0),
            'today_players': r.get('players', 0),
            'linked_to_history': r.get('linked', 0),
            'unlinked_players': r.get('unlinked', 0),
            'sport_identity_errors': r.get('identity_errors', 0),
            'generated_at': rep.get('generated_at', '')
        }).encode())
    
    def save_ticket(self):
        import json
        from urllib.parse import parse_qs
        
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length).decode('utf-8')
        params = parse_qs(post_data)
        
        # Extract single values from lists
        data = {k: v[0] if v else '' for k, v in params.items()}
        
        conn = get_db()
        try:
            # Insert ticket
            cursor = conn.execute("""
                INSERT INTO tickets (owner_name, sportsbook_platform, wager_amount, potential_payout, 
                                     ticket_type, environment, status, date_time_placed, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)
            """, (
                data.get('owner_name', 'Owner'),
                data.get('sportsbook_platform', 'Unknown'),
                float(data.get('wager_amount', 0)),
                float(data.get('potential_payout', 0)),
                'PARLAY' if data.get('leg_2_player') else 'SINGLE',
                data.get('environment', 'REAL'),
                'PENDING',
                data.get('notes') or None
            ))
            ticket_id = cursor.lastrowid
            
            # Insert legs
            leg_num = 1
            while True:
                player_key = f'leg_{leg_num}_player'
                if not data.get(player_key):
                    break
                    
                market = data.get(f'leg_{leg_num}_market', '')
                line_str = data.get(f'leg_{leg_num}_line', '')
                line = float(line_str) if line_str else None
                side = data.get(f'leg_{leg_num}_side', 'MORE')
                odds_str = data.get(f'leg_{leg_num}_odds', '')
                odds = float(odds_str) if odds_str else None
                sport = data.get(f'leg_{leg_num}_sport', data.get('sport', 'NFL'))
                game_id = data.get(f'leg_{leg_num}_game_id', data.get('game_id', ''))
                
                conn.execute("""
                    INSERT INTO ticket_legs (ticket_id, sport, game_id, player, market, line, side, odds, leg_status, player_id, team)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (ticket_id, sport, game_id, data.get(player_key, ''), market, line, side, odds, 'PENDING',
                      data.get(f'leg_{leg_num}_player_id') or None, data.get(f'leg_{leg_num}_team') or None))
                
                leg_num += 1
            
            conn.commit()
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': True, 'ticket_id': ticket_id}).encode())
        except Exception as e:
            conn.rollback()
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': False, 'error': str(e)}).encode())
        finally:
            conn.close()
    
    def get_player_best_market(self):
        import sqlite3
        import json
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        data = json.loads(body.decode())
        player_id = data.get('player_id', '')
        if not player_id:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'player_id required'}).encode())
            return
        conn = sqlite3.connect('/var/lib/liveintel/intel.db', timeout=30.0)
        conn.execute('PRAGMA busy_timeout = 30000')
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM player_market_stats WHERE player_id = ? ORDER BY hit_rate DESC, sample_size DESC", (player_id,)).fetchall()
        conn.close()
        if not rows:
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'player_id': player_id, 'best_market': None, 'alternatives': [], 'reason': 'No market data available'}).encode())
            return
        market_stats = [dict(row) for row in rows]
        market_stats.sort(key=lambda x: (x.get('hit_rate', 0), x.get('sample_size', 0)), reverse=True)
        best = market_stats[0] if market_stats else None
        alternatives = market_stats[1:3] if len(market_stats) > 1 else []
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'player_id': player_id, 'best_market': best, 'alternatives': alternatives, 'all_markets': market_stats}).encode())
    
    def serve_raw_data(self):
        conn = get_db()
        player_stats = conn.execute("""SELECT player_name, team, segment, value 
                                      FROM player_segment_stats 
                                      ORDER BY last_updated DESC LIMIT 10""").fetchall()
        team_situation = conn.execute("""SELECT * FROM team_situation_stats LIMIT 10""").fetchall()
        player_opportunity = conn.execute("""SELECT player_name, targets, carries FROM player_opportunity LIMIT 10""").fetchall()
        conn.close()
        
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({
            'player_stats': [dict(r) for r in player_stats],
            'team_situation': [dict(r) for r in team_situation],
            'player_opportunity': [dict(r) for r in player_opportunity]
        }).encode())
    
    def serve_tickets(self):
        conn = get_db()
        
        # Get OWNER tickets
        owner_rows = conn.execute("""SELECT owner_name, wager_amount, potential_payout, status, environment, id
                                   FROM tickets 
                                   ORDER BY created_at DESC LIMIT 20""").fetchall()
        
        # Get AI bankrolls and picks
        basketball_bankroll = conn.execute("""SELECT * FROM basketball_ai_bankroll ORDER BY updated_at DESC LIMIT 1""").fetchone()
        basketball_picks = conn.execute("""SELECT game_id, player_name, market, line, side, book, result, profit_loss, pick_time 
                                           FROM basketball_ai_picks 
                                           ORDER BY pick_time DESC LIMIT 10""").fetchall()
        
        nfl_bankroll = conn.execute("""SELECT * FROM nfl_ai_bankroll ORDER BY updated_at DESC LIMIT 1""").fetchone()
        nfl_picks = conn.execute("""SELECT game_id, player_name, market, line, side, book, result, profit_loss, pick_time 
                                   FROM nfl_ai_picks 
                                   ORDER BY pick_time DESC LIMIT 10""").fetchall()
        
        cfb_bankroll = conn.execute("""SELECT * FROM cfb_ai_bankroll ORDER BY updated_at DESC LIMIT 1""").fetchone()
        cfb_picks = conn.execute("""SELECT game_id, player_name, market, line, side, book, result, profit_loss, pick_time 
                                   FROM cfb_ai_picks 
                                   ORDER BY pick_time DESC LIMIT 10""").fetchall()
        
        conn.close()
        
        # Build AI sections with bankroll and picks
        def build_ai_section(ai_name, bankroll, picks):
            bankroll_dict = dict(bankroll) if bankroll else {}
            current = bankroll_dict.get('current_bankroll', 100.0) or 100.0
            starting = bankroll_dict.get('starting_bankroll', 100.0) or 100.0
            wagered = bankroll_dict.get('total_wagered', 0) or 0
            returned = bankroll_dict.get('total_returned', 0) or 0
            roi = bankroll_dict.get('roi', 0) or 0
            wins = bankroll_dict.get('wins', 0) or 0
            losses = bankroll_dict.get('losses', 0) or 0
            
            picks_list = [dict(p) for p in picks]
            last_result = None
            if picks_list:
                for p in picks_list:
                    if p.get('result') in ('WIN', 'LOSS'):
                        last_result = {
                            'result': p['result'],
                            'profit_loss': p.get('profit_loss', 0),
                            'player': p.get('player_name', ''),
                            'market': p.get('market', ''),
                            'line': p.get('line', 0),
                            'side': p.get('side', '')
                        }
                        break
            
            progress = (current / 10000 * 100) if current > 0 else 0
            
            return {
                'name': ai_name,
                'current_bankroll': current,
                'starting_bankroll': starting,
                'total_wagered': wagered,
                'total_returned': returned,
                'roi': roi,
                'wins': wins,
                'losses': losses,
                'progress_pct': progress,
                'goal': 10000,
                'picks': picks_list,
                'last_result': last_result,
                'has_picks': len(picks_list) > 0
            }
        
        basketball_section = build_ai_section('BASKETBALL_AI', basketball_bankroll, basketball_picks)
        nfl_section = build_ai_section('NFL_AI', nfl_bankroll, nfl_picks)
        cfb_section = build_ai_section('CFB_AI', cfb_bankroll, cfb_picks)
        
        # Also include owner tickets for backward compat
        all_tickets = [dict(r) for r in owner_rows]
        all_tickets.extend([{'owner_name': 'BASKETBALL_AI', **dict(p)} for p in basketball_picks])
        all_tickets.extend([{'owner_name': 'NFL_AI', **dict(p)} for p in nfl_picks])
        all_tickets.extend([{'owner_name': 'CFB_AI', **dict(p)} for p in cfb_picks])
        
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({
            'tickets': all_tickets,
            'ai_sections': {
                'BASKETBALL_AI': basketball_section,
                'NFL_AI': nfl_section,
                'CFB_AI': cfb_section
            }
        }).encode())

    def serve_bankroll(self):
        conn = get_db()
        
        # Get OWNER bankroll accounts
        owner_rows = conn.execute("""SELECT owner_name, starting_bankroll, current_bankroll, total_wagered, total_returned, environment
                                     FROM bankroll_accounts 
                                     ORDER BY updated_at DESC LIMIT 10""").fetchall()
        
        # Get AI bankrolls
        basketball_ai = conn.execute("""SELECT 'BASKETBALL_AI' as owner_name, starting_bankroll, current_bankroll, total_wagered, total_returned, 'AI_TEST' as environment
                                        FROM basketball_ai_bankroll ORDER BY updated_at DESC LIMIT 1""").fetchall()
        
        nfl_ai = conn.execute("""SELECT 'NFL_AI' as owner_name, starting_bankroll, current_bankroll, total_wagered, total_returned, 'AI_TEST' as environment
                                FROM nfl_ai_bankroll ORDER BY updated_at DESC LIMIT 1""").fetchall()
        
        cfb_ai = conn.execute("""SELECT 'CFB_AI' as owner_name, starting_bankroll, current_bankroll, total_wagered, total_returned, 'AI_TEST' as environment
                                FROM cfb_ai_bankroll ORDER BY updated_at DESC LIMIT 1""").fetchall()
        
        conn.close()
        
        all_rows = list(owner_rows) + list(basketball_ai) + list(nfl_ai) + list(cfb_ai)
        
        accounts = []
        for row in all_rows:
            account = dict(row)
            account['current_balance'] = account.pop('current_bankroll', 0)
            starting = account.get('starting_bankroll', 0) or 0
            current = account.get('current_balance', 0) or 0
            account['roi'] = round(((current - starting) / starting * 100) if starting > 0 else 0, 2)
            accounts.append(account)
        
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'accounts': accounts}).encode())

    def serve_search_player(self):
        search_term = urllib.parse.unquote(self.path.replace('/api/search-player?', '').replace('/api/search-player', ''))
        
        if not search_term:
            search_term = ''
        
        conn = get_db()
        query = """
            SELECT DISTINCT player_name, team, position, sport, player_id
            FROM multi_sport_players
            WHERE player_name LIKE ?
            ORDER BY player_name ASC
            LIMIT 50
        """
        search_pattern = '%' + search_term + '%'
        rows = conn.execute(query, (search_pattern,)).fetchall()
        conn.close()
        
        players = []
        for row in rows:
            player = dict(row)
            players.append(player)
        
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'players': players, 'count': len(players), 'search_term': search_term}).encode())

    def auto_settle(self):
        """API endpoint to trigger auto-settlement of all pending tickets."""
        import json
        
        conn = get_db()
        settled_count = 0
        
        try:
            # Get all PENDING tickets
            pending_tickets = conn.execute("""
                SELECT id, owner_name, sportsbook_platform, wager_amount, potential_payout, 
                       ticket_type, environment, status
                FROM tickets 
                WHERE status = 'PENDING'
            """).fetchall()
            
            for ticket in pending_tickets:
                ticket_id = ticket['id']
                ticket_status, actual_payout = settle_ticket_legs(ticket_id, conn)
                
                if ticket_status != 'PENDING':
                    # Update ticket
                    conn.execute("""
                        UPDATE tickets 
                        SET status = ?, actual_payout = ?, settled_at = datetime('now')
                        WHERE id = ?
                    """, (ticket_status, actual_payout, ticket_id))
                    
                    # Update bankroll
                    update_owner_bankroll(
                        ticket['owner_name'], 
                        ticket['environment'], 
                        ticket['wager_amount'], 
                        actual_payout, 
                        ticket_status, 
                        conn
                    )
                    
                    # Update AI bankrolls for AI tickets
                    if ticket['owner_name'] in ['BASKETBALL_AI', 'NFL_AI', 'CFB_AI']:
                        update_ai_bankroll(
                            ticket['owner_name'],
                            ticket['owner_name'],
                            ticket['wager_amount'],
                            actual_payout,
                            ticket_status,
                            conn
                        )
                        
                        # Update AI pick results
                        legs = conn.execute("SELECT * FROM ticket_legs WHERE ticket_id = ?", (ticket_id,)).fetchall()
                        for leg in legs:
                            wager_per_leg = ticket['wager_amount'] / len(legs) if legs else ticket['wager_amount']
                            update_ai_pick_result(
                                ticket['owner_name'],
                                leg['game_id'],
                                leg['player'],
                                leg['market'],
                                leg['line'],
                                leg['side'],
                                leg['leg_status'],
                                leg['current_value'],
                                wager_per_leg,
                                conn
                            )
                    
                    settled_count += 1
            
            # Also settle AI picks that aren't linked to tickets
            # Legacy picks lack provenance and are excluded from settlement.
            
            conn.commit()
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': True, 'settled_count': settled_count}).encode())
            
        except Exception as e:
            conn.rollback()
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': False, 'error': str(e)}).encode())
        finally:
            conn.close()


    def do_POST(self):
        if self.path == '/api/manual-offer':
            conn = None
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length <= 0 or length > 65536:
                    raise ValueError('Offer body must be 1–65536 bytes')
                data = json.loads(self.rfile.read(length))
                from manual_offers import save_offer_entry
                conn = sqlite3.connect(DB_PATH, timeout=30)
                conn.row_factory = sqlite3.Row
                result = save_offer_entry(conn, data)
                status = 201 if result['saved'] else 422
            except (ValueError, TypeError) as exc:
                result = {'status':'REJECTED','offer_id':None,'ai_id':None,'saved':False,'reason':str(exc),'tickets_created':0}
                status = 422
            except Exception:
                result = {'status':'REJECTED','offer_id':None,'ai_id':None,'saved':False,'reason':'Offer could not be saved; no valid offer was created','tickets_created':0}
                status = 500
            finally:
                if conn is not None:
                    if conn.in_transaction: conn.rollback()
                    conn.close()
            self.send_response(status)
            self.send_header('Content-type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())
            return
        if self.path.startswith('/api/'):
            self.handle_api()
        elif self.path == '/recalc':
            self.handle_recalc()
        elif self.path == '/add_prop':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            fields = urllib.parse.parse_qs(post_data.decode())
            
            conn = get_db()
            cur = conn.cursor()
            cur.execute("INSERT INTO prop_lines (game_id, player_name, market, line, side, book, observed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (fields['game_id'][0], fields['player'][0], fields['market'][0], 
                           float(fields['line'][0]), fields['side'][0], fields['book'][0], datetime.datetime.now()))
            prop_id = cur.lastrowid
            conn.commit()
            
            game_id = fields['game_id'][0]
            player = fields['player'][0]
            market = fields['market'][0]
            line = float(fields['line'][0])
            side = fields['side'][0]
            
            player_row = conn.execute("""
                SELECT DISTINCT player_id, team FROM player_segment_stats 
                WHERE game_id = ? AND player_name = ? AND segment = 'FULL GAME' LIMIT 1
            """, (game_id, player)).fetchone()
            
            if player_row:
                player_id = player_row['player_id']
                team = player_row['team']
                
                stat_type_map = {
                    'Passing Yards': 'passing_yards',
                    'Rushing Yards': 'rushing_yards',
                    'Receiving Yards': 'receiving_yards',
                    'Kicking Points': 'kicking_points',
                }
                stat_type = stat_type_map.get(market, 'passing_yards')
                
                stat_row = conn.execute("""
                    SELECT value FROM player_segment_stats
                    WHERE game_id = ? AND player_id = ? AND segment = 'FULL GAME' AND stat_type = ?
                """, (game_id, player_id, stat_type)).fetchone()
                
                current_val = stat_row['value'] if stat_row else 0
                
                play_row = conn.execute("""
                    SELECT period, clock FROM plays 
                    WHERE game_id = ? ORDER BY event_timestamp DESC LIMIT 1
                """, (game_id,)).fetchone()
                
                period = play_row['period'] if play_row else '?'
                clock = play_row['clock'] if play_row else '?'
                
                proj = calculate_projections(prop_id, current_val, line, side, game_id, player, player_id, team, stat_type, period, clock)
                store_projection(prop_id, proj)
            
            conn.close()
            
            self.send_response(303)
            self.send_header('Location', '/')
            self.end_headers()

    def handle_recalc(self):
        conn = get_db()
        props = conn.execute('SELECT id, game_id, player_name, market, line, side FROM prop_lines').fetchall()
        conn.close()
        
        for p in props:
            prop_id, game_id, player, market, line, side = p
            
            conn2 = get_db()
            player_row = conn2.execute('''
                SELECT DISTINCT player_id, team FROM player_segment_stats 
                WHERE game_id = ? AND player_name = ? AND segment = 'FULL GAME' LIMIT 1
            ''', (game_id, player)).fetchone()
            conn2.close()
            
            if not player_row:
                continue
            player_id = player_row['player_id']
            team = player_row['team']
            
            stat_type_map = {
                'Passing Yards': 'passing_yards',
                'Rushing Yards': 'rushing_yards',
                'Receiving Yards': 'receiving_yards',
                'Kicking Points': 'kicking_points',
            }
            stat_type = stat_type_map.get(market, 'passing_yards')
            
            conn3 = get_db()
            stat_row = conn3.execute('''
                SELECT value FROM player_segment_stats
                WHERE game_id = ? AND player_id = ? AND segment = 'FULL GAME' AND stat_type = ?
            ''', (game_id, player_id, stat_type)).fetchone()
            conn3.close()
            
            current_val = stat_row['value'] if stat_row else 0
            
            conn4 = get_db()
            play_row = conn4.execute('''
                SELECT period, clock FROM plays 
                WHERE game_id = ? ORDER BY event_timestamp DESC LIMIT 1
            ''', (game_id,)).fetchone()
            conn4.close()
            
            period = play_row['period'] if play_row else '?'
            clock = play_row['clock'] if play_row else '?'
            
            proj = calculate_projections(prop_id, current_val, line, side, game_id, player, player_id, team, stat_type, period, clock)
            # Update projection in DB
            conn5 = get_db()
            conn5.execute("""
                INSERT INTO prop_projections 
                (prop_id, current_value, remaining_needed, projected_low, projected_median, projected_high,
                 signal, confidence, reasons, historical_median, historical_75th, historical_90th, 
                 historical_sample, calculated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (prop_id, proj['current_value'], proj.get('diff', 0), 
                  proj['projected_low'], proj['projected_median'], proj['projected_high'], 
                  proj['signal'], proj['confidence'], proj['reasons'], 
                  proj['historical_median'], proj['historical_75th'], proj['historical_90th'], 
                  proj['historical_sample'], datetime.datetime.now()))
            conn5.commit()
            conn5.close()
        
        self.send_response(303)
        self.send_header('Location', '/')
        self.end_headers()

def run():
    server = HTTPServer(('0.0.0.0', 8111), DashboardHandler)
    print('Dashboard running on port 8111...')
    server.serve_forever()

if __name__ == '__main__':
    run()
