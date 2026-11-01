"""Read-only adapters for Math Lab and Intelligence; existing ledgers remain authoritative."""
import json
from collections import defaultdict
from datetime import datetime, timezone
from board_view import current_games, rows, number, fresh, instant, ZONE
from sport_math import live_math, role_signals, segment_scores, SPORTS
from heat_view import heat_data, MARKETS, stat_value, ALIASES
from verified_offers import scan_offers

AI_SPORTS={'BASKETBALL_AI':{'NBA','WNBA','NCAAB'},'NFL_AI':{'NFL'},'CFB_AI':{'CFB'}}


def history(c, player, day, source=None):
    cols=source['cols'] if source else {r['name'] for r in c.execute('PRAGMA table_info(multi_sport_players)')}
    if 'slate_date' not in cols: return []
    logs={}
    games=source['completed_games'][player['sport']] if source and 'completed_games' in source else {g['id']:g for g in (source['games'] if source else rows(c,'games')) if g['sport']==player['sport'] and str(g['status']).lower() in ('final','post','completed','ended')}
    identities={p['game_id']:p for p in (source['players'][(str(player['player_id']),player['sport'])] if source else rows(c,'multi_sport_players','player_id=? AND sport=?',(player['player_id'],player['sport'])))
                if p.get('slate_date') and p['slate_date']==str(p['game_date'])[:10] and p['slate_date']<day and p['player_name']==player['player_name']}
    for r in (source['history'][str(player['player_id'])] if source else rows(c,'player_game_stats','player_id=? AND segment=?',(player['player_id'],'FULL GAME'))):
        g=games.get(r['game_id']); ident=identities.get(r['game_id'])
        if not g or not ident or r['team'] not in (g['home_team'],g['away_team']) or r['player_name']!=player['player_name']: continue
        try: payload=json.loads(r['stats_json'])
        except (ValueError,TypeError): continue
        if not isinstance(payload,dict): continue
        item=logs.setdefault(r['game_id'],dict(date=ident['slate_date'],payload={}))
        for key,value in payload.items():
            if key in item['payload'] and item['payload'][key]!=value: item['ambiguous']=True
            item['payload'][key]=value
    result=sorted(logs.values(),key=lambda h:h['date'],reverse=True)[:10]
    if len({h['date'] for h in result})!=len(result): return []
    return result


def observations(c,g):
    output=[]
    for p in rows(c,'plays','game_id=?',(g['id'],)):
        try: score=json.loads(p.get('score_after') or '{}')
        except (ValueError,TypeError): continue
        if isinstance(score,dict): output.append(dict(period=p.get('period'),clock=p.get('clock'),home=score.get('home'),away=score.get('away')))
    return output


def history_source(c):
    """Read each history table once for a request, preserving history's filters."""
    source=dict(cols={r['name'] for r in c.execute('PRAGMA table_info(multi_sport_players)')},games=rows(c,'games'),players=defaultdict(list),history=defaultdict(list),by_sport=defaultdict(list),states={s['game_id']:s for s in rows(c,'game_state')})
    for p in rows(c,'multi_sport_players'):
        source['players'][(str(p['player_id']),p['sport'])].append(p);source['by_sport'][p['sport']].append(p)
    for h in rows(c,'player_game_stats'):
        if h['segment']=='FULL GAME': source['history'][str(h['player_id'])].append(h)
    source['completed_games']=defaultdict(dict)
    for g in source['games']:
        if str(g['status']).lower() in ('final','post','completed','ended'):
            source['completed_games'][g['sport']][g['id']]=g
    return source


