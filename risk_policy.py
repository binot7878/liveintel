"""Integer-cent, bankroll-relative TEST risk limits. No financial writes."""
from decimal import Decimal,ROUND_DOWN
VERSION='SURVIVAL_V1'
def cash(value):return int(Decimal(str(value))*100)
def floor_pct(amount,pct):return int((Decimal(amount)*Decimal(str(pct))).quantize(Decimal('1'),rounding=ROUND_DOWN))
def policy(balance,high,pending=0):
    if any(not isinstance(x,int) or isinstance(x,bool) for x in (balance,high,pending)):raise ValueError('Integer cents required')
    if balance<0 or pending<0 or high<balance or high<=0:raise ValueError('Invalid bankroll risk state')
    dd=Decimal(high-balance)/high
    normal,highest,exposure=(.05,0,.10) if dd>=Decimal('.4') else (.05,.10,.20) if dd>=Decimal('.2') else (.10,.20,.30)
    available=max(0,balance-pending);limit=floor_pct(balance,exposure)
    return dict(version=VERSION,balance_cents=balance,high_water_cents=high,pending_cents=pending,available_cents=available,
        drawdown_pct=float(dd*100),normal_pct=normal*100,highest_pct=highest*100,exposure_pct=exposure*100,
        normal_cents=floor_pct(available,normal),highest_cents=floor_pct(available,highest) if highest else None,
        single_cap_cents=floor_pct(balance,.20),exposure_cap_cents=limit,remaining_exposure_cents=max(0,limit-pending))
def validate_stake(balance,high,pending,confidence,stake):
    p=policy(balance,high,pending)
    if confidence not in ('NORMAL','HIGHEST'):raise ValueError('Unknown confidence tier')
    expected=p['normal_cents'] if confidence=='NORMAL' else p['highest_cents']
    if expected is None:raise ValueError('Highest-confidence sizing disabled at 40% drawdown; PASS')
    if expected<=0:raise ValueError('Risk-sized stake below one cent; PASS')
    if stake!=expected:raise ValueError('Stake must equal current available-bankroll risk size')
    if stake>p['available_cents']:raise ValueError('Over available bankroll')
    if stake>p['single_cap_cents']:raise ValueError('Single ticket exceeds 20% bankroll cap')
    if pending+stake>p['exposure_cap_cents']:raise ValueError('Total open exposure exceeds current drawdown-adjusted limit')
    return p
