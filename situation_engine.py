import re
import json
from collections import defaultdict
from datetime import datetime

TEAM_ID_MAP = {
    '1': 'ATL', '2': 'BUF', '3': 'CHI', '4': 'CIN', '5': 'CLE', '6': 'DAL',
    '7': 'DEN', '8': 'DET', '9': 'GB', '10': 'TEN', '11': 'IND', '12': 'KC',
    '13': 'LV', '14': 'LAR', '15': 'MIA', '16': 'MIN', '17': 'NE', '17': 'NYG',
    '18': 'NYJ', '19': 'PHI', '20': 'ARI', '21': 'PIT', '22': 'LAC', '23': 'SF',
    '24': 'SEA', '25': 'TB', '26': 'WSH', '26': 'WAS', '28': 'JAC', '29': 'CAR',
    '30': 'NO', '33': 'BAL', '34': 'HOU'
}

class GameState:
    def __init__(self, game_id):
        self.game_id = game_id
        self.period = 1
        self.clock = "15:00"
        self.away_score = 0
        self.home_score = 0
        self.away_team = ""
        self.home_team = ""
        self.away_team_id = ""
        self.home_team_id = ""
        self.possession = ""
        self.down = 1
        self.distance = 10
        self.yard_line = 50
        self.drive_number = 0
        self.drive_start = None
        self.drive_plays = 0
        self.drive_yards = 0
        self.red_zone = False
        self.goal_to_go = False
        self.fg_range = False
        self.timeouts_away = 3
        self.timeouts_home = 3
        self.last_play_time = None
        
        # Run/Pass tracking
        self.team_rush_plays = defaultdict(int)
        self.team_pass_plays = defaultdict(int)
        self.team_total_plays = defaultdict(int)
        self.team_rush_yards = defaultdict(int)
        self.team_pass_yards = defaultdict(int)
        
        # Per quarter
        self.quarter_rush = defaultdict(lambda: defaultdict(int))
        self.quarter_pass = defaultdict(lambda: defaultdict(int))
        
        # Red zone
        self.red_zone_entries = defaultdict(int)
        self.red_zone_plays = defaultdict(int)
        self.red_zone_td = defaultdict(int)
        self.red_zone_fg = defaultdict(int)
        self.goal_to_go = defaultdict(int)
        
        # Kicker
        self.fg_attempts = defaultdict(int)
        self.fg_makes = defaultdict(int)
        self.fg_misses = defaultdict(int)
        self.pat_attempts = defaultdict(int)
        self.pat_makes = defaultdict(int)
        self.fg_range_entries = defaultdict(int)
        self.drive_stalls_fg_range = defaultdict(int)
        
        # 2-minute drill
        self.two_minute_plays = []
        self.last_5_plays = []
        self.last_2_plays = []
        self.last_1_plays = []
        
        # Player opportunity
        self.player_targets = defaultdict(int)
        self.player_carries = defaultdict(int)
        self.player_pass_att = defaultdict(int)
        self.player_rush_att = defaultdict(int)
        self.player_rz_targets = defaultdict(int)
        self.player_rz_carries = defaultdict(int)
        self.player_rz_touch = defaultdict(int)
        
        # Game script
        self.leading_team_id = None
        self.trailing_team_id = None
        self.leading_team = None
        self.trailing_team = None
        self.score_diff = 0
        self.script = "TIED + NORMAL"
        
    def get_quarterly_rates(self):
        return {q: {
            'rush': self.quarter_rush[q],
            'pass': self.quarter_pass[q]
        } for q in range(1, 5)}
        
    def update_score(self, away, home):
        self.away_score = away
        self.home_score = home
        self.score_diff = home - away
        self._update_game_script()
        
    def _update_game_script(self):
        if self.score_diff > 0:
            self.leading_team_id = self.home_team_id
            self.trailing_team_id = self.away_team_id
            self.leading_team = self.home_team
            self.trailing_team = self.away_team
            diff = self.score_diff
        elif self.score_diff < 0:
            self.leading_team_id = self.away_team_id
            self.trailing_team_id = self.home_team_id
            self.leading_team = self.away_team
            self.trailing_team = self.home_team
            diff = abs(self.score_diff)
        else:
            self.leading_team_id = None
            self.trailing_team_id = None
            self.leading_team = None
            self.trailing_team = None
            diff = 0
            
        if diff == 0:
            magnitude = "TIED"
        elif diff <= 8:
            magnitude = "ONE-SCORE"
        elif diff <= 16:
            magnitude = "TWO-SCORE"
        else:
            magnitude = "BLOWOUT"
            
        if diff == 0:
            self.script = f"TIED + NORMAL"
        elif self.leading_team_id:
            leading_abbr = get_team_abbr(self.leading_team_id)
            if self._period_to_int() == 4 and self._clock_to_seconds() < 300:
                self.script = f"{leading_abbr} LEADING + KILL CLOCK"
            elif self._period_to_int() == 4 and self._clock_to_seconds() < 120:
                self.script = f"{leading_abbr} LEADING + END-GAME DESPERATION"
            elif magnitude == "BLOWOUT":
                self.script = f"{leading_abbr} LEADING + KILL CLOCK"
            else:
                self.script = f"{leading_abbr} LEADING + NORMAL"
    
    def _period_to_int(self):
        try:
            return int(self.period)
        except:
            return 1

    def _clock_to_seconds(self):
        try:
            m, s = self.clock.split(':')
            return int(m) * 60 + int(s)
        except:
            return 900
            
    def update_from_play(self, play):
        self.period = play.get('period', self.period)
        self.clock = play.get('clock', self.clock)
        
        # Parse score from score_after JSON string FIRST
        score_after = play.get('score_after')
        if isinstance(score_after, str):
            try:
                score_data = json.loads(score_after)
                self.away_score = score_data.get('away', self.away_score)
                self.home_score = score_data.get('home', self.home_score)
            except:
                pass
        else:
            self.away_score = play.get('away_score', self.away_score)
            self.home_score = play.get('home_score', self.home_score)
        
        self.update_score(self.away_score, self.home_score)
        
        start = play.get('start', {})
        end = play.get('end', {})
        
        if start.get('down'):
            self.down = start['down']
        if start.get('distance'):
            self.distance = start['distance']
        if start.get('yardLine'):
            self.yard_line = start['yardLine']
            
        # Possession from start.team
        if start.get('team', {}).get('id'):
            team_id = start['team']['id']
            self.possession = str(team_id)
            
        # Red zone / goal to go / fg range
        ytl = start.get('yardsToEndzone', 100)
        if ytl <= 20:
            self.red_zone = True
        else:
            self.red_zone = False
        if ytl <= 10:
            self.goal_to_go = True
        else:
            self.goal_to_go = False
        if ytl <= 35:
            self.fg_range = True
        else:
            self.fg_range = False
            
        # Drive tracking
        drive_id = play.get('drive_id')
        if drive_id and drive_id != self.drive_start:
            self.drive_start = drive_id
            self.drive_number += 1
            self.drive_plays = 0
            self.drive_yards = 0
        self.drive_plays += 1
        self.drive_yards += play.get('yards', 0) or 0
        
        # Timeouts
        desc = play.get('description', '').lower()
        if 'timeout' in desc:
            # Hard to parse which team, skip for now
            pass
            
        # Last play time
        self.last_play_time = play.get('wallclock')
        
        # Play type tracking
        possession_team = self.possession
        play_type = play.get('play_type', '')
        yards = play.get('yards', 0) or 0
        
        if possession_team:
            self.team_total_plays[possession_team] += 1
            if 'pass' in play_type.lower():
                self.team_pass_plays[possession_team] += 1
                self.team_pass_yards[possession_team] += yards
                self.quarter_pass[self._period_to_int()][possession_team] += 1
            elif 'rush' in play_type.lower():
                self.team_rush_plays[possession_team] += 1
                self.team_rush_yards[possession_team] += yards
                self.quarter_rush[self._period_to_int()][possession_team] += 1
                
        # Red zone tracking
        if self.red_zone:
            self.red_zone_plays[possession_team] += 1
            if self.drive_plays == 1:
                self.red_zone_entries[possession_team] += 1
            # Check for TD/FG in red zone
            if play.get('score_after'):
                # TD scored in red zone
                pass
                
        # Kicker tracking
        if 'field goal' in play.get('play_type', '').lower() or 'fg' in play.get('description', '').lower():
            self.fg_attempts[possession_team] += 1
            if 'good' in play.get('description', '').lower():
                self.fg_makes[possession_team] += 1
                if self.fg_range:
                    self.fg_range_entries[possession_team] += 1
            else:
                self.fg_misses[possession_team] += 1
                
        if 'extra point' in play.get('play_type', '').lower() or 'pat' in play.get('description', '').lower():
            self.pat_attempts[possession_team] += 1
            if 'good' in play.get('description', '').lower():
                self.pat_makes[possession_team] += 1
                
        # 2-minute drill tracking
        clock_sec = self._clock_to_seconds()
        if self._period_to_int() in (2, 4) and clock_sec <= 120:
            self.two_minute_plays.append(play)
        if clock_sec <= 300:
            self.last_5_plays.append(play)
        if clock_sec <= 120:
            self.last_2_plays.append(play)
        if clock_sec <= 60:
            self.last_1_plays.append(play)
            
        # Player opportunity
        participants = play.get('participants', [])
        if isinstance(participants, str):
            try:
                participants = json.loads(participants)
            except:
                participants = []
        for p in participants:
            athlete = p.get('athlete', {})
            athlete_id = athlete.get('id')
            athlete_name = athlete.get('displayName')
            p_type = p.get('type')
            
            if athlete_id:
                if p_type in ('rusher', 'passer'):
                    self.player_carries[athlete_id] += 1
                    self.player_rush_att[athlete_id] += 1
                    if self.red_zone:
                        self.player_rz_carries[athlete_id] += 1
                        self.player_rz_touch[athlete_id] += 1
                elif p_type in ('receiver', 'passer'):
                    self.player_targets[athlete_id] += 1
                    self.player_pass_att[athlete_id] += 1
                    if self.red_zone:
                        self.player_rz_targets[athlete_id] += 1
                        self.player_rz_touch[athlete_id] += 1
                        
    def get_run_pass_rate(self, team_id):
        total = self.team_total_plays[team_id]
        if total == 0:
            return 0, 0
        rush = self.team_rush_plays[team_id] / total
        pass_r = self.team_pass_plays[team_id] / total
        return rush, pass_r
        
    def get_quarter_run_pass(self, team_id):
        result = {}
        for q in range(1, 5):
            total = self.quarter_rush[q][team_id] + self.quarter_pass[q][team_id]
            if total > 0:
                result[q] = {
                    'rush_pct': self.quarter_rush[q][team_id] / total,
                    'pass_pct': self.quarter_pass[q][team_id] / total,
                    'rush': self.quarter_rush[q][team_id],
                    'pass': self.quarter_pass[q][team_id]
                }
        return result
        
    def get_situation_summary(self):
        """Return current situation for dashboard"""
        leading = self.leading_team
        trailing = self.trailing_team
        
        situation = {
            'period': self.period,
            'clock': self.clock,
            'score': f"{self.away_team} {self.away_score} - {self.home_score} {self.home_team}",
            'score_diff': self.score_diff,
            'possession': self.possession,
            'down': self.down,
            'distance': self.distance,
            'yard_line': self.yard_line,
            'red_zone': self.red_zone,
            'goal_to_go': self.goal_to_go,
            'fg_range': self.fg_range,
            'drive_number': self.drive_number,
            'drive_plays': self.drive_plays,
            'drive_yards': self.drive_yards,
            'game_script': self.script,
            'leading_team': leading,
            'trailing_team': trailing,
        }
        
        # Run/pass rates
        for team_id in [self.away_team, self.home_team]:
            if team_id:
                rush_pct, pass_pct = self.get_run_pass_rate(team_id)
                situation[f'{team_id}_rush_pct'] = round(rush_pct * 100, 1)
                situation[f'{team_id}_pass_pct'] = round(pass_pct * 100, 1)
                
        return situation


