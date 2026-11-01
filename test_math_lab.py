"""Synthetic-only signal and read-only research regression coverage."""
import json
import unittest
from datetime import timedelta
from sport_math import timing, live_math, role_signals, segment_scores, SPORTS
from math_lab import research
import test_heat_view
from board_view import board_data


class MathTests(unittest.TestCase):
    def test_sport_specific_segments(self):
        for sport,label in [('NBA','Quarter'),('WNBA','Quarter'),('NCAAB','Half'),('NFL','Quarter'),('CFB','Quarter'),('NHL','Period'),('MLB','Inning')]:
            self.assertTrue(timing(sport,1,'1:00')['label'].startswith(label))
        self.assertEqual(timing('NCAAB',2,'10:00')['elapsed'],1800)
        self.assertIsNone(timing('MLB',9,'1:00')['elapsed'])
        self.assertIsNone(timing('FUTURE',1,'1:00')['elapsed'])

    def test_overtime_and_final_period(self):
        for sport,period in [('NBA',5),('WNBA',5),('NCAAB',3)]:
            t=timing(sport,period,'2:00');self.assertEqual(t['remaining'],120);self.assertEqual(t['label'],'OT 1')
            self.assertEqual(timing(sport,period+1,'2:00')['label'],'OT 2')
            m=live_math(dict(sport=sport,period=period,clock='2:00',status='LIVE',home_score=100,away_score=99))
            self.assertIsNotNone(m['projection'])
        self.assertEqual(timing('NBA',4,'0:10')['remaining'],10)
        self.assertIsNone(timing('CFB',5,'2:00')['elapsed'])
        self.assertIsNone(timing('NHL',4,'2:00')['elapsed']) # unknown regular/postseason OT format

    def test_comeback_recalculates(self):
        game=dict(sport='NBA',period=4,clock='2:00',status='LIVE',home_score=100,away_score=98)
        obs=[dict(period=4,clock='7:00',home=96,away=76)]
        m=live_math(game,obs)
        self.assertIn('LEAD COLLAPSE RISK',m['signals']);self.assertLess(m['margin'],0)
        game.update(home_score=115,away_score=98)
        later=live_math(game,obs)
        self.assertNotIn('LEAD COLLAPSE RISK',later['signals'])
        self.assertNotEqual(m['projection'],later['projection'])

    def test_fail_closed(self):
        for bad in [None,'',float('nan'),-1]:
            m=live_math(dict(sport='NBA',period=4,clock='2:00',status='LIVE',home_score=bad,away_score=90))
            self.assertIsNone(m['projection'])
        self.assertIsNone(timing('NBA',4,'12:99')['elapsed'])
        m=live_math(dict(sport='NBA',period=4,clock='2:00',status='PRE',home_score=0,away_score=0))
        self.assertEqual(m['signals'],[])
        self.assertIsNone(m['win_probability'])

    def test_no_fake_window(self):
        g=dict(sport='NBA',period=4,clock='2:00',status='LIVE',home_score=100,away_score=98)
        self.assertEqual(live_math(g,[dict(period=1,clock='10:00',home=2,away=0)])['signals'],[])
        self.assertEqual(segment_scores('NBA',[dict(period=1,clock='0:00',home=25,away=20),dict(period=2,clock='0:00',home=48,away=44)]),[dict(label='Quarter 1',home=25,away=20),dict(label='Quarter 2',home=23,away=24)])

    def test_bench_measured_role(self):
        history=[dict(minutes=12,usage=10,fga=4)]*5
        badges=role_signals(dict(starter=False,minutes=24,usage=20,fga=8),history)
        self.assertIn('BENCH SURGE',badges);self.assertIn('MINUTES UP',badges)
        self.assertEqual(role_signals({},history),[])
        self.assertNotIn('BENCH SURGE',role_signals(dict(minutes=24),history))


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_heat_view.HeatWorkspaceTests(methodName='runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.c=self.fixture.c;self.clock=self.fixture.clock

    def test_pregame_heat_and_no_duplicate_records(self):
        self.fixture.history([78,91,66,84,71])
        self.c.execute('PRAGMA query_only=ON')
        before=self.c.total_changes
        r=research(self.c,self.clock)
        card=next(c for c in r['cards'] if c['kind']=='player' and c['market']=='Receiving Yards')
        self.assertEqual(card['projection'],78);self.assertEqual(card['heat']['hits'],5)
        self.assertEqual(card['ai_ids'],['CFB_AI']);self.assertIsNone(card['current_line'])
        self.assertEqual(self.c.total_changes,before)
        self.assertEqual(self.c.execute('select count(*) from challenge_tickets').fetchone()[0],0)
        self.assertNotIn('BOBBY',r['ai_filters'])
        self.assertEqual(board_data(self.c,'research',clock=self.clock),r)

    def test_all_sports_and_unranked_eligible(self):
        for sport in list(SPORTS)+['FUTURE']:
            self.c.execute('UPDATE games SET sport=?',(sport,));self.c.execute('UPDATE multi_sport_players SET sport=?',(sport,))
            r=research(self.c,self.clock)
            self.assertTrue(any(c['kind']=='team' and c['sport']==sport for c in r['cards']))
            self.assertTrue(any(c['kind']=='player' and c['sport']==sport for c in r['cards']))
            self.assertTrue(all(c['projection'] is None for c in r['cards']))

    def test_live_research_fresh_and_stale(self):
        self.c.execute("UPDATE games SET status='LIVE'")
        r=research(self.c,self.clock)
        self.assertTrue(all(c['game']['status']=='LIVE' for c in r['cards']))
        self.c.execute("UPDATE games SET last_updated='2020-01-01'")
        r=research(self.c,self.clock)
        self.assertTrue(all(c['math']['projection'] is None for c in r['cards']))

    def test_unranked_team_repeated_wins_feed_heat(self):
        self.fixture.history([80]*5)
        for i in range(5):
            self.c.execute("INSERT INTO game_state(game_id,home_score,away_score,last_updated) VALUES(?,?,?,?)",('past-'+str(i),30,10,self.clock.isoformat()))
        self.c.commit()
        from heat_view import heat_data
        cards=heat_data(self.c,clock=self.clock)['cards']
        team=next(c for c in cards if c.get('kind')=='team')
        self.assertEqual(team['hits'],5)
        self.assertEqual(team['sport'],'CFB')
        self.assertNotIn('rank',team)
        r=research(self.c,self.clock)
        team=next(c for c in r['cards'] if c['kind']=='team' and c['team']=='HOME')
        self.assertIn('HOT TEAM',team['signals'])
        self.assertEqual(team['average'],30)

    def test_ai_ticket_is_pick_and_settlement_idempotent(self):
        from challenge_ledger import settle_ticket, snapshot
        self.c.execute("UPDATE games SET sport='NBA'")
        self.c.execute("UPDATE multi_sport_players SET sport='NBA'")
        self.c.commit()
        self.fixture.fixture.qualified_fixture()
        before=board_data(self.c,clock=self.clock+timedelta(seconds=1))
        ticket_id=before['specialists'][0]['pick']['ticket_id']
        for _ in range(2): research(self.c,self.clock)
        self.assertEqual(self.c.execute('SELECT count(*) FROM challenge_tickets').fetchone()[0],1)
        t=dict(self.c.execute('SELECT * FROM challenge_tickets WHERE id=?',(ticket_id,)).fetchone())
        legs=self.c.execute('SELECT id FROM challenge_ticket_legs WHERE ticket_id=?',(ticket_id,)).fetchall()
        args=dict(ai_id=t['ai_id'],challenge_run_id=t['challenge_run_id'],leg_results={l['id']:'WIN' for l in legs},settlement_source='synthetic final evidence')
        settle_ticket(self.c,ticket_id,**args)
        settle_ticket(self.c,ticket_id,**args)
        state=snapshot(self.c,t['ai_id'])
        self.assertEqual(state['wins'],1);self.assertEqual(state['current_bankroll'],110);self.assertEqual(state['pending_exposure'],0)
        self.assertEqual(self.c.execute('SELECT count(*) FROM challenge_tickets').fetchone()[0],1)

    def test_research_http_is_read_only(self):
        from io import BytesIO
        from types import SimpleNamespace
        from unittest.mock import patch
        import dashboard
        handler=dashboard.DashboardHandler.__new__(dashboard.DashboardHandler)
        handler.rfile=BytesIO(b'GET /api/board?view=research HTTP/1.0\r\n\r\n')
        handler.wfile=BytesIO();handler.client_address=('127.0.0.1',0);handler.server=SimpleNamespace(server_name='fixture',server_port=0)
        self.c.commit()
        with patch.object(dashboard,'DB_PATH',self.fixture.fixture.fixture.path): handler.handle_one_request()
        raw=handler.wfile.getvalue();self.assertIn(b'200 OK',raw)
        payload=json.loads(raw.split(b'\r\n\r\n')[1])
        self.assertIn('cards',payload);self.assertIn('NHL',payload['sports'])
        self.assertEqual(self.c.execute('SELECT count(*) FROM challenge_tickets').fetchone()[0],0)

    def test_role_fields_from_explicit_feed_only(self):
        from daily_espn_pipeline import extract_basketball
        game=dict(id='g',sport='NBA',status='LIVE',home_team='H',away_team='A',team_ids={'H':'1','A':'2'},game_date='2026-10-31')
        group=dict(team={'abbreviation':'H','id':'1'},statistics=[dict(labels=['PTS','MIN'],athletes=[dict(athlete={'id':'123','displayName':'Bench Test'},starter=False,stats=['10','22:30'])])])
        p=extract_basketball([group],game,[],[])[0]
        self.assertEqual(p['starter'],0);self.assertEqual(p['minutes'],22.5)
        del group['statistics'][0]['athletes'][0]['starter']
        p=extract_basketball([group],game,[],[])[0]
        self.assertNotIn('starter',p)

if __name__=='__main__': unittest.main()
