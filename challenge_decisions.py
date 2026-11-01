"""Trusted local specialist proposal gateway. TEST ledger only; no bookmaker placement."""
import json,hashlib,sqlite3,argparse
from datetime import datetime,timezone
from pathlib import Path
from challenge_ledger import SPORTS,snapshot,create_ticket,decimal,cents,now

def initialize_decisions(c):
    c.execute('BEGIN IMMEDIATE')
    try:
        row=c.execute("SELECT sql FROM sqlite_master WHERE name='challenge_decisions'").fetchone()
        old=[]
        if row and "'BET $50'" in row[0]:
            old=[tuple(x) for x in c.execute('SELECT * FROM challenge_decisions ORDER BY id')]
            c.execute('ALTER TABLE challenge_decisions RENAME TO challenge_decisions_fixed_stake_backup')
        c.execute("""CREATE TABLE IF NOT EXISTS challenge_decisions(
          id INTEGER PRIMARY KEY,challenge_run_id TEXT NOT NULL,ai_id TEXT NOT NULL,
          action TEXT NOT NULL CHECK(action='PASS' OR action LIKE 'BET $%'),reason TEXT NOT NULL,
          ticket_id INTEGER,confidence_score TEXT,source TEXT,proposal_json TEXT,created_at TEXT NOT NULL)""")
        if old:c.executemany('INSERT INTO challenge_decisions VALUES(?,?,?,?,?,?,?,?,?,?)',old)
        if row and "'BET $50'" in row[0]:c.execute('DROP TABLE challenge_decisions_fixed_stake_backup')
        c.commit()
    except Exception:c.rollback();raise

def timestamp(value):
    try:
        dt=datetime.fromisoformat(value.replace('Z','+00:00'))
        if dt.tzinfo is None:raise ValueError()
        return dt.astimezone(timezone.utc)
    except (AttributeError,TypeError,ValueError):raise ValueError('Missing timezone-aware market timestamp')

def validate(c,ai,proposal,clock):
    if not isinstance(proposal,dict):raise ValueError('Invalid specialist proposal')
    state=snapshot(c,ai)
    if proposal.get('ai_id')!=ai or proposal.get('challenge_run_id')!=state['run']['challenge_run_id']:raise ValueError('Specialist/challenge mismatch')
    if proposal.get('qualified') is not True:raise ValueError(proposal.get('reason') or 'Specialist did not qualify an opportunity')
    if not isinstance(proposal.get('reason'),str) or not proposal['reason'].strip():raise ValueError('Missing qualification reason')
    score=decimal(proposal.get('confidence_score'))
    rules=dict(c.execute('SELECT rule_name,rule_value FROM ai_wager_rules WHERE ai_id=?',(ai,)).fetchall())
    low=decimal(rules.get('fifty_confidence_threshold'));high=decimal(rules.get('hundred_confidence_threshold'))
    if not (0<=low<high<=1) or not 0<=score<=1:raise ValueError('Invalid confidence policy or score')
    if score<low:raise ValueError('Confidence below normal-qualified threshold')
    from risk_policy import policy,cash
    risk=policy(cash(state['current_bankroll']),cash(state['high_water_mark']),cash(state['pending_exposure']))
    tier='HIGHEST' if score>=high else 'NORMAL'
    stake_cents=risk['highest_cents'] if tier=='HIGHEST' else risk['normal_cents']
    if stake_cents is None or stake_cents<=0:raise ValueError('Risk policy disables this sizing; PASS')
    amount=stake_cents/100
    if cents(proposal.get('stake'))!=stake_cents:raise ValueError('Quoted stake does not match qualified decision size')
    if amount>state['available']:raise ValueError('Insufficient unreserved bankroll for qualified stake')
    observed=timestamp(proposal.get('observed_at'));expiry=timestamp(proposal.get('expires_at'))
    if not 0<=(clock-observed).total_seconds()<=120 or expiry<=clock or expiry<=observed:raise ValueError('Market terms stale, expired, or future-dated')
    if not isinstance(proposal.get('offer_id'),str) or not proposal['offer_id'].strip():raise ValueError('Missing stable offer identity')
    legs=proposal.get('legs')
    if not isinstance(legs,list) or not legs:raise ValueError('Missing legs')
    for leg in legs:
        if leg.get('sport') not in SPORTS[ai]:raise ValueError('Wrong specialist sport')
        if leg.get('line') is None:raise ValueError('Missing market line')
        decimal(leg['line'])
        game=c.execute('SELECT * FROM games WHERE id=?',(leg.get('game_id'),)).fetchone()
        if not game or game['sport']!=leg['sport'] or str(game['status']).lower() not in ('pre','in','scheduled','live'):raise ValueError('Game not current PRE/LIVE in correct sport')
        if leg.get('player') or leg.get('player_id'):
            if not leg.get('player') or not str(leg.get('player_id','')).isdigit():raise ValueError('Missing ESPN player identity')
            rows=c.execute('SELECT player_name,team FROM multi_sport_players WHERE game_id=? AND player_id=? AND sport=?',(leg['game_id'],str(leg['player_id']),leg['sport'])).fetchall()
            if not any(r['player_name']==leg['player'] and r['team'] in (game['home_team'],game['away_team']) for r in rows):raise ValueError('Player identity not verified for selected game/team')
    key=hashlib.sha256((ai+'|'+state['run']['challenge_run_id']+'|'+proposal['offer_id']).encode()).hexdigest()
    return dict(ai_id=ai,challenge_run_id=state['run']['challenge_run_id'],book=proposal.get('book'),ticket_type=proposal.get('ticket_type'),legs=legs,
        stake=amount,confidence=tier,potential_payout=proposal.get('potential_payout'),source=proposal.get('source'),creation_key=key,
        odds=proposal.get('odds'),payout_multiplier=proposal.get('payout_multiplier'),payout_structure=proposal.get('payout_structure')),score

