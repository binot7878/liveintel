(function () {
  'use strict';

  const state = { data: null, config: null, draft: [], revision: null, screen: window.__STITCH_SCREEN__ || '' };
  const routes = { live: '/live', players: '/players', 'heats-on': '/heat', heat: '/heat', 'math-lab': '/math', math: '/math', 'watch-5': '/watch', watch: '/watch', picks: '/picks', tickets: '/tickets', intelligence: '/intelligence' };
  const externalAction = /TRANSMIT|PLACE\s*\/\s*SAVE|EXECUTE AUTOMATED|CASH OUT|SYNC TO BOOKMAKER|ROUTE TO (?:PINNACLE|CIRCA|DRAFTKINGS|FANDUEL)|HEDGE LOCK/i;

  function all(selector, root) { return Array.from((root || document).querySelectorAll(selector)); }
  function one(selector, root) { return (root || document).querySelector(selector); }
  function money(value) { const number = Number(value); return Number.isFinite(number) ? number.toLocaleString('en-US', { style: 'currency', currency: 'USD' }) : '—'; }
  function number(value, digits) { const found = Number(value); return Number.isFinite(found) ? found.toFixed(digits == null ? 1 : digits) : '—'; }
  function dateLabel(value) { const date = value ? new Date(value + 'T12:00:00') : null; return date && !Number.isNaN(+date) ? date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'America/New_York' }).toUpperCase() : '—'; }
  function leafElements(root) { return all('*', root).filter(element => element.children.length === 0 && element.textContent.trim()); }
  function findLeaf(pattern, root) { return leafElements(root || document).find(element => pattern.test(element.textContent.trim())); }
  function text(element, value) { if (element) { element.textContent = value == null || value === '' ? '—' : String(value); element.classList.add('li-bound'); } return element; }
  function fillLeaves(root, values) {
    const leaves = leafElements(root);
    values.forEach((value, index) => text(leaves[index], value));
    for (let index = values.length; index < leaves.length; index += 1) text(leaves[index], '');
  }
  function pending(element, label) {
    if (!element) return;
    element.classList.add('li-source-pending');
    element.dataset.pendingLabel = label || 'DATA SOURCE PENDING';
  }
  function unpending(element) { if (element) { element.classList.remove('li-source-pending'); delete element.dataset.pendingLabel; } }
  function notify(message, error) {
    let toast = one('#liveintel-runtime-toast');
    if (!toast) { toast = document.createElement('div'); toast.id = 'liveintel-runtime-toast'; document.body.appendChild(toast); }
    toast.textContent = message; toast.classList.toggle('error', Boolean(error)); toast.classList.add('open');
    clearTimeout(notify.timer); notify.timer = setTimeout(() => toast.classList.remove('open'), 3600);
  }
  async function api(path, options) {
    const response = await fetch(path, options);
    let body = {};
    try { body = await response.json(); } catch (_) { /* fail below */ }
    if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
    return body;
  }
  function post(path, payload) {
    return api(path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-LiveIntel-Development': '1' }, body: JSON.stringify(payload) });
  }

  async function calculate(kind, payload) {
    try {
      const result = await post('/api/stitch/calculate', { kind, ...payload });
      if (result.fail_closed) {
        notify(`Calculation ${kind} failed closed: missing required inputs`, true);
      }
      return result;
    } catch (error) {
      notify(`Calculation ${kind} failed: ${error.message}`, true);
      return { kind, result: null, available: false, fail_closed: true };
    }
  }

  function addEnvironmentNotice() {
    const notice = document.createElement('div'); notice.id = 'liveintel-development-state';
    notice.textContent = 'DEVELOPMENT STITCH • REAL LIVEINTEL READS • WRITES ISOLATED • NO WAGER EXECUTION';
    document.body.appendChild(notice);
  }

  function bindNavigation() {
    all('nav a[data-path]').forEach(link => {
      const path = routes[link.dataset.path] || routes[link.textContent.trim().toLowerCase()];
      if (path) link.href = path;
    });
    const tickets = one('#navTicketsBtn'); if (tickets) tickets.href = '/tickets';
    const ledger = one('#viewInLedgerActionBtn'); if (ledger) ledger.href = '/tickets';
  }

  function bindCommon(data) {
    const header = one('header'); if (!header) return;
    const global = data.global || {};
    const stateText = global.health && global.health.state === 'LIVE' ? 'FEED LIVE' : 'FEED DEGRADED';
    leafElements(header).forEach(element => {
      const value = element.textContent.trim();
      if (/^LIVE SLATE\s*[•·]/i.test(value)) text(element, `LIVE SLATE • ${dateLabel(global.slate_date)}`);
      else if (/^FEED (?:LIVE|SYNC|ONLINE)/i.test(value)) text(element, stateText);
      else if (/^\d+ms$/i.test(value)) text(element, global.health && global.health.state === 'LIVE' ? 'DB CURRENT' : 'STALE CHECK');
      else if (/^SLIP\s*\(\d+\)/i.test(value)) text(element, `SLIP (${state.draft.length})`);
    });
    const roll = findLeaf(/^ROLL$/i, header); if (roll && roll.parentElement) {
      const values = leafElements(roll.parentElement).filter(element => element !== roll);
      if (values[0]) text(values[0], money(global.bankroll));
    }
    const pl = findLeaf(/^P\/L$/i, header); if (pl && pl.parentElement) {
      const values = leafElements(pl.parentElement).filter(element => element !== pl);
      if (values[0]) text(values[0], money(global.pl));
    }
  }

  function gameSituation(game) {
    const recorded = game.recorded || {};
    const parts = [];
    if (game.possession || recorded.possession) parts.push(`POSS ${game.possession || recorded.possession}`);
    if (game.down || recorded.down) parts.push(`${game.down || recorded.down}${game.distance || recorded.distance ? ` & ${game.distance || recorded.distance}` : ''}`);
    if (game.red_zone) parts.push('RED ZONE');
    return parts.join(' • ') || 'SITUATION NOT SUPPLIED';
  }

  function bindLive(data) {
    const games = data.games || [];

    const counts = games.reduce((acc, game) => {
      const sport = String(game.sport || '').toLowerCase();
      acc.all += 1;
      if (sport) acc[sport] = (acc[sport] || 0) + 1;
      return acc;
    }, { all: 0 });

    all('[data-sport-filter]').forEach(button => {
      const key = String(button.dataset.sportFilter || '').toLowerCase();
      const badge = button.querySelector('span:last-child');
      if (badge) text(badge, String(counts[key] || 0));
    });

    leafElements(document.querySelector('main')).forEach(element => {
      const value = element.textContent.trim();

      if (/^BOOKMAKERS:/i.test(value)) {
        text(element, 'MARKET DATA: SOURCE PENDING');
      } else if (/^LATENCY:/i.test(value)) {
        const live = data.global &&
                     data.global.health &&
                     data.global.health.state === 'LIVE';
        text(element, live ? 'INGEST: CURRENT' : 'INGEST: DEGRADED');
      }
    });

    const cards = all('.matchup-card');
    cards.forEach((card, index) => {
      const game = data.games[index];
      if (!game) { pending(card, 'NO ADDITIONAL CURRENT LIVEINTEL GAME'); return; }
      unpending(card); card.dataset.gameId = game.id; card.dataset.sport = game.sport;
      const blocks = Array.from(card.children);
      const headLeaves = blocks[0] ? leafElements(blocks[0]) : [];
      text(headLeaves[0], game.sport); text(headLeaves[1], game.slate_date || 'CURRENT SLATE');
      text(headLeaves[headLeaves.length - 1], `${game.period_label || game.status || '—'}${game.clock ? ` ${game.clock}` : ''}`);
      const scoreRows = blocks[1] ? Array.from(blocks[1].children).filter(element => element.classList.contains('flex')) : [];
      const recorded = game.recorded || {};
      const teams = [[game.away, game.away_score ?? recorded.away_score], [game.home, game.home_score ?? recorded.home_score]];
      scoreRows.slice(0, 2).forEach((row, rowIndex) => {
        const leaves = leafElements(row); const team = teams[rowIndex];
        if (leaves.length >= 3) { text(leaves[0], team[0]); text(leaves[1], team[0]); text(leaves[leaves.length - 1], team[1] == null ? '—' : team[1]); }
      });
      const metrics = blocks[2] ? Array.from(blocks[2].children) : [];
      metrics.slice(0, 2).forEach(metric => {
        pending(metric, 'LIVE MARKET • DATA SOURCE PENDING');
        const leaves = leafElements(metric);
        if (leaves[0]) text(leaves[0], 'LIVE MARKET');
        if (leaves[1]) text(leaves[1], 'DATA SOURCE PENDING');
      });
      if (metrics[2]) {
        unpending(metrics[2]);
        const leaves = leafElements(metrics[2]);
        text(leaves[0], 'GAME STATE');
        text(leaves[1], gameSituation(game));
      }
      const footer = blocks[blocks.length - 1];
      if (footer) {
        const detail = leafElements(footer).find(
          element => !element.closest('button') &&
                     !element.classList.contains('material-symbols-outlined')
        );
        text(detail, game.status === 'FINAL' ? 'FINAL • RECORDED' : game.status === 'LIVE' ? 'LIVEINTEL STATE CURRENT' : `START ${game.start_time || 'TIME NOT SUPPLIED'}`);
        const button = one('button', footer);
        if (button) {
          button.dataset.liAction = 'gamecast';
          button.dataset.url = game.gamecast || '';
          const buttonLeaves = leafElements(button).filter(
            element => !/arrow_forward|open_in_new/i.test(element.textContent)
          );
          const buttonLabel = buttonLeaves.find(
            element => !/^[•·]$/.test(element.textContent.trim())
          );
          if (buttonLabel) {
            text(buttonLabel, game.status === 'FINAL' ? 'RECAP & LOG' : 'GAMECAST');
          }
        }
      }
    });
    const radar = Array.from(document.querySelectorAll('main div')).find(element => /QUANT LIVE EDGE RADAR/i.test(element.textContent) && element.children.length < 8);
    if (radar) pending(radar, data.markets && data.markets.length ? 'EDGE REQUIRES VERIFIED PRICE • RECORDED LINES HAVE NO ODDS' : 'EDGE / ARB DATA SOURCE PENDING');
    const timer = one('#syncTimer'); if (timer) text(timer, '15s');
  }

  function playerMarket(player) {
    const priority = ['points', 'rebounds', 'assists', 'pass_yards', 'rush_yards', 'receiving_yards', 'receptions', 'three_pm', 'fga', 'minutes'];
    return priority.find(key => player.windows && player.windows[key] && player.windows[key].values.length) || Object.keys(player.windows || {})[0] || '';
  }
  function titleStat(key) { return key ? key.replaceAll('_', ' ').replace(/\b\w/g, char => char.toUpperCase()) : 'HISTORY NOT AVAILABLE'; }

  function bindPlayers(data) {
    const players = data.players || [];
    const detailGrid = Array.from(document.querySelectorAll('main div')).find(element => /xl:grid-cols-12/.test(element.className || '') && !element.querySelector('table'));
    if (detailGrid) Array.from(detailGrid.children).forEach((panel, index) => pending(panel, index === 0 ? 'SELECTED PLAYER DETAIL • CHOOSE A REAL MATRIX ROW' : 'MATCHUP ROTATION DETAIL • REAL ROW SELECTION REQUIRED'));
    const rows = all('tbody tr');
    rows.forEach((row, index) => {
      const player = players[index];
      if (!player) { pending(row, 'NO ADDITIONAL CURRENT PLAYER'); return; }
      unpending(row); row.dataset.playerId = player.player_id; row.dataset.sport = player.sport; row.dataset.team = player.team;
      const cells = Array.from(row.children); const market = playerMarket(player); const window = player.windows[market] || {};
      if (cells[0]) fillLeaves(cells[0], [player.name, `${player.team}${player.game ? ` • ${player.game}` : ''}`]);
      if (cells[1]) fillLeaves(cells[1], [player.position || '—']);
      if (cells[2]) fillLeaves(cells[2], [player.role]);
      if (cells[3]) fillLeaves(cells[3], [player.minutes == null ? '—' : number(player.minutes, 1)]);
      if (cells[4]) fillLeaves(cells[4], ['NOT DERIVABLE']);
      if (cells[5]) fillLeaves(cells[5], [titleStat(market)]);
      if (cells[6]) fillLeaves(cells[6], ['SOURCE PENDING']);
      if (cells[7]) fillLeaves(cells[7], [window.l5 == null ? '—' : number(window.l5, 1)]);
      if (cells[8]) fillLeaves(cells[8], [window.l10 == null ? '—' : number(window.l10, 1), 'LINE REQUIRED']);
      if (cells[9]) fillLeaves(cells[9], ['PRICE REQUIRED']);
      const buttons = cells[10] ? all('button', cells[10]) : [];
      buttons.forEach((button, buttonIndex) => {
        button.dataset.playerId = player.player_id; button.dataset.playerName = player.name; button.dataset.team = player.team; button.dataset.sport = player.sport;
        button.dataset.liAction = buttonIndex === 0 ? 'watch-add' : 'ticket-add';
        if (buttonIndex > 0) { button.dataset.market = titleStat(market); button.dataset.gameId = player.game_id; }
      });
    });
    const applyPlayerFilters = () => {
      const query = (one('#player-filter-input')?.value || '').trim().toLowerCase();
      const activeRole = document.querySelector('[data-player-role-filter].li-active-role')?.dataset.playerRoleFilter || 'all';

      rows.forEach(row => {
        const matchesText = !query || row.textContent.toLowerCase().includes(query);
        const role = (row.dataset.playerRole || '').toLowerCase();
        const matchesRole = activeRole === 'all' || role === activeRole;
        row.classList.toggle('li-hidden', !(matchesText && matchesRole));
      });
    };

    const roleButtons = Array.from(document.querySelectorAll('button')).filter(button =>
      /^(ALL|STARTERS|BENCH|ROTATION)$/i.test(button.textContent.trim())
    );

    roleButtons.forEach(button => {
      const label = button.textContent.trim().toLowerCase();
      button.dataset.playerRoleFilter =
        label === 'starters' ? 'starter' :
        label === 'bench' ? 'bench' :
        label === 'rotation' ? 'rotation' : 'all';

      button.addEventListener('click', () => {
        roleButtons.forEach(item => item.classList.remove('li-active-role'));
        button.classList.add('li-active-role');
        applyPlayerFilters();
      });
    });

    const allRoleButton = roleButtons.find(button => button.dataset.playerRoleFilter === 'all');
    if (allRoleButton) allRoleButton.classList.add('li-active-role');

    rows.forEach((row, index) => {
      const player = players[index];
      if (player) row.dataset.playerRole = String(player.role || '').toLowerCase();
    });

    const input = one('#player-filter-input');
    if (input) input.addEventListener('input', applyPlayerFilters);

    const activeClaim = leafElements(document.querySelector('main')).find(
      element => /LIVE SLATE:\s*\d+\s+ACTIVE PROPS IDENTIFIED/i.test(element.textContent)
    );
    if (activeClaim) text(activeClaim, `LIVE SLATE: ${players.length} CURRENT PLAYERS • PROP SOURCE PENDING`);
  }

  function bindHeat(data) {
    const heat = data.heat || { cards: [] }; const cards = all('#players-grid > div');
    cards.forEach((cardElement, index) => {
      const card = heat.cards[index];
      if (!card) { pending(cardElement, 'NO ADDITIONAL QUALIFYING PATTERN'); return; }
      unpending(cardElement); cardElement.dataset.playerId = card.player_id; cardElement.dataset.sport = card.sport; cardElement.dataset.team = card.team;
      const header = cardElement.children[0];
      const name = header && Array.from(header.querySelectorAll('span')).find(element => /font-headline-sm/.test(element.className));
      text(name, card.player); const headerLeaves = header ? leafElements(header) : [];
      text(headerLeaves[0], (card.player || '?').split(/\s+/).map(part => part[0]).join('').slice(0, 2).toUpperCase());
      const teamRole = headerLeaves.find(element => /•/.test(element.textContent)); text(teamRole, `${card.team} • ${card.sport}`);
      const matchup = headerLeaves.find(element => /(?:vs|@)\s/i.test(element.textContent)); text(matchup, card.game_id ? `GAME ${card.game_id}` : 'RECORDED HISTORY');
      const marketValue = headerLeaves[headerLeaves.length - 1]; text(marketValue, card.market);
      const content = cardElement.children[1]; const labels = content ? leafElements(content) : [];
      const currentLabel = labels.findIndex(element => /^CURRENT LINE$/i.test(element.textContent));
      if (currentLabel >= 0) text(labels[currentLabel + 1], card.current_line == null ? 'NOT RECORDED' : `${card.side} ${card.current_line}`);
      const consensusLabel = labels.findIndex(element => /^CONSENSUS$/i.test(element.textContent));
      if (consensusLabel >= 0) text(labels[consensusLabel + 1], 'SOURCE PENDING');
      const probabilityLabel = labels.findIndex(element => /REC HIT PROB/i.test(element.textContent));
      if (probabilityLabel >= 0) text(labels[probabilityLabel + 1], card.total ? `${number(100 * card.hits / card.total, 1)}% HIST` : '—');
      const streak = labels.find(element => /^\d+\s*\/\s*\d+/i.test(element.textContent)); text(streak, `${card.hits} / ${card.total} RECORDED`);
      const average = labels.find(element => /^AVG:/i.test(element.textContent)); text(average, `AVG: ${number((card.values || []).reduce((a, b) => a + Number(b || 0), 0) / Math.max((card.values || []).length, 1), 1)} ${card.market}`);
      const logGrid = content && Array.from(content.querySelectorAll('div')).find(element => /grid-cols-(?:8|10)/.test(element.className));
      if (logGrid) Array.from(logGrid.children).forEach((box, valueIndex) => { const leaves = leafElements(box); text(leaves[0], `G${valueIndex + 1}`); text(leaves[1], card.values[valueIndex] == null ? '—' : card.values[valueIndex]); });
      Array.from(content ? content.querySelectorAll('div') : []).filter(element => /TOTAL O\/U CORR|STEAM DETECTED|EDGE/.test(element.textContent)).forEach(element => pending(element, 'MARKET CORRELATION • VERIFIED MARKET SOURCE PENDING'));
      const buttons = all('button', cardElement.children[2]);
      buttons.forEach((button, buttonIndex) => {
        button.dataset.playerId = card.player_id; button.dataset.playerName = card.player; button.dataset.team = card.team; button.dataset.sport = card.sport;
        button.dataset.liAction = buttonIndex === 0 ? 'watch-add' : 'ticket-add'; button.dataset.market = card.market; button.dataset.line = card.current_line == null ? '' : card.current_line;
        button.dataset.side = card.side; button.dataset.odds = card.odds == null ? '' : card.odds; button.dataset.book = card.book || ''; button.dataset.gameId = card.game_id || '';
      });
    });
    text(one('#player-active-count-badge'), `SHOWING ${Math.min(cards.length, heat.cards.length)} OF ${heat.total} QUALIFYING PATTERNS`);
    pending(one('#total-ou-correlation-bar'), 'MARKET CORRELATION • MULTI-BOOK SOURCE PENDING');
    pending(one('#sgp-correlation-engine'), 'SGP MARKET / PRICE / ALIGNED CORRELATION INPUTS PENDING');
    const drawer = one('#sgp-betslip-drawer'); if (drawer) drawer.dataset.liveintelDraft = 'true';
  }

  function researchTeams(data) { return (data.research && data.research.cards || []).filter(card => card.kind === 'team').slice(0, 2); }
  function bindMath(data) {
    const teams = researchTeams(data); const main = one('main'); if (!main) return;
    const demoNames = [/ATLANTA DREAM/i, /GOLDEN STATE/i];
    teams.forEach((team, index) => {
      const name = findLeaf(demoNames[index], main); text(name, team.team);
      const parent = name && name.parentElement && name.parentElement.parentElement;
      if (parent) {
        const recent = findLeaf(/^RECENT FULL-GAME AVERAGE$/i, parent.parentElement || parent);
        if (recent && recent.parentElement) text(leafElements(recent.parentElement).filter(element => element !== recent)[0], team.average == null ? '—' : `${number(team.average, 1)} PPG (RECORDED)`);
      }
    });
    all('div', main).filter(element => element.children.length && /SPORTSBOOK LINE/.test(element.textContent) && element.textContent.length < 300).forEach(element => pending(element, 'SPORTSBOOK LINE / EDGE • DATA SOURCE PENDING'));
    const execution = findLeaf(/DIRECT LAB EXECUTION/i, main); if (execution) pending(execution.parentElement, 'EXECUTION REQUIRES VERIFIED MARKET PRICES');
    const matrix = one('#prop-correlation-matrix-container'); if (matrix) pending(matrix, 'ALIGNED TARGET HISTORY + REAL MARKET LINES REQUIRED');
    const projectionsAvailable = teams.length === 2 && teams.every(team => team.math && team.math.projection != null);
    if (!projectionsAvailable) {
      leafElements(main).filter(element => /TEAM PROJECTION/i.test(element.textContent)).forEach(element => {
        let panel = element.parentElement;
        while (panel && panel !== main && !/bg-surface-container-low/.test(panel.className || '')) panel = panel.parentElement;
        pending(panel, 'LIVE PROJECTION INPUTS NOT AVAILABLE • RECORDED HISTORY RETAINED');
      });
      const cadence = findLeaf(/INNING & QUARTER FLOW CADENCE/i, main); if (cadence) pending(cadence.closest('section') || cadence.parentElement && cadence.parentElement.parentElement, 'CURRENT SEGMENT PROJECTION INPUTS NOT AVAILABLE');
      const sensitivity = findLeaf(/SENSITIVITY SIM/i, main); if (sensitivity) pending(sensitivity.parentElement && sensitivity.parentElement.parentElement, 'MODEL PARAMETERS REQUIRE CURRENT PROJECTION INPUTS');
    }
    all('button', main).forEach(button => {
      if (/DIRECT LAB EXECUTION|EXECUTE OPTIMAL SLIP|DISPATCH TO BETSLIP/i.test(button.textContent)) {
        const odds = button.dataset.odds || (button.parentElement && button.parentElement.dataset.odds);
        const prob = button.dataset.probability || (button.parentElement && button.parentElement.dataset.probability);
        const overOdds = button.dataset.overOdds || (button.parentElement && button.parentElement.dataset.overOdds);
        const underOdds = button.dataset.underOdds || (button.parentElement && button.parentElement.dataset.underOdds);

        if (odds && prob) {
          button.dataset.liAction = 'calc-kelly';
        } else if (overOdds && underOdds) {
          button.dataset.liAction = 'calc-no-vig';
        } else if (odds) {
          button.dataset.liAction = 'calc-implied';
        } else {
          button.dataset.liAction = 'calculation-pending';
        }
      }
    });
  }

  function bindWatch(data) {
    const watch = data.watch || []; const ticketData = data.tickets || {};
    for (let index = 0; index < 5; index += 1) {
      const element = one(`#cardSlot0${index + 1}`) || all('main [class*="grid-cols-5"] > div')[index];
      if (!element) continue;
      const target = watch[index];
      if (!target) { pending(element, 'EMPTY WATCH SLOT'); continue; }
      unpending(element); element.draggable = true; element.dataset.watchIndex = index; element.dataset.playerId = target.player_id || target.id;
      const knownNames = [/D\.\s*WHITE|DERRICK WHITE/i, /MISSOURI/i, /L\.\s*BALL|LAMelo Ball/i, /C\.J\.\s*STROUD/i, /C\.\s*HOLMGREN/i];
      text(findLeaf(knownNames[index] || /$a/, element), target.player_name || target.name);
      leafElements(element).filter(leaf => /3-POINTERS MADE|TEAM TOTAL OVER|OVER 7\.5 ASSISTS|PASS YARDS UNDER|OVER 7\.5 REBOUNDS/i.test(leaf.textContent)).forEach(leaf => text(leaf, target.market || 'RECORDED HISTORY'));
      const values = target.values || []; const latest = values[0];
      text(one(`#card${index + 1}LinePrice`), 'LINE: DATA SOURCE PENDING');
      text(one(`#card${index + 1}ScoreVal`), latest == null ? '—' : latest);
      text(one(`#card${index + 1}ScoreTarget`), target.market || 'RECORDED HISTORY');
      text(one(`#card${index + 1}ScoreSub`), `${values.length} RECORDED GAMES`);
      text(one(`#card${index + 1}Rotation`), target.average == null ? 'HISTORICAL CONTEXT AVAILABLE' : `AVG ${number(target.average, 1)}`);
      text(one(`#card${index + 1}HitProb`), `HISTORY N=${values.length}`);
      const bar = one(`#card${index + 1}Bar`); if (bar) bar.style.width = values.length ? '100%' : '0%';
      pending(one(`#card${index + 1}SteamBox`), 'ODDS MOVEMENT SOURCE PENDING');
      all('button', element).forEach(button => {
        button.dataset.liAction = /ADD|ROUTE|TICKET/i.test(button.textContent) ? 'ticket-add' : button.dataset.liAction || '';
        Object.assign(button.dataset, { playerId: target.player_id || target.id || '', playerName: target.player_name || target.name || '', team: target.team || '', sport: target.sport || '', market: target.market || '', line: '', odds: '', book: '', side: 'OVER' });
      });
    }
    text(one('#totalEdgeMetric'), '—');
    text(one('#ribbonSteamCount'), 'MARKET FEED PENDING'); text(one('#pinLatency'), 'PENDING'); text(one('#circaLatency'), 'PENDING');
    const ticker = findLeaf(/LIVE LINE TICKER/i); if (ticker) pending(ticker.parentElement && ticker.parentElement.parentElement, 'LIVE MULTI-BOOK TICKER • DATA SOURCE PENDING');
    setLabelValue(document, /LIVE SLATE TRIGGERS/i, `${watch.filter(target => target.game_id).length} CURRENT`);
    setLabelValue(document, /TOTAL TARGET EDGE/i, 'PRICE REQUIRED');
    setLabelValue(document, /RAPID STEAM ALERTS|STEAM ALERTS/i, 'SOURCE PENDING');
    const ledger = findLeaf(/WATCH 5 RAPID DRIFT|RAPID EXECUTION ARBITRAGE|RAPID ARB LEDGER/i); if (ledger) {
      const section = ledger.closest('section') || ledger.parentElement && ledger.parentElement.parentElement;
      const table = section && one('table', section); if (table) pending(table.parentElement || table, 'MULTI-BOOK ODDS / MOVEMENT SOURCE PENDING');
    }
    const exposure = findLeaf(/^MONITORED EXPOSURE$/i); if (exposure && exposure.parentElement) {
      const value = leafElements(exposure.parentElement).find(element => element !== exposure && /^\$/.test(element.textContent)); text(value, money(ticketData.active_exposure));
    }
    wireWatchDrag();
  }

  function setLabelValue(container, labelPattern, value) {
    const label = findLeaf(labelPattern, container); if (!label) return;
    const parent = label.parentElement; const candidates = leafElements(parent).filter(element => element !== label);
    if (candidates[0]) text(candidates[0], value);
  }
  function closestCardFromHeading(element) {
    let node = element;
    while (node && node !== document.body) { if (/bg-surface-container-low/.test(node.className || '') && node.querySelectorAll('button').length) return node; node = node.parentElement; }
    return element && element.parentElement;
  }

  function bindPicks(data) {
    const bobbyHeading = findLeaf(/BOBBY|OWNER POSITION/i); const bobbyCard = closestCardFromHeading(bobbyHeading);
    if (bobbyCard) {
      setLabelValue(bobbyCard, /OWNER BANKROLL|CURRENT BANKROLL/i, money(data.bobby && data.bobby.bankroll));
      setLabelValue(bobbyCard, /TOTAL EXPOSURE|EXPOSURE/i, money(data.bobby && data.bobby.exposure));
      setLabelValue(bobbyCard, /OPEN TICKETS|OPEN SLIPS/i, `${data.bobby && data.bobby.open_count || 0} ACTIVE`);
      const active = Array.from(bobbyCard.querySelectorAll('div')).find(element => /ACTIVE TICKET/.test(element.textContent) && element.children.length < 8);
      if (active && !(data.bobby && data.bobby.tickets && data.bobby.tickets.length)) pending(active.parentElement || active, 'NO RECORDED BOBBY TICKET');
    }
    (data.specialists || []).forEach(specialist => {
      const heading = findLeaf(new RegExp(`^${specialist.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`, 'i'));
      const card = closestCardFromHeading(heading); if (!card) return;
      setLabelValue(card, /^BANKROLL$|CURRENT BANKROLL/i, money(specialist.bankroll));
      setLabelValue(card, /^RECORD$/i, `${specialist.wins || 0}-${specialist.losses || 0}`);
      setLabelValue(card, /^EXPOSURE$/i, money(specialist.exposure));
      const ticketArea = Array.from(card.querySelectorAll('div')).find(element => /AI TICKET SUBMISSION|PRIMARY DIRECT SIGNAL|NO QUALIFIED/.test(element.textContent) && element.children.length < 10);
      if (ticketArea && !specialist.pick) pending(ticketArea.parentElement || ticketArea, 'NO AUTHORITATIVE QUALIFIED AI TICKET');
    });
    const aggregate = findLeaf(/FOUR HORSEMEN RUNNER INDEX/i);
    if (aggregate && aggregate.parentElement) {
      const container = aggregate.parentElement.parentElement || aggregate.parentElement;
      const wins = (data.specialists || []).reduce((sum, item) => sum + Number(item.wins || 0), Number(data.bobby && data.bobby.wins || 0));
      const losses = (data.specialists || []).reduce((sum, item) => sum + Number(item.losses || 0), Number(data.bobby && data.bobby.losses || 0));
      const bankroll = (data.specialists || []).reduce((sum, item) => sum + Number(item.bankroll || 0), Number(data.bobby && data.bobby.bankroll || 0));
      leafElements(container).forEach(element => {
        if (/AGGREGATE RECORD:/i.test(element.textContent)) text(element, `AGGREGATE RECORD: ${wins}-${losses}`);
        else if (/COMBINED BANKROLL:/i.test(element.textContent)) text(element, `COMBINED BANKROLL: ${money(bankroll)}`);
        else if (/PACER:/i.test(element.textContent)) text(element, 'PACER: INSUFFICIENT SETTLED SAMPLE');
      });
    }
    const benchmark = one('#historicalWinRateSuite'); if (benchmark) pending(benchmark, 'SETTLED CHALLENGE SAMPLE INSUFFICIENT • CLV SOURCE PENDING');
    const correlation = findLeaf(/CROSS-SPECIALIST CORRELATION ALERT/i); if (correlation) pending(correlation.parentElement && correlation.parentElement.parentElement || correlation.parentElement, 'ALIGNED AUTHORITATIVE TICKET SAMPLE REQUIRED');
    const velocity = findLeaf(/CUMULATIVE BANKROLL VELOCITY/i); if (velocity) pending(velocity.parentElement && velocity.parentElement.parentElement, 'HISTORICAL CHALLENGE TRAJECTORY SAMPLE INSUFFICIENT');
    const hashes = findLeaf(/PROVABLE LEDGER HASHES/i); if (hashes) pending(hashes.parentElement && hashes.parentElement.parentElement, 'NO AUTHORITATIVE CHALLENGE TICKETS RECORDED');
    const sync = findLeaf(/SYNC PROTOCOL ACTIVE/i); if (sync) pending(sync.parentElement && sync.parentElement.parentElement, 'CHALLENGE STATUS BOUND • ORACLE / CRYPTOGRAPHIC CLAIMS NOT SUPPLIED');
  }

  function bindTickets(data) {
    const portfolio = data.tickets || {}; const main = one('main');
    setLabelValue(main, /^TOTAL BANKROLL$/i, money(data.bobby && data.bobby.bankroll));
    setLabelValue(main, /ACTIVE EXPOSURE/i, money(portfolio.active_exposure));
    setLabelValue(main, /SETTLED P\/L/i, money(portfolio.settled_pl));
    setLabelValue(main, /WIN RATE/i, 'INSUFFICIENT REAL SAMPLE');
    setLabelValue(main, /CLV BEAT RATE/i, 'SOURCE PENDING');
    const streamTitle = findLeaf(/ACTIVE TICKETS & LEDGER STREAM/i);
    if (streamTitle) {
      const section = streamTitle.closest('section') || streamTitle.parentElement && streamTitle.parentElement.parentElement;
      if (!portfolio.tickets || !portfolio.tickets.length) pending(section, 'NO RECORDED OWNER TICKETS IN AUTHORITATIVE VIEW');
    }
    if (!portfolio.tickets || !portfolio.tickets.length) {
      const kpi = findLeaf(/^TOTAL BANKROLL$/i); if (kpi) pending(kpi.closest('section') || kpi.parentElement && kpi.parentElement.parentElement, 'NO AUTHORITATIVE OWNER PORTFOLIO SAMPLE');
    }
    [/ACTIVE EXPOSURE BY LEAGUE/i, /BOOKMAKER ALLOCATION/i, /REAL-TIME HEDGE|KELLY HEDGE/i, /SGP CORRELATION EXPOSURE/i].forEach(pattern => {
      const heading = findLeaf(pattern); if (heading) pending(heading.parentElement && heading.parentElement.parentElement || heading.parentElement, /BOOKMAKER|HEDGE/.test(pattern.source) ? 'VERIFIED MARKET / ACCOUNT SOURCE PENDING' : 'ALIGNED TICKET SAMPLE REQUIRED');
    });
    all('button', main).filter(button => /EXPORT|TAX REPORT|AUDIT/i.test(button.textContent)).forEach(button => button.dataset.liAction = 'export');
  }

  function bindIntelligence(data) {
    const research = data.research && data.research.cards || [];
    const qualified = research.filter(card => card.qualified && card.current_line != null && card.odds != null);
    text(one('#intel-opp-count'), `${qualified.length} OPPORTUNIT${qualified.length === 1 ? 'Y' : 'IES'}`);
    const feed = one('#active-targets-feed'); const empty = one('#empty-state-container');
    if (!qualified.length) {
      if (feed) feed.classList.add('li-hidden');
      if (empty) { empty.classList.remove('hidden'); empty.classList.add('flex'); }
      const builder = findLeaf(/QUICK TICKET BUILDER/i); if (builder) pending(builder.parentElement && builder.parentElement.parentElement || builder.parentElement, 'NO QUALIFIED CURRENT TARGETS');
    }
    const search = one('#search-input');
    if (search && feed) search.addEventListener('input', () => all(':scope > div', feed).forEach(card => card.classList.toggle('li-hidden', !card.textContent.toLowerCase().includes(search.value.toLowerCase()))));
  }

  function bindScreen(data) {
    bindCommon(data);
    if (state.screen === 'live_today_scoreboard') bindLive(data);
    else if (state.screen === 'players_research_rotation_matrix') bindPlayers(data);
    else if (state.screen === 'heat_s_on_repeat_performers') bindHeat(data);
    else if (state.screen.startsWith('math_lab_')) bindMath(data);
    else if (state.screen.startsWith('watch_5_')) bindWatch(data);
    else if (state.screen === 'picks_positions_specialist_challenge') bindPicks(data);
    else if (state.screen.startsWith('tickets_')) bindTickets(data);
    else if (state.screen === 'intelligence_ticket_builder') bindIntelligence(data);
    updateDraftDisplay();
  }

  function oddsDecimal(value) {
    const price = Number(value); if (!Number.isFinite(price) || price === 0) return null;
    return price > 0 ? 1 + price / 100 : 1 + 100 / Math.abs(price);
  }
  function draftPricing() {
    if (!state.draft.length || state.draft.some(leg => oddsDecimal(leg.odds) == null)) return { decimal: null, payout: null, profit: null };
    const decimal = state.draft.reduce((product, leg) => product * oddsDecimal(leg.odds), 1); const stake = Number(one('#slipStakeInput') && one('#slipStakeInput').value || one('#ticket-stake') && one('#ticket-stake').value || 0);
    return { decimal, payout: stake > 0 ? Math.round(stake * decimal * 100) / 100 : null, profit: stake > 0 ? Math.round(stake * (decimal - 1) * 100) / 100 : null, stake };
  }
  function updateDraftDisplay() {
    const count = state.draft.length; const price = draftPricing();
    ['headerSlipCount', 'floatLegCount'].forEach(id => text(one(`#${id}`), count));
    text(one('#slipLegCountBadge'), `${count} ACTIVE LEG${count === 1 ? '' : 'S'}`); text(one('#parlayLegsCount'), count);
    text(one('#slipTotalOddsDisplay'), price.decimal == null ? 'PRICE REQUIRED' : `${number(price.decimal, 2)}x`);
    text(one('#slipPayoutDisplay'), price.payout == null ? '—' : money(price.payout)); text(one('#slipProfitDisplay'), price.profit == null ? '—' : money(price.profit));
    all('header').forEach(header => leafElements(header).filter(element => /^SLIP\s*\(\d+\)/i.test(element.textContent)).forEach(element => text(element, `SLIP (${count})`)));
    const container = one('#slipLegsContainer');
    if (container) {
      all('.li-runtime-leg', container).forEach(element => element.remove());
      all('.leg-item', container).forEach(element => element.classList.add('li-hidden'));
      if (!count) {
        const empty = document.createElement('div'); empty.className = 'li-runtime-leg p-3 bg-surface-container rounded text-outline font-mono text-xs'; empty.textContent = 'NO REAL LEGS ADDED'; container.appendChild(empty);
      } else state.draft.forEach((leg, index) => {
        const item = document.createElement('div'); item.className = 'li-runtime-leg p-3 bg-surface-container rounded border border-surface-container-high font-mono text-xs flex justify-between gap-2';
        const label = document.createElement('span'); label.textContent = `${leg.sport} • ${leg.player || leg.team} • ${leg.side} ${leg.line == null ? '' : leg.line} ${leg.market}`;
        const remove = document.createElement('button'); remove.textContent = '×'; remove.dataset.liAction = 'draft-remove'; remove.dataset.index = index; remove.className = 'text-error';
        item.append(label, remove); container.appendChild(item);
      });
    }
  }
  function addDraftFromButton(button) {
    const line = button.dataset.line === '' ? null : Number(button.dataset.line); const odds = button.dataset.odds === '' ? null : Number(button.dataset.odds);
    const leg = { sport: button.dataset.sport, game_id: button.dataset.gameId, player_id: button.dataset.playerId, player: button.dataset.playerName, team: button.dataset.team,
      market: button.dataset.market, line: Number.isFinite(line) ? line : null, side: button.dataset.side || 'OVER', odds: Number.isFinite(odds) ? odds : null, book: button.dataset.book || '' };
    if (!leg.sport || !(leg.player || leg.team) || !leg.market) return notify('A recorded target and market are required.', true);
    state.draft.push(leg); updateDraftDisplay(); notify(leg.odds == null ? 'Leg added to draft. A verified sportsbook price is still required before recording.' : 'Leg added to ticket draft.');
  }

  async function recordDraft() {
    if (!state.draft.length) return notify('The ticket draft is empty.', true);
    const price = draftPricing(); if (price.decimal == null || price.payout == null) return notify('Verified odds and a positive stake are required. Ticket failed closed.', true);
    const books = Array.from(new Set(state.draft.map(leg => leg.book).filter(Boolean))); if (!books.length) return notify('A recorded sportsbook is required.', true);
    try {
      const result = await post('/api/stitch/tickets/record', { owner_name: 'Bobby', book: books.join(' / '), stake: price.stake, potential_payout: price.payout, legs: state.draft });
      if (result.wager_placed !== false) throw new Error('Unsafe ticket response rejected: record-only confirmation missing');
      state.draft = []; updateDraftDisplay(); notify(`Ticket #${result.ticket_id} recorded in the isolated development ledger. No wager was placed.`);
    } catch (error) { notify(error.message, true); }
  }

  async function watchAction(button, remove) {
    const payload = { player_id: button.dataset.playerId, player_name: button.dataset.playerName, team: button.dataset.team, sport: button.dataset.sport, is_pinned: true };
    try { await post(remove ? '/api/stitch/watch/remove' : '/api/stitch/watch/add', payload); notify(remove ? 'Removed from isolated development Watch 5.' : 'Added to isolated development Watch 5.'); await refreshFull(); }
    catch (error) { notify(error.message, true); }
  }

  function wireWatchDrag() {
    const cards = all('[data-watch-index]'); let source = null;
    cards.forEach(card => {
      card.addEventListener('dragstart', () => { source = Number(card.dataset.watchIndex); });
      card.addEventListener('dragover', event => event.preventDefault());
      card.addEventListener('drop', async event => {
        event.preventDefault(); const destination = Number(card.dataset.watchIndex); if (source == null || source === destination) return;
        const ids = cards.map(item => item.dataset.playerId); const moved = ids.splice(source, 1)[0]; ids.splice(destination, 0, moved);
        try { await post('/api/stitch/watch/reorder', { player_ids: ids }); notify('Watch order saved in isolated development data.'); await refreshFull(); }
        catch (error) { notify(error.message, true); }
      });
    });
  }

  function exportVisible() {
    const rows = [['screen', 'text'], [state.screen, document.querySelector('main').innerText.replace(/\s+/g, ' ').trim()]];
    const blob = new Blob([rows.map(row => row.map(value => `"${String(value).replaceAll('"', '""')}"`).join(',')).join('\n')], { type: 'text/csv' });
    const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = `liveintel-${state.screen}.csv`; link.click(); URL.revokeObjectURL(link.href);
  }

  function bindActions() {
    document.addEventListener('click', event => {
      const button = event.target.closest('button,a'); if (!button) return;
      const label = button.textContent.replace(/\s+/g, ' ').trim();
      if (externalAction.test(label)) { event.preventDefault(); event.stopImmediatePropagation(); return notify('External sportsbook execution is not authorized. Component remains source-pending.', true); }
      const action = button.dataset.liAction;
      if (action === 'gamecast') { event.preventDefault(); event.stopImmediatePropagation(); if (button.dataset.url) window.open(button.dataset.url, '_blank', 'noopener'); }
      else if (action === 'watch-add') { event.preventDefault(); event.stopImmediatePropagation(); watchAction(button, false); }
      else if (action === 'watch-remove') { event.preventDefault(); event.stopImmediatePropagation(); watchAction(button, true); }
      else if (action === 'ticket-add') { event.preventDefault(); event.stopImmediatePropagation(); addDraftFromButton(button); }
      else if (action === 'draft-remove') { event.preventDefault(); event.stopImmediatePropagation(); state.draft.splice(Number(button.dataset.index), 1); updateDraftDisplay(); }
      else if (action === 'export') { event.preventDefault(); event.stopImmediatePropagation(); exportVisible(); }
      else if (action === 'calculation-pending') { event.preventDefault(); event.stopImmediatePropagation(); notify('Required market inputs are not connected. Calculation failed closed.', true); }
      else if (action === 'calc-kelly') {
        event.preventDefault(); event.stopImmediatePropagation();
        const prob = parseFloat(button.dataset.probability);
        const odds = parseFloat(button.dataset.odds);
        if (!Number.isFinite(prob) || prob <= 0 || prob >= 1 || !Number.isFinite(odds) || odds === 0) {
          notify('Kelly calculation failed closed: valid probability (0 < p < 1) and non-zero odds required.', true);
          return;
        }
        const decimalOdds = odds > 0 ? 1 + odds / 100 : 1 + 100 / Math.abs(odds);
        calculate('kelly', { probability: prob, decimal_odds: decimalOdds }).then(res => {
          if (res.available) notify(`Kelly sizing calculated: ${res.result.fraction} fraction`);
        });
      }
      else if (action === 'calc-no-vig') {
        event.preventDefault(); event.stopImmediatePropagation();
        const overOdds = parseFloat(button.dataset.overOdds);
        const underOdds = parseFloat(button.dataset.underOdds);
        if (!Number.isFinite(overOdds) || !Number.isFinite(underOdds) || overOdds === 0 || underOdds === 0) {
          notify('No-vig calculation failed closed: both over and under odds are required.', true);
          return;
        }
        calculate('no_vig_probability', { over_odds: overOdds, under_odds: underOdds }).then(res => {
          if (res.available) notify(`No-vig probabilities: Over ${res.result.over_fair}, Under ${res.result.under_fair}`);
        });
      }
      else if (action === 'calc-implied') {
        event.preventDefault(); event.stopImmediatePropagation();
        const odds = parseFloat(button.dataset.odds);
        if (!Number.isFinite(odds) || odds === 0) {
          notify('Implied probability calculation failed closed: valid non-zero odds required.', true);
          return;
        }
        calculate('implied_probability', { odds }).then(res => {
          if (res.available) notify(`Implied probability: ${res.result.implied_probability}`);
        });
      }
      else if (button.id === 'stageTicketBtn' || button.id === 'transmitTicketBtn' || /RECORD TICKET/i.test(label)) { event.preventDefault(); event.stopImmediatePropagation(); recordDraft(); }
    }, true);
    document.addEventListener('paste', event => {
      if (event.target.matches('input,textarea,[contenteditable=true]')) return;
      const value = event.clipboardData && event.clipboardData.getData('text'); if (!value) return;
      const parsed = [];
      value.split(/\r?\n/).forEach(line => {
        const match = line.trim().match(/^(.+?)\s+(over|under|more|less)\s+(-?\d+(?:\.\d+)?)\s+(.+?)(?:\s+([+-]\d+))?$/i);
        if (match) parsed.push({ sport: 'UNSPECIFIED', player: match[1], team: '', game_id: '', player_id: '', side: match[2].toUpperCase(), line: Number(match[3]), market: match[4], odds: match[5] ? Number(match[5]) : null, book: '' });
      });
      if (parsed.length) { event.preventDefault(); state.draft.push(...parsed); updateDraftDisplay(); notify(`${parsed.length} pasted leg${parsed.length === 1 ? '' : 's'} parsed. Sport, game, and book must be completed before recording.`); }
    });
    const stake = one('#slipStakeInput') || one('#ticket-stake'); if (stake) stake.addEventListener('input', updateDraftDisplay);
  }

  async function refreshFull() {
    state.data = await api(`/api/stitch/bootstrap?screen=${encodeURIComponent(state.screen)}`); bindScreen(state.data);
  }
  async function poll() {
    try {
      const data = await api('/api/stitch/updates'); if (data.revision !== state.revision) { state.revision = data.revision; state.data = Object.assign({}, state.data, data); bindScreen(state.data); }
    } catch (_) { const notice = one('#liveintel-development-state'); if (notice) notice.textContent = 'DEVELOPMENT STITCH • LIVE UPDATE DEGRADED • LAST DATA RETAINED'; }
  }

  async function init() {
    addEnvironmentNotice(); bindNavigation(); bindActions();
    try {
      [state.config, state.data] = await Promise.all([api('/api/stitch/config'), api(`/api/stitch/bootstrap?screen=${encodeURIComponent(state.screen)}`)]);
      bindScreen(state.data); state.revision = state.data.generated_at;
      setInterval(poll, 15000);
    } catch (error) { notify(`LiveIntel data binding failed safely: ${error.message}`, true); }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true }); else init();
}());
