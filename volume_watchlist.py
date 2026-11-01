"""Volume Watchlist - Player volume intelligence system.

Tracks high-volume players who consistently receive unusually high usage
and compares their historical volume with current live game volume.
"""
import datetime as dt
import json
import os
import sqlite3
import time
from typing import Dict, List, Optional, Any

DB_PATH = "/var/lib/liveintel/intel.db"


class VolumeWatchlistPlayer:
    """Represents a player on the volume watchlist."""

    def __init__(self,
                 player_id: str,
                 player_name: str,
                 position: str,
                 team: str = "",
                 sport: str = "nfl",
                 watch_reason: str = "",
                 date_added: dt.datetime = None):
        self.player_id = player_id
        self.player_name = player_name
        self.position = position.upper()
        self.team = team
        self.sport = sport.lower()
        self.watch_reason = watch_reason
        self.date_added = date_added or dt.datetime.now(dt.timezone.utc)
        self.active = True

        # Historical metrics
        self.last_5_games = []
        self.last_10_games = []
        self.current_season_stats = {}
        self.current_game_metrics = {}
        self.segment_usage = {}

        # Watch counters
        self.carries_rate = 0.0
        self.attempts_rate = 0.0
        self.targets_rate = 0.0
        self.receptions_rate = 0.0
        self.touches_rate = 0.0
        self.fgs_made = 0

        # Hit rates for thresholds
        self.carries_12_plus_rate = 0.0
        self.carries_15_plus_rate = 0.0
        self.carries_18_plus_rate = 0.0
        self.carries_20_plus_rate = 0.0

        self.attempts_25_plus_rate = 0.0
        self.attempts_30_plus_rate = 0.0
        self.attempts_35_plus_rate = 0.0
        self.attempts_40_plus_rate = 0.0

        self.targets_5_plus_rate = 0.0
        self.targets_7_plus_rate = 0.0
        self.targets_10_plus_rate = 0.0
        self.targets_12_plus_rate = 0.0

        self.receptions_7_plus_rate = 0.0
        self.receptions_10_plus_rate = 0.0
        self.receptions_12_plus_rate = 0.0

        self.fg_opportunity_rate = 0.0

    def to_dict(self) -> Dict:
        """Convert player to dictionary for JSON storage."""
        return {
            "player_id": self.player_id,
            "player_name": self.player_name,
            "position": self.position,
            "team": self.team,
            "sport": self.sport,
            "watch_reason": self.watch_reason,
            "date_added": self.date_added.isoformat(),
            "active": self.active,
            "last_5_games": self.last_5_games,
            "last_10_games": self.last_10_games,
            "current_season_stats": self.current_season_stats,
            "current_game_metrics": self.current_game_metrics,
            "segment_usage": self.segment_usage,
            "carries_rate": self.carries_rate,
            "attempts_rate": self.attempts_rate,
            "targets_rate": self.targets_rate,
            "receptions_rate": self.receptions_rate,
            "touches_rate": self.touches_rate,
            "fgs_made": self.fgs_made,
            "carries_12_plus_rate": self.carries_12_plus_rate,
            "carries_15_plus_rate": self.carries_15_plus_rate,
            "carries_18_plus_rate": self.carries_18_plus_rate,
            "carries_20_plus_rate": self.carries_20_plus_rate,
            "attempts_25_plus_rate": self.attempts_25_plus_rate,
            "attempts_30_plus_rate": self.attempts_30_plus_rate,
            "attempts_35_plus_rate": self.attempts_35_plus_rate,
            "attempts_40_plus_rate": self.attempts_40_plus_rate,
            "targets_5_plus_rate": self.targets_5_plus_rate,
            "targets_7_plus_rate": self.targets_7_plus_rate,
            "targets_10_plus_rate": self.targets_10_plus_rate,
            "targets_12_plus_rate": self.targets_12_plus_rate,
            "receptions_7_plus_rate": self.receptions_7_plus_rate,
            "receptions_10_plus_rate": self.receptions_10_plus_rate,
            "receptions_12_plus_rate": self.receptions_12_plus_rate,
            "fg_opportunity_rate": self.fg_opportunity_rate,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'VolumeWatchlistPlayer':
        """Create a player from dictionary data."""
        player = cls(
            player_id=data.get("player_id", ""),
            player_name=data.get("player_name", ""),
            position=data.get("position", ""),
            team=data.get("team", ""),
            sport=data.get("sport", "nfl"),
            watch_reason=data.get("watch_reason", ""),
            date_added=dt.datetime.fromisoformat(data.get("date_added", dt.datetime.now(dt.timezone.utc).isoformat())),
        )
        player.active = data.get("active", True)
        player.last_5_games = data.get("last_5_games", [])
        player.last_10_games = data.get("last_10_games", [])
        player.current_season_stats = data.get("current_season_stats", {})
        player.current_game_metrics = data.get("current_game_metrics", {})
        player.segment_usage = data.get("segment_usage", {})
        player.carries_rate = data.get("carries_rate", 0.0)
        player.attempts_rate = data.get("attempts_rate", 0.0)
        player.targets_rate = data.get("targets_rate", 0.0)
        player.receptions_rate = data.get("receptions_rate", 0.0)
        player.touches_rate = data.get("touches_rate", 0.0)
        player.fgs_made = data.get("fgs_made", 0)
        player.carries_12_plus_rate = data.get("carries_12_plus_rate", 0.0)
        player.carries_15_plus_rate = data.get("carries_15_plus_rate", 0.0)
        player.carries_18_plus_rate = data.get("carries_18_plus_rate", 0.0)
        player.carries_20_plus_rate = data.get("carries_20_plus_rate", 0.0)
        player.attempts_25_plus_rate = data.get("attempts_25_plus_rate", 0.0)
        player.attempts_30_plus_rate = data.get("attempts_30_plus_rate", 0.0)
        player.attempts_35_plus_rate = data.get("attempts_35_plus_rate", 0.0)
        player.attempts_40_plus_rate = data.get("attempts_40_plus_rate", 0.0)
        player.targets_5_plus_rate = data.get("targets_5_plus_rate", 0.0)
        player.targets_7_plus_rate = data.get("targets_7_plus_rate", 0.0)
        player.targets_10_plus_rate = data.get("targets_10_plus_rate", 0.0)
        player.targets_12_plus_rate = data.get("targets_12_plus_rate", 0.0)
        player.receptions_7_plus_rate = data.get("receptions_7_plus_rate", 0.0)
        player.receptions_10_plus_rate = data.get("receptions_10_plus_rate", 0.0)
        player.receptions_12_plus_rate = data.get("receptions_12_plus_rate", 0.0)
        player.fg_opportunity_rate = data.get("fg_opportunity_rate", 0.0)
        return player


class VolumeWatchlistManager:
    """Manages the volume watchlist with SQLite persistence."""

    def __init__(self):
        self.watchlist_file = "/opt/liveintel/data/watchlist.json"

    def load(self) -> Dict:
        """Load watchlist data from JSON file."""
        try:
            with open(self.watchlist_file) as f:
                self.watchlist_data = json.load(f)
                return self.watchlist_data
        except (OSError, ValueError):
            self.watchlist_data = {"players": {}, "next_id": 1000}
            return self.watchlist_data

    def save(self) -> bool:
        """Save watchlist data to JSON file."""
        os.makedirs(os.path.dirname(self.watchlist_file), exist_ok=True)
        with open(self.watchlist_file, "w") as f:
            json.dump(self.watchlist_data, f, default=str, indent=2)
        return True

    def get_all_players(self) -> List[VolumeWatchlistPlayer]:
        """Get all active players from watchlist."""
        players = []
        for player_id, player_data in self.watchlist_data.get("players", {}).items():
            if player_data.get("active", True):
                player = VolumeWatchlistPlayer.from_dict(player_data)
                players.append(player)
        return players

    def add_player(self,
                   player_id: str,
                   player_name: str,
                   position: str,
                   team: str = "",
                   sport: str = "nfl",
                   watch_reason: str = "") -> VolumeWatchlistPlayer:
        """Add a player to the watchlist."""
        self.watchlist_data = self.load()

        if player_id not in self.watchlist_data.get("players", {}):
            player = VolumeWatchlistPlayer(
                player_id=player_id,
                player_name=player_name,
                position=position,
                team=team,
                sport=sport,
                watch_reason=watch_reason,
            )
            self.watchlist_data.get("players", {})[player_id] = player.to_dict()
            self.save()
            return player
        return self.get_player(player_id)

    def update_player(self, player_id: str, **kwargs) -> bool:
        """Update a player's information."""
        self.watchlist_data = self.load()
        players = self.watchlist_data.get("players", {})
        if player_id not in players:
            return False

        for key, value in kwargs.items():
            players[player_id][key] = value

        self.save()
        return True

    def remove_player(self, player_id: str) -> bool:
        """Remove a player from watchlist (mark inactive instead of deleting)."""
        return self.update_player(player_id, active=False)

    def get_players_by_position(self, position: str) -> List[VolumeWatchlistPlayer]:
        """Get players filtered by position."""
        all_players = self.get_all_players()
        return [p for p in all_players if p.position == position.upper()]

    def get_player(self, player_id: str) -> Optional[VolumeWatchlistPlayer]:
        """Get a specific player by ID."""
        self.watchlist_data = self.load()
        players = self.watchlist_data.get("players", {})
        if player_id not in players:
            return None

        player_data = players[player_id]
        if not player_data.get("active", True):
            return None

        return VolumeWatchlistPlayer.from_dict(player_data)


def analyze_player_volume_from_db(player_name: str, position: str) -> Dict:
    """
    Analyze player stats from SQLite database to determine if they qualify for volume watchlist.

    Returns hit rates and watch reason for the player.
    """
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA busy_timeout = 30000")

    result = {}

    if position == "QB":
        rows = conn.execute("""
            SELECT COALESCE(SUM(CASE WHEN stat_type='passing_yards' THEN value ELSE 0 END), 0) as pass_attempts,
                   COALESCE(SUM(CASE WHEN stat_type='rushing_yards' THEN value ELSE 0 END), 0) as rush_attempts
            FROM player_segment_stats
            WHERE player_name = ? AND segment = 'FULL GAME'
        """, (player_name,)).fetchone()

        pass_attempts = rows[0] if rows else 0
        rush_attempts = rows[1] if rows else 0

        if pass_attempts > 0:
            result["attempts_rate"] = pass_attempts / 10.0

            if pass_attempts >= 40:
                result["attempts_40_plus_rate"] = 1.0
            elif pass_attempts >= 35:
                result["attempts_35_plus_rate"] = 1.0
            elif pass_attempts >= 30:
                result["attempts_30_plus_rate"] = 1.0
            elif pass_attempts >= 25:
                result["attempts_25_plus_rate"] = 1.0

        if rush_attempts > 0:
            result["rush_rate"] = rush_attempts / 10.0

    elif position == "RB":
        rows = conn.execute("""
            SELECT COALESCE(SUM(CASE WHEN stat_type='rushing_yards' THEN value ELSE 0 END), 0) as rush_attempts,
                   COALESCE(SUM(CASE WHEN stat_type='rushing_yards' THEN value ELSE 0 END), 0) as touches,
                   COALESCE(SUM(CASE WHEN stat_type='receiving_yards' THEN value ELSE 0 END), 0) as targets,
                   COALESCE(SUM(CASE WHEN stat_type='receiving_yards' THEN value ELSE 0 END), 0) as receptions
            FROM player_segment_stats
            WHERE player_name = ? AND segment = 'FULL GAME'
        """, (player_name,)).fetchone()

        rush_attempts = rows[0] if rows else 0
        touches = rows[1] if rows else 0
        targets = rows[2] if rows else 0
        receptions = rows[3] if rows else 0

        if rush_attempts > 0:
            result["carries_rate"] = rush_attempts / 10.0

            if rush_attempts >= 20:
                result["carries_20_plus_rate"] = 1.0
            elif rush_attempts >= 18:
                result["carries_18_plus_rate"] = 1.0
            elif rush_attempts >= 15:
                result["carries_15_plus_rate"] = 1.0
            elif rush_attempts >= 12:
                result["carries_12_plus_rate"] = 1.0

        if touches > 0:
            result["touches_rate"] = touches / 10.0

        if targets > 0:
            result["targets_rate"] = targets / 10.0

        if receptions > 0:
            result["receptions_rate"] = receptions / 10.0

    elif position in ["WR", "TE"]:
        rows = conn.execute("""
            SELECT COALESCE(SUM(CASE WHEN stat_type='receiving_yards' THEN value ELSE 0 END), 0) as targets,
                   COALESCE(SUM(CASE WHEN stat_type='receiving_yards' THEN value ELSE 0 END), 0) as receptions
            FROM player_segment_stats
            WHERE player_name = ? AND segment = 'FULL GAME'
        """, (player_name,)).fetchone()

        targets = rows[0] if rows else 0
        receptions = rows[1] if rows else 0

        if targets > 0:
            result["targets_rate"] = targets / 10.0

            if targets >= 12:
                result["targets_12_plus_rate"] = 1.0
            elif targets >= 10:
                result["targets_10_plus_rate"] = 1.0
            elif targets >= 7:
                result["targets_7_plus_rate"] = 1.0
            elif targets >= 5:
                result["targets_5_plus_rate"] = 1.0

        if receptions > 0:
            result["receptions_rate"] = receptions / 10.0

            if receptions >= 12:
                result["receptions_12_plus_rate"] = 1.0
            elif receptions >= 10:
                result["receptions_10_plus_rate"] = 1.0
            elif receptions >= 7:
                result["receptions_7_plus_rate"] = 1.0

    elif position == "K":
        rows = conn.execute("""
            SELECT COALESCE(SUM(CASE WHEN stat_type='fg_made' THEN value ELSE 0 END), 0) as fg_made,
                   COALESCE(SUM(CASE WHEN stat_type='fg_att' THEN value ELSE 0 END), 0) as fg_attempts
            FROM player_segment_stats
            WHERE player_name = ? AND segment = 'FULL GAME'
        """, (player_name,)).fetchone()

        fg_made = rows[0] if rows else 0
        fg_attempts = rows[1] if rows else 0

        if fg_attempts > 0:
            result["fg_pct"] = fg_made / fg_attempts

        if fg_attempts >= 2:
            result["fg_opportunity_rate"] = 1.0

    conn.close()
    return result


def determine_watch_reason(position: str, volume_analysis: Dict) -> str:
    """
    Determine why a player is worth following based on position and volume analysis.
    """
    if position == "QB":
        if volume_analysis.get("attempts_40_plus_rate", 0) > 0:
            return "HIGH PASS VOLUME QB"
        elif volume_analysis.get("attempts_35_plus_rate", 0) > 0:
            return "HIGH PASS VOLUME QB"
        elif volume_analysis.get("attempts_30_plus_rate", 0) > 0:
            return "HIGH PASS VOLUME QB"
        elif volume_analysis.get("attempts_25_plus_rate", 0) > 0:
            return "HIGH PASS VOLUME QB"
        elif volume_analysis.get("attempts_rate", 0) > 0.25:
            return "WORKHORSE QB"
        elif volume_analysis.get("rush_rate", 0) > 0.1:
            return "DUAL-THREAT QB"

    elif position == "RB":
        if volume_analysis.get("carries_20_plus_rate", 0) > 0:
            return "WORKHORSE RB"
        elif volume_analysis.get("carries_18_plus_rate", 0) > 0:
            return "WORKHORSE RB"
        elif volume_analysis.get("carries_15_plus_rate", 0) > 0:
            return "WORKHORSE RB"
        elif volume_analysis.get("carries_12_plus_rate", 0) > 0:
            return "WORKHORSE RB"
        elif volume_analysis.get("touches_rate", 0) > 0.3:
            return "GOAL-LINE BACK"
        elif volume_analysis.get("receptions_rate", 0) > 0.2:
            return "RED-ZONE WORKHORSE"

    elif position in ["WR", "TE"]:
        if volume_analysis.get("targets_12_plus_rate", 0) > 0:
            return "TARGET MONSTER"
        elif volume_analysis.get("targets_10_plus_rate", 0) > 0:
            return "TARGET MONSTER"
        elif volume_analysis.get("targets_7_plus_rate", 0) > 0:
            return "TARGET MONSTER"
        elif volume_analysis.get("targets_5_plus_rate", 0) > 0:
            return "TARGET MONSTER"
        elif volume_analysis.get("receptions_12_plus_rate", 0) > 0:
            return "RECEPTION FLOOR"
        elif volume_analysis.get("receptions_10_plus_rate", 0) > 0:
            return "RECEPTION FLOOR"

    elif position == "K":
        if volume_analysis.get("fg_opportunity_rate", 0) > 0:
            return "HIGH KICKER OPPORTUNITY"
        elif volume_analysis.get("fg_pct", 0) > 0.90:
            return "HIGH KICKER OPPORTUNITY"

    return "HIGH VOLUME PLAYER"


def get_player_segment_stats(player_id: str, stat_type: str, segment: str = "FULL GAME"):
    """Get player stats from database for a specific segment."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT value FROM player_segment_stats WHERE player_id = ? AND stat_type = ? AND segment = ?",
        (player_id, stat_type, segment)
    ).fetchall()

    conn.close()
    return [r[0] for r in rows]


def get_player_stats(player_name: str, stat_type: str, season: int = 2026, segment: str = "FULL GAME"):
    """Get player stats from database."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT value FROM player_segment_stats WHERE player_name = ? AND stat_type = ? AND segment = ?",
        (player_name, stat_type, segment)
    ).fetchall()

    conn.close()
    return [r[0] for r in rows]


