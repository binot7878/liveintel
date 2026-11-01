"""Isolated completed-boxscore, category and bounded-backfill regressions."""
import copy
import datetime
import json
import tempfile
import unittest
from unittest.mock import patch

import daily_espn_pipeline as p
import espn_backfill as b
from espn_history import persist_completed, summary_game
from espn_stats import upsert_player_stats
import test_daily_espn_pipeline


class CompletedHistoryTests(unittest.TestCase):
    def setUp(self):
        test_daily_espn_pipeline.IngestionTests.setUp(self)
        self.conn.executescript('''DROP TABLE player_game_stats;
            CREATE TABLE player_game_stats(game_id TEXT,player_id TEXT,player_name TEXT,team TEXT,
                stats_json TEXT,segment TEXT,last_updated TEXT,PRIMARY KEY(game_id,player_id,segment));
            CREATE TABLE prop_lines(id INTEGER PRIMARY KEY);
            CREATE TABLE game_state(game_id TEXT PRIMARY KEY,home_score INTEGER,away_score INTEGER,
                home_team TEXT,away_team TEXT,last_updated TEXT);''')
        self.summary = {'header':{'id':'401873023','league':{'abbreviation':'NFL'},'competitions':[{
            'id':'401873023','date':'2026-10-31T01:00:00Z','status':{'type':{'completed':True,'state':'post'}},
            'competitors':[{'homeAway':'home','score':'24','team':{'id':'9','abbreviation':'GB'}},
                           {'homeAway':'away','score':'20','team':{'id':'29','abbreviation':'CAR'}}]}]},
            'boxscore':{'players':[{'team':{'id':'29','abbreviation':'CAR'},'statistics':[
                {'name':'passing','labels':['C/ATT','YDS'],'athletes':[{'athlete':self.player,'stats':['20/30','250'],'starter':False}]},
                {'name':'rushing','labels':['CAR','YDS'],'athletes':[{'athlete':self.player,'stats':['4','35']}]},
                {'name':'receiving','labels':['REC','YDS','TGTS'],'athletes':[{'athlete':self.player,'stats':['2','25','3']}]}]}],
                'teams':[{'team':{'id':'29','abbreviation':'CAR'},'statistics':[{'name':'totalYards','displayValue':'350'}]}]}}
        self.game=summary_game(self.summary,'NFL')

    def save(self, summary=None):
        with self.conn:
            return persist_completed(self.conn, summary or self.summary, self.game)

    def test_completed_idempotent_with_categories_starter_and_final_scores(self):
        self.assertEqual(self.save(),1)
        self.save()
        self.assertEqual(self.conn.execute('SELECT count(*) FROM multi_sport_players').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM player_game_stats').fetchone()[0],1)
        row=self.conn.execute('SELECT * FROM multi_sport_players').fetchone()
        self.assertEqual((row['starter'],row['pass_yards'],row['rush_yards'],row['receiving_yards']),(0,250,35,25))
        self.assertEqual((row['game_date'],row['slate_date']),('2026-10-30','2026-10-30'))
        game=self.conn.execute('SELECT * FROM games').fetchone()
        self.assertEqual((game['status'],game['scheduled_at']),('post','2026-10-31T01:00:00Z'))
        score=self.conn.execute('SELECT * FROM game_state').fetchone()
        self.assertEqual((score['home_score'],score['away_score']),(24,20))
        payload=json.loads(self.conn.execute('SELECT stats_json FROM player_game_stats').fetchone()[0])
        self.assertEqual(payload['passing']['YDS'],'250')
        self.assertEqual(payload['receiving']['YDS'],'25')
        self.assertEqual(payload['rushing']['YDS'],'35')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM espn_team_game_totals').fetchone()[0],1)

    def test_blank_followup_preserves_values_and_categories(self):
        self.save()
        partial=copy.deepcopy(self.summary)
        partial['boxscore']['players'][0]['statistics']=[{'name':'receiving','labels':['REC','YDS'],'athletes':[
            {'athlete':self.player,'stats':['','']}]}]
        partial['boxscore']['teams'][0]['statistics'][0]['displayValue']=''
        for c in partial['header']['competitions'][0]['competitors']: c['score']=''
        self.save(partial)
        row=self.conn.execute('SELECT * FROM multi_sport_players').fetchone()
        self.assertEqual((row['pass_yards'],row['receiving_yards'],row['starter']),(250,25,0))
        data=json.loads(self.conn.execute('SELECT stats_json FROM player_game_stats').fetchone()[0])
        self.assertEqual(data['receiving']['YDS'],'25')
        self.assertEqual(data['passing']['YDS'],'250')
        self.assertEqual(self.conn.execute('SELECT home_score FROM game_state').fetchone()[0],24)
        self.assertEqual(json.loads(self.conn.execute('SELECT stats_json FROM espn_team_game_totals').fetchone()[0])['totalYards'],'350')

    def test_repairs_legacy_identity_dates_and_preserves_unobserved_stats(self):
        self.conn.execute('''INSERT INTO multi_sport_players(sport,player_id,player_name,team,game_id,game_date,segment,points)
            VALUES(?,?,?,?,?,?,?,?)''',('CFB','4685720','Bad label','BAD',self.game['id'],None,'FULL GAME',9))
        self.conn.execute('INSERT INTO games VALUES(?,?,?,?,?,?)',(self.game['id'],'NFL','BAD','OLD','LIVE','2026-10-31'))
        self.conn.execute('INSERT INTO player_game_stats VALUES(?,?,?,?,?,?,?)',
            (self.game['id'],'4685720','Bad label','BAD','{"kicking":{"PTS":"3"}}','FULL GAME','2026-10-31'))
        self.conn.commit()
        self.save()
        row=self.conn.execute('SELECT * FROM multi_sport_players').fetchone()
        self.assertEqual((row['sport'],row['team'],row['player_name'],row['game_date'],row['points']),('NFL','CAR','Bryce Young','2026-10-30',9))
        self.assertEqual(json.loads(self.conn.execute('SELECT stats_json FROM player_game_stats').fetchone()[0])['kicking']['PTS'],'3')

    def test_invalid_identity_or_date_rolls_back_entire_game(self):
        for mutate in ('date','team','id'):
            bad=copy.deepcopy(self.summary)
            if mutate=='date': bad['header']['competitions'][0]['date']='2026-10-31'
            if mutate=='team': bad['boxscore']['players'][0]['team']['id']='999'
            if mutate=='id': bad['header']['id']='999'
            with self.assertRaises(ValueError): self.save(bad)
            self.assertEqual(self.conn.execute('SELECT count(*) FROM games').fetchone()[0],0)

    def test_duplicate_legacy_rows_abort_without_partial_final_write(self):
        for _ in range(2):
            self.conn.execute('INSERT INTO multi_sport_players(player_id,game_id,segment) VALUES(?,?,?)',('4685720',self.game['id'],'FULL GAME'))
        self.conn.commit()
        with self.assertRaises(ValueError): self.save()
        self.assertEqual(self.conn.execute('SELECT count(*) FROM games').fetchone()[0],0)

    def test_basketball_minutes_and_unknown_starter(self):
        summary=copy.deepcopy(self.summary)
        summary['header']['league']['abbreviation']='WNBA'
        summary['boxscore']['players'][0]['statistics']=[{'name':'totals','labels':['MIN','PTS','FG','3PT','REB','AST'],
            'athletes':[{'athlete':self.player,'stats':['12:30','15','6-10','3-5','2','1']}]}]
        with self.conn:
            persist_completed(self.conn,summary,summary_game(summary,'WNBA'))
        row=self.conn.execute('SELECT * FROM multi_sport_players').fetchone()
        self.assertEqual(row['minutes'],12.5)
        self.assertIsNone(row['starter'])
        data=json.loads(self.conn.execute('SELECT stats_json FROM player_game_stats').fetchone()[0])
        from heat_view import stat_value
        self.assertEqual(stat_value(data,'Minutes','WNBA'),12.5)
        self.assertEqual(stat_value(data,'Shot Attempts','WNBA'),10)

    def test_shared_category_upsert_preserves_segments_and_zeroes(self):
        for category,stats in [('passing',{'YDS':'250'}),('rushing',{'YDS':'0'}),('passing',{'YDS':''})]:
            upsert_player_stats(self.conn,'1','2','Name','CAR',{category:stats},'FULL GAME','now')
        upsert_player_stats(self.conn,'1','2','Name','CAR',{'passing':{'YDS':'50'}},'1H','now')
        rows=self.conn.execute('SELECT * FROM player_game_stats ORDER BY segment').fetchall()
        self.assertEqual(len(rows),2)
        full=json.loads(next(r['stats_json'] for r in rows if r['segment']=='FULL GAME'))
        self.assertEqual(full,{'passing':{'YDS':'250'},'rushing':{'YDS':'0'}})

    def test_bench_player_completed_history_reaches_heat_and_math(self):
        from heat_view import heat_data
        from math_lab import history, team_history
        from board_view import ZONE
        self.conn.execute('INSERT INTO games(id,sport,home_team,away_team,status,last_updated) VALUES(?,?,?,?,?,?)',
                          ('999','NFL','GB','CAR','PRE','2026-10-31T12:00:00-04:00'))
        p.ensure_context_columns(self.conn)
        self.conn.execute('INSERT INTO multi_sport_players(sport,player_id,player_name,team,game_id,game_date,slate_date,segment,starter) VALUES(?,?,?,?,?,?,?,?,?)',
                          ('NFL','4685720','Bryce Young','CAR','999','2026-10-31','2026-10-31','FULL GAME',0))
        self.conn.commit()
        for i in range(4):
            summary=copy.deepcopy(self.summary)
            gid=str(1000+i)
            summary['header']['id']=gid
            comp=summary['header']['competitions'][0]
            comp['id']=gid
            comp['date']=f'2026-10-{30-i:02d}T20:00:00Z'
            with self.conn:
                persist_completed(self.conn,summary,summary_game(summary,'NFL'))
        clock=datetime.datetime(2026,10,31,12,tzinfo=ZONE)
        heat=heat_data(self.conn,clock=clock)
        self.assertGreater(heat['total'],0)
        self.assertTrue(any(card['player_id']=='4685720' for card in heat['cards']))
        player=dict(self.conn.execute("SELECT * FROM multi_sport_players WHERE game_id='999'").fetchone())
        self.assertEqual(len(history(self.conn,player,'2026-10-31')),4)
        self.assertEqual(len(team_history(self.conn,'NFL','CAR','2026-10-31')),4)
        metrics=b.counts(self.conn,'2026-10-31')['NFL']
        self.assertEqual(metrics['completed_historical_player_rows'],4)
        self.assertEqual(metrics['starter_values_populated'],4)
        self.assertEqual(metrics['math_lab_usable_historical_rows'],4)
        self.assertEqual(metrics['heat_qualifying_historical_rows'],4)

    def test_daily_pipeline_ingests_final(self):
        with patch.object(p,'discover_slate',return_value=[self.game]),patch.object(p,'fetch_json',return_value=self.summary),patch.object(p,'save_report'):
            result=p.process_league(self.conn,'NFL','football/nfl',self.game['game_date'])
        self.assertEqual(result['fatal_error_count'],0)
        self.assertEqual(result['completed_players'],1)

    def test_reconcile_ingests_new_final_with_players(self):
        self.conn.execute('INSERT INTO games VALUES(?,?,?,?,?,?)',(self.game['id'],'NFL','GB','CAR','LIVE','old'))
        self.conn.commit()
        with patch.object(p,'fetch_json',return_value=self.summary):
            self.assertEqual(p.reconcile_previous_games(self.conn,'NFL','football/nfl',set()),[])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM player_game_stats').fetchone()[0],1)

    def test_cfb_season_discovery_uses_all_fbs_and_espn_dates(self):
        from urllib.parse import urlparse,parse_qs
        class Client:
            def get(inner,url):
                params=parse_qs(urlparse(url).query)
                self.assertEqual(params['groups'],['80'])
                return dict(leagues=[dict(season=dict(year=2026,startDate='2026-08-01T04:00Z',endDate='2027-02-01T05:00Z'))],events=[])
        year,games=b.season_events(Client(),p.LEAGUES[3],'2026-10-31')
        self.assertEqual((year,games),('2026',[]))

    def test_season_discovery_single_dates_for_every_league(self):
        from urllib.parse import urlparse,parse_qs
        from urllib.error import HTTPError
        for league in p.LEAGUES:
            with self.subTest(sport=league['sport']):
                requested=[]
                class Client:
                    def get(inner,url):
                        parsed=urlparse(url)
                        params=parse_qs(parsed.query)
                        self.assertEqual(parsed.path,'/apis/site/v2/sports/'+league['espn_path']+'/scoreboard')
                        self.assertEqual(params['limit'],['1000'])
                        self.assertEqual(params.get('groups'),['80'] if league['sport']=='CFB' else None)
                        if 'dates' in params:
                            value=params['dates'][0]
                            # Reproduce ESPN's rejection of the former monthly range.
                            if '-' in value:
                                raise HTTPError(url,400,'Failed to get events endpoint.',{},None)
                            self.assertRegex(value,r'^\d{8}$')
                            requested.append(value)
                        return dict(leagues=[dict(season=dict(year=2026,startDate='2026-09-30T04:00Z',endDate='2026-10-04T04:00Z'))],events=[])
                self.assertEqual(b.season_events(Client(),league,'2026-10-02'),('2026',[]))
                self.assertEqual(requested,['20260930','20261001','20261002'])

    def test_season_discovery_deduplicates_weekly_scoreboards(self):
        event={'id':self.game['id'],'date':'2026-10-31T01:00Z','season':{'year':2026},
               'competitions':self.summary['header']['competitions']}
        class Client:
            def get(inner,url):
                return dict(leagues=[dict(season=dict(year=2026,startDate='2026-10-29T04:00Z',endDate='2026-11-02T05:00Z'))],events=[event])
        year,games=b.season_events(Client(),p.LEAGUES[2],'2026-10-31')
        self.assertEqual(year,'2026')
        self.assertEqual([g['id'] for g in games],[self.game['id']])

    def test_legacy_writer_keeps_multiple_player_categories(self):
        import ingestor
        self.conn.execute("""CREATE TABLE player_segment_stats(game_id TEXT,player_id TEXT,player_name TEXT,
            team TEXT,segment TEXT,stat_type TEXT,value REAL,last_updated TEXT,
            PRIMARY KEY(game_id,player_id,segment,stat_type))""")
        with self.conn, patch.object(ingestor,'aggregate_qb_passing_segments',return_value={}), \
             patch.object(ingestor,'aggregate_rb_rushing_segments',return_value={}), \
             patch.object(ingestor,'aggregate_wr_receiving_segments',return_value={}):
            ingestor.store_player_game_stats(self.conn,self.summary,self.game['id'],[],datetime.datetime.now())
        data=json.loads(self.conn.execute('SELECT stats_json FROM player_game_stats').fetchone()[0])
        self.assertEqual(set(data),{'passing','rushing','receiving'})
        self.assertEqual(self.conn.execute('SELECT count(*) FROM player_game_stats').fetchone()[0],1)

    def test_cache_reuses_completed_summary_and_throttles_new_requests(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(p,'fetch_json',return_value=self.summary) as fetch,patch.object(b.time,'sleep') as sleep:
            client=b.CachedESPN(directory)
            client.get('https://example.invalid/summary?event=1',final=True)
            client.get('https://example.invalid/summary?event=1',final=True)
            client.get('https://example.invalid/summary?event=2',final=True)
            self.assertEqual((fetch.call_count,client.hits),(2,1))
            sleep.assert_called_once()

    def test_invalid_athlete_ids_are_quarantined_without_losing_game(self):
        summary=copy.deepcopy(self.summary)
        summary['header']['league']['abbreviation']='NCAAF'
        game=summary_game(summary,'CFB')
        bad={'athlete':{'id':'-7768','displayName':' Team','links':[]},'stats':['1','-2']}
        summary['boxscore']['players'][0]['statistics'][1]['athletes'].append(bad)
        summary['boxscore']['players'][0]['statistics'][2]['athletes'].append(
            {'athlete':{'id':'unknown','displayName':'Unidentified'},'stats':['1','2','3']})
        for _ in range(2):
            with self.conn:self.assertEqual(persist_completed(self.conn,summary,game),1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM games').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT home_score FROM game_state').fetchone()[0],24)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM espn_team_game_totals').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM player_game_stats').fetchone()[0],1)
        row=self.conn.execute('SELECT * FROM multi_sport_players').fetchone()
        self.assertEqual(row['sport'],'CFB')
        self.assertEqual((row['player_id'],row['game_date'],row['slate_date']),('4685720','2026-10-30','2026-10-30'))
        quarantine=self.conn.execute('SELECT * FROM espn_player_quarantine ORDER BY raw_player_id').fetchall()
        self.assertEqual(len(quarantine),2)
        self.assertEqual(quarantine[0]['reason'],'team_aggregate')
        self.assertEqual(json.loads(quarantine[0]['payload_json']),bad)
        self.assertEqual(quarantine[1]['reason'],'invalid_athlete_id')
        self.assertEqual(self.conn.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_quarantined_ids_do_not_hide_team_contradictions(self):
        summary=copy.deepcopy(self.summary)
        group=summary['boxscore']['players'][0]
        group['team']['id']='999'
        for cat in group['statistics']:
            cat['athletes']=[{'athlete':{'id':'-1','displayName':'Team'},'stats':[]}]
        with self.assertRaisesRegex(ValueError,'team mismatch'):
            with self.conn:persist_completed(self.conn,summary,self.game)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM games').fetchone()[0],0)

    def test_legacy_invalid_stats_move_to_quarantine_without_losing_payload(self):
        self.conn.execute('INSERT INTO player_game_stats VALUES(?,?,?,?,?,?,?)',
            (self.game['id'],'-7768','Team','CAR','{"rushing":{"YDS":"-2"}}','FULL GAME','old'))
        self.conn.commit()
        self.save();self.save()
        self.assertEqual(self.conn.execute("SELECT count(*) FROM player_game_stats WHERE player_id='-7768'").fetchone()[0],0)
        row=self.conn.execute("SELECT * FROM espn_player_quarantine WHERE reason='invalid_legacy_athlete_id'").fetchone()
        self.assertEqual(json.loads(json.loads(row['payload_json'])['stats_json']),{'rushing':{'YDS':'-2'}})
        self.assertEqual(self.conn.execute('SELECT count(*) FROM espn_player_quarantine').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM player_game_stats').fetchone()[0],1)

    def test_football_missing_participation_stays_unavailable(self):
        summary=copy.deepcopy(self.summary)
        summary['boxscore']['players'][0]['statistics'][0]['athletes'][0].pop('starter')
        with self.conn:persist_completed(self.conn,summary,self.game)
        row=self.conn.execute('SELECT * FROM multi_sport_players').fetchone()
        self.assertIsNone(row['starter']);self.assertIsNone(row['minutes'])

    def test_summary_404_continues_but_outage_stops_league(self):
        from urllib.error import HTTPError
        for status,expected in [(404,3),(503,1)]:
            with self.subTest(status=status):
                class Client:
                    requests=0;hits=0
                    def get(inner,url,final=False):
                        inner.requests+=1
                        if inner.requests==1:raise HTTPError(url,status,'unavailable',{},None)
                        return {'id':url.rsplit('=',1)[1]}
                games=[{'id':str(i)} for i in (11,12,13)]
                client=Client()
                with patch.object(b,'counts',return_value={}),patch.object(b,'season_events',return_value=('2026',games)), \
                     patch.object(b,'summary_game',side_effect=lambda s,sport:dict(id=s['id'],status='post')), \
                     patch.object(b,'persist_completed',return_value=1):
                    report=b.run(self.conn,client,'2026-10-31',repair_existing=False,sports={'WNBA'})
                self.assertEqual(client.requests,expected)
                if status==404:
                    self.assertEqual(report['errors'],[])
                    self.assertEqual(report['leagues']['WNBA']['completed_games'],2)
                    self.assertEqual(report['leagues']['WNBA']['unavailable_summaries'][0]['event_id'],'11')
                else:self.assertEqual(len(report['errors']),1)

    def test_targeted_resume_skips_successful_events_and_other_sports(self):
        self.save()
        # The fixture has one team total; a complete two-team event is required to skip.
        self.conn.execute("INSERT INTO espn_team_game_totals VALUES(?,?,?,?,?)",(self.game['id'],'9','GB','{}','now'))
        self.conn.commit()
        class Client:
            requests=0;hits=0
            def get(inner,*args,**kwargs):raise AssertionError('completed event refetched')
        with patch.object(b,'counts',return_value={}),patch.object(b,'season_events',return_value=('2026',[self.game])) as discover:
            report=b.run(self.conn,Client(),'2026-10-31',repair_existing=False,sports={'NFL'},skip_completed=True)
        self.assertEqual(discover.call_count,1)
        self.assertEqual(set(report['leagues']),{'NFL'})
        self.assertEqual(report['leagues']['NFL']['skipped_completed'],1)


if __name__=='__main__': unittest.main()
