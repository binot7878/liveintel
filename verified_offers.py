"""Existing LiveIntel manual props -> verified offers -> independent specialist decisions.
No quote fetching, odds defaults, line generation, owner reads, or historical writes.
"""
import argparse,hashlib,json,os,sqlite3,tempfile
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from challenge_ledger import SPORTS,decimal,cents,validate_terms,snapshot,required,now
from challenge_decisions import evaluate,initialize_decisions,timestamp
MAX_AGE=120
SOURCE_FIELDS=('id','game_id','player_name','market','line','side','book','observed_at')

def initialize_gateway(c):
    c.execute('''CREATE TABLE IF NOT EXISTS verified_manual_offer_terms(
       offer_id TEXT PRIMARY KEY,source_rows_json TEXT NOT NULL,terms_json TEXT NOT NULL,created_at TEXT NOT NULL)''')
    c.execute('''CREATE TRIGGER IF NOT EXISTS manual_offer_terms_immutable BEFORE UPDATE ON verified_manual_offer_terms
       BEGIN SELECT RAISE(ABORT,'Quote attachments are immutable; capture a new offer'); END''');c.commit()

def source_row(c,prop_id):
    row=c.execute('SELECT * FROM prop_lines WHERE id=?',(prop_id,)).fetchone()
    if not row:raise ValueError('Source prop missing')
    return {k:dict(row).get(k) for k in SOURCE_FIELDS}

def source_time(value,zone=None):
    try:return timestamp(value)
    except ValueError:
        if not zone:raise ValueError('Source timestamp timezone is not recorded; explicit source timezone required')
        dt=datetime.fromisoformat(value)
        z=ZoneInfo(zone)
        # Ambiguous local times cannot establish freshness.
        if dt.replace(tzinfo=z,fold=0).utcoffset()!=dt.replace(tzinfo=z,fold=1).utcoffset():raise ValueError('Ambiguous source timestamp')
        localized=dt.replace(tzinfo=z)
        if localized.astimezone(timezone.utc).astimezone(z).replace(tzinfo=None)!=dt:raise ValueError('Invalid local source timestamp')
        return localized.astimezone(timezone.utc)

def fresh(dt,clock):
    if not 0<=(clock-dt).total_seconds()<=MAX_AGE:raise ValueError('Stale or future-dated source offer')

def route(sport):return next((ai for ai,sports in SPORTS.items() if sport in sports),None)

def eligible_game_players(c,game_id,sport,clock=None):
    """Shared current-slate/identity gate for quote validation and owner intake."""
    clock=clock or datetime.now(timezone.utc)
    game=c.execute('SELECT * FROM games WHERE id=?',(game_id,)).fetchone()
    if not game or game['sport']!=sport or route(sport) is None:
        raise ValueError('Wrong source game or sport')
    if str(game['status']).lower() not in ('pre','in','scheduled','live'):
        raise ValueError('Wrong or inactive source game')
    today=clock.astimezone(ZoneInfo('America/New_York')).date().isoformat()
    rows=c.execute('SELECT player_id,player_name,team,game_date FROM multi_sport_players WHERE game_id=? AND sport=?',(game_id,sport)).fetchall()
    if not rows or any(str(r['game_date'])[:10]!=today for r in rows):
        raise ValueError('Game is not verified on the current ESPN slate date')
    identities={}
    for r in rows:
        if r['player_name'] and str(r['player_id']).isdigit() and r['team'] in (game['home_team'],game['away_team']):
            identities.setdefault(r['player_name'],set()).add(str(r['player_id']))
    players={}
    for r in rows:
        if len(identities.get(r['player_name'],()))==1 and str(r['player_id']) in identities[r['player_name']] and r['team'] in (game['home_team'],game['away_team']):
            players[(str(r['player_id']),r['player_name'],r['team'])]=dict(r)
    return dict(game),list(players.values()),today