def research(c, clock=None):
    clock=clock or datetime.now(timezone.utc); day=clock.astimezone(ZONE).date().isoformat()
    source=history_source(c)
    games=current_games(c,clock); lookup={g['id']:g for g in games}
    heat=heat_data(c,clock=clock,all_cards=True)['cards']
    cards=[]; math_by_game={}; history_by_player={}
    for g in games:
        obs=observations(c,g)
        math=live_math(g,obs); math['segments']=segment_scores(g['sport'],obs)
        math_by_game[g['id']]=math
        for side in ('away','home'):
            team=g[side]
            recorded=team_history(c,g['sport'],team,day,source)
            streak=0
            for h in recorded:
                if h['margin']<=0: break
                streak+=1
            team_heat=dict(hits=streak,total=streak,values=[h['score'] for h in recorded[:streak]],threshold=None,milestone=False) if streak>=4 else None
            cards.append(dict(id=f"team:{g['sport']}:{g['id']}:{team}",kind='team',player=team,team=team,player_id='',sport=g['sport'],game_id=g['id'],market='Team score',
                              current_line=None,projection=(math['projection'][0 if side=='away' else 1] if math['projection'] else None),edge=None,side='',odds=None,
                              signals=math['signals']+(['W'+str(streak)+' RECORDED GAMES','HOT TEAM'] if team_heat else []),math=math,values=[h['score'] for h in recorded],heat=team_heat,role=None,average=round(sum(h['score'] for h in recorded)/len(recorded),1) if recorded else None,average_margin=round(sum(h['margin'] for h in recorded)/len(recorded),1) if recorded else None,ai_read=math['basis']+' Team history covers dated recorded games only; rank is unavailable.',game=g))
    for p in rows(c,'multi_sport_players','substr(game_date,1,10)=?',(day,)):
        g=lookup.get(p['game_id'])
        if not g or g['sport']!=p['sport'] or p['team'] not in (g['home'],g['away']): continue
        key=(str(p['player_id']),p['sport'],p['player_name'])
        if key not in history_by_player:
            history_by_player[key]=history(c,p,day,source)
        logs=history_by_player[key]; live=p.get('live_stats_available') and g['status']=='LIVE' and fresh(p.get('last_updated'),clock)
        flat=[{} if h.get('ambiguous') else h['payload'].get('totals',h['payload'].get('',h['payload'])) for h in logs]
        normalized=[]
        for h in flat:
            normalized.append(dict(minutes=h.get('minutes',h.get('MIN')),usage=h.get('usage',h.get('USG%')),fga=h.get('fga',h.get('FGA')),targets=h.get('targets')))
        current_role=dict(p)
        if p.get('starter') in (0,1): current_role['starter']=bool(p['starter'])
        badges=role_signals(current_role,normalized) if live else []
        relevant={'NBA':'Points','WNBA':'Points','NCAAB':'Points','NFL':'Receiving Yards','CFB':'Receiving Yards','NHL':'Goals','MLB':'Hits'}.get(p['sport'],'Production')
        fields={'Points':'points','Rebounds':'rebounds','Assists':'assists','Receiving Yards':'receiving_yards','Rushing Yards':'rush_yards','Passing Yards':'pass_yards','Receptions':'receptions','Goals':'goals','Hits':'hits','Minutes':'minutes','Shot Attempts':'fga','3PA':'three_pa','Targets':'targets','Rush Attempts':'rush_attempts','Pass Attempts':'pass_attempts','Shots':'shots','Runs':'runs','RBIs':'rbis','Strikeouts':'strikeouts','Time on Ice':'time_on_ice'}
        for market in list(MARKETS) if relevant in MARKETS else [relevant]:
            values=[None if h.get('ambiguous') else stat_value(h['payload'],market,p['sport']) if market in MARKETS else None for h in logs]
            current=number(p.get(fields.get(market,''))) if live else None
            if not any(v is not None for v in values) and current is None and market!=relevant: continue
            avg=round(sum(values)/len(values),1) if len(values)>=4 and all(v is not None for v in values) else None
            current=number(p.get(fields.get(market,''))) if live else None
            player_math=math_by_game[g['id']]
            projected=round(current*player_math['horizon']/player_math['elapsed'],1) if current is not None and player_math['elapsed'] and player_math['elapsed']>=60 else avg
            signal=list(badges)
            if current is not None and avg is not None and current>avg: signal.append('ABOVE RECENT AVERAGE')
            pattern=next((h for h in heat if h['sport']==p['sport'] and h['player_id']==str(p['player_id']) and h['market']==market),None)
            cards.append(dict(id=f"player:{p['sport']}:{g['id']}:{p['player_id']}:{market}",kind='player',player=p['player_name'],player_id=str(p['player_id']),team=p['team'],sport=p['sport'],game_id=g['id'],market=market,
                              current_line=pattern['current_line'] if pattern else None,projection=projected,edge=None,side=pattern['side'] if pattern else '',odds=pattern['odds'] if pattern else None,
                              signals=signal,current=current,average=avg,math=player_math,values=values,dates=[h['date'] for h in logs],heat=pattern,role=p.get('starter'),game=g,
                              ai_read=('Current production extrapolated using elapsed game time, without assuming minutes or lineup changes. ' if current is not None and player_math['elapsed'] else '')+(f'Recent recorded full-game average across {len(values)} games. Descriptive baseline, not a calibrated forecast.' if avg is not None else 'Insufficient complete recent history.')))
    # Verified quotes enrich research; they do not create or submit challenge picks.
    for proposal in scan_offers(c,clock)[0]:
        for i,leg in enumerate(proposal['legs']):
            g=lookup.get(leg['game_id'])
            if not g or g['sport']!=leg['sport']: continue
            market=ALIASES.get(leg['market'].lower(),leg['market'])
            card=next((x for x in cards if x['game_id']==g['id'] and x['player_id']==str(leg.get('player_id') or '') and x['player']==leg['player'] and x['market'].lower()==market.lower()),None)
            if card is None:
                card=dict(id=f"offer:{proposal['offer_id']}:{i}",kind='player' if leg.get('player_id') else 'team',player=leg['player'],player_id=str(leg.get('player_id') or ''),team=leg.get('team',''),sport=g['sport'],game_id=g['id'],market=leg['market'],projection=None,signals=[],math=live_math(g,observations(c,g)),values=[],heat=None,role=None,game=g,ai_read='Verified current market. Supporting statistical history is unavailable.')
                cards.append(card)
            card.update(current_line=number(leg['line']),side=leg['side'],book=proposal['book'],odds=proposal.get('odds') if len(proposal['legs'])==1 and proposal['ticket_type']=='STRAIGHT' else None)
            card['edge']=round(card['projection']-card['current_line'],1) if card['projection'] is not None and card['current_line'] is not None else None
    for card in cards:
        if card.get('current') is not None and card.get('current_line') is not None and card['current_line']>0 and .9*card['current_line']<=card['current']<card['current_line']:
            card['signals'].append('PLAYER APPROACHING LINE')
        card['ai_ids']=[ai for ai,sports in AI_SPORTS.items() if card['sport'] in sports]
        card['qualified']=bool(card.get('heat') and not card['heat'].get('milestone') and card.get('edge') is not None and ((card.get('side') in ('OVER','MORE') and card['edge']>0) or (card.get('side') in ('UNDER','LESS') and card['edge']<0)))
    return dict(cards=cards,games=games,sports=sorted(set(SPORTS)|{g['sport'] for g in games}),ai_filters=list(AI_SPORTS),basis='Research signals only. Missing data is unavailable.')


