"""Read-only survival achievements from current verified ticket events and risk audits."""
import json
from datetime import datetime
from decimal import Decimal
from challenge_ledger import run,SPORTS,validate_terms,payout
from risk_policy import policy,validate_stake

def dt(v):
    d=datetime.fromisoformat(v.replace('Z','+00:00'))
    if d.tzinfo is None:raise ValueError('Unverified timestamp')
    return d

def assess(start,tickets,audits,started_at):
    """Pure replay. Invalid evidence yields no award instead of manufactured history."""
    events=[];invalid=0
    if len({t.get('id') for t in tickets})!=len(tickets):return dict(SURVIVAL_SCORE=0,CURRENT_BADGES=[],NEXT_AWARD='VERIFIED EVIDENCE REQUIRED',PROGRESS_TO_NEXT_AWARD=0,EVIDENCE_STATUS='INCOMPLETE')
    for t in tickets:
        try:
            if t['stake_cents']<=0 or not t['source']:raise ValueError()
            created=dt(t['created_at'])
            if created<dt(started_at):raise ValueError()
            rules=validate_terms(t['ticket_type'],t['number_of_legs'],t['stake_cents'],t['odds_decimal'],t['payout_multiplier'],json.loads(t['payout_structure']),t['potential_payout_cents'])
            events.append((created,1,t['id'],'CREATE',t))
            if t['ticket_result']!='PENDING':
                settled=dt(t['settled_at'])
                if settled<created or t['ticket_result'] not in ('WIN','LOSS','PUSH') or not t['settlement_source']:raise ValueError()
                outcomes=t['leg_outcomes']
                if len(outcomes)!=t['number_of_legs'] or any(x not in ('WIN','LOSS','PUSH') for x in outcomes):raise ValueError()
                key='|'.join(outcomes)
                if key in rules:expected_actual=payout(t['stake_cents'],rules[key])
                elif t['ticket_type']=='PICKEM':raise ValueError()
                elif all(x=='WIN' for x in outcomes):expected_actual=t['potential_payout_cents']
                elif 'LOSS' in outcomes:expected_actual=0
                elif t['ticket_type']=='STRAIGHT' and key=='PUSH':expected_actual=t['stake_cents']
                else:raise ValueError()
                actual=t['actual_payout_cents'];pl=t['profit_loss_cents']
                if actual!=expected_actual:raise ValueError()
                if actual is None or actual<0 or pl!=actual-t['stake_cents']:raise ValueError()
                expected='WIN' if pl>0 else 'LOSS' if pl<0 else 'PUSH'
                if expected!=t['ticket_result']:raise ValueError()
                events.append((settled,0,t['id'],'SETTLE',t))
        except (ValueError,KeyError,TypeError):invalid+=1
    if invalid:return dict(SURVIVAL_SCORE=0,CURRENT_BADGES=[],NEXT_AWARD='VERIFIED EVIDENCE REQUIRED',PROGRESS_TO_NEXT_AWARD=0,EVIDENCE_STATUS='INCOMPLETE',INVALID_RECORDS=invalid)
    balance=high=minimum=start;pending=0;maxdd=Decimal(0);settled=[];violations=0;missing=0;comeback=False;recovering_peak=None;open_ids=set()
    for _,_,_,kind,t in sorted(events):
        if kind=='CREATE':
            evidence=audits.get(t['id'])
            try:
                expected=validate_stake(balance,high,pending,t['confidence'],t['stake_cents'])
                if evidence is None:missing+=1
                elif evidence!=expected:violations+=1
            except ValueError:violations+=1
            pending+=t['stake_cents'];open_ids.add(t['id'])
        else:
            if t['id'] not in open_ids:violations+=1
            else:open_ids.remove(t['id']);pending-=t['stake_cents']
            balance+=t['profit_loss_cents'];minimum=min(minimum,balance);settled.append(t)
            if recovering_peak is not None and balance>recovering_peak:comeback=True;recovering_peak=None
            high=max(high,balance)
            drawdown=Decimal(high-balance)/high
            maxdd=max(maxdd,drawdown)
            if drawdown>=Decimal('.2') and recovering_peak is None:recovering_peak=high
            # Exposure can breach a tightened drawdown cap after a loss; stop new risk and record the breach.
            if balance<0:violations+=1
            else:
                if pending>policy(balance,high,pending)['exposure_cap_cents']:violations+=1
    n=len(settled);complete=missing==0;clean=complete and violations==0;alive=minimum>0
    badges=[]
    if complete:
        if alive and n>=25:badges.append('SURVIVOR')
        if n>=25 and maxdd<Decimal('.25'):badges.append('DISCIPLINED')
        if n>=50 and clean:badges.append('RISK MASTER')
        if comeback:badges.append('COMEBACK')
        if alive and n>=100:badges.append('IRON BANKROLL')
        for target,label in [(100000,'$1K CLUB'),(500000,'$5K CLUB'),(1000000,'$10K CHAMPION')]:
            if high>=target:badges.append(label)
    consistency=sum(t['profit_loss_cents']>=0 for t in settled[-25:])/min(n,25) if n else 0
    raw=20+20*max(0,1-float(maxdd)/.5)+(20 if clean else 0)+20*min(n/100,1)+10*consistency+(10 if comeback or maxdd<Decimal('.2') else 0)
    cap=min(100,10+.9*n) if n else 0
    score=int(min(raw,cap,100 if clean else 20)) if alive and complete else 0
    targets=[]
    if alive:targets.append(('SURVIVOR',min(100,n/25*100)))
    if alive and maxdd<Decimal('.25'):targets.append(('DISCIPLINED',min(100,n/25*100)))
    if clean:targets.append(('RISK MASTER',min(100,n/50*100)))
    if alive:targets.append(('IRON BANKROLL',min(100,n/100*100)))
    targets.extend((label,max(0,min(100,(high-start)/(goal-start)*100))) for goal,label in [(100000,'$1K CLUB'),(500000,'$5K CLUB'),(1000000,'$10K CHAMPION')])
    # COMEBACK is opportunistic, never a target that encourages taking a drawdown.
    next_award,progress=next(((label,p) for label,p in targets if label not in badges),('ALL ELIGIBLE MILESTONES EARNED',100))
    if not complete:next_award='VERIFIED RISK EVIDENCE REQUIRED';progress=0
    return dict(SURVIVAL_SCORE=score,CURRENT_BADGES=badges,NEXT_AWARD=next_award,PROGRESS_TO_NEXT_AWARD=round(progress,2),SETTLED_TICKETS=n,MAX_DRAWDOWN_PCT=round(float(maxdd*100),2),EXPOSURE_RULE_VIOLATIONS=violations,MISSING_RISK_AUDITS=missing,EVIDENCE_STATUS='VERIFIED' if complete else 'INCOMPLETE',RISK_COMPLIANCE='COMPLIANT' if clean else 'NONCOMPLIANT' if complete else 'UNVERIFIED',EVER_BROKE=not alive,RECOVERY_VERIFIED=comeback,SAMPLE_CAP=cap)