def build_offer(c,offer_id,saved_rows,terms,clock=None):
    clock=clock or datetime.now(timezone.utc)
    if not isinstance(terms,dict) or not isinstance(saved_rows,list) or not saved_rows:raise ValueError('Missing stored source rows or terms')
    source=required(terms.get('source'));kind=required(terms.get('ticket_type'));book=required(terms.get('book'))
    if book.casefold() in ('manual_entry','test manual','manual','unknown'):raise ValueError('Book is a source label, not an explicit book')
    stake=cents(terms.get('stake'))
    eligible=terms.get('eligible_stakes')
    if not isinstance(eligible,list) or not eligible or any(cents(v)<=0 for v in eligible) or stake not in [cents(v) for v in eligible]:raise ValueError('Missing explicit positive stake eligibility')
    if stake<=0:raise ValueError('Stake must be positive')
    potential=cents(terms.get('potential_payout'))
    observed=timestamp(terms.get('observed_at'));fresh(observed,clock)
    expiry=timestamp(terms.get('expires_at'))
    if expiry<=clock or expiry<=observed:raise ValueError('Offer terms expired')
    legs=[];ai=None;dates=[];times=[observed]
    for saved in saved_rows:
        row=source_row(c,saved.get('id'))
        if row!=saved:raise ValueError('Source prop changed since terms captured; reject exact-context mismatch')
        if row['book']!=book:raise ValueError('Book does not match source prop')
        if row['line'] is None:raise ValueError('Missing source line')
        decimal(row['line'])
        for k in ('game_id','market','side'):required(row[k])
        dt=source_time(row['observed_at'],terms.get('source_timezone'));fresh(dt,clock);times.append(dt)
        game=c.execute('SELECT * FROM games WHERE id=?',(row['game_id'],)).fetchone()
        if not game or str(game['status']).lower() not in ('pre','in','scheduled','live'):raise ValueError('Wrong or inactive source game')
        specialist=route(game['sport'])
        if specialist is None or (ai is not None and ai!=specialist):raise ValueError('Wrong sport or mixed specialist ticket')
        if terms.get('ai_id') is not None and terms['ai_id']!=specialist:raise ValueError('Wrong specialist for source sport')
        if terms.get('sport') is not None and terms['sport']!=game['sport']:raise ValueError('Wrong declared offer sport')
        ai=specialist
        _,rows,today=eligible_game_players(c,row['game_id'],game['sport'],clock)
        player=row['player_name'];pid=None
        if player:
            identities={(str(r['player_id']),r['player_name']) for r in rows if r['player_name']==player and str(r['player_id']).isdigit() and r['team'] in (game['home_team'],game['away_team'])}
            if len(identities)!=1:raise ValueError('Player identity missing/ambiguous for source game/team')
            pid=next(iter(identities))[0]
        elif not terms.get('team_market'):raise ValueError('Player missing; team market must be explicit')
        if terms.get('player_ids',{}).get(str(row['id']),pid)!=pid:raise ValueError('Attached player ID mismatches source game')
        legs.append(dict(sport=game['sport'],game_id=row['game_id'],player=player,player_id=pid,market=row['market'],line=row['line'],side=row['side']))
        dates.append(today)
    rules=validate_terms(kind,len(legs),stake,terms.get('odds'),terms.get('payout_multiplier'),terms.get('payout_structure'),potential)
    oldest=min(times)
    # Stable offer ID binds complete raw source context and exact stored quote terms.
    fingerprint=hashlib.sha256(json.dumps([saved_rows,terms],sort_keys=True).encode()).hexdigest()
    proposal=dict(ai_id=ai,challenge_run_id=snapshot(c,ai)['run']['challenge_run_id'],offer_id='manual:'+fingerprint,
        qualified=False,reason='Valid offer; no verified specialist evaluation qualified a bet',book=book,ticket_type=kind,
        stake=stake/100,potential_payout=potential/100,source=source,legs=legs,observed_at=oldest.isoformat(),expires_at=expiry.isoformat(),
        odds=terms.get('odds'),payout_multiplier=terms.get('payout_multiplier'),payout_structure=rules)
    public=dict(OFFER_ID=proposal['offer_id'],OFFER_SOURCE=source,BOOK=book,TICKET_TYPE=kind,GAME=[l['game_id'] for l in legs],PLAYER=[l['player'] for l in legs],MARKET=[l['market'] for l in legs],LINE=[l['line'] for l in legs],SIDE=[l['side'] for l in legs],ODDS_OR_MULTIPLIER=terms.get('odds') if kind!='PICKEM' else terms.get('payout_multiplier'),POTENTIAL_PAYOUT=potential/100,OFFER_AGE=round((clock-oldest).total_seconds(),2),VALIDATION_STATUS='VALID',AI_ID=ai)
    proposal['offer_validation']=public
    return proposal,public

def capture_terms(c,offer_id,prop_ids,terms,clock=None):
    """Explicit local capture of supplied terms. Source rows are never edited."""
    required(offer_id)
    if len(set(prop_ids))!=len(prop_ids):raise ValueError('Duplicate source legs')
    rows=[source_row(c,p) for p in prop_ids]
    proposal,public=build_offer(c,offer_id,rows,terms,clock)
    c.execute('INSERT INTO verified_manual_offer_terms VALUES(?,?,?,?)',(offer_id,json.dumps(rows,sort_keys=True),json.dumps(terms,sort_keys=True),now()));c.commit()
    return public

