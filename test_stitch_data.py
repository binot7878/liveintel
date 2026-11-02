import json
import sqlite3
from datetime import datetime, timezone
import unittest

import stitch_data


SCHEMA = """
CREATE TABLE owner_watchlist(id INTEGER PRIMARY KEY,player_name TEXT NOT NULL,team TEXT,sport TEXT,player_id TEXT,added_at TEXT DEFAULT CURRENT_TIMESTAMP,is_pinned INTEGER DEFAULT 0,source TEXT);
CREATE TABLE tickets(id INTEGER PRIMARY KEY AUTOINCREMENT,owner_name TEXT NOT NULL,sportsbook_platform TEXT NOT NULL,date_time_placed TEXT NOT NULL,wager_amount REAL NOT NULL,potential_payout REAL NOT NULL,actual_payout REAL,ticket_type TEXT NOT NULL,notes TEXT,status TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP,settled_at TEXT,environment TEXT DEFAULT 'TEST');
CREATE TABLE ticket_legs(id INTEGER PRIMARY KEY AUTOINCREMENT,ticket_id INTEGER NOT NULL,sport TEXT NOT NULL,league TEXT,game_id TEXT,player_id TEXT,team TEXT,player TEXT,market TEXT NOT NULL,line REAL,side TEXT NOT NULL,odds REAL,current_value REAL,leg_status TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP,settled_at TEXT,FOREIGN KEY(ticket_id) REFERENCES tickets(id));
CREATE TABLE ticket_events(id INTEGER PRIMARY KEY AUTOINCREMENT,ticket_id INTEGER NOT NULL,leg_id INTEGER,event_type TEXT NOT NULL,event_data TEXT,timestamp TEXT DEFAULT CURRENT_TIMESTAMP,created_by TEXT DEFAULT 'SYSTEM');
"""