def evaluate(c,ai,proposal=None,clock=None):
    """Append one decision; ticket creation remains inside the ledger's atomic reservation."""
    if ai not in SPORTS:raise ValueError('Unknown specialist; owner access prohibited')
    state=snapshot(c,ai);action='PASS';ticket=None;score=None
    reason='No current verified specialist proposal with book, line, odds/payout terms and qualification evidence'
    if proposal is not None:
        try:
            params,score=validate(c,ai,proposal,clock or datetime.now(timezone.utc))
            existing=c.execute('SELECT id FROM challenge_tickets WHERE creation_key=?',(params['creation_key'],)).fetchone()
            if existing:raise ValueError('Offer already processed; no duplicate bet')
            ticket=create_ticket(c,**params);action='BET $'+format(params['stake'],'.2f');reason=proposal['reason']
        except (ValueError,TypeError,KeyError,sqlite3.IntegrityError) as exc:
            reason=str(exc) or 'Incomplete market terms; review required'
    # Recheck the run after ledger commit so decisions cannot attach to a replaced run.
    if snapshot(c,ai)['run']['challenge_run_id']!=state['run']['challenge_run_id']:raise ValueError('Challenge changed during decision')
    c.execute('INSERT INTO challenge_decisions(challenge_run_id,ai_id,action,reason,ticket_id,confidence_score,source,proposal_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
        (state['run']['challenge_run_id'],ai,action,reason,ticket,str(score) if score is not None else None,proposal.get('source') if isinstance(proposal,dict) else None,json.dumps(proposal,sort_keys=True) if proposal is not None else None,now()));c.commit()
    return latest_decision(c,ai)

def latest_decision(c,ai):
    state=snapshot(c,ai)
    exists=c.execute("SELECT 1 FROM sqlite_master WHERE name='challenge_decisions'").fetchone()
    row=c.execute('SELECT * FROM challenge_decisions WHERE ai_id=? AND challenge_run_id=? ORDER BY id DESC LIMIT 1',(ai,state['run']['challenge_run_id'])).fetchone() if exists else None
    return dict(row) if row else dict(action='PASS',reason='No verified decision recorded; current book/market terms and qualification required',ticket_id=None,created_at=None)

def ticket_views(c,ai):
    views=[]
    for t in snapshot(c,ai)['tickets']:
        t['legs']=[dict(l) for l in c.execute('SELECT * FROM challenge_ticket_legs WHERE ticket_id=? ORDER BY leg_number',(t['id'],))]
        views.append(t)
    return views

def main():
    p=argparse.ArgumentParser(description='Local TEST-only challenge decision runner')
    p.add_argument('--database',required=True);p.add_argument('--proposals',type=Path)
    args=p.parse_args(); proposals=json.loads(args.proposals.read_text()) if args.proposals and args.proposals.exists() else {}
    if not isinstance(proposals,dict) or any(ai not in SPORTS for ai in proposals):raise SystemExit('Invalid specialist proposal envelope')
    c=sqlite3.connect(args.database,timeout=30);c.row_factory=sqlite3.Row;initialize_decisions(c)
    print(json.dumps({ai:evaluate(c,ai,proposals.get(ai)) for ai in SPORTS},indent=2))
if __name__=='__main__':main()