# Team ID to abbreviation mapping (ESPN IDs)
def get_team_abbr(team_id):
    return TEAM_ID_MAP.get(str(team_id), str(team_id))


def parse_play_text_for_situation(play_text):
    """Parse play text for additional situation info"""
    info = {}
    if not play_text:
        return info
        
    # Two-minute warning
    if 'two minute warning' in play_text.lower():
        info['two_minute_warning'] = True
        
    # Spike
    if 'spiked' in play_text.lower() or 'spike' in play_text.lower():
        info['spike'] = True
        
    # No huddle
    if 'no huddle' in play_text.lower():
        info['no_huddle'] = True
        
    # Sideline / out of bounds
    if 'out of bounds' in play_text.lower() or 'ran ob' in play_text.lower():
        info['sideline'] = True
        
    # Timeout
    if 'timeout' in play_text.lower():
        info['timeout'] = True
        
    return info


def determine_situation_alerts(game_state):
    """Generate situation alerts based on current game state"""
    alerts = []
    
    if not game_state.leading_team_id:
        return alerts
        
    period = int(game_state.period) if game_state.period else 1
    clock_sec = game_state._clock_to_seconds()
    score_diff = abs(game_state.score_diff)
    leading = game_state.leading_team_id
    trailing = game_state.trailing_team_id
    
    # Kill clock
    if period == 4 and clock_sec < 300 and leading:
        rush_pct, _ = game_state.get_run_pass_rate(leading)
        if rush_pct > 0.65:
            alerts.append({
                'type': 'KILL CLOCK',
                'team': get_team_abbr(leading),
                'detail': f'Rush rate {rush_pct:.0%} in 4Q with lead'
            })
            
    # Comeback mode / passing surge
    if trailing and period >= 3 and clock_sec < 900:
        _, pass_pct = game_state.get_run_pass_rate(trailing)
        if pass_pct > 0.70:
            alerts.append({
                'type': 'PASSING SURGE',
                'team': get_team_abbr(trailing),
                'detail': f'Pass rate {pass_pct:.0%} while trailing'
            })
            
    # Red zone
    if game_state.red_zone:
        alerts.append({
            'type': 'RED ZONE',
            'team': get_team_abbr(game_state.possession),
            'detail': f'At {game_state.yard_line} yard line'
        })
        
    if game_state.goal_to_go:
        alerts.append({
            'type': 'GOAL LINE',
            'team': get_team_abbr(game_state.possession),
            'detail': 'Goal-to-go situation'
        })
        
    # Kicker watch
    if game_state.fg_range and game_state.drive_plays > 3:
        alerts.append({
            'type': 'KICKER WATCH',
            'team': get_team_abbr(game_state.possession),
            'detail': f'Drive at {game_state.yard_line}, {game_state.drive_plays} plays'
        })
        
    # 2-minute drill
    if period in (2, 4) and game_state._clock_to_seconds() <= 120:
        alerts.append({
            'type': '2-MINUTE DRILL',
            'team': get_team_abbr(game_state.possession),
            'detail': f'{game_state.clock} remaining in {game_state.period}Q'
        })
        
    # Comeback mode
    if trailing and score_diff <= 8 and period == 4:
        alerts.append({
            'type': 'COMEBACK MODE',
            'team': get_team_abbr(trailing),
            'detail': f'Down {score_diff} in 4Q'
        })
        
    # Blowout mode
    if score_diff > 16:
        alerts.append({
            'type': 'BLOWOUT MODE',
            'team': get_team_abbr(leading),
            'detail': f'Leading by {score_diff}'
        })
        
    return alerts