def team_history(c, sport, team, day, source=None):
    """Recorded completed games, never an inferred season record or AP ranking."""
    cache=source.setdefault('team_dates',{}) if source else {}
    key=(sport,day)
    if key not in cache:
        dates={}
        for p in (source['by_sport'][sport] if source else rows(c,'multi_sport_players','sport=?',(sport,))):
            if p.get('slate_date') and p['slate_date']==str(p.get('game_date'))[:10] and p['slate_date']<day:
                dates.setdefault(p['game_id'],set()).add(p['slate_date'])
        cache[key]=dates
    dates=cache[key]
    states=source['states'] if source else {s['game_id']:s for s in rows(c,'game_state')}
    output=[]
    for g in (source['games'] if source else rows(c,'games','sport=?',(sport,))):
        if g['sport']!=sport: continue
        if str(g['status']).lower() not in ('final','post','completed','ended') or team not in (g['home_team'],g['away_team']): continue
        ds=dates.get(g['id'],set()); state=states.get(g['id'],{})
        updated,final_at=instant(state.get('last_updated')),instant(g.get('last_updated'))
        if not updated or not final_at or updated<final_at: continue
        h,a=number(state.get('home_score')),number(state.get('away_score'))
        if len(ds)!=1 or h is None or a is None: continue
        scored,allowed=(h,a) if team==g['home_team'] else (a,h)
        output.append(dict(date=next(iter(ds)),score=scored,margin=scored-allowed,venue='home' if team==g['home_team'] else 'away'))
    if len({h['date'] for h in output})!=len(output): return []
    return sorted(output,key=lambda h:h['date'],reverse=True)[:10]
