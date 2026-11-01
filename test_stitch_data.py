import json
import sqlite3
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

    def test_stats_extraction_uses_recorded_values(self):
        payload = {"shooting": {"FG": "8/15", "3PT": "3/7"}, "totals": {"points": 22, "assists": 6}}
        self.assertEqual(stitch_data.totals(json.dumps(payload))["points"], 22)
        self.assertEqual(stitch_data.totals(payload)["fga"], 15)


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