def get_prop_situation_effect(prop, game_state, player_game_stats, historical_segments):
    """Analyze how current game situation affects a prop"""
    if not game_state or prop.get('outcome') != 'LIVE':
        return None
        
    player_name = prop['player_name']
    market = prop['market']
    side = prop['side']
    game_id = prop['game_id']
    
    # Find player_id
    player_id = None
    for pid, stats in player_game_stats.items():
        if stats.get('name') == player_name and stats.get('game_id') == game_id:
            player_id = pid
            break
            
    if not player_id:
        return None
        
    effect = {
        'type': 'NEUTRAL',
        'detail': [],
        'confidence': 'LOW'
    }
    
    # QB passing props
    if 'Passing' in market:
        leading = game_state.leading_team_id
        trailing = game_state.trailing_team_id
        period = game_state._period_to_int()
        clock_sec = game_state._clock_to_seconds()
        
        # Team trailing late -> more passing
        if trailing and period == 4 and clock_sec < 300:
            if side == 'OVER':
                effect['type'] = 'POSITIVE'
                effect['detail'].append('Team trailing late in 4Q - passing volume likely to increase')
                effect['confidence'] = 'MEDIUM'
            elif side == 'UNDER':
                effect['type'] = 'NEGATIVE'
                effect['detail'].append('Team trailing late - passing volume increasing')
                effect['confidence'] = 'MEDIUM'
                
        # Team leading in 4Q
        if leading and period == 4:
            if side == 'UNDER':
                effect['type'] = 'POSITIVE'
                effect['detail'].append('Team leading in 4Q - rush-heavy, passing declining')
                effect['confidence'] = 'MEDIUM'
            elif side == 'OVER':
                effect['type'] = 'NEGATIVE'
                effect['detail'].append('Team leading - pass attempts likely decreasing')
                effect['confidence'] = 'MEDIUM'
                
    # Rushing props
    elif 'Rushing' in market:
        leading = game_state.leading_team_id
        period = game_state._period_to_int()
        
        if leading and period == 4:
            if side == 'OVER':
                effect['type'] = 'POSITIVE'
                effect['detail'].append('Team leading in 4Q - kill clock rushing')
                effect['confidence'] = 'MEDIUM'
            elif side == 'UNDER':
                effect['type'] = 'NEGATIVE'
                effect['detail'].append('Team leading - more rush attempts likely')
                effect['confidence'] = 'MEDIUM'
                
    # Receiving props
    elif 'Receiving' in market:
        trailing = game_state.trailing_team_id
        period = game_state._period_to_int()
        clock_sec = game_state._clock_to_seconds()
        
        if trailing and period >= 3 and clock_sec < 600:
            if side == 'OVER':
                effect['type'] = 'POSITIVE'
                effect['detail'].append('Team trailing - catch-up passing increases targets')
                effect['confidence'] = 'MEDIUM'
            elif side == 'UNDER':
                effect['type'] = 'NEGATIVE'
                effect['detail'].append('Trailing team passes more - targets increasing')
                effect['confidence'] = 'MEDIUM'
                
    # Kicker props
    elif 'Kicking' in market:
        if game_state.fg_range and game_state.drive_plays > 2:
            if side == 'OVER':
                effect['type'] = 'POSITIVE'
                effect['detail'].append('Team in FG range - kicker opportunity high')
                effect['confidence'] = 'HIGH'
            elif side == 'UNDER':
                effect['type'] = 'NEGATIVE'
                effect['detail'].append('Team in FG range - more attempts likely')
                effect['confidence'] = 'HIGH'
                
    return effect if effect['type'] != 'NEUTRAL' else None


def format_situation_for_dashboard(game_state, alerts, prop_effects):
    """Format situation data for dashboard display"""
    return {
        'game_state': game_state.get_situation_summary(),
        'alerts': alerts,
        'prop_effects': prop_effects,
        'quarterly_rates': game_state.get_quarterly_rates()
    }