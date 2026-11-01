"""TEST specialist tickets: immutable creation terms, serialized stake reservation, explicit payouts."""
import json,sqlite3,uuid
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP
from datetime import datetime,timezone
SPORTS={'BASKETBALL_AI':('NBA','WNBA'),'NFL_AI':('NFL',),'CFB_AI':('CFB',)}
RESULTS={'WIN','LOSS','PUSH'}
def now():return datetime.now(timezone.utc).isoformat()
def decimal(v):
    try:d=Decimal(str(v))
    except (InvalidOperation,TypeError):raise ValueError('Missing/invalid number')
    if not d.is_finite():raise ValueError('Invalid number')
    return d
def cents(v):
    d=decimal(v)
    if d!=d.quantize(Decimal('.01')):raise ValueError('Money requires exact cents')
    return int(d*100)
def payout(stake,multiplier):return int((Decimal(stake)*decimal(multiplier)).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
def required(v):
    if not isinstance(v,str) or not v.strip():raise ValueError('Missing identity/provenance/terms')
    return v.strip()
def migrate_empty_fixed_stakes(c):
    row=c.execute("SELECT sql FROM sqlite_master WHERE name='challenge_tickets'").fetchone()
    if row and 'IN(5000,10000)' in row[0].replace(' ',''):
        if c.execute('SELECT COUNT(*) FROM challenge_tickets').fetchone()[0] or c.execute('SELECT COUNT(*) FROM challenge_ticket_legs').fetchone()[0]:
            raise ValueError('Nonempty fixed-stake ledger requires explicit migration; no rows changed')
        c.execute('BEGIN IMMEDIATE')
        try:
            c.execute('DROP TABLE challenge_ticket_legs')
            c.execute('DROP TABLE challenge_tickets')
            c.commit()
        except Exception:c.rollback();raise

def initialize(c):
    migrate_empty_fixed_stakes(c)
    # Do not silently drop balances from an earlier pick-ledger deployment.
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='challenge_picks'").fetchone() and c.execute('SELECT COUNT(*) FROM challenge_picks').fetchone()[0]:
        raise ValueError('Existing verified picks require explicit ticket-term migration; no changes made')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS challenge_runs(challenge_run_id TEXT PRIMARY KEY,ai_id TEXT NOT NULL,environment TEXT NOT NULL CHECK(environment='TEST'),created_at TEXT NOT NULL,starting_cents INTEGER NOT NULL CHECK(starting_cents=10000),goal_cents INTEGER NOT NULL CHECK(goal_cents=1000000),active INTEGER NOT NULL CHECK(active IN(0,1)));
    CREATE UNIQUE INDEX IF NOT EXISTS challenge_one_active ON challenge_runs(ai_id) WHERE active=1;
    CREATE TABLE IF NOT EXISTS challenge_tickets(
      id INTEGER PRIMARY KEY,creation_key TEXT NOT NULL UNIQUE,challenge_run_id TEXT NOT NULL REFERENCES challenge_runs(challenge_run_id),ai_id TEXT NOT NULL,
      book TEXT NOT NULL,ticket_type TEXT NOT NULL CHECK(ticket_type IN('STRAIGHT','PARLAY','PICKEM')),number_of_legs INTEGER NOT NULL CHECK(number_of_legs>0),
      stake_cents INTEGER NOT NULL CHECK(stake_cents>0),confidence TEXT NOT NULL CHECK(confidence IN('NORMAL','HIGHEST')),
      odds_decimal TEXT,payout_multiplier TEXT,payout_structure TEXT NOT NULL,potential_payout_cents INTEGER NOT NULL CHECK(potential_payout_cents>0),
      source TEXT NOT NULL,created_at TEXT NOT NULL,ticket_result TEXT NOT NULL DEFAULT 'PENDING' CHECK(ticket_result IN('PENDING','WIN','LOSS','PUSH')),
      actual_payout_cents INTEGER,profit_loss_cents INTEGER,settled_at TEXT,settlement_source TEXT,review_reason TEXT,learned_at TEXT);
    CREATE TABLE IF NOT EXISTS challenge_ticket_legs(
      id INTEGER PRIMARY KEY,ticket_id INTEGER NOT NULL REFERENCES challenge_tickets(id),leg_number INTEGER NOT NULL,sport TEXT NOT NULL,game_id TEXT NOT NULL,
      player TEXT,player_id TEXT,market TEXT NOT NULL,line TEXT,side TEXT NOT NULL,result TEXT NOT NULL DEFAULT 'PENDING' CHECK(result IN('PENDING','WIN','LOSS','PUSH')),UNIQUE(ticket_id,leg_number));
    CREATE TABLE IF NOT EXISTS challenge_ticket_risk(ticket_id INTEGER PRIMARY KEY REFERENCES challenge_tickets(id),policy_version TEXT NOT NULL,risk_json TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TRIGGER IF NOT EXISTS challenge_risk_immutable BEFORE UPDATE ON challenge_ticket_risk BEGIN SELECT RAISE(ABORT,'Immutable creation risk evidence'); END;
    CREATE TRIGGER IF NOT EXISTS challenge_ticket_terms_immutable BEFORE UPDATE OF creation_key,challenge_run_id,ai_id,book,ticket_type,number_of_legs,stake_cents,confidence,odds_decimal,payout_multiplier,payout_structure,potential_payout_cents,source,created_at ON challenge_tickets BEGIN SELECT RAISE(ABORT,'Immutable ticket terms'); END;
    CREATE TRIGGER IF NOT EXISTS challenge_leg_terms_immutable BEFORE UPDATE OF ticket_id,leg_number,sport,game_id,player,player_id,market,line,side ON challenge_ticket_legs BEGIN SELECT RAISE(ABORT,'Immutable leg terms'); END;
    ''')
    c.execute('BEGIN IMMEDIATE')
    try:
        for ai in SPORTS:
            if not c.execute('SELECT 1 FROM challenge_runs WHERE ai_id=? AND active=1',(ai,)).fetchone():c.execute('INSERT INTO challenge_runs VALUES(?,?,?,?,10000,1000000,1)',(str(uuid.uuid4()),ai,'TEST',now()))
        c.commit()
    except Exception:c.rollback();raise

def run(c,ai):
    if ai not in SPORTS:raise ValueError('Unknown specialist')
    r=c.execute('SELECT * FROM challenge_runs WHERE ai_id=? AND active=1',(ai,)).fetchone()
    if not r:raise ValueError('No active challenge')
    return dict(r)
def snapshot(c,ai):
    r=run(c,ai)
    tickets=[dict(x) for x in c.execute('SELECT * FROM challenge_tickets WHERE ai_id=? AND challenge_run_id=? ORDER BY id',(ai,r['challenge_run_id']))]
    settled=[t for t in tickets if t['ticket_result']!='PENDING']
    pl=sum(t['profit_loss_cents'] for t in settled);w=sum(t['stake_cents'] for t in settled)
    reserve=sum(t['stake_cents'] for t in tickets if t['ticket_result']=='PENDING')
    balance=r['starting_cents']+pl;high=r['starting_cents'];running=high
    for t in sorted(settled,key=lambda t:(t['settled_at'],t['id'])):running+=t['profit_loss_cents'];high=max(high,running)
    return dict(run=r,tickets=tickets,current_bankroll=balance/100,available=(balance-reserve)/100,pending_exposure=reserve/100,starting_bankroll=100.,high_water_mark=high/100,total_wagered=w/100,total_profit_loss=pl/100,roi=pl/w*100 if w else 0.,wins=sum(t['ticket_result']=='WIN' for t in settled),losses=sum(t['ticket_result']=='LOSS' for t in settled))

def validate_terms(kind,n,stake,odds,multiplier,structure,potential):
    if kind not in ('STRAIGHT','PARLAY','PICKEM') or n<1 or (kind=='STRAIGHT' and n!=1) or (kind in ('PARLAY','PICKEM') and n<2):raise ValueError('Invalid ticket/leg structure')
    if not isinstance(structure,dict):raise ValueError('Explicit payout structure required (empty map allowed for sportsbook bets)')
    rules={}
    for key,value in structure.items():
        parts=key.split('|')
        if len(parts)!=n or any(x not in RESULTS for x in parts) or decimal(value)<0:raise ValueError('Invalid exact ordered leg-outcome payout rule')
        rules[key]=str(decimal(value))
    if kind=='PICKEM':
        if odds is not None or multiplier is None or not rules:raise ValueError('Pickem needs multiplier and entry-specific payout structure')
        m=decimal(multiplier)
        if m<=0 or max(map(decimal,rules.values()))!=m or decimal(rules.get('|'.join(['WIN']*n)))!=m:raise ValueError('Pickem maximum payout not corroborated by terms')
        expected=payout(stake,m)
    else:
        if multiplier is not None:raise ValueError('Sportsbook tickets use explicit decimal odds')
        if odds is None or decimal(odds)<=1:raise ValueError('Missing ticket-level decimal odds')
        expected=payout(stake,odds)
        allwin='|'.join(['WIN']*n)
        if allwin in rules and decimal(rules[allwin])!=decimal(odds):raise ValueError('Conflicting win payout')
    if expected!=potential or potential<=0 or any(payout(stake,v)>potential for v in rules.values()):raise ValueError('Potential payout conflicts with stored ticket terms')
    return rules

def create_ticket(c,*,ai_id,challenge_run_id,book,ticket_type,legs,stake,confidence,potential_payout,source,creation_key,odds=None,payout_multiplier=None,payout_structure=None):
    required(book);required(source);required(creation_key)
    w=cents(stake);potential=cents(potential_payout)
    if confidence not in ('NORMAL','HIGHEST') or w<=0:raise ValueError('Invalid confidence or stake')
    rules=validate_terms(ticket_type,len(legs),w,odds,payout_multiplier,payout_structure,potential)
    c.execute('BEGIN IMMEDIATE')
    try:
        s=snapshot(c,ai_id)
        if s['run']['challenge_run_id']!=challenge_run_id:raise ValueError('Challenge mismatch')
        from risk_policy import validate_stake
        risk=validate_stake(cents(s['current_bankroll']),cents(s['high_water_mark']),cents(s['pending_exposure']),confidence,w)
        for leg in legs:
            if leg.get('sport') not in SPORTS[ai_id]:raise ValueError('Wrong specialist sport')
            for key in ('game_id','market','side'):required(leg.get(key))
            if leg.get('player') and not leg.get('player_id'):raise ValueError('Missing player identity')
            if leg.get('line') is not None:decimal(leg['line'])
            game=c.execute('SELECT sport FROM games WHERE id=?',(leg['game_id'],)).fetchone()
            if not game or game['sport']!=leg['sport']:raise ValueError('Wrong game/sport')
        cur=c.execute('''INSERT INTO challenge_tickets(creation_key,challenge_run_id,ai_id,book,ticket_type,number_of_legs,stake_cents,confidence,odds_decimal,payout_multiplier,payout_structure,potential_payout_cents,source,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(creation_key,challenge_run_id,ai_id,book,ticket_type,len(legs),w,confidence,str(decimal(odds)) if odds is not None else None,str(decimal(payout_multiplier)) if payout_multiplier is not None else None,json.dumps(rules,sort_keys=True),potential,source,now()))
        for i,l in enumerate(legs):c.execute('INSERT INTO challenge_ticket_legs(ticket_id,leg_number,sport,game_id,player,player_id,market,line,side) VALUES(?,?,?,?,?,?,?,?,?)',(cur.lastrowid,i+1,l['sport'],l['game_id'],l.get('player'),l.get('player_id'),l['market'],str(l['line']) if l.get('line') is not None else None,l['side']))
        c.execute('INSERT INTO challenge_ticket_risk VALUES(?,?,?,?)',(cur.lastrowid,risk['version'],json.dumps(risk,sort_keys=True),now()))
        c.commit();return cur.lastrowid
    except Exception:c.rollback();raise

def settle_ticket(c,ticket_id,*,ai_id,challenge_run_id,leg_results,settlement_source):
    required(settlement_source)
    c.execute('BEGIN IMMEDIATE')
    try:
        t=c.execute('SELECT * FROM challenge_tickets WHERE id=?',(ticket_id,)).fetchone()
        if not t or t['ai_id']!=ai_id or t['challenge_run_id']!=challenge_run_id or run(c,ai_id)['challenge_run_id']!=challenge_run_id:raise ValueError('Ticket/specialist/challenge mismatch')
        legs=c.execute('SELECT * FROM challenge_ticket_legs WHERE ticket_id=? ORDER BY leg_number',(ticket_id,)).fetchall()
        if len(legs)!=t['number_of_legs'] or set(leg_results)!={l['id'] for l in legs} or any(v not in RESULTS for v in leg_results.values()):raise ValueError('Exact game-scoped leg IDs and final outcomes required')
        if t['ticket_result']!='PENDING':
            if all(l['result']==leg_results[l['id']] for l in legs):c.rollback();return
            raise ValueError('Settlement conflict requires review')
        try:
            w=t['stake_cents'];rules=validate_terms(t['ticket_type'],len(legs),w,t['odds_decimal'],t['payout_multiplier'],json.loads(t['payout_structure']),t['potential_payout_cents'])
            key='|'.join(leg_results[l['id']] for l in legs)
            if key in rules:actual=payout(w,rules[key])
            elif t['ticket_type']=='PICKEM':raise ValueError('Missing entry-specific outcome payout')
            elif all(v=='WIN' for v in leg_results.values()):actual=t['potential_payout_cents']
            elif 'LOSS' in leg_results.values():actual=0
            elif t['ticket_type']=='STRAIGHT' and key=='PUSH':actual=w
            else:raise ValueError('Reduced/pushed parlay requires explicit stored payout rule')
        except (ValueError,TypeError,InvalidOperation):
            c.execute('UPDATE challenge_tickets SET review_reason=? WHERE id=?',('Missing or invalid ticket-specific settlement terms',ticket_id));c.commit()
            raise ValueError('REVIEW REQUIRED; bankroll unchanged')
        profit=actual-w
        if cents(snapshot(c,ai_id)['current_bankroll'])+profit<0:raise ValueError('Negative bankroll blocked')
        result='WIN' if profit>0 else 'LOSS' if profit<0 else 'PUSH'
        for l in legs:c.execute('UPDATE challenge_ticket_legs SET result=? WHERE id=?',(leg_results[l['id']],l['id']))
        c.execute('UPDATE challenge_tickets SET ticket_result=?,actual_payout_cents=?,profit_loss_cents=?,settled_at=?,settlement_source=?,review_reason=NULL WHERE id=?',(result,actual,profit,now(),settlement_source,ticket_id))
        c.commit()
    except Exception:
        if c.in_transaction:c.rollback()
        raise

def create_pick(c,*,ai_id,sport,game_id,market,side,wager,odds,confidence,source,creation_key,book,challenge_run_id,potential_payout,player=None,player_id=None,line=None):
    """Single-leg adapter. Newly required ticket metadata is always explicit."""
    return create_ticket(c,ai_id=ai_id,challenge_run_id=challenge_run_id,book=book,ticket_type='STRAIGHT',
        legs=[dict(sport=sport,game_id=game_id,market=market,side=side,player=player,player_id=player_id,line=line)],
        stake=wager,odds=odds,confidence=confidence,source=source,creation_key=creation_key,
        potential_payout=potential_payout,payout_structure={})

def settle(c,pick_id,result,*,game_id,ai_id,settlement_source):
    """Single-leg adapter. Never interpret a parlay as an individual wager."""
    ticket=c.execute('SELECT * FROM challenge_tickets WHERE id=?',(pick_id,)).fetchone()
    legs=c.execute('SELECT * FROM challenge_ticket_legs WHERE ticket_id=?',(pick_id,)).fetchall()
    if not ticket or ticket['ticket_type']!='STRAIGHT' or len(legs)!=1 or legs[0]['game_id']!=game_id or ticket['ai_id']!=ai_id:
        raise ValueError('Single-leg settlement context mismatch')
    return settle_ticket(c,pick_id,ai_id=ai_id,challenge_run_id=ticket['challenge_run_id'],leg_results={legs[0]['id']:result},settlement_source=settlement_source)
