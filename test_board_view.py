"""Operational source contracts. All mutations are confined to synthetic SQLite fixtures."""
import json
import sqlite3
import unittest
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode

import dashboard
from board_view import board_data, current_games, players
import test_manual_offers


class BoardViewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_manual_offers.ManualOfferTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.c, self.clock = self.fixture.c, self.fixture.clock
        self.c.executescript('''
        ALTER TABLE games ADD COLUMN last_updated TEXT;
        ALTER TABLE multi_sport_players ADD COLUMN position TEXT;
        ALTER TABLE multi_sport_players ADD COLUMN points INTEGER;
        ALTER TABLE multi_sport_players ADD COLUMN live_stats_available INTEGER;
        ALTER TABLE multi_sport_players ADD COLUMN last_updated TEXT;
        CREATE TABLE game_state(game_id TEXT,period INTEGER,clock TEXT,away_score INTEGER,home_score INTEGER,
            possession TEXT,down INTEGER,distance INTEGER,game_script TEXT,red_zone INTEGER,last_updated TEXT);
        CREATE TABLE owner_watchlist(id INTEGER PRIMARY KEY,player_id TEXT,player_name TEXT,sport TEXT,team TEXT,is_pinned INTEGER,added_at TEXT);
        DROP TABLE tickets;
        DROP TABLE ticket_legs;
        DROP TABLE bankroll_accounts;
        CREATE TABLE tickets(id INTEGER PRIMARY KEY,owner_name TEXT,environment TEXT,sportsbook_platform TEXT,
            wager_amount REAL,potential_payout REAL,actual_payout REAL,status TEXT,date_time_placed TEXT,settled_at TEXT,ticket_type TEXT,notes TEXT);
        CREATE TABLE ticket_legs(id INTEGER PRIMARY KEY,ticket_id INTEGER,sport TEXT,game_id TEXT,player_id TEXT,team TEXT,
            player TEXT,market TEXT,line REAL,side TEXT,odds REAL,leg_status TEXT);
        CREATE TABLE bankroll_accounts(owner_name TEXT,environment TEXT,current_bankroll REAL);
        CREATE TABLE nfl_ai_bankroll(current_bankroll REAL);
        INSERT INTO nfl_ai_bankroll VALUES(373);
        CREATE TABLE cfb_ai_bankroll(current_bankroll REAL);
        INSERT INTO cfb_ai_bankroll VALUES(191);
        ''')
        self.c.execute('UPDATE games SET last_updated=?',(self.clock.isoformat(),))
        for name in ['Early Test','Test','Test2','Test Owner','Parlay Owner','TEST_USER','NFL_AI','CFB_AI']:
            self.c.execute("INSERT INTO tickets(owner_name,environment,sportsbook_platform,wager_amount,potential_payout,status) VALUES(?,'REAL','SYNTHETIC',10,20,'PENDING')",(name,))
        self.c.commit()

    def test_only_verified_challenge_balances_and_four_desks(self):
        def access(action, table, col, db, source):
            if action==sqlite3.SQLITE_READ and table in ('nfl_ai_bankroll','cfb_ai_bankroll'):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        self.c.set_authorizer(access)
        data = board_data(self.c,clock=self.clock)
        self.assertEqual([s['bankroll'] for s in data['specialists']], [100,100,100])
        self.assertEqual(len(data['specialists'])+1, 4)
        self.assertEqual(data['bobby']['name'],'BOBBY')
        self.assertEqual(data['tickets'],[])
        self.assertEqual(data['offers'],[])

    def test_today_is_slate_date_never_update_date(self):
        for gid, day in [('old',(self.clock-timedelta(days=1)).date().isoformat()),('future',(self.clock+timedelta(days=1)).date().isoformat())]:
            self.c.execute('INSERT INTO games VALUES(?,?,?,?,?,?)',(gid,'NFL','H','A','LIVE',self.clock.isoformat()))
            self.c.execute('INSERT INTO multi_sport_players(player_id,player_name,team,game_date,game_id,sport) VALUES(?,?,?,?,?,?)',('999','Wrong-day Player','H',day,gid,'NFL'))
        self.c.commit()
        games=current_games(self.c,self.clock)
        self.assertEqual([g['id'] for g in games],['fixture-game'])

    def test_grouped_current_games_matches_original_date_and_sport_rules(self):
        day=self.clock.date().isoformat()
        yesterday=(self.clock-timedelta(days=1)).date().isoformat()
        for gid,dates in [('none',[None]),('mixed',[day,yesterday]),('valid',[day,day+'T15:00:00Z']),('null_mixed',[day,None]),('empty',[])]:
            self.c.execute('INSERT INTO games VALUES(?,?,?,?,?,?)',(gid,'NFL','H','A','PRE',self.clock.isoformat()))
            for i,date in enumerate(dates):
                self.c.execute('INSERT INTO multi_sport_players(player_id,player_name,team,game_date,game_id,sport) VALUES(?,?,?,?,?,?)',(str(500+i),'Fixture','H',date,gid,'NFL'))
        # Another sport's rows cannot exclude an otherwise valid game.
        self.c.execute('INSERT INTO multi_sport_players(player_id,player_name,team,game_date,game_id,sport) VALUES(?,?,?,?,?,?)',('900','Other','H',yesterday,'valid','CFB'))
        expected=[r['id'] for r in self.c.execute('''SELECT g.id FROM games g WHERE EXISTS
            (SELECT 1 FROM multi_sport_players p WHERE p.game_id=g.id AND p.sport=g.sport AND substr(p.game_date,1,10)=?)
            AND NOT EXISTS (SELECT 1 FROM multi_sport_players p WHERE p.game_id=g.id AND p.sport=g.sport
            AND (p.game_date IS NULL OR substr(p.game_date,1,10)<>?)) ORDER BY g.sport,g.id''',(day,day))]
        self.assertEqual([g['id'] for g in current_games(self.c,self.clock)],expected)
        self.assertEqual(expected,['fixture-game','valid'])

    def test_stale_halftime_cannot_remain_live(self):
        self.c.execute("UPDATE games SET status='HALFTIME',last_updated=?",((self.clock-timedelta(hours=2)).isoformat(),))
        self.c.execute('INSERT INTO game_state VALUES(?,?,?,?,?,?,?,?,?,?,?)',('fixture-game',2,'HALFTIME',10,10,'HOME',1,10,'HALFTIME',0,self.clock.isoformat()))
        self.c.commit()
        self.assertEqual(current_games(self.c,self.clock)[0]['category'],'UNAVAILABLE')
        self.c.execute("UPDATE games SET status='FINAL',last_updated=?",(self.clock.isoformat(),))
        self.c.commit()
        g=current_games(self.c,self.clock)[0]
        self.assertEqual(g['category'],'FINAL')
        self.assertNotEqual(g['status'],'LIVE')

    def test_players_require_game_and_pregame_hides_zeroes(self):
        self.c.execute('UPDATE multi_sport_players SET points=0,live_stats_available=1,last_updated=?',(self.clock.isoformat(),))
        self.c.commit()
        games=current_games(self.c,self.clock)
        self.assertEqual(players(self.c,games,self.clock,{})['players'],[])
        found=players(self.c,games,self.clock,dict(sport='NBA',game='fixture-game'))
        self.assertEqual(len(found['players']),2)
        self.assertTrue(all(p['stats']==[] for p in found['players']))
        self.assertEqual(players(self.c,games,self.clock,dict(sport='NFL',game='fixture-game'))['players'],[])
        self.assertEqual(players(self.c,games,self.clock,dict(sport='NBA',game='fixture-game',team='HOME'))['total'],1)

    def test_players_paginated_and_searchable(self):
        for i in range(100):
            self.c.execute('INSERT INTO multi_sport_players(player_id,player_name,team,game_date,game_id,sport) VALUES(?,?,?,?,?,?)',(str(1000+i),'Fixture '+str(i),'HOME',self.fixture.day,'fixture-game','NBA'))
        self.c.commit()
        games=current_games(self.c,self.clock)
        response=players(self.c,games,self.clock,dict(sport='NBA',game='fixture-game'))
        self.assertEqual(len(response['players']),30)
        self.assertEqual(response['total'],102)
        self.assertEqual(players(self.c,games,self.clock,dict(sport='NBA',game='fixture-game',q='Second'))['total'],1)

    def test_only_five_pinned_players_and_no_game_state(self):
        for i in range(8):
            self.c.execute('INSERT INTO owner_watchlist VALUES(?,?,?,?,?,?,?)',(i,str(2000+i),'Pinned '+str(i),'NFL','TEAM',1,self.clock.isoformat()))
        self.c.execute('INSERT INTO owner_watchlist VALUES(?,?,?,?,?,?,?)',(99,'9999','Not pinned','NBA','HOME',0,self.clock.isoformat()))
        self.c.commit()
        data=board_data(self.c,clock=self.clock)
        self.assertEqual(len(data['watch']),5)
        self.assertTrue(all(w['status']=='NO GAME TODAY' for w in data['watch']))
        self.assertNotIn('Not pinned',json.dumps(data['watch']))

    def test_owner_operational_data_and_bobby_metrics(self):
        self.c.execute("INSERT INTO tickets(owner_name,environment,sportsbook_platform,wager_amount,potential_payout,status) VALUES('Bobby','REAL','SYNTHETIC',20,40,'PENDING')")
        self.c.execute("INSERT INTO bankroll_accounts VALUES('Bobby','REAL',250)")
        self.c.commit()
        data=board_data(self.c,clock=self.clock)
        self.assertEqual(len(data['tickets']),1)
        self.assertEqual(data['bobby']['bankroll'],250)
        self.assertEqual(data['bobby']['exposure'],20)
        self.assertEqual(data['bobby']['open_count'],1)
        for name in ['TEST_USER','Early Test','Test Owner']:
            self.assertNotIn(name,json.dumps(data))

    def test_no_old_intelligence_or_prop_sources(self):
        self.c.execute("INSERT INTO prop_lines(game_id,player_name,market,line,side,book,observed_at) VALUES('old','Bryce Young','SYNTHETIC',220.5,'MORE','SYNTHETIC','2000-01-01')")
        self.c.commit()
        data=board_data(self.c,clock=self.clock)
        self.assertEqual(data['intelligence'],dict(alerts=[],situations=[],impacts=[],opportunities=[]))
        self.assertNotIn('Bryce Young',json.dumps(data))

    def test_projection_is_read_only(self):
        self.c.execute('PRAGMA query_only=ON')
        self.c.execute('BEGIN')
        before=self.c.total_changes
        board_data(self.c,clock=self.clock)
        self.assertEqual(self.c.total_changes,before)
        self.c.rollback()

    def test_board_http_is_read_only_and_excludes_legacy(self):
        before = self.c.total_changes
        status, body, headers = self.fixture.dispatch('GET','/api/board')
        self.assertEqual(status,200)
        self.assertIn(b'Cache-Control: no-store',headers)
        self.assertEqual(body['tickets'],[])
        self.assertEqual(len(body['specialists']),3)
        self.assertEqual(self.c.total_changes,before)

    def test_schedule_hydrates_known_same_date_teams_only(self):
        import board_view
        from io import BytesIO
        game = current_games(self.c,self.clock)[0]
        event = dict(id='fixture-game',date=self.fixture.day+'T23:00:00Z',competitions=[dict(competitors=[
            dict(homeAway='home',team=dict(id='1',abbreviation='HOME')),
            dict(homeAway='away',team=dict(id='2',abbreviation='AWAY'))])])
        payload = json.dumps(dict(events=[event,dict(event,id='unlinked-game')])).encode()
        board_view._SCHEDULE_CACHE.update(day='',at=None,times={})
        with patch('urllib.request.urlopen',return_value=BytesIO(payload)):
            times=board_view.schedule_times([game],self.clock)
        self.assertEqual(set(times),{'fixture-game'})
        self.assertIn('ET',times['fixture-game'])
        board_view._SCHEDULE_CACHE.update(day='',at=None,times={})

    def qualified_fixture(self):
        from manual_offers import save_manual_offer
        from verified_offers import scan_offers
        from challenge_decisions import initialize_decisions, evaluate
        self.c.execute('CREATE TABLE ai_wager_rules(ai_id TEXT,rule_name TEXT,rule_value TEXT)')
        self.c.executemany('INSERT INTO ai_wager_rules VALUES(?,?,?)',[
            ('BASKETBALL_AI','fifty_confidence_threshold','0.6'),
            ('BASKETBALL_AI','hundred_confidence_threshold','0.9')])
        self.c.commit()
        initialize_decisions(self.c)
        saved=save_manual_offer(self.c,self.fixture.data,self.clock)
        self.assertTrue(saved['saved'],saved)
        proposal=scan_offers(self.c,self.clock)[0][0]
        proposal.update(qualified=True,confidence_score='0.8',reason='Synthetic qualification for isolated test')
        result=evaluate(self.c,'BASKETBALL_AI',proposal,self.clock)
        self.assertIsNotNone(result['ticket_id'],result)

    def test_current_pick_requires_verified_offer_ticket_and_decision(self):
        self.qualified_fixture()
        data=board_data(self.c,clock=self.clock+timedelta(seconds=1))
        ai=data['specialists'][0]
        self.assertEqual(ai['pick']['legs'][0]['player'],'Fixture Player')
        self.assertEqual(ai['bankroll'],100)
        self.assertEqual(ai['exposure'],10)
        self.assertEqual(ai['pick']['payout'],20)
        self.assertEqual(data['tickets'],[])

    def test_expired_pick_disappears_exposure_remains(self):
        self.qualified_fixture()
        data=board_data(self.c,clock=self.clock+timedelta(seconds=121))
        self.assertIsNone(data['specialists'][0]['pick'])
        self.assertEqual(data['specialists'][0]['exposure'],10)

    def test_final_game_cannot_surface_as_current_pick(self):
        self.qualified_fixture()
        self.c.execute("UPDATE games SET status='FINAL'")
        self.c.commit()
        self.assertIsNone(board_data(self.c,clock=self.clock)['specialists'][0]['pick'])

    def test_owner_writer_keeps_each_leg_context(self):
        params=dict(owner_name='Bobby',environment='REAL',sportsbook_platform='SYNTHETIC',wager_amount='10',potential_payout='30')
        for n,sport,gid,pid in [(1,'NBA','fixture-game','123'),(2,'CFB','other-fixture','456')]:
            for k,v in dict(sport=sport,game_id=gid,player='Fixture '+pid,player_id=pid,team='HOME',market='SYNTHETIC',line='1.5',side='MORE',odds='3').items():
                params[f'leg_{n}_{k}']=v
        body=urlencode(params).encode()
        handler=dashboard.DashboardHandler.__new__(dashboard.DashboardHandler)
        handler.rfile=BytesIO(b'POST /api/save-ticket HTTP/1.0\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
        handler.wfile=BytesIO();handler.client_address=('127.0.0.1',0);handler.server=SimpleNamespace(server_name='fixture',server_port=0)
        with patch.object(dashboard,'DB_PATH',self.fixture.path):
            handler.handle_one_request()
        result=json.loads(handler.wfile.getvalue().split(b'\r\n\r\n')[1])
        self.assertTrue(result['success'],result)
        legs=self.c.execute('SELECT sport,game_id,player_id,team FROM ticket_legs ORDER BY id').fetchall()
        self.assertEqual([tuple(l) for l in legs],[('NBA','fixture-game','123','HOME'),('CFB','other-fixture','456','HOME')])
        self.assertEqual(self.c.execute('SELECT count(*) FROM challenge_tickets').fetchone()[0],0)


if __name__=='__main__':
    unittest.main()
