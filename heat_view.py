"""Read-only repeat patterns from dated, completed, identity-linked box scores.

Historical hit frequency is descriptive. It is never a true win probability or
an invented sportsbook line. Undated legacy history is not called 'recent'.
"""
import json
from collections import defaultdict
from datetime import datetime, timezone
from board_view import ZONE, current_games, number, text, rows
from verified_offers import scan_offers

MARKETS = {
    'Minutes': (('MIN','minutes'), (20,30)),
    'Shot Attempts': (('FGA','fga'), (10,15)),
    '3PA': (('3PA','three_pa'), (5,10)),
    'Targets': (('receiving','TGTS','targets'), (5,10)),
    'Rush Attempts': (('rushing','CAR','rush_attempts'), (10,20)),
    'Pass Attempts': (('passing','ATT','pass_attempts'), (20,30)),
    'Time on Ice': (('TOI','time_on_ice'), (15,20)),
    'Goals': (('G','goals'), (1,2)),
    'Shots': (('S','shots'), (3,5)),
    'Hits': (('H','hits'), (1,2)),
    'Runs': (('R','runs'), (1,2)),
    'RBIs': (('RBI','rbis'), (1,2)),
    'Strikeouts': (('K','strikeouts'), (3,5,8)),
    'Points': (('PTS','points'), (10,20,30)),
    'Rebounds': (('REB','rebounds'), (5,10,15)),
    'Assists': (('AST','assists'), (3,5,10)),
    'Passing Yards': (('passing','YDS','pass_yds','pass_yards'), (150,200,250,300)),
    'Rushing Yards': (('rushing','YDS','rush_yds','rush_yards'), (25,50,75,100)),
    'Receiving Yards': (('receiving','YDS','rec_yds','receiving_yards'), (25,50,75,100)),
    'Receptions': (('receiving','REC','rec','receptions'), (3,5,8)),
}
ALIASES = {'pts':'Points','reb':'Rebounds','ast':'Assists','pass_yds':'Passing Yards',
    'rush_yds':'Rushing Yards','recv_yds':'Receiving Yards','receiving yards':'Receiving Yards'}


def stat_value(payload, market, sport):
    keys, _ = MARKETS[market]
    allowed={'NHL':{'Goals','Assists','Shots','Time on Ice'},'MLB':{'Hits','Runs','RBIs','Strikeouts'}}
    if sport in allowed:
        if market not in allowed[sport]: return None
        stats=payload.get('totals',payload.get('',payload))
        return next((number(stats.get(k)) for k in keys if isinstance(stats,dict) and number(stats.get(k)) is not None and number(stats.get(k))>=0),None)
    if market in ('Goals','Shots','Hits','Runs','RBIs','Strikeouts','Time on Ice'): return None
    if sport in ('NBA','WNBA','NCAAB'):
        if market not in ('Points','Rebounds','Assists','Minutes','Shot Attempts','3PA'):
            return None
        stats = payload.get('totals', payload.get('', payload))
        candidates = [stats.get(k) for k in keys] if isinstance(stats,dict) else []
    else:
        if market in ('Points','Rebounds','Assists','Minutes','Shot Attempts','3PA'):
            return None
        category,key,*flat = keys
        category_stats = payload.get(category,{})
        stats = payload.get('totals',payload.get('',payload))
        candidates = ([category_stats.get(key)] if isinstance(category_stats,dict) else []) + ([stats.get(k) for k in flat] if isinstance(stats,dict) else [])
    return next((number(v) for v in candidates if number(v) is not None and number(v)>=0),None)


def pattern(values, threshold, side, milestone=False):
    choices=[]
    for size in (4,5,8,10):
        if len(values)<size or any(v is None for v in values[:size]):
            continue
        sample=values[:size]
        hits=sum(v>=threshold if milestone else v>threshold if side in ('MORE','OVER') else v<threshold for v in sample)
        if hits>=4 and hits/size>=.8:
            choices.append(dict(hits=hits,total=size,values=sample))
    return max(choices,key=lambda p:(p['hits']/p['total'],p['total']),default=None)