def scan_offers(c,clock=None):
    clock=clock or datetime.now(timezone.utc);offers=[];proposals=[];covered=set();seen=set()
    exists=c.execute("SELECT 1 FROM sqlite_master WHERE name='verified_manual_offer_terms'").fetchone()
    for item in c.execute('SELECT * FROM verified_manual_offer_terms ORDER BY created_at,offer_id') if exists else []:
        saved=json.loads(item['source_rows_json']);terms=json.loads(item['terms_json']);covered.update(r['id'] for r in saved)
        try:
            p,public=build_offer(c,item['offer_id'],saved,terms,clock)
            if p['offer_id'] not in seen:proposals.append(p);seen.add(p['offer_id'])
            else:public['VALIDATION_STATUS']='DUPLICATE';public['REASON']='Same exact source and terms; only one proposal emitted'
        except (ValueError,TypeError,KeyError) as exc:
            games=[c.execute('SELECT sport FROM games WHERE id=?',(r.get('game_id'),)).fetchone() for r in saved]
            ais={route(g['sport']) for g in games if g}
            public=dict(OFFER_ID=item['offer_id'],OFFER_SOURCE=terms.get('source'),BOOK=terms.get('book'),TICKET_TYPE=terms.get('ticket_type'),GAME=[r.get('game_id') for r in saved],PLAYER=[r.get('player_name') for r in saved],MARKET=[r.get('market') for r in saved],LINE=[r.get('line') for r in saved],SIDE=[r.get('side') for r in saved],ODDS_OR_MULTIPLIER=terms.get('odds') or terms.get('payout_multiplier'),POTENTIAL_PAYOUT=terms.get('potential_payout'),OFFER_AGE=None,VALIDATION_STATUS='REJECTED',REASON=str(exc),AI_ID=next(iter(ais)) if len(ais)==1 else None)
        offers.append(public)
    # Existing manual records without attached terms remain visible as rejected sources.
    for row in c.execute('SELECT * FROM prop_lines ORDER BY id'):
        if row['id'] in covered:continue
        g=c.execute('SELECT sport FROM games WHERE id=?',(row['game_id'],)).fetchone()
        offers.append(dict(OFFER_ID='prop_lines:'+str(row['id']),OFFER_SOURCE='prop_lines:'+str(row['id']),BOOK=row['book'],TICKET_TYPE=None,GAME=[row['game_id']],PLAYER=[row['player_name']],MARKET=[row['market']],LINE=[row['line']],SIDE=[row['side']],ODDS_OR_MULTIPLIER=None,POTENTIAL_PAYOUT=None,OFFER_AGE=None,VALIDATION_STATUS='REJECTED',REASON='No explicit stored ticket type, stake, odds or entry payout attachment',AI_ID=route(g['sport']) if g else None))
    return proposals,offers

def evaluate_offers(c,evaluations=None,clock=None):
    clock=clock or datetime.now(timezone.utc);proposals,offers=scan_offers(c,clock);evaluations=evaluations or {};decisions={}
    for ai in SPORTS:
        candidates=[p for p in proposals if p['ai_id']==ai]
        prepared=[]
        for p in candidates:
            p=dict(p);e=evaluations.get(ai,{}).get(p['offer_id'])
            if isinstance(e,dict):
                try:
                    if e.get('challenge_run_id')!=p['challenge_run_id'] or e.get('offer_id')!=p['offer_id']:raise ValueError('Evaluation context mismatch')
                    fresh(timestamp(e.get('evaluated_at')),clock)
                    required(e.get('source'));required(e.get('reason'))
                    p.update(qualified=e.get('qualified') is True,confidence_score=e.get('confidence_score'),reason=e['reason'],evaluation_source=e['source'])
                except ValueError as exc:p['reason']=str(exc)
            prepared.append(p)
        # Qualified evidence first; valid market data alone never selects BET.
        prepared.sort(key=lambda p:p.get('qualified') is True,reverse=True)
        if not prepared:decisions[ai]=evaluate(c,ai,None,clock)
        else:
            for p in prepared:
                d=evaluate(c,ai,p,clock)
                if d['action']!='PASS':break
            decisions[ai]=d
    return dict(offers=offers,decisions=decisions,valid_current_offers=len(proposals),tickets_created=sum(d['ticket_id'] is not None and d['action']!='PASS' for d in decisions.values()),passes=sum(d['action']=='PASS' for d in decisions.values()))

def write_evaluations(path,result):
    """Publish the complete final result without exposing a partial JSON file."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    payload=dict(result,generated_at=now(),evaluations=result['decisions'])
    temp_path=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,
                                         prefix='.'+path.name+'.',suffix='.tmp',delete=False) as f:
            temp_path=f.name
            json.dump(payload,f,indent=2)
            f.flush();os.fsync(f.fileno())
        os.replace(temp_path,path)
    finally:
        if temp_path is not None and os.path.exists(temp_path):os.unlink(temp_path)

def main():
    ap=argparse.ArgumentParser(description='Verified existing-manual-prop offer gateway; TEST only')
    ap.add_argument('--database',required=True);ap.add_argument('--capture',type=Path);ap.add_argument('--evaluations',type=Path);ap.add_argument('--scan-only',action='store_true')
    args=ap.parse_args();c=sqlite3.connect(('file:'+args.database+'?mode=ro') if args.scan_only else args.database,uri=args.scan_only,timeout=30);c.row_factory=sqlite3.Row
    if args.scan_only:
        _,offers=scan_offers(c);print(json.dumps(offers,indent=2));return
    initialize_gateway(c);initialize_decisions(c)
    if args.capture:
        payload=json.loads(args.capture.read_text());print(json.dumps(capture_terms(c,payload['offer_id'],payload['prop_ids'],payload['terms']),indent=2));return
    evaluations=json.loads(args.evaluations.read_text()) if args.evaluations and args.evaluations.exists() else {}
    result=evaluate_offers(c,evaluations)
    if args.evaluations:write_evaluations(args.evaluations,result)
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