def get_last_n_games_stats(player_name: str, stat_type: str, n: int = 10, season: int = 2026):
    """Get player's stats for last n games."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT value, game_id
        FROM player_segment_stats
        WHERE player_name = ? AND stat_type = ? AND segment = 'FULL GAME'
        ORDER BY game_id DESC
        LIMIT ?
    """, (player_name, stat_type, n)).fetchall()

    conn.close()
    return [(r[0], r[1]) for r in rows]


def calculate_live_vs_history(current_value: float, historical_avg: float) -> Dict:
    """
    Compare current live usage to historical usage.
    Returns status label and deviation percentage.
    """
    if historical_avg == 0:
        return {"status": "UNKNOWN", "deviation_pct": 0.0}

    deviation_pct = ((current_value - historical_avg) / historical_avg) * 100

    if deviation_pct >= 50:
        status = "VOLUME WELL ABOVE NORMAL"
    elif deviation_pct >= 25:
        status = "VOLUME ABOVE NORMAL"
    elif deviation_pct >= -10 and deviation_pct <= 10:
        status = "ON NORMAL PACE"
    elif deviation_pct >= -25:
        status = "VOLUME BELOW NORMAL"
    else:
        status = "VOLUME WELL BELOW NORMAL"

    return {"status": status, "deviation_pct": round(deviation_pct, 1)}


