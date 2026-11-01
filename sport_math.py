"""Pure descriptive signal layer. No betting decisions, probabilities or writes."""
import math
import re

SPORTS = {
    'NBA': dict(segment='Quarter', count=4, seconds=720, overtime=300, unit='points'),
    'WNBA': dict(segment='Quarter', count=4, seconds=600, overtime=300, unit='points'),
    'NCAAB': dict(segment='Half', count=2, seconds=1200, overtime=300, unit='points'),
    'NFL': dict(segment='Quarter', count=4, seconds=900, overtime=None, unit='points'),
    'CFB': dict(segment='Quarter', count=4, seconds=900, overtime=None, unit='points'),
    'NHL': dict(segment='Period', count=3, seconds=1200, overtime=None, unit='goals'),
    'MLB': dict(segment='Inning', count=9, seconds=None, overtime=None, unit='runs'),
}
PATHS = {'NBA':'basketball/nba','WNBA':'basketball/wnba','NCAAB':'basketball/mens-college-basketball',
         'NFL':'football/nfl','CFB':'football/college-football','NHL':'hockey/nhl','MLB':'baseball/mlb'}


def numeric(value):
    if value is None or isinstance(value, bool) or value=='': return None
    try:
        n=float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError): return None


def timing(sport, period, clock):
    cfg=SPORTS.get(sport, {})
    p=numeric(period)
    label=cfg.get('segment','Segment')
    result=dict(label=f'{label} {int(p)}' if p and p.is_integer() else '—', elapsed=None, remaining=None, horizon=None)
    if not p or not p.is_integer() or p<1: return result
    p=int(p); count=cfg.get('count',0); seconds=cfg.get('seconds')
    if p>count and count and sport!='MLB': result['label']=f'OT {p-count}'
    match=re.fullmatch(r'(\d{1,2}):([0-5]\d)',str(clock))
    if not seconds or not match: return result
    left=int(match[1])*60+int(match[2]); duration=seconds if p<=count else cfg.get('overtime')
    if duration is None or left>duration: return result
    horizon=count*seconds if p<=count else count*seconds+(p-count)*duration
    elapsed=(p-1)*seconds+seconds-left if p<=count else count*seconds+(p-count-1)*duration+duration-left
    result.update(elapsed=elapsed,remaining=horizon-elapsed,horizon=horizon)
    return result


def live_math(game, observations=()):
    t=timing(game.get('sport'),game.get('period'),game.get('clock'))
    out=dict(**t,lead=None,pace=None,projection=None,margin=None,run=None,signals=[],windows=[],win_probability=None,
             basis='Scoring-rate extrapolation to the current regulation/overtime horizon; not a win probability.')
    home,away=numeric(game.get('home_score')),numeric(game.get('away_score'))
    if game.get('status')!='LIVE' or home is None or away is None or min(home,away)<0: return out
    out['lead']=home-away
    elapsed=t['elapsed']
    if not elapsed: return out
    out['pace']=round((home+away)*60/elapsed,2)
    rates=[away/elapsed,home/elapsed]
    samples=[]
    for o in observations:
        ot=timing(game['sport'],o.get('period'),o.get('clock'))['elapsed']
        oh,oa=numeric(o.get('home')),numeric(o.get('away'))
        if ot is not None and 0<=ot<elapsed and oh is not None and oa is not None and 0<=oh<=home and 0<=oa<=away:
            samples.append((ot,oa,oh))
    # Conflicting snapshots at the same clock are not suitable window endpoints.
    samples=[s for s in set(samples) if len({x[1:] for x in samples if x[0]==s[0]})==1]
    for window in (30,60,120,300):
        candidates=[s for s in samples if s[0]<=elapsed-window and elapsed-window-s[0]<=15]
        if not candidates: continue
        start=max(candidates); dt=elapsed-start[0]
        out['windows'].append(dict(seconds=dt,away=away-start[1],home=home-start[2]))
    if out['windows']:
        run=out['windows'][-1]; out['run']=run
        if run['home']==0 and run['away']==0: out['signals'].append('SCORING DROUGHT')
        rates=[run['away']/run['seconds'],run['home']/run['seconds']]
        recent=sum(rates)*60
        if recent>out['pace']*1.2: out['signals'].append('PACE RISING')
        elif recent<out['pace']*.8: out['signals'].append('PACE FALLING')
        delta=run['home']-run['away']; lead=out['lead']
        if lead and delta*lead<0:
            out['signals']+=['LEAD WEAKENING','COMEBACK PRESSURE']
        elif lead and delta*lead>0: out['signals'].append('LEAD STABLE')
    projected=[away+rates[0]*t['remaining'],home+rates[1]*t['remaining']]
    out['projection']=[round(v,1) for v in projected]; out['margin']=round(projected[1]-projected[0],1)
    if out['run'] and out['lead']*out['margin']<0:
        out['signals']+=['PROJECTED MARGIN CROSSING ZERO','LEAD COLLAPSE RISK']
    return out


def role_signals(current, history):
    """Only comparable completed-game samples; partial minutes cannot imply a decline."""
    badges=[]
    for key,badge in [('minutes','MINUTES UP'),('usage','USAGE UP'),('targets','ROLE CHANGE'),('fga','ROLE CHANGE')]:
        values=[numeric(h.get(key)) for h in history[:5]]
        value=numeric(current.get(key))
        if len(values)>=4 and all(v is not None for v in values) and value is not None:
            avg=sum(values)/len(values)
            if avg>0 and value>=avg*1.25: badges.append(badge)
    if current.get('starter') is False and badges: badges.append('BENCH SURGE')
    return list(dict.fromkeys(badges))


def segment_scores(sport, observations):
    """Only exact period-end cumulative scores establish completed segments."""
    cfg=SPORTS.get(sport,{})
    if not cfg.get('seconds'): return []
    ends={}
    for o in observations:
        p=numeric(o.get('period')); h=numeric(o.get('home')); a=numeric(o.get('away'))
        if p and p.is_integer() and o.get('clock') in ('0:00','00:00') and h is not None and a is not None:
            ends.setdefault(int(p),set()).add((a,h))
    output=[]; previous=(0,0)
    for p in range(1,max(ends,default=0)+1):
        if len(ends.get(p,set()))!=1: break
        end=next(iter(ends[p])); diff=[end[i]-previous[i] for i in (0,1)]
        if min(diff)<0: break
        output.append(dict(label=timing(sport,p,'0:00')['label'],away=diff[0],home=diff[1]));previous=end
    return output