class StitchCalculationTests(unittest.TestCase):
    def test_hit_rate_is_deterministic_and_strict(self):
        self.assertEqual(stitch_data.hit_rate([12, 10, 8, 11], 10, "OVER"),
                         {"hits": 2, "pushes": 1, "sample": 4, "rate": 0.5, "line": 10.0, "side": "OVER"})

    def test_calculations_fail_closed(self):
        self.assertIsNone(stitch_data.hit_rate([1, 2], None))
        self.assertIsNone(stitch_data.pearson([1, 2], [1, 2]))
        self.assertIsNone(stitch_data.kelly(None, 2.0))
        self.assertTrue(stitch_data.calculation({"kind": "kelly", "probability": None, "decimal_odds": 2})["fail_closed"])

    def test_correlation_and_covariance(self):
        self.assertEqual(stitch_data.pearson([1, 2, 3, 4], [2, 4, 6, 8]), 1.0)
        self.assertEqual(stitch_data.sample_covariance([1, 2, 3], [2, 4, 6]), 2.0)

    def test_kelly_is_capped_and_never_negative(self):
        self.assertEqual(stitch_data.kelly(0.55, 2.0)["fraction"], 0.1)
        self.assertEqual(stitch_data.kelly(0.3, 2.0)["fraction"], 0.0)
        self.assertEqual(stitch_data.kelly(0.9, 5.0)["fraction"], 0.25)

    def test_implied_probability(self):
        self.assertEqual(stitch_data.implied_probability(100), 0.5)
        self.assertEqual(stitch_data.implied_probability(-100), 0.5)
        self.assertEqual(stitch_data.implied_probability(150), 0.4)
        self.assertEqual(stitch_data.implied_probability(-150), 0.6)
        self.assertAlmostEqual(stitch_data.implied_probability(200), 1 / 3, places=6)
        self.assertAlmostEqual(stitch_data.implied_probability(-200), 2 / 3, places=6)
        self.assertIsNone(stitch_data.implied_probability(0))
        self.assertIsNone(stitch_data.implied_probability(None))
        self.assertIsNone(stitch_data.implied_probability("garbage"))

    def test_no_vig_probability(self):
        result = stitch_data.no_vig_probability(-110, -110)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["over_fair"], 0.5, places=6)
        self.assertAlmostEqual(result["under_fair"], 0.5, places=6)

        result = stitch_data.no_vig_probability(-150, 120)
        self.assertIsNotNone(result)
        p1 = 150 / 250.0
        p2 = 100 / 220.0
        total = p1 + p2
        self.assertAlmostEqual(result["over_fair"], p1 / total, places=6)
        self.assertAlmostEqual(result["under_fair"], p2 / total, places=6)

        self.assertIsNone(stitch_data.no_vig_probability(None, -110))
        self.assertIsNone(stitch_data.no_vig_probability(-110, 0))

        dispatched = stitch_data.calculation({
            "kind": "no_vig_probability",
            "over_odds": -110,
            "under_odds": -110,
        })
        self.assertTrue(dispatched["available"])
        self.assertFalse(dispatched["fail_closed"])
        self.assertEqual(dispatched["result"]["over_fair"], 0.5)

    def test_system_health_interprets_naive_liveintel_timestamps_as_eastern(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE game_state(last_updated TEXT)")
        connection.execute("CREATE TABLE games(last_updated TEXT)")
        connection.execute("CREATE TABLE player_game_stats(last_updated TEXT)")
        connection.execute("CREATE TABLE plays(local_received_timestamp TEXT)")
        connection.execute("CREATE TABLE challenge_decisions(created_at TEXT)")

        connection.execute("INSERT INTO game_state(last_updated) VALUES(?)", ("2026-11-01 14:50:45",))
        connection.execute("INSERT INTO games(last_updated) VALUES(?)", ("2026-11-01 14:50:45",))
        connection.execute("INSERT INTO player_game_stats(last_updated) VALUES(?)", ("2026-11-01 14:50:45",))
        connection.execute("INSERT INTO plays(local_received_timestamp) VALUES(?)", ("2026-11-01T19:50:45Z",))
        connection.execute("INSERT INTO challenge_decisions(created_at) VALUES(?)", ("2026-11-01T19:50:45+00:00",))

        now = datetime(2026, 11, 1, 19, 51, 0, tzinfo=timezone.utc)
        result = stitch_data.system_health(connection, now)

        feeds = {feed["name"]: feed for feed in result["feeds"]}

        self.assertLess(feeds["game_state"]["age_seconds"], 60)
        self.assertLess(feeds["games"]["age_seconds"], 60)
        self.assertLess(feeds["player_stats"]["age_seconds"], 60)
        self.assertLess(feeds["plays"]["age_seconds"], 60)
        self.assertLess(feeds["challenge"]["age_seconds"], 60)

        connection.close()

    def test_stats_extraction_uses_recorded_values(self):
        payload = {"shooting": {"FG": "8/15", "3PT": "3/7"}, "totals": {"points": 22, "assists": 6}}
        self.assertEqual(stitch_data.totals(json.dumps(payload))["points"], 22)
        self.assertEqual(stitch_data.totals(payload)["fga"], 15)


class StitchPortfolioTests(unittest.TestCase):
    def sample(self):
        return {"bobby": {"name": "BOBBY", "bankroll": None, "today_pl": 40}, "tickets": [
            {"id": 1, "owner": "Bobby", "group": "OPEN", "book": "Recorded Book", "stake": 25, "actual_payout": None},
            {"id": 2, "owner": "Bobby", "group": "WON", "book": "Recorded Book", "stake": 40, "actual_payout": 80},
        ]}

    def test_exact_normalized_open_and_win_aggregates(self):
        totals = stitch_data.portfolio(None, self.sample())
        self.assertEqual(totals["open_count"], 1)
        self.assertEqual(totals["open"], 1)
        self.assertEqual(totals["active_exposure"], 25)
        self.assertEqual(totals["settled"], 1)
        self.assertEqual(totals["settled_pl"], 40)
        self.assertEqual(totals["book_allocation"], [{"book": "Recorded Book", "exposure": 25}])

    def test_normalized_group_overrides_legacy_status(self):
        data = self.sample()
        data["tickets"][1]["status"] = "OPEN"
        self.assertEqual(stitch_data.portfolio(None, data)["active_exposure"], 25)
        data["tickets"].append({"group": "VOID", "owner": "Bobby", "stake": 10, "actual_payout": 10})
        self.assertEqual(stitch_data.portfolio(None, data)["settled_pl"], 40)

    def test_incomplete_settlement_fails_closed(self):
        for missing in (None, "", float("nan"), -1):
            data = self.sample()
            data["tickets"][1]["actual_payout"] = missing
            totals = stitch_data.portfolio(None, data)
            self.assertIsNone(totals["settled_pl"])
            self.assertEqual(totals["active_exposure"], 25)
            self.assertIsNone(stitch_data.owner_portfolio(data)["today_pl"])

    def test_missing_open_stake_and_book_are_not_invented(self):
        data = self.sample()
        data["tickets"][0]["stake"] = None
        self.assertIsNone(stitch_data.portfolio(None, data)["active_exposure"])
        self.assertIsNone(stitch_data.portfolio(None, data)["book_allocation"])
        data["tickets"][0]["stake"] = 25
        data["tickets"][0]["book"] = ""
        self.assertEqual(stitch_data.portfolio(None, data)["active_exposure"], 25)
        self.assertIsNone(stitch_data.portfolio(None, data)["book_allocation"])

    def test_unknown_status_is_not_open_and_missing_source_is_pending(self):
        data = {"tickets": [{"group": "HISTORY", "stake": 500}]}
        totals = stitch_data.portfolio(None, data)
        self.assertEqual(totals["open_count"], 0)
        self.assertIsNone(totals["active_exposure"])
        self.assertIsNone(totals["settled_pl"])
        self.assertIsNone(stitch_data.portfolio(None, {})["open_count"])
        self.assertEqual(stitch_data.portfolio(None, {"tickets": []})["settled_pl"], 0)

    def test_raw_settled_missing_payout_does_not_become_open(self):
        data = {"tickets": [{"status": "SETTLED", "wager_amount": 40}]}
        totals = stitch_data.portfolio(None, data)
        self.assertEqual(totals["open_count"], 0)
        self.assertEqual(totals["active_exposure"], 0)
        self.assertIsNone(totals["settled_pl"])

    def test_owner_summary_and_shared_bootstrap_consume_same_totals(self):
        from unittest.mock import patch
        data = self.sample()
        owner = stitch_data.owner_portfolio(data)
        self.assertIsNone(owner["bankroll"])
        self.assertEqual((owner["open_count"], owner["exposure"], owner["settled_pl"]), (1, 25, 40))
        with patch.object(stitch_data, "board_data", side_effect=lambda c, view, filters, now: data if view == "overview" else {}), \
             patch.object(stitch_data, "system_health", return_value={"state": "LIVE", "feeds": []}), \
             patch.object(stitch_data, "provider_registry", return_value=[]), \
             patch.object(stitch_data, "market_lines", return_value=[]):
            payload = stitch_data.bootstrap(None, "picks_positions_specialist_challenge")
            update = stitch_data.updates(None)
        self.assertEqual(payload["global"]["slip_count"], 1)
        self.assertEqual(payload["bobby"]["exposure"], 25)
        self.assertEqual(update["tickets"]["settled_pl"], 40)
        self.assertEqual(update["bobby"]["open_count"], 1)


class StitchActionTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self.connection.execute("PRAGMA foreign_keys=ON")

    def tearDown(self):
        self.connection.close()

    def test_watch_add_cap_remove_and_reorder(self):
        for index in range(5):
            stitch_data.add_watch(self.connection, {"player_id": str(index), "player_name": f"P{index}", "team": "T", "sport": "NBA"})
        with self.assertRaisesRegex(ValueError, "full"):
            stitch_data.add_watch(self.connection, {"player_id": "6", "player_name": "P6"})
        stitch_data.reorder_watch(self.connection, {"player_ids": ["4", "3", "2", "1", "0"]})
        stitch_data.remove_watch(self.connection, {"player_id": "0"})
        self.assertEqual(self.connection.execute("select count(*) from owner_watchlist").fetchone()[0], 4)

    def test_ticket_record_is_record_only(self):
        result = stitch_data.record_ticket(self.connection, {
            "owner_name": "Bobby", "book": "Recorded Book", "stake": 10, "potential_payout": 19.09,
            "legs": [{"sport": "NBA", "game_id": "1", "player_id": "2", "player": "Recorded Player", "team": "BOS", "market": "Points", "line": 20.5, "side": "OVER", "odds": -110}],
        })
        self.assertTrue(result["recorded"])
        self.assertFalse(result["wager_placed"])
        ticket = self.connection.execute("select * from tickets where id=?", (result["ticket_id"],)).fetchone()
        self.assertEqual(ticket["environment"], "TEST")
        self.assertEqual(ticket["status"], "OPEN")
        self.assertEqual(self.connection.execute("select count(*) from ticket_legs").fetchone()[0], 1)

    def test_ticket_rejects_missing_market_inputs(self):
        with self.assertRaises(ValueError):
            stitch_data.record_ticket(self.connection, {"book": "B", "stake": 10, "potential_payout": 20, "legs": [{"sport": "NBA", "player": "P", "market": "Points", "side": "OVER"}]})


class StitchProductionContractTests(unittest.TestCase):
    def test_production_contract_has_real_data_and_no_synthetic_provider(self):
        connection = stitch_data.connect()
        try:
            data = stitch_data.bootstrap(connection, "live_today_scoreboard")
        finally:
            connection.close()
        self.assertTrue(data["games"])
        self.assertEqual(data["global"]["health"]["database"], "ok")
        providers = {item["provider"]: item for item in data["providers"]}
        self.assertTrue(providers["ESPN"]["connected"])
        self.assertFalse(providers["MULTI_BOOK_ODDS"]["connected"])
        for market in data["markets"]:
            self.assertIsNone(market["odds"])


if __name__ == "__main__":
    unittest.main()