def awards(c,ai):
    r=run(c,ai)
    tickets=[dict(x) for x in c.execute('SELECT * FROM challenge_tickets WHERE challenge_run_id=? AND ai_id=? ORDER BY id',(r['challenge_run_id'],ai))]
    audits={}
    for t in tickets:
        legs=c.execute('SELECT sport,result FROM challenge_ticket_legs WHERE ticket_id=?',(t['id'],)).fetchall()
        if len(legs)!=t['number_of_legs'] or any(l['sport'] not in SPORTS[ai] for l in legs):
            return dict(SURVIVAL_SCORE=0,CURRENT_BADGES=[],NEXT_AWARD='VERIFIED SPORT EVIDENCE REQUIRED',PROGRESS_TO_NEXT_AWARD=0,EVIDENCE_STATUS='INCOMPLETE')
        t['leg_outcomes']=[l['result'] for l in legs]
        exists=c.execute("SELECT 1 FROM sqlite_master WHERE name='challenge_ticket_risk'").fetchone()
        risk=c.execute('SELECT risk_json FROM challenge_ticket_risk WHERE ticket_id=?',(t['id'],)).fetchone() if exists else None
        if risk:
            try:audits[t['id']]=json.loads(risk['risk_json'])
            except (ValueError,TypeError):pass
    return assess(r['starting_cents'],tickets,audits,r['created_at'])