def heat_data(c, filters=None, clock=None, *, all_cards=False):
    clock=clock or datetime.now(timezone.utc)
    filters=filters or {}
    day=clock.astimezone(ZONE).date().isoformat()
    games={g['id']:g for g in current_games(c,clock) if g['status'] in ('PRE','LIVE')}
    current={}
    for row in c.execute('SELECT * FROM multi_sport_players WHERE substr(game_date,1,10)=?',(day,)):
        p=dict(row);game=games.get(p['game_id'])
        if not game or game['sport']!=p['sport'] or p['team'] not in (game['home'],game['away']) or not str(p['player_id']).isdigit():
            continue
        current[(p['sport'],str(p['player_id']))]=dict(player_id=str(p['player_id']),player=p['player_name'],team=p['team'],sport=p['sport'],game_id=p['game_id'])
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='player_game_stats'").fetchone():
        return dict(cards=[],total=0,reason='Dated recent performance is not available.')
    cols={r['name'] for r in c.execute('PRAGMA table_info(multi_sport_players)')}
    if 'slate_date' not in cols:
        return dict(cards=[],total=0,reason='Dated recent performance is not available.')
    history={}
    # No ordering by game IDs or ingestion timestamps. ESPN slate dates establish chronology.
    query='''SELECT h.*,g.sport,m.game_date,m.slate_date FROM player_game_stats h
        JOIN games g ON g.id=h.game_id
        JOIN multi_sport_players m ON m.game_id=h.game_id AND m.player_id=h.player_id AND m.sport=g.sport
        JOIN (SELECT DISTINCT player_id,sport FROM multi_sport_players
              WHERE substr(game_date,1,10)=?) p ON p.player_id=h.player_id AND p.sport=g.sport
        WHERE lower(g.status) IN ('post','final','completed','ended') AND h.segment='FULL GAME'
        AND m.slate_date IS NOT NULL AND m.slate_date=substr(m.game_date,1,10)
        AND m.slate_date<? AND h.team IN (g.home_team,g.away_team)'''
    for row in c.execute(query,(day,day)):
        r=dict(row);key=(r['sport'],str(r['player_id']));p=current.get(key)
        if not p or r['player_name']!=p['player']:
            continue
        try:
            date=datetime.fromisoformat(r['slate_date']).date()
            payload=json.loads(r['stats_json'])
            if not isinstance(payload,dict):continue
        except (ValueError,TypeError):continue
        game=history.setdefault(key,{}).setdefault(r['game_id'],dict(date=date.isoformat(),payload={}))
        for category,stats in payload.items():
            if category in game['payload'] and game['payload'][category]!=stats:
                game['ambiguous']=True
            game['payload'][category]=stats
    quotes={}
    for proposal in scan_offers(c,clock)[0]:
        for leg in proposal['legs']:
            market=ALIASES.get(str(leg['market']).lower(),next((m for m in MARKETS if m.lower()==str(leg['market']).lower()),''))
            if market and leg.get('player_id'):
                quotes[(leg['sport'],str(leg['player_id']),market)]=dict(line=number(leg['line']),side=leg['side'],book=proposal['book'],
                    odds=proposal.get('odds') if len(proposal['legs'])==1 and proposal['ticket_type']=='STRAIGHT' else None)
    cards=[]
    for key,p in current.items():
        if filters.get('sport') and p['sport']!=filters['sport']:continue
        if filters.get('q') and filters['q'].casefold() not in p['player'].casefold():continue
        logs=list(history.get(key,{}).values())
        # Multiple records on an indistinguishable date cannot establish last-N order.
        if len({r['date'] for r in logs})!=len(logs):continue
        logs=sorted(logs,key=lambda r:r['date'],reverse=True)[:10]
        for market,(_,milestones) in MARKETS.items():
            values=[None if r.get('ambiguous') else stat_value(r['payload'],market,p['sport']) for r in logs]
            quote=quotes.get((*key,market))
            if quote and quote['side'] not in ('MORE','OVER','LESS','UNDER'):quote=None
            chosen=None;threshold=None;side='MORE'
            if quote and quote['line'] is not None and quote['side'] in ('MORE','OVER','LESS','UNDER'):
                threshold=quote['line'];side=quote['side'];chosen=pattern(values,threshold,side)
            else:
                for target in milestones:
                    candidate=pattern(values,target,'MORE',True)
                    if candidate and (chosen is None or (candidate['hits']/candidate['total'],candidate['total'],target)>(chosen['hits']/chosen['total'],chosen['total'],threshold)):
                        chosen=candidate;threshold=target
            if chosen:
                cards.append(dict(**p,id=p['sport']+':'+p['player_id']+':'+market,market=market,
                    current_line=quote['line'] if quote else None,book=quote['book'] if quote else '',odds=quote['odds'] if quote else None,
                    threshold=threshold,milestone=not bool(quote),side=side,**chosen,
                    dates=[r['date'] for r in logs[:chosen['total']]],history_basis='Most recent dated, recorded completed games. Historical frequency is not a forecast.'))
    cards.extend(team_patterns(c,games,day,filters))
    cards.sort(key=lambda r:(-r['hits']/r['total'],-r['total'],r['player'],r['market']))
    try:offset=max(0,int(filters.get('offset',0)))
    except (TypeError,ValueError):offset=0
    return dict(cards=cards if all_cards else cards[offset:offset+30],total=len(cards),offset=offset,
        reason='' if cards else 'No qualifying pattern in the available dated history.')


def team_patterns(c, games, day, filters):
    from math_lab import team_history
    source=dict(games=rows(c,'games'),states={s['game_id']:s for s in rows(c,'game_state')},by_sport=defaultdict(list))
    for p in rows(c,'multi_sport_players'):
        source['by_sport'][p['sport']].append(p)
    cards=[]
    for g in games.values():
        for team in (g['away'],g['home']):
            if filters.get('sport') and filters['sport']!=g['sport']: continue
            if filters.get('q') and filters['q'].casefold() not in team.casefold(): continue
            logs=team_history(c,g['sport'],team,day,source)
            streak=0
            for log in logs:
                if log['margin']<=0: break
                streak+=1
            if streak<4: continue
            cards.append(dict(id=f"team:{g['sport']}:{g['id']}:{team}",kind='team',player=team,player_id='',team=team,sport=g['sport'],game_id=g['id'],market='Recorded wins',current_line=None,odds=None,book='',threshold=None,milestone=False,side='',hits=streak,total=streak,values=[r['margin'] for r in logs[:streak]],dates=[r['date'] for r in logs[:streak]],history_basis='Consecutive wins in dated recorded completed games; values are scoring margins. Not a complete season record.'))
    return cards
