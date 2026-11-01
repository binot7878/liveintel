"""Isolated ingestion regression tests. No network or production writes."""
import contextlib
import io
import json
import sqlite3
import sys
import unittest
from unittest.mock import patch

import daily_espn_pipeline as pipeline
import ingestor


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript('''
            CREATE TABLE games(id TEXT PRIMARY KEY,sport TEXT,home_team TEXT,
                               away_team TEXT,status TEXT,last_updated TEXT,
                               game_date TEXT,slate_date TEXT,scheduled_at TEXT);
            CREATE TABLE multi_sport_players(
                id INTEGER PRIMARY KEY, sport TEXT, player_id TEXT, player_name TEXT,
                team TEXT, position TEXT, game_id TEXT, game_date TEXT, segment TEXT,
                last_updated TEXT,points INTEGER,rebounds INTEGER,assists INTEGER,
                fga INTEGER,fgm INTEGER,three_pa INTEGER,three_pm INTEGER,
                pass_attempts INTEGER,completions INTEGER,pass_yards INTEGER,
                rush_attempts INTEGER,rush_yards INTEGER,targets INTEGER,
                receptions INTEGER,receiving_yards INTEGER);
            CREATE TABLE player_game_stats(game_id TEXT, player_id TEXT);
        ''')
        # Recorded production identities; test envelopes are confined to memory.
        self.game = dict(id='401873023', sport='NFL', game_date='2026-10-30',
                         home_team='GB', away_team='CAR', team_ids={'GB': '', 'CAR': ''},
                         status='PRE')
        self.player = {'id': '4685720', 'displayName': 'Bryce Young'}

    def summary(self):
        comp = {'id': self.game['id'], 'date': '2026-10-30T23:00:00Z',
                'status': {'type': {'state': 'pre'}},
                'competitors': [{'homeAway': 'home', 'team': {'abbreviation': 'GB'}},
                                {'homeAway': 'away', 'team': {'abbreviation': 'CAR'}}]}
        return {'header': {'id': self.game['id'], 'league': {'abbreviation': 'NFL'},
                           'competitions': [comp]},
                'rosters': [{'team': {'abbreviation': 'CAR'},
                             'roster': [{'athlete': self.player}]}]}

    def rows(self, summary=None):
        return pipeline.extract_football(
            pipeline.summary_player_groups(summary or self.summary(), self.game),
            self.game, [], [])

    def test_new_york_date_not_utc_or_update_time(self):
        self.assertEqual(pipeline.event_date('2026-10-31T01:00:00Z'), '2026-10-30')
        self.assertEqual(pipeline.event_date('2026-10-31T23:00:00Z'), '2026-10-31')
        with self.assertRaises(ValueError):
            pipeline.event_date('2026-10-31T01:00:00')

    def test_status_mapping_and_completed_precedence(self):
        for state, expected in [('pre', 'PRE'), ('in', 'LIVE'), ('halftime', 'LIVE'),
                                ('post', 'post')]:
            self.assertEqual(pipeline.game_status({'status': {'type': {'state': state}}}), expected)
        self.assertEqual(pipeline.game_status({'status': {'type': {'state': 'in', 'completed': True}}}), 'post')
        self.assertEqual(pipeline.game_status({'status': {'type': {'state': 'pre', 'name': 'STATUS_POSTPONED'}}}), 'postponed')

    def test_discovery_uses_explicit_date_and_filters_weekly_response(self):
        summary = self.summary()
        event = dict(id=self.game['id'], date='2026-10-30T23:00:00Z',
                     competitions=summary['header']['competitions'])
        with patch.object(pipeline, 'fetch_json', return_value={'events': [event]}) as fetch:
            self.assertEqual(pipeline.discover_slate('NFL', 'football/nfl', '2026-10-31'), [])
            self.assertIn('dates=20261031', fetch.call_args.args[0])
            result = pipeline.discover_slate('NFL', 'football/nfl', '2026-10-30')
            self.assertEqual(result[0]['status'], 'PRE')
            self.assertEqual(result[0]['scheduled_at'], '2026-10-30T23:00:00Z')
            self.assertEqual(result[0]['slate_date'], '2026-10-30')

    def test_event_roster_links_player_without_inventing_stats(self):
        pipeline.validate_summary(self.summary(), self.game)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['game_id'], rows[0]['team'], rows[0]['player_id']),
                         (self.game['id'], 'CAR', self.player['id']))
        self.assertIsNone(rows[0]['pass_yards'])

    def test_each_league_uses_own_dated_route(self):
        for league in pipeline.LEAGUES:
            with patch.object(pipeline, 'fetch_json', return_value={'events': []}) as fetch:
                self.assertEqual(pipeline.discover_slate(league['sport'], league['espn_path'], '2026-10-31'), [])
                url = fetch.call_args.args[0]
                self.assertIn('/' + league['espn_path'] + '/scoreboard?', url)
                self.assertIn('dates=20261031', url)
                if league['sport'] == 'CFB':
                    self.assertIn('groups=80', url)

    def test_continuous_nfl_ingestor_skips_weekly_other_dates(self):
        summary = self.summary()
        clock_value = pipeline.datetime.datetime(2026, 10, 31, tzinfo=pipeline.SLATE_ZONE)
        event = dict(id=self.game['id'], date='2026-10-30T23:00:00Z',
                     competitions=summary['header']['competitions'])
        with patch.object(ingestor, 'get_db') as db, \
             patch.object(ingestor.requests, 'get') as fetch, \
             patch.object(ingestor, 'store_game_lifecycle') as store, \
             patch.object(ingestor.datetime, 'datetime', wraps=ingestor.datetime.datetime) as clock, \
             contextlib.redirect_stdout(io.StringIO()):
            clock.now.return_value = clock_value
            fetch.return_value.status_code = 200
            fetch.return_value.json.return_value = {'events': [event]}
            ingestor.ingest()
            self.assertEqual(fetch.call_args.kwargs['params']['dates'], '20261031')
            store.assert_not_called()

    def test_wrong_team_and_wrong_summary_identity_rejected(self):
        summary = self.summary()
        summary['rosters'][0]['team']['abbreviation'] = 'IND'
        self.assertEqual(self.rows(summary), [])
        summary = self.summary()
        summary['header']['id'] = '401809236'
        with self.assertRaises(ValueError):
            pipeline.validate_summary(summary, self.game)

    def test_preserves_row_ids_stats_and_slate_date(self):
        rows = self.rows()
        rows[0]['pass_yards'] = 209
        with self.conn:
            pipeline.persist_players(self.conn, self.game, rows)
        before = dict(self.conn.execute('SELECT * FROM multi_sport_players').fetchone())
        with self.conn:
            pipeline.persist_players(self.conn, self.game, self.rows())
        after = dict(self.conn.execute('SELECT * FROM multi_sport_players').fetchone())
        self.assertEqual((before['id'], before['pass_yards'], before['game_date']),
                         (after['id'], after['pass_yards'], after['game_date']))
        changed = dict(self.game, game_date='2026-10-31')
        with self.assertRaises(ValueError), self.conn:
            pipeline.persist_players(self.conn, changed, self.rows())
        self.assertEqual(self.conn.execute('SELECT game_date FROM multi_sport_players').fetchone()[0], '2026-10-30')

    def test_persists_status_without_reactivating_final_game(self):
        with self.conn:
            pipeline.persist_game(self.conn, dict(self.game, status='post'))
        with self.assertRaises(ValueError), self.conn:
            pipeline.persist_game(self.conn, self.game)
        self.assertEqual(self.conn.execute('SELECT status FROM games').fetchone()[0], 'post')

    def test_persist_game_preserves_schedule_fields(self):
        game = dict(
            self.game,
            slate_date='2026-10-30',
            scheduled_at='2026-10-30T23:00:00Z'
        )
        with self.conn:
            pipeline.persist_game(self.conn, game)

        stored = dict(self.conn.execute(
            'SELECT game_date,slate_date,scheduled_at FROM games WHERE id=?',
            (game['id'],)
        ).fetchone())

        self.assertEqual(stored['game_date'], '2026-10-30')
        self.assertEqual(stored['slate_date'], '2026-10-30')
        self.assertEqual(stored['scheduled_at'], '2026-10-30T23:00:00Z')

    def test_process_pregame_roster_end_to_end_and_idempotence(self):
        with patch.object(pipeline, 'discover_slate', return_value=[self.game]), \
             patch.object(pipeline, 'fetch_json', return_value=self.summary()), \
             patch.object(pipeline, 'save_report'), contextlib.redirect_stdout(io.StringIO()):
            first = pipeline.process_league(self.conn, 'NFL', 'football/nfl', '2026-10-30')
            second = pipeline.process_league(self.conn, 'NFL', 'football/nfl', '2026-10-30')
        self.assertEqual((first['pre'], first['linked'], first['unlinked']), (1, 1, 0))
        self.assertEqual(second['linked'], 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM multi_sport_players').fetchone()[0], 1)

    def test_empty_boxscore_preserves_existing_players(self):
        with self.conn:
            pipeline.persist_players(self.conn, self.game, self.rows())
        summary = self.summary()
        summary.pop('rosters')
        with patch.object(pipeline, 'discover_slate', return_value=[self.game]), \
             patch.object(pipeline, 'fetch_json', return_value=summary), \
             patch.object(pipeline, 'save_report'), contextlib.redirect_stdout(io.StringIO()):
            result = pipeline.process_league(self.conn, 'NFL', 'football/nfl', '2026-10-30')
        self.assertEqual(result['status'], 'ok_with_warnings')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM multi_sport_players').fetchone()[0], 1)

    def test_reconciles_old_status_from_own_summary_without_changing_date(self):
        with self.conn:
            pipeline.persist_game(self.conn, self.game)
            pipeline.persist_players(self.conn, self.game, self.rows())
        summary = self.summary()
        summary['header']['competitions'][0]['status']['type']['state'] = 'post'
        with patch.object(pipeline, 'fetch_json', return_value=summary):
            errors = pipeline.reconcile_previous_games(self.conn, 'NFL', 'football/nfl', set())
        self.assertEqual(errors, [])
        self.assertEqual(self.conn.execute('SELECT status FROM games').fetchone()[0], 'post')
        self.assertEqual(self.conn.execute('SELECT game_date FROM multi_sport_players').fetchone()[0], '2026-10-30')

    def test_basketball_event_roster(self):
        game = dict(id='401809236', sport='NBA', game_date='2026-01-01',
                    home_team='IND', away_team='OKC', team_ids={'IND': '', 'OKC': ''})
        summary = {'rosters': [{'team': {'abbreviation': 'IND'}, 'roster': [
            {'athlete': {'id': '3149673', 'displayName': 'Pascal Siakam'}}]}]}
        rows = pipeline.extract_basketball(pipeline.summary_player_groups(summary, game), game, [], [])
        self.assertEqual(rows[0]['game_id'], game['id'])
        self.assertIsNone(rows[0]['points'])

    def test_discovery_failure_is_not_successful_empty_slate(self):
        with patch.object(pipeline, 'fetch_json', side_effect=OSError('source unavailable')), \
             patch.object(pipeline, 'save_report'), contextlib.redirect_stdout(io.StringIO()):
            result = pipeline.process_league(self.conn, 'NFL', 'football/nfl', '2026-10-31')
        self.assertEqual(result['status'], 'source_fetch_failed')
        self.assertIsNone(result['games'])
        self.assertIsNone(result['linked'])
        self.assertGreater(result['error_count'], 0)

    def pregame_case(self, sport='NBA'):
        if sport == 'CFB':
            game = dict(id='401866451', sport=sport, game_date='2026-10-30',
                        home_team='SAC', away_team='KENT', team_ids={'SAC': '16', 'KENT': '2309'},
                        season_year='2026', status='PRE')
            player = {'id': '5119841', 'displayName': 'Dru DeShields'}
        else:
            game = dict(id='401809236', sport=sport, game_date='2026-01-01',
                        home_team='IND', away_team='OKC', team_ids={'IND': '11', 'OKC': '25'},
                        season_year='2026', status='PRE')
            player = {'id': '3149673', 'displayName': 'Pascal Siakam'}
        return game, player

    def test_flat_and_grouped_team_rosters_supply_identity_only(self):
        for sport in ('NBA', 'WNBA', 'CFB'):
            game, player = self.pregame_case(sport)
            team = game['away_team'] if sport == 'CFB' else game['home_team']
            for athletes in ([player], [{'position': 'offense', 'items': [player]}]):
                data = {'team': {'id': game['team_ids'][team], 'abbreviation': team},
                        'season': {'year': int(game['season_year'])}, 'athletes': athletes}
                group = pipeline.team_roster_group(data, game, team, 'ESPN_TEST_RESPONSE')
                extract = pipeline.extract_football if sport == 'CFB' else pipeline.extract_basketball
                rows = extract([group], game, [], [])
                self.assertEqual(rows[0]['team_id'], game['team_ids'][team])
                self.assertEqual(rows[0]['slate_date'], game['game_date'])
                self.assertEqual(rows[0]['player_identity_available'], 1)
                self.assertEqual(rows[0]['live_stats_available'], 0)
                self.assertTrue(all(rows[0].get(field) is None for field in pipeline.STAT_FIELDS))

    def test_team_roster_wrong_team_or_season_rejected(self):
        game, player = self.pregame_case()
        for tid, season in [('25', 2026), ('11', 2025)]:
            with self.assertRaises(ValueError):
                pipeline.team_roster_group({'team': {'id': tid}, 'season': {'year': season},
                                            'athletes': [player]}, game, 'IND', 'ESPN_TEST_RESPONSE')

    def test_roster_fallback_cached_and_not_used_for_live_or_final(self):
        game, player = self.pregame_case()
        cache, errors = {}, []
        def roster(url):
            team = 'IND' if '/teams/11/' in url else 'OKC'
            return {'team': {'id': game['team_ids'][team], 'abbreviation': team},
                    'season': {'year': 2026}, 'athletes': [player] if team == 'IND' else []}
        with patch.object(pipeline, 'fetch_json', side_effect=roster) as fetch:
            groups = pipeline.player_groups({}, game, 'basketball/nba', cache, errors)
            pipeline.player_groups({}, game, 'basketball/nba', cache, errors)
            self.assertEqual(fetch.call_count, 2)
            for status in ('LIVE', 'post'):
                self.assertEqual(pipeline.player_groups({}, dict(game, status=status), 'basketball/nba', cache, errors), [])
            self.assertEqual(fetch.call_count, 2)
        self.assertEqual(errors, [])
        self.assertEqual(len(groups), 2)

    def test_pregame_placeholder_boxscore_does_not_create_stats(self):
        game, player = self.pregame_case()
        groups = [{'team': {'id': '11', 'abbreviation': 'IND'}, 'statistics': [
            {'labels': ['PTS', 'FG'], 'athletes': [{'athlete': player, 'stats': ['0', '0-0']}]}]}]
        row = pipeline.extract_basketball(groups, game, [], [])[0]
        self.assertIsNone(row['points'])
        self.assertIsNone(row['fga'])
        self.assertEqual(row['live_stats_available'], 0)

    def test_pre_to_live_upgrades_same_row_without_duplicates(self):
        game, player = self.pregame_case()
        group = pipeline.team_roster_group({'team': {'id': '11', 'abbreviation': 'IND'},
                                           'season': {'year': 2026}, 'athletes': [player]},
                                          game, 'IND', 'ESPN_TEST_RESPONSE')
        rows = pipeline.extract_basketball([group], game, [], [])
        with self.conn:
            pipeline.persist_game(self.conn, game)
            self.assertEqual(pipeline.persist_players(self.conn, game, rows), 1)
        before = dict(self.conn.execute('SELECT * FROM multi_sport_players').fetchone())
        live = dict(game, status='LIVE')
        group = {'team': {'id': '11', 'abbreviation': 'IND'}, 'statistics': [
            {'labels': ['PTS', 'FG'], 'athletes': [{'athlete': player, 'stats': ['0', '0-1']}]}]}
        with self.conn:
            pipeline.persist_game(self.conn, live)
            self.assertEqual(pipeline.persist_players(self.conn, live,
                pipeline.extract_basketball([group], live, [], [])), 0)
        after = dict(self.conn.execute('SELECT * FROM multi_sport_players').fetchone())
        self.assertEqual(before['id'], after['id'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM multi_sport_players').fetchone()[0], 1)
        self.assertEqual((after['points'], after['fga'], after['live_stats_available']), (0, 1, 1))
        with self.assertRaises(ValueError), self.conn:
            pipeline.persist_game(self.conn, game)

    def test_final_games_validate_summary_before_writing(self):
        game, player = self.pregame_case()
        with self.conn:
            pipeline.persist_game(self.conn, dict(game, status='post'))
        before = dict(self.conn.execute('SELECT * FROM games').fetchone())
        with patch.object(pipeline, 'discover_slate', return_value=[dict(game, status='post')]), \
             patch.object(pipeline, 'fetch_json') as fetch, patch.object(pipeline, 'save_report'), \
             contextlib.redirect_stdout(io.StringIO()):
            pipeline.process_league(self.conn, 'NBA', 'basketball/nba', game['game_date'])
        fetch.assert_called_once()
        self.assertEqual(before, dict(self.conn.execute('SELECT * FROM games').fetchone()))
        with self.assertRaises(ValueError), self.conn:
            pipeline.persist_players(self.conn, dict(game, status='post'), [])

    def test_roster_fallback_when_event_summary_unavailable(self):
        game, player = self.pregame_case()
        def response(url):
            if '/summary?' in url:
                raise OSError('pregame summary unavailable')
            team = 'IND' if '/teams/11/' in url else 'OKC'
            return {'team': {'id': game['team_ids'][team], 'abbreviation': team},
                    'season': {'year': 2026}, 'athletes': [player] if team == 'IND' else []}
        with patch.object(pipeline, 'discover_slate', return_value=[game]), \
             patch.object(pipeline, 'fetch_json', side_effect=response), \
             patch.object(pipeline, 'save_report'), contextlib.redirect_stdout(io.StringIO()):
            result = pipeline.process_league(self.conn, 'NBA', 'basketball/nba', game['game_date'])
        self.assertEqual((result['players'], result['linked'], result['pregame_identities_created']), (1, 1, 1))
        self.assertEqual(result['live_stats_available'], 0)
        self.assertTrue(result['summary_warnings'])

    def cfb_summary(self):
        game, player = self.pregame_case('CFB')
        summary = self.summary()
        summary['header']['id'] = game['id']
        summary['header']['league']['abbreviation'] = 'CFB'
        comp = summary['header']['competitions'][0]
        comp['id'] = game['id']
        comp['competitors'] = [
            {'homeAway': side, 'team': {'id': game['team_ids'][game[side+'_team']],
                                       'abbreviation': game[side+'_team']}}
            for side in ('home', 'away')]
        summary['rosters'] = [
            {'team': {'id': game['team_ids']['KENT'], 'abbreviation': 'KENT'},
             'roster': [{'athlete': player}]},
            {'team': {'id': game['team_ids']['SAC'], 'abbreviation': 'SAC'},
             'roster': [{'athlete': {'id': '5158748', 'displayName': 'Jackson Sharman'}}]}]
        return game, summary

    def run_cfb_main(self, game, summary):
        results = []
        process = pipeline.process_league
        def capture(*args):
            result = process(*args)
            results.append(result)
            return result
        with patch.object(sys, 'argv', ['daily_espn_pipeline.py', '--date', game['game_date']]), \
             patch.object(pipeline, 'LEAGUES', [{'sport': 'CFB', 'espn_path': 'football/college-football'}]), \
             patch.object(pipeline, 'get_db', return_value=self.conn), \
             patch.object(pipeline, 'discover_slate', return_value=[game]), \
             patch.object(pipeline, 'fetch_json', return_value=summary), \
             patch.object(pipeline, 'save_report'), \
             patch.object(pipeline, 'process_league', side_effect=capture), \
             contextlib.redirect_stdout(io.StringIO()):
            code = pipeline.main()
        return code, results[0]

    def test_complete_cfb_with_supplemental_warning_exits_zero(self):
        game, summary = self.cfb_summary()
        resolve = pipeline.player_groups
        def with_warning(summary, game, path, cache, warnings, integrity):
            groups = resolve(summary, game, path, cache, warnings, integrity)
            warnings.append('supplemental team endpoint temporarily unavailable')
            return groups
        with patch.object(pipeline, 'player_groups', side_effect=with_warning):
            code, result = self.run_cfb_main(game, summary)
        self.assertEqual(code, 0)
        self.assertEqual((result['players'], result['linked'], result['unlinked']), (2, 2, 0))
        self.assertEqual(result['usable_games'], 1)
        self.assertEqual(result['error_count'], 0)
        self.assertEqual(result['status'], 'ok_with_warnings')
        self.assertTrue(any(w['category'] == 'supplemental_roster' for w in result['warning_details']))

    def test_database_write_failure_exits_nonzero(self):
        game, summary = self.cfb_summary()
        with patch.object(pipeline, 'persist_players', side_effect=sqlite3.OperationalError('database write failed')):
            code, result = self.run_cfb_main(game, summary)
        self.assertEqual(code, 1)
        self.assertTrue(any(e['category'] == 'database' for e in result['fatal_errors']))

    def test_identity_integrity_failure_exits_nonzero_even_with_usable_game(self):
        game, summary = self.cfb_summary()
        summary['rosters'][0]['roster'][0]['athlete']['team'] = {'id': '16', 'abbreviation': 'SAC'}
        code, result = self.run_cfb_main(game, summary)
        self.assertEqual(code, 1)
        self.assertEqual(result['usable_games'], 1)
        self.assertGreater(result['identity_errors'], 0)
        self.assertTrue(any(e['category'] == 'identity_integrity' for e in result['fatal_errors']))

    def test_core_discovery_failure_main_exits_nonzero(self):
        with patch.object(sys, 'argv', ['daily_espn_pipeline.py', '--date', '2026-10-31']), \
             patch.object(pipeline, 'get_db', return_value=self.conn), \
             patch.object(pipeline, 'discover_slate', side_effect=OSError('ESPN unavailable')), \
             patch.object(pipeline, 'save_report'), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(pipeline.main(), 1)

    def test_database_connection_failure_exits_nonzero(self):
        with patch.object(sys, 'argv', ['daily_espn_pipeline.py']), \
             patch.object(pipeline, 'get_db', side_effect=sqlite3.OperationalError('database unavailable')), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(pipeline.main(), 1)

    def test_discovered_live_game_without_usable_context_is_fatal(self):
        game, summary = self.cfb_summary()
        game['status'] = 'LIVE'
        summary['rosters'] = []
        with patch.object(pipeline, 'player_groups', return_value=[]):
            code, result = self.run_cfb_main(game, summary)
        self.assertEqual(code, 1)
        self.assertEqual(result['usable_games'], 0)
        self.assertTrue(any(e['category'] == 'unusable_game_context' for e in result['fatal_errors']))

    def test_discovered_pregame_without_usable_context_waits_without_failing(self):
        game, summary = self.cfb_summary()
        game['status'] = 'PRE'
        summary['rosters'] = []
        with patch.object(pipeline, 'player_groups', return_value=[]):
            code, result = self.run_cfb_main(game, summary)

        self.assertEqual(code, 0)
        self.assertEqual(result['usable_games'], 0)
        self.assertEqual(result['fatal_error_count'], 0)
        self.assertIn(game['id'], result['games_awaiting_player_context'])
        self.assertTrue(any(
            warning['category'] == 'awaiting_player_context'
            for warning in result['warning_details']
        ))

    def test_rejected_supplemental_abbreviation_does_not_admit_bad_players(self):
        game, summary = self.cfb_summary()
        summary['rosters'] = summary['rosters'][:1]
        resolve = pipeline.player_groups
        rejected = {'team': {'id': game['team_ids']['SAC'], 'abbreviation': 'OTHER'},
                    'season': {'year': 2026}, 'athletes': [{'id': '5158748', 'displayName': 'Jackson Sharman'}]}
        def resolve_rejected(summary, game, path, cache, warnings, integrity):
            with patch.object(pipeline, 'fetch_json', return_value=rejected):
                return resolve(summary, game, path, cache, warnings, integrity)
        with patch.object(pipeline, 'player_groups', side_effect=resolve_rejected):
            code, result = self.run_cfb_main(game, summary)
        self.assertEqual(code, 0)
        self.assertEqual(result['players'], 1)
        self.assertEqual(result['error_count'], 0)
        self.assertTrue(any('abbreviation mismatch' in w['reason'] for w in result['warning_details']))
        self.assertTrue(any(w['category'] == 'incomplete_team_coverage' for w in result['warning_details']))

    def test_supplemental_numeric_identity_mismatch_remains_fatal(self):
        game, summary = self.cfb_summary()
        summary['rosters'] = summary['rosters'][:1]
        resolve = pipeline.player_groups
        def resolve_rejected(summary, game, path, cache, warnings, integrity):
            with patch.object(pipeline, 'fetch_json', return_value={'team': {'id': '41'}}):
                return resolve(summary, game, path, cache, warnings, integrity)
        with patch.object(pipeline, 'player_groups', side_effect=resolve_rejected):
            code, result = self.run_cfb_main(game, summary)
        self.assertEqual(code, 1)
        self.assertTrue(result['roster_integrity_errors'])

    def test_auxiliary_reconciliation_corrupt_json_is_warning(self):
        with self.conn:
            pipeline.persist_game(self.conn, self.game)
        fatal = []
        with patch.object(pipeline, 'fetch_json', side_effect=json.JSONDecodeError('bad auxiliary JSON', '', 0)):
            warnings = pipeline.reconcile_previous_games(self.conn, 'NFL', 'football/nfl', set(), fatal)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(fatal, [])


if __name__ == '__main__':
    unittest.main()