def get_current_game_stats(player_name: str):
    """Get player's current game stats from player_segment_stats."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.row_factory = sqlite3.Row

    # First get the most recent game_id for this player
    latest_game = conn.execute("""
        SELECT game_id
        FROM player_segment_stats
        WHERE player_name = ? AND segment IN ('1H', '2H', 'FULL GAME')
        ORDER BY game_id DESC
        LIMIT 1
    """, (player_name,)).fetchone()
    
    if not latest_game:
        conn.close()
        return {}
    
    game_id = latest_game['game_id']
    
    # Get quarter-level stats for the current/latest game
    rows = conn.execute("""
        SELECT segment, stat_type, value
        FROM player_segment_stats
        WHERE player_name = ? AND game_id = ? AND segment IN ('Q1', 'Q2', 'Q3', 'Q4', '1H', '2H', 'FULL GAME')
        ORDER BY
            CASE WHEN segment = 'FULL GAME' THEN 0 ELSE 1 END,
            segment
    """, (player_name, game_id)).fetchall()

    conn.close()

    current_stats = {}
    for r in rows:
        segment = r['segment']
        stat_type = r['stat_type']
        value = r['value']
        
        if segment == 'FULL GAME':
            if stat_type not in current_stats:
                current_stats[stat_type] = {'FULL GAME': value}
            else:
                current_stats[stat_type]['FULL GAME'] = value
        elif segment == '1H' or segment == '2H':
            period = '1H' if segment == '1H' else '2H'
            if stat_type not in current_stats:
                current_stats[stat_type] = {period: value}
            else:
                current_stats[stat_type][period] = value
        elif segment.startswith('Q') and len(segment) == 2 and segment[1].isdigit():
            if stat_type not in current_stats:
                current_stats[stat_type] = {segment: value}
            else:
                current_stats[stat_type][segment] = value

    return current_stats


def get_player_historical_stats(player_name: str, stat_type: str, n: int = 100, season: int = 2026):
    """Get player's historical stats for volume analysis."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT segment, value
        FROM player_segment_stats
        WHERE player_name = ? AND stat_type = ? AND segment != 'FULL GAME'
        ORDER BY segment
        LIMIT ?
    """, (player_name, stat_type, n)).fetchall()

    conn.close()
    return [(r['segment'], r['value']) for r in rows]


def get_workload_metrics(player_name: str, position: str, season: int = 2026):
    """
    Calculate comprehensive workload metrics for a player.
    Returns stats breakdown and analysis.
    """
    metrics = {}
    
    # Get current game stats
    current_stats = get_current_game_stats(player_name)
    
    # Get historical stats for analysis
    rushing_stats = get_player_historical_stats(player_name, 'rushing_yards', 50)
    passing_stats = get_player_historical_stats(player_name, 'passing_yards', 50)
    receiving_stats = get_player_historical_stats(player_name, 'receiving_yards', 50)
    
    if position == "RB":
        # Calculate RB metrics from current game
        latest_carries = None
        for seg_type, values in current_stats.items():
            if seg_type == 'rushing_yards' and 'FULL GAME' in values:
                latest_carries = values['FULL GAME']
                break
        
        if latest_carries is not None:
            metrics['LATEST_GAME_RUSH_ATTEMPTS'] = latest_carries
            metrics['LATEST_GAME_RUSH_YARDS'] = latest_carries
            
            # Calculate per-quarter breakdown from FULL GAME
            q1_carries = 0
            q2_carries = 0
            q3_carries = 0
            q4_carries = 0
            ot_carries = 0
            
            for seg_type, values in current_stats.items():
                if seg_type == 'rushing_yards':
                    for segment, value in values.items():
                        if segment == 'Q1':
                            q1_carries = value
                        elif segment == 'Q2':
                            q2_carries = value
                        elif segment == 'Q3':
                            q3_carries = value
                        elif segment == 'Q4':
                            q4_carries = value
                        elif segment == 'OT':
                            ot_carries = value
            
            metrics['Q1_RUSH_ATTEMPTS'] = q1_carries
            metrics['Q2_RUSH_ATTEMPTS'] = q2_carries
            metrics['Q3_RUSH_ATTEMPTS'] = q3_carries
            metrics['Q4_RUSH_ATTEMPTS'] = q4_carries
            metrics['OT_RUSH_ATTEMPTS'] = ot_carries
            metrics['1H_RUSH_ATTEMPTS'] = q1_carries + q2_carries
            metrics['2H_RUSH_ATTEMPTS'] = q3_carries + q4_carries
            metrics['FULL_GAME_RUSH_ATTEMPTS'] = q1_carries + q2_carries + q3_carries + q4_carries + ot_carries
        
        # Calculate rates based on historical data
        total_rushing_yards = sum(v for _, v in rushing_stats if isinstance(v, (int, float)))
        if total_rushing_yards > 0:
            metrics['SEASON_AVG_RUSH_YDS'] = total_rushing_yards / max(1, len([v for _, v in rushing_stats if isinstance(v, (int, float))]))
        
        # Calculate percentile rates for rushing yards
        rushing_values = [v for _, v in rushing_stats if isinstance(v, (int, float))]
        if rushing_values:
            rushing_values.sort()
            n = len(rushing_values)
            
            if n >= 20:
                metrics['20+_RATE'] = sum(1 for v in rushing_values if v >= 20) / n
            if n >= 15:
                metrics['15+_RATE'] = sum(1 for v in rushing_values if v >= 15) / n
            if n >= 12:
                metrics['12+_RATE'] = sum(1 for v in rushing_values if v >= 12) / n
            if n >= 10:
                metrics['10+_RATE'] = sum(1 for v in rushing_values if v >= 10) / n
            if n >= 8:
                metrics['8+_RATE'] = sum(1 for v in rushing_values if v >= 8) / n
            if n >= 5:
                metrics['5+_RATE'] = sum(1 for v in rushing_values if v >= 5) / n
        
        # Calculate carry share from historical context
        if rushing_stats:
            avg_rushing_per_game = sum(v for _, v in rushing_stats if isinstance(v, (int, float))) / max(1, len([v for _, v in rushing_stats if isinstance(v, (int, float))]))
            if avg_rushing_per_game > 0:
                metrics['CARRY_SHARE'] = (latest_carries or 0) / avg_rushing_per_game

    elif position == "QB":
        # Calculate QB metrics from current game
        latest_pass_attempts = None
        latest_pass_yards = None
        latest_qb_rush_attempts = None
        latest_qb_rush_yards = None
        
        for seg_type, values in current_stats.items():
            if seg_type == 'passing_yards' and 'FULL GAME' in values:
                latest_pass_attempts = values['FULL GAME']
                latest_pass_yards = values['FULL GAME']
            elif seg_type == 'rushing_yards' and 'FULL GAME' in values:
                latest_qb_rush_attempts = values['FULL GAME']
                latest_qb_rush_yards = values['FULL GAME']
        
        metrics['LATEST_GAME_PASS_ATTEMPTS'] = latest_pass_attempts or 0
        metrics['LATEST_GAME_PASS_YARDS'] = latest_pass_yards or 0
        metrics['LATEST_GAME_QB_RUSH_ATTEMPTS'] = latest_qb_rush_attempts or 0
        metrics['LATEST_GAME_QB_RUSH_YARDS'] = latest_qb_rush_yards or 0
        
        # Calculate per-quarter breakdown for passing
        q1_passes = 0
        q2_passes = 0
        q3_passes = 0
        q4_passes = 0
        ot_passes = 0
        
        for seg_type, values in current_stats.items():
            if seg_type == 'passing_yards':
                for segment, value in values.items():
                    if segment == 'Q1':
                        q1_passes = value
                    elif segment == 'Q2':
                        q2_passes = value
                    elif segment == 'Q3':
                        q3_passes = value
                    elif segment == 'Q4':
                        q4_passes = value
                    elif segment == 'OT':
                        ot_passes = value
        
        metrics['Q1_PASS_ATTEMPTS'] = q1_passes
        metrics['Q2_PASS_ATTEMPTS'] = q2_passes
        metrics['Q3_PASS_ATTEMPTS'] = q3_passes
        metrics['Q4_PASS_ATTEMPTS'] = q4_passes
        metrics['OT_PASS_ATTEMPTS'] = ot_passes
        metrics['1H_PASS_ATTEMPTS'] = q1_passes + q2_passes
        metrics['2H_PASS_ATTEMPTS'] = q3_passes + q4_passes
        metrics['FULL_GAME_PASS_ATTEMPTS'] = q1_passes + q2_passes + q3_passes + q4_passes + ot_passes
        
        # Calculate passer rates (attempt-based percentiles)
        passing_attempts = [v for seg, v in passing_stats if isinstance(v, (int, float))]
        if passing_attempts:
            passing_attempts.sort()
            n = len(passing_attempts)
            
            if n >= 10:
                metrics['40+_RATE'] = sum(1 for v in passing_attempts if v >= 40) / n
            if n >= 8:
                metrics['35+_RATE'] = sum(1 for v in passing_attempts if v >= 35) / n
            if n >= 6:
                metrics['30+_RATE'] = sum(1 for v in passing_attempts if v >= 30) / n
            if n >= 4:
                metrics['25+_RATE'] = sum(1 for v in passing_attempts if v >= 25) / n
            if n >= 3:
                metrics['20+_RATE'] = sum(1 for v in passing_attempts if v >= 20) / n
        
        # QB rushing analysis
        qb_rush_attempts = [v for seg, v in rushing_stats if isinstance(v, (int, float))]
        if qb_rush_attempts:
            qb_rush_attempts.sort()
            n = len(qb_rush_attempts)
            
            if n >= 10:
                metrics['10+_QB_RUSH_RATE'] = sum(1 for v in qb_rush_attempts if v >= 10) / n
            if n >= 8:
                metrics['5+_QB_RUSH_RATE'] = sum(1 for v in qb_rush_attempts if v >= 5) / n

    elif position in ["WR", "TE"]:
        # Calculate WR/TE metrics from current game
        latest_targets = None
        latest_receptions = None
        latest_receiving_yards = None
        
        for seg_type, values in current_stats.items():
            if seg_type == 'receiving_yards' and 'FULL GAME' in values:
                latest_targets = values['FULL GAME']
                latest_receptions = values['FULL GAME']
                latest_receiving_yards = values['FULL GAME']
        
        metrics['LATEST_GAME_TARGETS'] = latest_targets or 0
        metrics['LATEST_GAME_RECEPTIONS'] = latest_receptions or 0
        metrics['LATEST_GAME_RECEIVING_YARDS'] = latest_receiving_yards or 0
        
        # Calculate per-quarter breakdown for receiving
        q1_targets = 0
        q2_targets = 0
        q3_targets = 0
        q4_targets = 0
        
        for seg_type, values in current_stats.items():
            if seg_type == 'receiving_yards':
                for segment, value in values.items():
                    if segment == 'Q1':
                        q1_targets = value
                    elif segment == 'Q2':
                        q2_targets = value
                    elif segment == 'Q3':
                        q3_targets = value
                    elif segment == 'Q4':
                        q4_targets = value
        
        metrics['Q1_TARGETS'] = q1_targets
        metrics['Q2_TARGETS'] = q2_targets
        metrics['Q3_TARGETS'] = q3_targets
        metrics['Q4_TARGETS'] = q4_targets
        metrics['1H_TARGETS'] = q1_targets + q2_targets
        metrics['2H_TARGETS'] = q3_targets + q4_targets
        metrics['FULL_GAME_TARGETS'] = q1_targets + q2_targets + q3_targets + q4_targets
        
        # Target rate percentiles
        receiving_values = [v for seg, v in receiving_stats if isinstance(v, (int, float))]
        if receiving_values:
            receiving_values.sort()
            n = len(receiving_values)
            
            if n >= 10:
                metrics['12+_TARGET_RATE'] = sum(1 for v in receiving_values if v >= 12) / n
            if n >= 8:
                metrics['10+_TARGET_RATE'] = sum(1 for v in receiving_values if v >= 10) / n
            if n >= 6:
                metrics['7+_TARGET_RATE'] = sum(1 for v in receiving_values if v >= 7) / n
            if n >= 4:
                metrics['5+_TARGET_RATE'] = sum(1 for v in receiving_values if v >= 5) / n
        
        # Reception rate
        if receiving_values:
            metrics['RECEPTION_RATE'] = sum(1 for v in receiving_values if v > 0) / n

    elif position == "K":
        # Calculate kicker metrics
        latest_fgs_made = None
        latest_fg_attempts = None
        
        for seg_type, values in current_stats.items():
            if seg_type == 'fg_made' and 'FULL GAME' in values:
                latest_fgs_made = values['FULL GAME']
            elif seg_type == 'fg_att' and 'FULL GAME' in values:
                latest_fg_attempts = values['FULL GAME']
        
        metrics['LATEST_GAME_FGS_MADE'] = latest_fgs_made or 0
        metrics['LATEST_GAME_FG_ATTEMPTS'] = latest_fg_attempts or 0
        
        if latest_fg_attempts and latest_fg_attempts > 0:
            metrics['FG_PCT'] = latest_fgs_made / latest_fg_attempts
        
        if latest_fg_attempts and latest_fg_attempts >= 2:
            metrics['FG_OPPORTUNITY_RATE'] = 1.0

    # Calculate historical averages for all positions
    for stat_type in ['passing_yards', 'rushing_yards', 'receiving_yards']:
        stats = get_player_historical_stats(player_name, stat_type, 50, season)
        values = [v for _, v in stats if isinstance(v, (int, float))]
        if values:
            metrics[f'{stat_type.upper().replace("_", "_")}_SEASON_AVG'] = sum(values) / len(values)
            metrics[f'{stat_type.upper().replace("_", "_")}_MEDIAN'] = sorted(values)[len(values) // 2]
            metrics[f'{stat_type.upper().replace("_", "_")}_75TH'] = sorted(values)[int(len(values) * 0.75)]
            metrics[f'{stat_type.upper().replace("_", "_")}_90TH'] = sorted(values)[int(len(values) * 0.9)]

    return metrics


def get_top_players_by_metric(metric_type: str, position_filter: str = "", limit: int = 10, season: int = 2026):
    """
    Get top players by a specific metric.
    Returns list of (player_name, value) tuples.
    """
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.row_factory = sqlite3.Row

    query = """
        SELECT player_name, value
        FROM player_segment_stats
        WHERE season = ? AND segment = 'FULL GAME'
    """
    
    params = [season]
    
    if position_filter:
        query += " AND position = ?"
        params.append(position_filter)
    
    if metric_type == "rush_attempts":
        query += " AND stat_type = 'rushing_yards'"
    elif metric_type == "pass_attempts":
        query += " AND stat_type = 'passing_yards'"
    elif metric_type == "targets":
        query += " AND stat_type = 'receiving_yards'"
    else:
        conn.close()
        return []
    
    query += " ORDER BY value DESC LIMIT ?"
    params.append(limit)
    
    rows = conn.execute(query, params).fetchall()
    conn.close()
    
    return [(r['player_name'], r['value']) for r in rows]


def get_player_position(player_name: str):
    """Get player's position from player_game_stats."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.row_factory = sqlite3.Row

    row = conn.execute(
        "SELECT position FROM player_game_stats WHERE player_name = ? LIMIT 1",
        (player_name,)
    ).fetchone()

    conn.close()
    return row['position'] if row else None


