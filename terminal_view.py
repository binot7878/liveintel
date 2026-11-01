"""Read-only presentation context. Existing qualification and calculations are unchanged."""
from board_view import current_games, rows, ZONE, fresh, instant
from math_lab import history, team_history, history_source
from heat_view import heat_data, stat_value
from challenge_decisions import ticket_views


def project(c, view, filters, clock):
    day = clock.astimezone(ZONE).date().isoformat()
    if view == 'terminal-heat':
        return heat_data(c, filters, clock, all_cards=True)
    if view == 'terminal-player':
        people = rows(c, 'multi_sport_players', 'player_id=? AND sport=?',
                      (filters.get('player', ''), filters.get('sport', '')))
        person = next((p for p in people if p['game_id'] == filters.get('game')), None)
        return {'history': history(c, person, day) if person else []}
    games = current_games(c, clock)
    roster = []
    for game in games:
        raw = rows(c, 'games', 'id=? AND sport=?', (game['id'], game['sport']))[0]
        stamp = instant(raw.get('scheduled_at'))
        game['start_time'] = stamp.astimezone(ZONE).strftime('%-I:%M %p ET') if stamp else None
        state = rows(c, 'game_state', 'game_id=?', (game['id'],))
        state = state[0] if state else {}
        # Recorded observations never alter current status or enter betting calculations.
        game['recorded'] = {k: state.get(k) for k in (
            'away_score', 'home_score', 'period', 'clock', 'possession', 'down', 'distance', 'last_updated')}
        game['recorded']['stale'] = not fresh(state.get('last_updated'), clock)
        unique = {}
        for p in rows(c, 'multi_sport_players', 'game_id=? AND sport=?', (game['id'], game['sport'])):
            if not str(p['player_id']).isdigit() or p['team'] not in (game['away'], game['home']):
                continue
            if p.get('segment') != 'FULL GAME':
                continue
            unique[str(p['player_id'])] = {k:p.get(k) for k in (
                'player_id','player_name','sport','team','position','game_id','starter','minutes')}
        roster.extend(unique.values())
    source = history_source(c)
    team_logs = {}
    for game in games:
        for team in (game['away'], game['home']):
            team_logs[game['sport'] + ':' + team] = team_history(c, game['sport'], team, day, source)
    pinned = rows(c, 'owner_watchlist', 'is_pinned=1')
    watched = []
    for pin in sorted(pinned, key=lambda p:p.get('added_at') or '', reverse=True)[:5]:
        item = dict(pin)
        if str(pin['player_id']).startswith('team:'):
            item['history'] = team_history(c, pin['sport'], pin['team'], day, source)
        else:
            people = rows(c, 'multi_sport_players', 'player_id=? AND sport=?', (str(pin['player_id']),pin['sport']))
            person = next((p for p in people if p['player_name']==pin['player_name']), None)
            item['history'] = history(c, person, day, source) if person else []
        item['market'] = 'Team score' if str(pin['player_id']).startswith('team:') else 'Points' if pin['sport'] in ('NBA', 'WNBA') else 'Receiving Yards'
        if item['market'] == 'Receiving Yards':
            candidates = ['Receiving Yards', 'Passing Yards', 'Rushing Yards']
            item['market'] = max(candidates, key=lambda market: sum(stat_value(h['payload'], market, pin['sport']) is not None for h in item['history']))
        item['values'] = [h.get('score') if item['market']=='Team score' else stat_value(h['payload'], item['market'], pin['sport']) for h in item['history']]
        values = item['values']
        item['average'] = round(sum(values)/len(values), 1) if len(values)>=4 and all(v is not None for v in values) else None
        watched.append(item)
    return {'games':games,'roster':roster,'watch_history':watched,'team_history':team_logs,
            'ai_tickets':{ai:ticket_views(c,ai) for ai in ('BASKETBALL_AI','NFL_AI','CFB_AI')}}
