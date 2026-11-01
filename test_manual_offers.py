"""Synthetic fixture tests only. No production offers or ticket placement."""
import copy
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from challenge_ledger import initialize, snapshot
from verified_offers import initialize_gateway, scan_offers
from manual_offers import save_manual_offer, save_offer_entry, eligible_contexts
import dashboard


class ManualOfferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='liveintel-intake-test-')
        self.path = str(Path(self.tmp.name) / 'fixture.db')
        self.c = sqlite3.connect(self.path)
        self.c.row_factory = sqlite3.Row
        from zoneinfo import ZoneInfo
        # Dependent board/history fixtures use clock.date() for slate records.
        # Keep the same instant in the slate zone across the UTC midnight boundary.
        self.clock = datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York'))
        self.day = self.clock.date().isoformat()
        self.c.executescript('''
        CREATE TABLE games(id TEXT PRIMARY KEY,sport TEXT,home_team TEXT,away_team TEXT,status TEXT);
        CREATE TABLE multi_sport_players(player_id TEXT,player_name TEXT,team TEXT,game_date TEXT,game_id TEXT,sport TEXT);
        CREATE TABLE prop_lines(id INTEGER PRIMARY KEY,game_id TEXT,player_name TEXT,market TEXT,line REAL,side TEXT,book TEXT,observed_at TEXT);
        CREATE TABLE tickets(id INTEGER PRIMARY KEY,owner_name TEXT,stake INTEGER);
        CREATE TABLE ticket_legs(id INTEGER PRIMARY KEY,ticket_id INTEGER);
        CREATE TABLE bankroll_accounts(owner TEXT,balance INTEGER);
        INSERT INTO tickets VALUES(1,'FIXTURE_OWNER',10);
        INSERT INTO ticket_legs VALUES(1,1);
        INSERT INTO bankroll_accounts VALUES('FIXTURE_OWNER',10000);
        ''')
        self.c.execute('INSERT INTO games VALUES(?,?,?,?,?)', ('fixture-game','NBA','HOME','AWAY','PRE'))
        self.c.execute('INSERT INTO multi_sport_players VALUES(?,?,?,?,?,?)', ('123','Fixture Player','HOME',self.day,'fixture-game','NBA'))
        self.c.execute('INSERT INTO multi_sport_players VALUES(?,?,?,?,?,?)', ('456','Second Fixture Player','AWAY',self.day,'fixture-game','NBA'))
        self.c.commit()
        initialize(self.c)
        initialize_gateway(self.c)
        self.data = dict(book='SYNTHETIC TEST BOOK', ticket_type='STRAIGHT', source='SYNTHETIC FIXTURE',
            source_captured_at=self.clock.isoformat(), freshness_ttl=120, number_of_legs=1,
            stake='10.00', potential_payout='20.00', odds_format='decimal', odds_decimal='2',
            legs=[dict(sport='NBA', game_id='fixture-game', player_id='123', player='Fixture Player',
                       player_team='HOME', market='SYNTHETIC TEST MARKET', line='1.5', side='OVER')])
        self.baseline = self.protected()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def protected(self):
        tables = ['tickets','ticket_legs','bankroll_accounts','challenge_runs','challenge_tickets','challenge_ticket_legs','challenge_ticket_risk']
        return {t:[tuple(r) for r in self.c.execute('SELECT * FROM '+t)] for t in tables}

    def save(self, data=None):
        result = save_manual_offer(self.c, data or self.data, self.clock)
        self.assertEqual(self.protected(), self.baseline)
        return result

    def reject(self, data=None):
        result = self.save(data)
        self.assertEqual(result['status'], 'REJECTED', result)
        self.assertFalse(result['saved'])
        self.assertIsNone(result['offer_id'])
        self.assertEqual(self.c.execute('SELECT count(*) FROM prop_lines').fetchone()[0], 0)
        self.assertEqual(self.c.execute('SELECT count(*) FROM verified_manual_offer_terms').fetchone()[0], 0)
        return result

    def test_VALID_STRAIGHT_SAVE(self):
        result = self.save()
        self.assertTrue(result['saved'], result)
        self.assertEqual(result['status'], 'VALID')
        self.assertEqual(result['ai_id'], 'BASKETBALL_AI')
        self.assertTrue(result['offer_id'].startswith('manual:'))
        proposals, _ = scan_offers(self.c, self.clock)
        self.assertEqual(len(proposals), 1)
        terms = json.loads(self.c.execute('SELECT terms_json FROM verified_manual_offer_terms').fetchone()[0])
        self.assertEqual(datetime.fromisoformat(terms['source_captured_at']), datetime.fromisoformat(self.data['source_captured_at']))
        self.assertEqual(terms['source'], 'SYNTHETIC FIXTURE')

    def test_MISSING_ODDS_REJECTED(self):
        self.data.pop('odds_decimal')
        self.reject()

    def test_INVALID_PLAYER_GAME_REJECTED(self):
        self.data['legs'][0]['player_id'] = '999'
        self.reject()

    def test_FINAL_GAME_REJECTED(self):
        self.c.execute("UPDATE games SET status='FINAL'")
        self.c.commit()
        self.assertEqual(eligible_contexts(self.c, self.clock), [])
        self.reject()

    def entry(self, kind):
        self.data['ticket_type'] = kind
        self.data['number_of_legs'] = 2
        self.data['legs'].append(dict(self.data['legs'][0], player_id='456', player='Second Fixture Player', player_team='AWAY'))
        if kind == 'PICKEM':
            self.data.pop('odds_decimal')
            self.data.update(odds_format='entry', payout_multiplier='2')

    def test_PICKEM_WITHOUT_PAYOUT_RULE_REJECTED(self):
        self.entry('PICKEM')
        self.reject()

    def test_PICKEM_EXPLICIT_RULE_SAVES(self):
        self.entry('PICKEM')
        self.data['payout_structure'] = {'WIN|WIN':'2', 'LOSS|LOSS':'0'}
        self.assertTrue(self.save()['saved'])

    def test_PICKEM_MISSING_MULTIPLIER_REJECTED(self):
        self.entry('PICKEM')
        self.data.pop('payout_multiplier')
        self.data['payout_structure'] = {'WIN|WIN':'2'}
        self.reject()

    def test_PARLAY_COMPLETE_COMBINED_TERMS(self):
        self.entry('PARLAY')
        self.assertTrue(self.save()['saved'])

    def test_PARLAY_EXPLICIT_PAYOUT_RULE(self):
        self.entry('PARLAY')
        self.data.pop('odds_decimal')
        self.data.update(odds_format='payout_rule', payout_structure={'WIN|WIN':'2'})
        self.assertTrue(self.save()['saved'])

    def test_PARLAY_INCOMPLETE_LEGS_REJECTED(self):
        self.entry('PARLAY')
        self.data['legs'][1].pop('market')
        self.reject()

    def test_SAVE_CREATES_NO_CHALLENGE_TICKET(self):
        self.assertTrue(self.save()['saved'])
        self.assertEqual(self.c.execute('SELECT count(*) FROM challenge_tickets').fetchone()[0], 0)

    def test_SAVE_CHANGES_NO_BANKROLL(self):
        before = snapshot(self.c, 'BASKETBALL_AI')
        self.assertTrue(self.save()['saved'])
        self.assertEqual(snapshot(self.c, 'BASKETBALL_AI'), before)

    def test_OWNER_TICKETS_UNCHANGED(self):
        self.assertTrue(self.save()['saved'])
        self.assertEqual(self.protected(), self.baseline)

    def test_STALE_SOURCE_AND_WRONG_DATE_REJECTED(self):
        self.data['source_captured_at'] = (self.clock-timedelta(seconds=121)).isoformat()
        self.reject()
        self.data['source_captured_at'] = self.clock.isoformat()
        self.c.execute("UPDATE multi_sport_players SET game_date='2000-01-01'")
        self.c.commit()
        self.reject()

    def test_PLAYER_TEAM_AND_AMBIGUITY_REJECTED(self):
        self.data['legs'][0]['player_team'] = 'AWAY'
        self.reject()
        self.data['legs'][0]['player_team'] = 'HOME'
        self.c.execute('INSERT INTO multi_sport_players VALUES(?,?,?,?,?,?)', ('789','Fixture Player','HOME',self.day,'fixture-game','NBA'))
        self.c.commit()
        self.assertEqual(len(eligible_contexts(self.c, self.clock)), 1)
        self.reject()

    def test_AMERICAN_ODDS_AND_PAYOUT_MATH(self):
        self.data.pop('odds_decimal')
        self.data.update(odds_format='american', american_odds='-110', stake='11', potential_payout='21')
        self.assertTrue(self.save()['saved'])

    def test_CONFLICTING_PAYOUT_REJECTED(self):
        self.data['potential_payout'] = '19.99'
        self.reject()

    def test_REQUIRED_OWNER_FIELDS(self):
        for key in ['source','source_captured_at','book','stake','potential_payout','odds_format']:
            data = copy.deepcopy(self.data)
            data.pop(key)
            self.reject(data)
        for key in ['player_id','player_team','line','market','side']:
            data = copy.deepcopy(self.data)
            data['legs'][0].pop(key)
            self.reject(data)

    def test_TTL_STARTS_AT_CAPTURE(self):
        self.data.update(source_captured_at=(self.clock-timedelta(seconds=100)).isoformat(), freshness_ttl=60)
        self.reject()

    def test_EXISTING_INCOMPLETE_REMAINS_REJECTED(self):
        self.c.execute("INSERT INTO prop_lines VALUES(1,'fixture-game','Fixture Player','SYNTHETIC',1,'OVER','SYNTHETIC',?)", (self.clock.isoformat(),))
        self.c.commit()
        self.assertTrue(self.save()['saved'])
        _, offers = scan_offers(self.c, self.clock)
        incomplete = next(o for o in offers if o['OFFER_ID'] == 'prop_lines:1')
        self.assertEqual(incomplete['VALIDATION_STATUS'], 'REJECTED')

    def simple_data(self):
        return dict(entry_mode='simple', book='SYNTHETIC TEST BOOK', stake='11', odds_format='american',
            american_odds='-110', legs=[dict(sport='NBA', game_id='fixture-game', player_id='123',
                market='SYNTHETIC TEST MARKET', line='1.5', side='MORE')])

    def test_SIMPLE_DEFAULTS_AND_AUTO_PAYOUT(self):
        result = save_offer_entry(self.c, self.simple_data(), self.clock)
        self.assertTrue(result['saved'], result)
        terms = json.loads(self.c.execute('SELECT terms_json FROM verified_manual_offer_terms').fetchone()[0])
        self.assertEqual(terms['source'], 'OWNER_MANUAL')
        self.assertEqual(terms['ticket_type'], 'STRAIGHT')
        self.assertEqual(datetime.fromisoformat(terms['source_captured_at']), self.clock)
        self.assertEqual(datetime.fromisoformat(terms['expires_at']), self.clock+timedelta(seconds=120))
        self.assertEqual(terms['potential_payout'], 21)
        self.assertEqual(terms['ai_id'], 'BASKETBALL_AI')
        leg = terms['declared_legs'][0]
        self.assertEqual((leg['player'], leg['player_team']), ('Fixture Player', 'HOME'))
        self.assertEqual(len(terms['declared_legs']), 1)
        self.assertEqual(self.protected(), self.baseline)

    def test_SIMPLE_DECIMAL_PAYOUT_ROUNDING(self):
        data = self.simple_data()
        data.pop('american_odds')
        data.update(odds_format='decimal', odds_decimal='1.915', stake='1')
        result = save_offer_entry(self.c, data, self.clock)
        self.assertTrue(result['saved'], result)
        self.assertEqual(result['offer']['POTENTIAL_PAYOUT'], 1.92)

    def test_SIMPLE_GATEWAY_REJECTIONS_PRESERVED(self):
        for update in [dict(american_odds='-99'), dict(book='manual_entry'), dict(potential_payout='99'),
                       dict(number_of_legs=2), dict(ticket_type='PICKEM'), dict(source='OTHER')]:
            data = dict(self.simple_data(), **update)
            self.assertFalse(save_offer_entry(self.c, data, self.clock)['saved'])
        data = self.simple_data()
        data['legs'][0]['player_team'] = 'AWAY'
        self.assertFalse(save_offer_entry(self.c, data, self.clock)['saved'])
        self.c.execute("UPDATE games SET status='FINAL'")
        self.c.commit()
        self.assertFalse(save_offer_entry(self.c, self.simple_data(), self.clock)['saved'])
        self.assertEqual(self.protected(), self.baseline)
        self.assertEqual(self.c.execute('SELECT count(*) FROM prop_lines').fetchone()[0], 0)

    def test_ADVANCED_CAPTURE_FRESHNESS_UNCHANGED(self):
        data = dict(self.simple_data(), entry_mode='advanced', source_captured_at=(self.clock-timedelta(seconds=121)).isoformat())
        self.assertFalse(save_offer_entry(self.c, data, self.clock)['saved'])
        data['source_captured_at'] = self.clock.isoformat()
        data['offer_expires_at'] = (self.clock-timedelta(seconds=1)).isoformat()
        self.assertFalse(save_offer_entry(self.c, data, self.clock)['saved'])

    def test_ADVANCED_PICKEM_REQUIRES_EXPLICIT_RULES(self):
        self.entry('PICKEM')
        data = dict(self.data, entry_mode='advanced', source='OWNER_MANUAL')
        result = save_offer_entry(self.c, data, self.clock)
        self.assertFalse(result['saved'])
        data['payout_structure'] = {'WIN|WIN':'2', 'LOSS|LOSS':'0'}
        result = save_offer_entry(self.c, data, self.clock)
        self.assertTrue(result['saved'], result)
        self.assertEqual(self.protected(), self.baseline)

    def test_ADVANCED_PARLAY_EXPLICIT_COMBINED_ODDS(self):
        self.entry('PARLAY')
        data = dict(self.data, entry_mode='advanced', source='OWNER_MANUAL')
        self.assertTrue(save_offer_entry(self.c, data, self.clock)['saved'])
        self.assertEqual(self.protected(), self.baseline)

    def test_SIMPLE_HTTP_SAVE_AUTO_FIELDS(self):
        status, body, _ = self.dispatch('POST', '/api/manual-offer', self.simple_data())
        self.assertEqual(status, 201, body)
        self.assertEqual(body['offer']['POTENTIAL_PAYOUT'], 21)
        self.assertEqual(body['tickets_created'], 0)
        self.assertEqual(self.protected(), self.baseline)

    def dispatch(self, method, path, data=None):
        # Exercise the actual HTTP parser and handler without opening a network socket.
        payload = json.dumps(data).encode() if data is not None else b''
        raw = (method+' '+path+' HTTP/1.0\r\nContent-Type: application/json\r\nContent-Length: '+str(len(payload))+'\r\n\r\n').encode()+payload
        handler = dashboard.DashboardHandler.__new__(dashboard.DashboardHandler)
        handler.rfile = BytesIO(raw)
        handler.wfile = BytesIO()
        handler.client_address = ('127.0.0.1', 0)
        handler.server = SimpleNamespace(server_name='fixture',server_port=0)
        with patch.object(dashboard, 'DB_PATH', self.path):
            handler.handle_one_request()
        headers, body = handler.wfile.getvalue().split(b'\r\n\r\n',1)
        return int(headers.split()[1]), json.loads(body), headers

    def test_HTTP_CONTEXTS_SAVE_AND_REJECT(self):
        status, body, headers = self.dispatch('GET', '/api/manual-offer-contexts')
        self.assertEqual(status, 200)
        self.assertIn(b'Cache-Control: no-store', headers)
        self.assertEqual(len(body['contexts']), 2)
        status, body, _ = self.dispatch('POST', '/api/manual-offer', self.data)
        self.assertEqual(status, 201, body)
        self.assertEqual(body['ai_id'], 'BASKETBALL_AI')
        self.data['legs'][0]['player_id'] = '999'
        status, body, _ = self.dispatch('POST', '/api/manual-offer', self.data)
        self.assertEqual(status, 422)
        self.assertEqual(body['status'], 'REJECTED')
        self.assertEqual(self.protected(), self.baseline)


if __name__ == '__main__':
    unittest.main(verbosity=2)
