"""Synthetic dated box scores and isolated owner-ticket integration checks."""
import json
import unittest
from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode

import dashboard
import test_board_view
from heat_view import heat_data
from board_view import board_data


class HeatWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_board_view.BoardViewTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.c,self.clock=self.fixture.c,self.fixture.clock
        self.c.executescript('''ALTER TABLE multi_sport_players ADD COLUMN slate_date TEXT;
            CREATE TABLE player_game_stats(game_id TEXT,player_id TEXT,player_name TEXT,team TEXT,stats_json TEXT,segment TEXT,last_updated TEXT);''')
        self.c.execute("UPDATE games SET sport='CFB'")
        self.c.execute("UPDATE multi_sport_players SET sport='CFB',slate_date=game_date")
        self.c.commit()

    def history(self,values):
        for i,value in enumerate(values):
            day=(self.clock-timedelta(days=i+1)).date().isoformat()
            gid='past-'+str(i)
            self.c.execute('INSERT INTO games VALUES(?,?,?,?,?,?)',(gid,'CFB','HOME','AWAY','FINAL',self.clock.isoformat()))
            self.c.execute('INSERT INTO multi_sport_players(player_id,player_name,team,game_date,slate_date,game_id,sport) VALUES(?,?,?,?,?,?,?)',('123','Fixture Player','HOME',day,day,gid,'CFB'))
            payload={'receiving':{'YDS':str(value)}} if value is not None else {'receiving':{}}
            self.c.execute('INSERT INTO player_game_stats VALUES(?,?,?,?,?,?,?)',(gid,'123','Fixture Player','HOME',json.dumps(payload),'FULL GAME',self.clock.isoformat()))
        self.c.commit()

    def test_actual_recent_patterns_without_invented_line_or_price(self):
        values=[78,91,66,84,71]
        self.history(values)
        card=next(c for c in heat_data(self.c,clock=self.clock)['cards'] if c['market']=='Receiving Yards')
        self.assertEqual((card['hits'],card['total']),(5,5))
        self.assertEqual(card['values'],values)
        self.assertTrue(card['milestone'])
        self.assertIsNone(card['current_line'])
        self.assertIsNone(card['odds'])
        self.assertNotIn('true_probability',card)
        self.assertEqual(card['game_id'],'fixture-game')

    def test_bulk_history_matches_paginated_results_and_avoids_correlated_scan(self):
        self.history([78,91,66,84,71])
        statements=[]
        self.c.set_trace_callback(statements.append)
        extra=[dict(player='Team '+str(i),market='Recorded wins',hits=4,total=4,kind='team') for i in range(40)]
        with patch('heat_view.team_patterns',return_value=extra):
            all_results=heat_data(self.c,clock=self.clock,all_cards=True)
            paged=[]
            for offset in range(0,all_results['total'],30):
                paged.extend(heat_data(self.c,{'offset':offset},self.clock)['cards'])
        self.c.set_trace_callback(None)
        self.assertEqual(paged,all_results['cards'])
        history_queries=[sql for sql in statements if sql.startswith('SELECT h.*,g.sport')]
        self.assertTrue(history_queries)
        self.assertFalse(any('EXISTS (SELECT 1 FROM multi_sport_players p' in sql for sql in history_queries))

    def test_bulk_math_history_preserves_identity_and_dates(self):
        from math_lab import history,history_source
        self.history([78,91,66,84,71])
        player=dict(self.c.execute("SELECT * FROM multi_sport_players WHERE game_id='fixture-game'").fetchone())
        day=self.clock.date().isoformat()
        direct=history(self.c,player,day)
        self.assertEqual(history(self.c,player,day,history_source(self.c)),direct)
        self.assertEqual(len(direct),5)

    def test_undated_history_cannot_make_recent_claims(self):
        self.history([100]*8)
        self.c.execute("UPDATE multi_sport_players SET slate_date=NULL WHERE game_id LIKE 'past-%'")
        self.c.commit()
        self.assertEqual(heat_data(self.c,clock=self.clock)['cards'],[])

    def test_missing_stats_are_not_zeroes_or_skipped_to_make_hits(self):
        self.history([100,None,100,100,100,100,100,100])
        self.assertEqual(heat_data(self.c,clock=self.clock)['cards'],[])

    def test_only_completed_games_and_matching_identity(self):
        self.history([100]*4)
        self.c.execute("UPDATE games SET status='PRE' WHERE id='past-0'")
        self.c.commit()
        self.assertEqual(heat_data(self.c,clock=self.clock)['cards'],[])
        self.c.execute("UPDATE games SET status='FINAL' WHERE id='past-0'")
        self.c.execute("UPDATE player_game_stats SET player_name='Different identity' WHERE game_id='past-0'")
        self.c.commit()
        self.assertEqual(heat_data(self.c,clock=self.clock)['cards'],[])

    def test_unique_games_not_duplicate_rows(self):
        self.history([100]*3)
        self.c.execute("INSERT INTO player_game_stats SELECT * FROM player_game_stats WHERE game_id='past-0'")
        self.c.commit()
        self.assertEqual(heat_data(self.c,clock=self.clock)['cards'],[])

    def test_real_current_verified_offer_can_supply_line_and_odds(self):
        from manual_offers import save_manual_offer
        self.history([78,91,66,84,71])
        data=dict(self.fixture.fixture.data)
        data['legs']=[dict(data['legs'][0],sport='CFB',market='Receiving Yards',line='62.5',side='OVER')]
        result=save_manual_offer(self.c,data,self.clock)
        self.assertTrue(result['saved'],result)
        card=next(c for c in heat_data(self.c,clock=self.clock)['cards'] if c['market']=='Receiving Yards')
        self.assertEqual(card['current_line'],62.5)
        self.assertEqual(card['odds'],'2')
        self.assertFalse(card['milestone'])
        self.assertEqual((card['hits'],card['total']),(5,5))

    def test_heat_read_only_and_filters(self):
        self.history([100]*8)
        self.c.execute('PRAGMA query_only=ON')
        before=self.c.total_changes
        result=board_data(self.c,'heat',dict(sport='NFL'),self.clock)
        self.assertEqual(result['cards'],[])
        self.assertEqual(self.c.total_changes,before)
        self.assertGreater(heat_data(self.c,clock=self.clock)['total'],0)

    def test_heat_http_projects_dated_patterns(self):
        self.history([100]*5)
        status,body,headers=self.fixture.fixture.dispatch('GET','/api/board?view=heat')
        self.assertEqual(status,200)
        self.assertGreater(body['total'],0)
        self.assertEqual(body['cards'][0]['hits'],5)
        self.assertIn(b'Cache-Control: no-store',headers)

    def test_manual_owner_leg_saves_and_updates_bobby_and_tickets(self):
        params=dict(owner_name='Bobby',environment='REAL',sportsbook_platform='SYNTHETIC',wager_amount='10',potential_payout='21',
            leg_1_sport='CFB',leg_1_game_id='fixture-game',leg_1_player='Manual Unlisted Player',leg_1_market='Receiving Yards',leg_1_line='62.5',leg_1_side='MORE',leg_1_odds='2.1')
        body=urlencode(params).encode()
        handler=dashboard.DashboardHandler.__new__(dashboard.DashboardHandler)
        handler.rfile=BytesIO(b'POST /api/save-ticket HTTP/1.0\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
        handler.wfile=BytesIO();handler.client_address=('127.0.0.1',0);handler.server=SimpleNamespace(server_name='fixture',server_port=0)
        with patch.object(dashboard,'DB_PATH',self.fixture.fixture.path):handler.handle_one_request()
        result=json.loads(handler.wfile.getvalue().split(b'\r\n\r\n')[1])
        self.assertTrue(result['success'],result)
        data=board_data(self.c,clock=self.clock)
        self.assertEqual(data['bobby']['open_count'],1)
        self.assertEqual(data['bobby']['exposure'],10)
        ticket=data['tickets'][0]
        self.assertEqual(ticket['legs'][0]['player'],'Manual Unlisted Player')
        self.assertEqual(ticket['legs'][0]['game_id'],'fixture-game')
        self.assertEqual(ticket['legs'][0]['odds'],2.1)
        self.assertEqual(self.c.execute('SELECT count(*) FROM challenge_tickets').fetchone()[0],0)
        self.c.execute("UPDATE tickets SET status='SETTLED',actual_payout=21,settled_at=? WHERE id=?",(self.clock.isoformat(),result['ticket_id']))
        self.c.commit()
        data=board_data(self.c,clock=self.clock)
        self.assertEqual(data['bobby']['today_pl'],11)
        self.assertEqual(data['bobby']['wins'],1)
        self.assertEqual(data['bobby']['open_count'],0)
        self.assertEqual(data['bobby']['exposure'],0)


if __name__=='__main__':unittest.main()
