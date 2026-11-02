"""Normalized, fail-closed data contracts for the exact Stitch frontend.

This module is intentionally presentation-neutral.  It reads the existing
LiveIntel schema and reuses the existing board/research qualification logic.
No synthetic odds, probabilities, projections, identities, or sportsbook data
are created here.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from board_view import board_data

ET = ZoneInfo("America/New_York")
PRODUCTION_DB = "/var/lib/liveintel/intel.db"


def connect(path: str = PRODUCTION_DB, *, readonly: bool = True) -> sqlite3.Connection:
    target = f"file:{path}?mode=ro" if readonly else path
    connection = sqlite3.connect(target, uri=readonly, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    if readonly:
        connection.execute("PRAGMA query_only=ON")
    return connection


def finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def clean(value: Any) -> str:
    if value is None:
        return ""
    result = str(value).strip()
    return "" if result.lower() in {"none", "null", "undefined", "n/a", "unknown"} else result


def json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        result = json.loads(value or "{}")
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def totals(payload: Any) -> dict[str, float]:
    """Extract only numeric, recorded statistics from an ESPN stats payload."""
    source = json_object(payload)
    result: dict[str, float] = {}
    aliases = {
        "PTS": "points", "REB": "rebounds", "AST": "assists", "MIN": "minutes",
        "FGA": "fga", "FGM": "fgm", "3PA": "three_pa", "3PM": "three_pm",
        "FTA": "fta", "FTM": "ftm", "STL": "steals", "BLK": "blocks",
        "TO": "turnovers", "TOV": "turnovers", "YDS": "yards", "REC": "receptions",
        "TGTS": "targets", "CAR": "rush_attempts", "ATT": "attempts",
    }
    recorded = source.get("totals")
    if isinstance(recorded, dict):
        for key, value in recorded.items():
            number = finite(value)
            if number is not None:
                result[str(key).lower()] = number
    for section in source.values():
        if not isinstance(section, dict):
            continue
        for key, value in section.items():
            key_text = str(key).upper()
            if "/" in str(value) and key_text in {"C/ATT", "FG", "3PT", "FT"}:
                try:
                    made, attempted = str(value).split("/", 1)
                    made_number, attempt_number = finite(made), finite(attempted)
                    prefix = {"C/ATT": "pass", "FG": "fg", "3PT": "three_p", "FT": "ft"}[key_text]
                    if made_number is not None:
                        result.setdefault(prefix + "m", made_number)
                    if attempt_number is not None:
                        result.setdefault(prefix + "a", attempt_number)
                except ValueError:
                    pass
                continue
            number = finite(value)
            alias = aliases.get(key_text)
            if alias and number is not None:
                result.setdefault(alias, number)
    return result


def mean(values: Iterable[Any]) -> float | None:
    found = [value for item in values if (value := finite(item)) is not None]
    return round(sum(found) / len(found), 3) if found else None


def sample_covariance(xs: Iterable[Any], ys: Iterable[Any]) -> float | None:
    pairs = [(x, y) for a, b in zip(xs, ys) if (x := finite(a)) is not None and (y := finite(b)) is not None]
    if len(pairs) < 3:
        return None
    xbar, ybar = mean(x for x, _ in pairs), mean(y for _, y in pairs)
    assert xbar is not None and ybar is not None
    return round(sum((x - xbar) * (y - ybar) for x, y in pairs) / (len(pairs) - 1), 6)


def pearson(xs: Iterable[Any], ys: Iterable[Any]) -> float | None:
    pairs = [(x, y) for a, b in zip(xs, ys) if (x := finite(a)) is not None and (y := finite(b)) is not None]
    if len(pairs) < 3:
        return None
    left, right = [x for x, _ in pairs], [y for _, y in pairs]
    try:
        if statistics.pstdev(left) == 0 or statistics.pstdev(right) == 0:
            return None
        return round(statistics.correlation(left, right), 6)
    except statistics.StatisticsError:
        return None


def hit_rate(values: Iterable[Any], threshold: Any, side: str = "OVER", window: int | None = None) -> dict[str, Any] | None:
    line = finite(threshold)
    found = [value for item in values if (value := finite(item)) is not None]
    if line is None or not found:
        return None
    if window:
        found = found[:window]
    direction = clean(side).upper()
    if direction not in {"OVER", "MORE", "UNDER", "LESS"}:
        return None
    over = direction in {"OVER", "MORE"}
    wins = sum(value > line if over else value < line for value in found)
    pushes = sum(value == line for value in found)
    return {"hits": wins, "pushes": pushes, "sample": len(found), "rate": round(wins / len(found), 6), "line": line, "side": direction}


def kelly(probability: Any, decimal_odds: Any, cap: float = 0.25) -> dict[str, Any] | None:
    p, price = finite(probability), finite(decimal_odds)
    if p is None or price is None or not 0 < p < 1 or price <= 1:
        return None
    b = price - 1
    raw = (b * p - (1 - p)) / b
    if raw <= 0:
        return {"raw": round(raw, 8), "fraction": 0.0, "cap": cap}
    return {"raw": round(raw, 8), "fraction": round(min(raw, cap), 8), "cap": cap}


def joint_hit_rate(left: Iterable[Any], right: Iterable[Any], left_line: Any, right_line: Any,
                   left_side: str = "OVER", right_side: str = "OVER") -> dict[str, Any] | None:
    lline, rline = finite(left_line), finite(right_line)
    pairs = [(x, y) for a, b in zip(left, right) if (x := finite(a)) is not None and (y := finite(b)) is not None]
    if lline is None or rline is None or len(pairs) < 3:
        return None
    lover, rover = left_side.upper() in {"OVER", "MORE"}, right_side.upper() in {"OVER", "MORE"}
    hits = sum((x > lline if lover else x < lline) and (y > rline if rover else y < rline) for x, y in pairs)
    return {"hits": hits, "sample": len(pairs), "rate": round(hits / len(pairs), 6)}


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _latest(connection: sqlite3.Connection, table: str, column: str) -> str:
    if not _table_exists(connection, table):
        return ""
    row = connection.execute(f"SELECT max({column}) FROM {table}").fetchone()
    return clean(row[0]) if row else ""


def system_health(connection: sqlite3.Connection, clock: datetime | None = None) -> dict[str, Any]:
    now = clock or datetime.now(timezone.utc)
    feeds = []
    for name, table, column, expected_seconds in (
        ("game_state", "game_state", "last_updated", 1800),
        ("games", "games", "last_updated", 86400),
        ("player_stats", "player_game_stats", "last_updated", 86400),
        ("plays", "plays", "local_received_timestamp", 1800),
        ("challenge", "challenge_decisions", "created_at", 3600),
    ):
        stamp_text = _latest(connection, table, column)
        age = None
        try:
            stamp = datetime.fromisoformat(stamp_text.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=ET)
            age = max(0, (now - stamp.astimezone(timezone.utc)).total_seconds())
        except (ValueError, TypeError):
            pass
        feeds.append({"name": name, "updated_at": stamp_text or None, "age_seconds": round(age, 1) if age is not None else None,
                      "expected_seconds": expected_seconds, "state": "LIVE" if age is not None and age <= expected_seconds else "STALE" if age is not None else "UNAVAILABLE"})
    quick = connection.execute("PRAGMA quick_check").fetchone()[0]
    return {"database": "ok" if quick == "ok" else "error", "feeds": feeds,
            "state": "LIVE" if quick == "ok" and any(feed["state"] == "LIVE" for feed in feeds) else "DEGRADED",
            "checked_at": now.isoformat()}


def provider_registry(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    manual_count = connection.execute("SELECT count(*) FROM prop_lines").fetchone()[0] if _table_exists(connection, "prop_lines") else 0
    providers = [
        ("ESPN", "sports_state", True, "Existing LiveIntel ingestion"),
        ("MANUAL_VERIFIED_OFFERS", "manual_market_terms", manual_count > 0, f"{manual_count} recorded prop lines"),
        ("MULTI_BOOK_ODDS", "odds", False, "DATA SOURCE PENDING"),
        ("PINNACLE", "sharp_benchmark", False, "DATA SOURCE PENDING"),
        ("CIRCA", "sharp_benchmark", False, "DATA SOURCE PENDING"),
        ("DRAFTKINGS", "book_quote", False, "DATA SOURCE PENDING"),
        ("FANDUEL", "book_quote", False, "DATA SOURCE PENDING"),
        ("BETMGM", "book_quote", False, "DATA SOURCE PENDING"),
        ("BETONLINE", "book_quote", False, "DATA SOURCE PENDING"),
        ("WEATHER", "weather", False, "DATA SOURCE PENDING"),
        ("MARKET_DEPTH", "liquidity", False, "DATA SOURCE PENDING"),
        ("SPORTSBOOK_ACCOUNT", "cashout_execution", False, "NOT AUTHORIZED / SOURCE PENDING"),
    ]
    return [{"provider": name, "capability": capability, "connected": connected, "detail": detail} for name, capability, connected, detail in providers]


def market_lines(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(connection, "prop_lines"):
        return []
    query = """SELECT p.*, q.projected_low,q.projected_median,q.projected_high,q.current_value,
                      q.confidence,q.signal,q.calculated_at
               FROM prop_lines p LEFT JOIN prop_projections q ON q.id=(
                 SELECT q2.id FROM prop_projections q2 WHERE q2.prop_id=p.id ORDER BY q2.id DESC LIMIT 1)
               ORDER BY p.observed_at DESC LIMIT 100"""
    result = []
    for row in connection.execute(query):
        item = dict(row)
        item["line"] = finite(item.get("line"))
        item["odds"] = None  # prop_lines does not contain a verified price.
        item["source_type"] = "RECORDED_MANUAL_LINE"
        item["edge"] = None
        projection, line = finite(item.get("projected_median")), item["line"]
        if projection is not None and line is not None:
            item["projection_delta"] = round(projection - line, 3)
        else:
            item["projection_delta"] = None
        result.append(item)
    return result


def game_logs(connection: sqlite3.Connection, game_id: str, limit: int = 100) -> list[dict[str, Any]]:
    if not game_id or not _table_exists(connection, "plays"):
        return []
    columns = {row[1] for row in connection.execute("PRAGMA table_info(plays)")}
    ordering = "event_timestamp" if "event_timestamp" in columns else "rowid"
    rows = connection.execute(f"SELECT * FROM plays WHERE game_id=? ORDER BY {ordering} DESC LIMIT ?", (game_id, min(max(limit, 1), 500)))
    return [dict(row) for row in rows]


def _player_history(connection: sqlite3.Connection, player_ids: list[str], limit_each: int = 10) -> dict[str, list[dict[str, Any]]]:
    if not player_ids:
        return {}
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    placeholders = ",".join("?" for _ in player_ids)
    query = f"""SELECT p.game_id,p.player_id,p.player_name,p.team,p.stats_json,p.segment,p.last_updated,
                       coalesce(g.game_date,g.slate_date,substr(p.last_updated,1,10)) game_date
                FROM player_game_stats p LEFT JOIN games g ON g.id=p.game_id
                WHERE p.player_id IN ({placeholders}) AND upper(coalesce(p.segment,'FULL GAME'))='FULL GAME'
                ORDER BY game_date DESC,p.last_updated DESC"""
    for row in connection.execute(query, player_ids):
        key = str(row["player_id"])
        if len(result[key]) >= limit_each:
            continue
        result[key].append({"game_id": row["game_id"], "date": clean(row["game_date"])[:10] or None,
                            "team": clean(row["team"]), "stats": totals(row["stats_json"])})
    return dict(result)


def player_catalog(connection: sqlite3.Connection, terminal: dict[str, Any], limit: int = 240) -> list[dict[str, Any]]:
    roster = terminal.get("roster", [])[:limit]
    histories = _player_history(connection, [str(row.get("player_id", "")) for row in roster if clean(row.get("player_id"))], 10)
    games = {str(game.get("id")): game for game in terminal.get("games", [])}
    result = []
    for row in roster:
        player_id = str(row.get("player_id", ""))
        history = histories.get(player_id, [])
        stat_keys = sorted({key for game in history for key in game["stats"]})
        windows = {}
        for key in stat_keys:
            values = [game["stats"].get(key) for game in history]
            windows[key] = {"l5": mean(values[:5]), "l10": mean(values[:10]), "values": [finite(value) for value in values if finite(value) is not None]}
        game = games.get(str(row.get("game_id")), {})
        starter = row.get("starter")
        role = "STARTER" if starter == 1 else "BENCH / ROTATION" if starter == 0 else "ROLE NOT SUPPLIED"
        result.append({"player_id": player_id, "name": clean(row.get("player_name")), "team": clean(row.get("team")),
                       "sport": clean(row.get("sport")), "position": clean(row.get("position")), "game_id": clean(row.get("game_id")),
                       "game": f"{game.get('away','')} @ {game.get('home','')}".strip(" @"), "starter": starter,
                       "role": role, "minutes": finite(row.get("minutes")), "history": history, "windows": windows})
    return result


def portfolio(connection: sqlite3.Connection, overview: dict[str, Any]) -> dict[str, Any]:
    """Aggregate authoritative normalized groups; unknown money never becomes zero."""
    supplied = isinstance(overview.get("tickets"), list)
    tickets = overview.get("tickets") if supplied else []

    def group(ticket):
        # A normalized group takes precedence over legacy/raw status fields.
        value = clean(ticket.get("group") if "group" in ticket else ticket.get("status")).upper()
        return {"PENDING": "OPEN", "WIN": "WON", "LOSS": "LOST", "PUSH": "VOID",
                "VOIDED": "VOID"}.get(value, value)

    def stake(ticket):
        value = finite(ticket.get("stake") if "stake" in ticket else ticket.get("wager_amount"))
        return value if value is not None and value >= 0 else None

    opened = [ticket for ticket in tickets if group(ticket) == "OPEN"]
    settled = [ticket for ticket in tickets if group(ticket) in {"WON", "LOST", "VOID", "SETTLED"}]
    groups_known = supplied and all(group(ticket) in {"OPEN", "WON", "LOST", "VOID", "SETTLED"} for ticket in tickets)
    exposure_known = groups_known and all(stake(ticket) is not None for ticket in opened)
    pnl_known = groups_known and all(stake(ticket) is not None and finite(ticket.get("actual_payout")) is not None
                                   and finite(ticket["actual_payout"]) >= 0 for ticket in settled)
    exposure = round(sum(stake(ticket) for ticket in opened), 2) if exposure_known else None
    pnl = round(sum(finite(ticket["actual_payout"]) - stake(ticket) for ticket in settled), 2) if pnl_known else None
    books: dict[str, float] = defaultdict(float)
    books_known = exposure_known and all(clean(ticket.get("book")) for ticket in opened)
    if books_known:
        for ticket in opened:
            books[clean(ticket["book"])] += stake(ticket)
    return {"tickets": tickets, "open": len(opened) if supplied else None,
            "open_count": len(opened) if supplied else None, "settled": len(settled) if supplied else None,
            "active_exposure": exposure, "settled_pl": pnl, "aggregates_authoritative": True,
            "book_allocation": [{"book": book, "exposure": round(value, 2)} for book, value in sorted(books.items())] if books_known else None}


def owner_portfolio(overview: dict[str, Any]) -> dict[str, Any]:
    """Keep owner account/record fields, using the same exposure/settlement rules."""
    owner = dict(overview.get("bobby") or {})
    tickets = overview.get("tickets")
    name = clean(owner.get("name") or "Bobby").casefold()
    own = [ticket for ticket in tickets if clean(ticket.get("owner")).casefold() == name] if isinstance(tickets, list) else None
    totals = portfolio(None, {"tickets": own})
    owner.update(exposure=totals["active_exposure"], open_count=totals["open_count"], settled_pl=totals["settled_pl"])
    if totals["settled_pl"] is None:
        owner["today_pl"] = None
    return owner


def _screen_needs(screen: str, token: str) -> bool:
    return token in screen or screen in {"all", ""}


def bootstrap(connection: sqlite3.Connection, screen: str = "all", clock: datetime | None = None) -> dict[str, Any]:
    now = clock or datetime.now(timezone.utc)
    overview = board_data(connection, "overview", {}, now)
    terminal = board_data(connection, "terminal", {}, now)
    owner = owner_portfolio(overview)
    ticket_totals = portfolio(connection, overview)
    result: dict[str, Any] = {
        "contract_version": 1,
        "generated_at": now.isoformat(),
        "screen": screen,
        "global": {"slate_date": overview.get("slate_date"), "health": system_health(connection, now),
                   "bankroll": owner.get("bankroll"), "pl": owner.get("today_pl"),
                   "slip_count": ticket_totals["open_count"]},
        "games": terminal.get("games", []),
        "watch": terminal.get("watch_history", []),
        "team_history": terminal.get("team_history", {}),
        "tickets": ticket_totals,
        "bobby": owner,
        "specialists": overview.get("specialists", []),
        "ai_tickets": terminal.get("ai_tickets", {}),
        "intelligence": overview.get("intelligence", {}),
        "providers": provider_registry(connection),
        "markets": market_lines(connection),
    }
    if any(token in screen for token in ("players", "watch", "intelligence")) or screen in {"all", ""}:
        result["players"] = player_catalog(connection, terminal)
    if "heat" in screen or screen in {"all", ""}:
        result["heat"] = board_data(connection, "heat", {}, now)
    if any(token in screen for token in ("math_lab", "intelligence")) or screen in {"all", ""}:
        result["research"] = board_data(connection, "research", {}, now)
    return result


def updates(connection: sqlite3.Connection, clock: datetime | None = None) -> dict[str, Any]:
    now = clock or datetime.now(timezone.utc)
    overview = board_data(connection, "overview", {}, now)
    terminal = board_data(connection, "terminal", {}, now)
    owner = owner_portfolio(overview)
    stamps = [feed.get("updated_at") or "" for feed in system_health(connection, now)["feeds"]]
    revision = max(stamps) if stamps else now.isoformat()
    return {"contract_version": 1, "generated_at": now.isoformat(), "revision": revision,
            "global": {"slate_date": overview.get("slate_date"), "health": system_health(connection, now),
                       "bankroll": owner.get("bankroll"), "pl": owner.get("today_pl")},
            "games": terminal.get("games", []), "watch": terminal.get("watch_history", []),
            "tickets": portfolio(connection, overview), "bobby": owner, "specialists": overview.get("specialists", []),
            "ai_tickets": terminal.get("ai_tickets", {}), "intelligence": overview.get("intelligence", {})}


def add_watch(connection: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    player_id, name = clean(payload.get("player_id")), clean(payload.get("player_name") or payload.get("name"))
    if not player_id or not name:
        raise ValueError("Recorded target identity and name are required")
    count = connection.execute("SELECT count(*) FROM owner_watchlist").fetchone()[0]
    existing = connection.execute("SELECT id FROM owner_watchlist WHERE player_id=?", (player_id,)).fetchone()
    if not existing and count >= 5:
        raise ValueError("Watch 5 is full")
    values = (name, clean(payload.get("team")), clean(payload.get("sport")), player_id, int(bool(payload.get("is_pinned", True))), "stitch-development")
    if existing:
        connection.execute("UPDATE owner_watchlist SET player_name=?,team=?,sport=?,is_pinned=?,source=? WHERE player_id=?",
                           (values[0], values[1], values[2], values[4], values[5], player_id))
    else:
        connection.execute("INSERT INTO owner_watchlist(player_name,team,sport,player_id,is_pinned,source) VALUES(?,?,?,?,?,?)", values)
    connection.commit()
    return {"success": True, "player_id": player_id}


def remove_watch(connection: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    player_id = clean(payload.get("player_id"))
    if not player_id:
        raise ValueError("player_id is required")
    connection.execute("DELETE FROM owner_watchlist WHERE player_id=?", (player_id,))
    connection.commit()
    return {"success": True, "player_id": player_id}


def reorder_watch(connection: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    order = [clean(value) for value in payload.get("player_ids", []) if clean(value)]
    current = [str(row[0]) for row in connection.execute("SELECT player_id FROM owner_watchlist")]
    if sorted(order) != sorted(current) or len(order) > 5:
        raise ValueError("Order must contain every current Watch target exactly once")
    # The existing schema has no position column. Preserve deterministic order by
    # assigning monotonically increasing timestamps without changing identities.
    base = datetime.now(timezone.utc).timestamp()
    for index, player_id in enumerate(order):
        stamp = datetime.fromtimestamp(base - index, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        connection.execute("UPDATE owner_watchlist SET added_at=? WHERE player_id=?", (stamp, player_id))
    connection.commit()
    return {"success": True, "player_ids": order}


def record_ticket(connection: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    legs = payload.get("legs")
    if not isinstance(legs, list) or not legs:
        raise ValueError("At least one ticket leg is required")
    stake = finite(payload.get("stake"))
    payout = finite(payload.get("potential_payout"))
    if stake is None or stake <= 0 or payout is None or payout < stake:
        raise ValueError("Valid positive stake and potential payout are required")
    book = clean(payload.get("book"))
    if not book:
        raise ValueError("A recorded sportsbook is required")
    normalized = []
    for leg in legs:
        if not isinstance(leg, dict):
            raise ValueError("Every ticket leg must be an object")
        side = clean(leg.get("side")).upper()
        if side not in {"OVER", "UNDER", "MORE", "LESS", "MONEYLINE"}:
            raise ValueError("Every leg needs a supported side")
        market = clean(leg.get("market"))
        sport = clean(leg.get("sport"))
        if not market or not sport or not clean(leg.get("player") or leg.get("team")):
            raise ValueError("Every leg needs sport, target, and market")
        line = finite(leg.get("line"))
        if side != "MONEYLINE" and line is None:
            raise ValueError("Every non-moneyline leg needs a line")
        try:
            odds = finite(leg.get("odds"))
        except OverflowError:
            odds = None
        if odds is None or odds == 0:
            raise ValueError("Every leg needs valid non-zero odds")
        normalized.append({**leg, "side": side, "line": line, "odds": odds})
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cursor = connection.execute("""INSERT INTO tickets(owner_name,sportsbook_platform,date_time_placed,wager_amount,potential_payout,
        ticket_type,notes,status,environment) VALUES(?,?,?,?,?,?,?,?,?)""",
        (clean(payload.get("owner_name")) or "Bobby", book, now, stake, payout,
         "SINGLE" if len(normalized) == 1 else "PARLAY", json.dumps({"entry_source": "STITCH_DEVELOPMENT", "record_only": True}),
         "OPEN", "TEST"))
    ticket_id = cursor.lastrowid
    for leg in normalized:
        connection.execute("""INSERT INTO ticket_legs(ticket_id,sport,league,game_id,player_id,team,player,market,line,side,odds,leg_status)
                              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (ticket_id, leg["sport"], clean(leg.get("league")), clean(leg.get("game_id")), clean(leg.get("player_id")),
                            clean(leg.get("team")), clean(leg.get("player")), clean(leg.get("market")), leg["line"], leg["side"], leg["odds"], "PENDING"))
    if _table_exists(connection, "ticket_events"):
        connection.execute("INSERT INTO ticket_events(ticket_id,event_type,event_data,created_by) VALUES(?,?,?,?)",
                           (ticket_id, "RECORDED", json.dumps({"source": "STITCH_DEVELOPMENT", "wager_placed": False}), "STITCH_DEV"))
    connection.commit()
    return {"success": True, "ticket_id": ticket_id, "recorded": True, "wager_placed": False}