def update_watchlist_player_stats(player_id: str):
    """Update volume watchlist stats for a player from current database data."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA busy_timeout = 30000")
    
    # Get player info
    player = conn.execute(
        "SELECT player_id, player_name, team, position FROM player_game_stats WHERE player_id = ? LIMIT 1",
        (player_id,)
    ).fetchone()
    
    if not player:
        conn.close()
        return
    
    # Calculate volume metrics
    player_name = player['player_name']
    position = player['position']
    
    metrics = get_workload_metrics(player_name, position)
    volume_analysis = analyze_player_volume_from_db(player_name, position)
    watch_reason = determine_watch_reason(position, volume_analysis)
    
    # Update watchlist.json
    manager = VolumeWatchlistManager()
    manager.watchlist_data = manager.load()
    
    if player_id in manager.watchlist_data.get("players", {}):
        player_data = manager.watchlist_data["players"][player_id]
        
        # Update existing player
        player_data["current_game_metrics"] = metrics
        player_data["current_season_stats"] = volume_analysis
        player_data["watch_reason"] = watch_reason
        player_data["active"] = True
        
        manager.save()
    else:
        # Add new player to watchlist
        new_player = VolumeWatchlistManager().add_player(
            player_id=player_id,
            player_name=player_name,
            position=position,
            team=player['team'],
            sport="nfl",
            watch_reason=watch_reason
        )
        new_player.current_game_metrics = metrics
        new_player.current_season_stats = volume_analysis
        
        # Save updated watchlist
        manager.watchlist_data = manager.load()
        manager.watchlist_data["players"][player_id] = new_player.to_dict()
        manager.save()
    
    conn.close()


def refresh_all_watchlist_players():
    """Refresh volume watchlist stats for all followed players."""
    manager = VolumeWatchlistManager()
    watchlist_data = manager.load()
    
    for player_id, player_data in watchlist_data.get("players", {}).items():
        if player_data.get("active", True):
            update_watchlist_player_stats(player_id)


def get_watchlist_summary():
    """Get summary statistics for the volume watchlist."""
    manager = VolumeWatchlistManager()
    manager.load()
    players = manager.get_all_players()
    
    summary = {
        "total_players": len(players),
        "by_position": {},
        "total_updates": len(players)
    }
    
    for player in players:
        position = player.position
        if position not in summary["by_position"]:
            summary["by_position"][position] = 0
        summary["by_position"][position] += 1
    
    return summary


def main():
    """Main entry point for the volume watchlist updater."""
    print("Updating volume watchlist from LiveIntel database...")
    
    # Refresh all watchlist players
    refresh_all_watchlist_players()
    
    # Get summary
    summary = get_watchlist_summary()
    
    print(f"Volume watchlist updated: {summary['total_players']} players")
    print(f"By position: {summary['by_position']}")


if __name__ == "__main__":
    main()