"""Sequential, cached ESPN season backfill; explicit DB path required."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import urllib.parse
import urllib.error

import daily_espn_pipeline as p
from espn_history import persist_completed, summary_game

ROOT = 'https://site.api.espn.com/apis/site/v2/sports/'


class CachedESPN:
    def __init__(self, directory, delay=1.0):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.delay = max(1.0, delay)
        self.last_request = None
        self.requests = 0
        self.hits = 0

    def get(self, url, final=False):
        path = self.directory / (hashlib.sha256(url.encode()).hexdigest()+'.json')
        if path.exists() and (final or time.time()-path.stat().st_mtime<21600):
            self.hits += 1
            return json.loads(path.read_text())
        if self.last_request is not None:
            time.sleep(max(0,self.delay-(time.monotonic()-self.last_request)))
        self.last_request = time.monotonic()
        self.requests += 1
        data = p.fetch_json(url)  # Fail once; no retry storm on unavailable ESPN.
        # Only completed summaries are cached indefinitely. Active events expire.
        if final and p.game_status(((data.get('header') or {}).get('competitions') or [{}])[0])!='post':
            return data
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(data))
        temp.replace(path)
        return data


def season_events(client, league, today):
    sport, route = league['sport'], league['espn_path']
    params = {'limit':1000}
    if sport=='CFB': params['groups']=80
    base = ROOT+route+'/scoreboard?'
    metadata = client.get(base+urllib.parse.urlencode(params))
    info = next((l for l in metadata.get('leagues',[]) if l.get('season')), None)
    if not info:
        raise ValueError('ESPN current-season metadata missing')
    season = info['season']
    year = p.numeric_id(season.get('year'))
    start = p.event_date(season.get('startDate'))
    end = min(p.event_date(season.get('endDate')),today)
    if not year or start>end:
        raise ValueError('ESPN current-season boundaries invalid')
    cursor = datetime.date.fromisoformat(start)
    stop = datetime.date.fromisoformat(end)
    events = {}
    while cursor<=stop:
        # The site scoreboard accepts a single YYYYMMDD, as discover_slate
        # uses. Month ranges return HTTP 400, including same-day ranges.
        params['dates'] = cursor.strftime('%Y%m%d')
        data = client.get(base+urllib.parse.urlencode(params))
        if not isinstance(data.get('events'),list) or len(data['events'])>=1000:
            raise ValueError('missing or possibly truncated season scoreboard')
        for event in data['events']:
            comp = (event.get('competitions') or [{}])[0]
            date = p.event_date(event.get('date') or comp.get('date'))
            if not start<=date<=end or p.numeric_id((event.get('season') or {}).get('year'))!=year:
                continue
            if p.game_status(comp)!='post': continue
            gid = p.numeric_id(event.get('id'))
            if not gid or (comp.get('id') is not None and p.numeric_id(comp['id'])!=gid):
                raise ValueError('invalid season event ID')
            sides = p.teams(comp)
            game = dict(id=gid,sport=sport,game_date=date,status='post',home_team=sides['home']['abbr'],
                        away_team=sides['away']['abbr'],team_ids={v['abbr']:v['id'] for v in sides.values()})
            if gid in events and events[gid]!=game:
                raise ValueError('conflicting season event identity')
            events[gid]=game
        cursor += datetime.timedelta(days=1)
    return year, list(events.values())


def counts(conn, today):
    """Count completed rows and actual research inputs, without a starter filter."""
    from heat_view import MARKETS, heat_data, stat_value
    from math_lab import history, history_source
    from board_view import current_games, ZONE
    clock = datetime.datetime.combine(datetime.date.fromisoformat(today),datetime.time(12),ZONE)
    result = {}
    cols = {r['name'] for r in conn.execute('PRAGMA table_info(multi_sport_players)')}
    for league in p.LEAGUES:
        sport=league['sport']
        rows = conn.execute('''SELECT m.* FROM multi_sport_players m JOIN games g ON g.id=m.game_id
            WHERE g.sport=? AND m.sport=g.sport AND m.segment='FULL GAME'
            AND lower(g.status) IN ('post','final','completed','ended')''',(sport,)).fetchall()
        valid=[]
        for row in rows:
            try:
                date=datetime.date.fromisoformat(str(row['game_date']))
                if 'slate_date' in cols and row['slate_date']==date.isoformat(): valid.append(row)
            except ValueError: pass
        historical = conn.execute('''SELECT h.game_id,h.player_id FROM player_game_stats h JOIN games g ON g.id=h.game_id
            WHERE g.sport=? AND h.segment='FULL GAME' AND lower(g.status) IN ('post','final','completed','ended')''',(sport,)).fetchall()
        dated={(r['game_id'],str(r['player_id'])) for r in valid}
        result[sport] = dict(completed_historical_player_rows=len(historical),linked_completed_player_rows=len(rows),
            rows_with_valid_game_dates=sum((r['game_id'],str(r['player_id'])) in dated for r in historical),
            starter_values_populated=sum(r['starter'] in (0,1) for r in rows) if 'starter' in cols else 0,
            minutes_values_populated=sum(r['minutes'] is not None for r in rows) if 'minutes' in cols else 0,
            heat_qualifying_historical_rows=0,math_lab_usable_historical_rows=0,heat_patterns=0)
    current = {g['id']:g for g in current_games(conn,clock) if g['status'] in ('PRE','LIVE')}
    source = history_source(conn)
    seen=set()
    for row in conn.execute('SELECT * FROM multi_sport_players WHERE substr(game_date,1,10)=?',(today,)):
        g=current.get(row['game_id']); key=(row['sport'],str(row['player_id']))
        if key in seen or key[0] not in result or not g or g['sport']!=key[0] or row['team'] not in (g['home'],g['away']): continue
        seen.add(key)
        logs=history(conn,dict(row),today,source)
        usable=sum(not h.get('ambiguous') and any(stat_value(h['payload'],m,key[0]) is not None for m in MARKETS) for h in logs)
        result[key[0]]['math_lab_usable_historical_rows']+=usable
    qualifying=set()
    for card in heat_data(conn,clock=clock,all_cards=True)['cards']:
        if card['sport'] in result:
            result[card['sport']]['heat_patterns']+=1
            if card.get('kind')!='team':
                qualifying.update((card['sport'],card['player_id'],date) for date in card['dates'])
    for sport,_,_ in qualifying:
        result[sport]['heat_qualifying_historical_rows']+=1
    return result


def run(conn, client, today, batch_size=20, repair_existing=True, sports=None, skip_completed=False):
    report = dict(date=today,before=counts(conn,today),leagues={},errors=[])
    for league in p.LEAGUES:
        sport,route = league['sport'],league['espn_path']
        if sports is not None and sport not in sports:
            continue
        detail = report['leagues'][sport] = dict(completed_games=0,player_rows=0,season=None,all_fbs=sport=='CFB',
                                               unavailable_summaries=[],skipped_completed=0)
        try:
            print(f'{sport}: discovering ESPN current season',flush=True)
            year,games=season_events(client,league,today)
            detail['season']=year
            candidates={g['id']:g for g in games}
            if repair_existing:
                for row in conn.execute('''SELECT DISTINCT g.id FROM games g WHERE g.sport=? AND
                    (EXISTS(SELECT 1 FROM player_game_stats h WHERE h.game_id=g.id) OR
                     EXISTS(SELECT 1 FROM multi_sport_players m WHERE m.game_id=g.id))''',(sport,)):
                    if p.numeric_id(row['id']): candidates.setdefault(row['id'],None)
            if skip_completed:
                complete = {r[0] for r in conn.execute('''SELECT g.id FROM games g JOIN game_state s ON s.game_id=g.id
                    WHERE g.sport=? AND lower(g.status) IN ('post','final','completed','ended')
                    AND s.home_score IS NOT NULL AND s.away_score IS NOT NULL
                    AND (SELECT count(DISTINCT team_id) FROM espn_team_game_totals t WHERE t.game_id=g.id)=2
                    AND EXISTS (SELECT 1 FROM multi_sport_players m JOIN player_game_stats h
                        ON h.game_id=m.game_id AND h.player_id=m.player_id AND h.segment=m.segment
                        WHERE m.game_id=g.id AND m.sport=g.sport AND m.segment='FULL GAME'
                        AND m.slate_date=m.game_date)''',(sport,))}
                detail['skipped_completed'] = len(complete & candidates.keys())
                candidates = {gid:g for gid,g in candidates.items() if gid not in complete}
            for index,(gid,game) in enumerate(candidates.items()):
                try:
                    summary=client.get(ROOT+route+'/summary?event='+gid,final=True)
                    validated=summary_game(summary,sport)
                    if validated['id']!=gid: raise ValueError('requested summary event ID mismatch')
                    if validated['status']!='post': continue
                    with conn:
                        n=persist_completed(conn,summary,game or validated)
                    detail['completed_games']+=1
                    detail['player_rows']+=n
                except Exception as exc:
                    if isinstance(exc,urllib.error.HTTPError):
                        exc.close()
                    if isinstance(exc,urllib.error.HTTPError) and exc.code==404:
                        detail['unavailable_summaries'].append(dict(event_id=gid,status=404,
                            url=ROOT+route+'/summary?event='+gid,reason='ESPN summary unavailable'))
                        print(f'{sport}: summary {gid} unavailable (HTTP 404); continuing',flush=True)
                        continue
                    report['errors'].append(f'{sport} {gid}: {exc}')
                    # Stop a league on network outage rather than hitting every event.
                    if isinstance(exc,OSError): break
                if (index+1)%batch_size==0:
                    print(f'{sport}: processed {index+1}/{len(candidates)} events',flush=True)
                    time.sleep(1)
        except Exception as exc:
            report['errors'].append(f'{sport}: {exc}')
    report.update(after=counts(conn,today),requests=client.requests,cache_hits=client.hits)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',required=True)
    parser.add_argument('--cache',default='/opt/liveintel/data/espn-cache')
    parser.add_argument('--report',default='/opt/liveintel/verification/espn-backfill.json')
    parser.add_argument('--date',type=datetime.date.fromisoformat,default=datetime.datetime.now(p.SLATE_ZONE).date())
    parser.add_argument('--batch-size',type=int,default=20)
    args=parser.parse_args()
    if args.batch_size<1: parser.error('batch-size must be positive')
    conn=sqlite3.connect(args.db,timeout=30)
    conn.row_factory=sqlite3.Row
    try:
        report=run(conn,CachedESPN(args.cache),args.date.isoformat(),args.batch_size)
        Path(args.report).write_text(json.dumps(report,indent=2))
        print(json.dumps(report,indent=2))
        return int(bool(report['errors']))
    finally: conn.close()


if __name__=='__main__': raise SystemExit(main())