def implied_probability(odds: Any) -> float | None:
    """Convert American odds to implied probability."""
    val = finite(odds)
    if val is None or val == 0:
        return None
    if val > 0:
        return round(100.0 / (val + 100.0), 6)
    return round(abs(val) / (abs(val) + 100.0), 6)


def no_vig_probability(over_odds: Any, under_odds: Any) -> dict[str, Any] | None:
    """Calculate no-vig fair probabilities from two American prices."""
    over = implied_probability(over_odds)
    under = implied_probability(under_odds)
    if over is None or under is None:
        return None
    total = over + under
    if total <= 0:
        return None
    return {
        "over_implied": over,
        "under_implied": under,
        "over_fair": round(over / total, 6),
        "under_fair": round(under / total, 6),
    }


def calculation(payload: dict[str, Any]) -> dict[str, Any]:
    kind = clean(payload.get("kind")).lower()
    if kind == "hit_rate":
        value = hit_rate(payload.get("values", []), payload.get("line"), payload.get("side", "OVER"), payload.get("window"))
    elif kind == "correlation":
        value = {"pearson_r": pearson(payload.get("left", []), payload.get("right", [])),
                 "covariance": sample_covariance(payload.get("left", []), payload.get("right", []))}
        if value["pearson_r"] is None or value["covariance"] is None:
            value = None
    elif kind == "joint_hit_rate":
        value = joint_hit_rate(payload.get("left", []), payload.get("right", []), payload.get("left_line"), payload.get("right_line"),
                               payload.get("left_side", "OVER"), payload.get("right_side", "OVER"))
    elif kind == "kelly":
        value = kelly(payload.get("probability"), payload.get("decimal_odds"), finite(payload.get("cap")) or 0.25)
    elif kind == "implied_probability":
        probability = implied_probability(payload.get("odds"))
        value = {"implied_probability": probability} if probability is not None else None
    elif kind == "no_vig_probability":
        value = no_vig_probability(payload.get("over_odds"), payload.get("under_odds"))
    else:
        raise ValueError("Unsupported calculation")
    return {"kind": kind, "result": value, "available": value is not None, "fail_closed": value is None}
