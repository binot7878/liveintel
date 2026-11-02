(function () {
  'use strict';

  const state = { data: null, config: null, draft: [], revision: null, screen: window.__STITCH_SCREEN__ || '' };
  const routes = { live: '/live', players: '/players', 'heats-on': '/heat', heat: '/heat', 'math-lab': '/math', math: '/math', 'watch-5': '/watch', watch: '/watch', picks: '/picks', tickets: '/tickets', intelligence: '/intelligence' };
  const externalAction = /TRANSMIT|PLACE\s*\/\s*SAVE|EXECUTE AUTOMATED|CASH OUT|SYNC TO BOOKMAKER|ROUTE TO (?:PINNACLE|CIRCA|DRAFTKINGS|FANDUEL)|HEDGE LOCK/i;

  function all(selector, root) { return Array.from((root || document).querySelectorAll(selector)); }
  function one(selector, root) { return (root || document).querySelector(selector); }
  function money(value) { const number = Number(value); return finiteInput(value) ? number.toLocaleString('en-US', { style: 'currency', currency: 'USD' }) : 'PENDING'; }
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
    if (!state.shell) {
      const leaves = leafElements(header);
      state.shell = {
        slate: leaves.filter(el => /^LIVE SLATE/i.test(el.textContent.trim())),
        feeds: leaves.filter(el => /^FEED(?:\s|:)/i.test(el.textContent.trim())),
        latency: leaves.filter(el => /^\d+ms$/i.test(el.textContent.trim())),
        roll: findLeaf(/^ROLL$/i, header)?.nextElementSibling,
        pl: findLeaf(/^P\/L$/i, header)?.nextElementSibling,
        footer: all('footer span').filter(el => /^(SOCKET:|LATENCY:|SYS HOLD:|INSTITUTIONAL ODDS ENGINE)/i.test(el.textContent.trim()))
      };
    }
    const global = data.global || {};
    const owner = data.bobby || {};
    const health = global.health?.state;
    state.shell.slate.forEach(el => text(el, `LIVE SLATE • ${global.slate_date ? dateLabel(global.slate_date) : 'PENDING'}`));
    state.shell.feeds.forEach(el => text(el, health === 'LIVE' ? 'FEED LIVE' : health === 'DEGRADED' ? 'FEED DEGRADED' : 'FEED PENDING'));
    state.shell.latency.forEach(el => text(el, health === 'LIVE' ? 'DB CURRENT' : 'LATENCY PENDING'));
    text(state.shell.roll, money(Object.hasOwn(owner, 'bankroll') ? owner.bankroll : global.bankroll));
    text(state.shell.pl, money(Object.hasOwn(owner, 'today_pl') ? owner.today_pl : global.pl));
    state.shell.footer.forEach(el => text(el, 'SOURCE PENDING'));
    leafElements(header).filter(el => /^SLIP\s*\(/i.test(el.textContent)).forEach(el => text(el, `SLIP (${state.draft.length})`));
  }

  function neutralizeData(root, label) {
    // A safe loading/empty copy retains the exported component tree and CSS.
    // No frozen values, inputs, prices, charts or actions are presented as data.
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = []; while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(node => {
      const parent = node.parentElement;
      if (!node.textContent.trim() || parent.closest('script,style,.material-symbols-outlined')) return;
      if (parent.closest('h1,h2') && !/\d|ORACLE|CRYPTOGRAPHIC|CONFIDENCE/i.test(node.textContent)) return;
      node.textContent = label || 'DATA SOURCE PENDING';
    });
    all('script', root).forEach(el => el.remove());
    const controls = [...(root.matches('button,input,select,textarea') ? [root] : []), ...all('button,input,select,textarea', root)];
    controls.forEach(el => {
      disableControl(el);
      if (el.matches('input,textarea')) { el.value = ''; el.removeAttribute('value'); el.checked = false; }
    });
    all('a', root).forEach(el => { el.removeAttribute('href'); el.setAttribute('aria-disabled', 'true'); });
    all('img,svg path,svg circle,svg polyline,svg polygon', root).forEach(el => { el.style.visibility = 'hidden'; });
    all('[style*="width:"]', root).forEach(el => { el.style.width = '0%'; });
    all('.items-end > *', root).forEach(el => { el.style.height = '0px'; });
    [root, ...all('*', root)].forEach(el => {
      ['playerId', 'playerIndex', 'gameId', 'team', 'sport', 'watchIndex', 'liAction', 'intelAction', 'recordedTicketId', 'targetId'].forEach(key => { delete el.dataset[key]; });
      ['onclick', 'oninput', 'onchange', 'onkeydown'].forEach(key => el.removeAttribute(key));
    });
  }

  function showDataPending(label) {
    if (!state.view) state.view = {
      original: one('main'), placeholder: null,
      extras: Array.from(document.body.children).filter(el =>
        /^(DIV|ASIDE|SECTION|BUTTON|FORM|DIALOG)$/.test(el.tagName) &&
        !['liveintel-development-state', 'liveintel-runtime-toast'].includes(el.id)
      ).map(original => ({ original, placeholder: null }))
    };
    const view = state.view;
    if (!view.original) return;
    const placeholder = view.original.cloneNode(true);
    neutralizeData(placeholder, label);
    // Old pending overlays must not hide the neutral component placeholders.
    all('.li-source-pending', placeholder).forEach(unpending);
    unpending(placeholder);
    (view.placeholder || view.original).replaceWith(placeholder);
    view.placeholder = placeholder;
    view.extras.forEach(extra => {
      const safe = extra.original.cloneNode(true);
      neutralizeData(safe, label);
      all('.li-source-pending', safe).forEach(unpending); unpending(safe);
      (extra.placeholder || extra.original).replaceWith(safe); extra.placeholder = safe;
    });
    all('header button').forEach(button => {
      if (!Object.hasOwn(button.dataset, 'liInitialDisabled')) button.dataset.liInitialDisabled = String(button.disabled);
      button.disabled = true;
    });
  }

  function restoreDataView() {
    if (!state.view?.placeholder) return;
    state.view.placeholder.replaceWith(state.view.original);
    state.view.placeholder = null;
    state.view.extras.forEach(extra => { extra.placeholder?.replaceWith(extra.original); extra.placeholder = null; });
    all('header button[data-li-initial-disabled]').forEach(button => {
      button.disabled = button.dataset.liInitialDisabled === 'true';
      delete button.dataset.liInitialDisabled;
    });
  }

  function prepareDataRegion(element, available, label) {
    if (!element.liDataTemplate) element.liDataTemplate = element.cloneNode(true);
    if (!available) {
      neutralizeData(element, 'DATA UNAVAILABLE');
      element.liDataEmpty = true; pending(element, label); return false;
    }
    if (element.liDataEmpty) {
      const template = element.liDataTemplate.cloneNode(true);
      element.replaceChildren(...Array.from(template.childNodes));
      element.liDataEmpty = false;
    }
    return true;
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
    const main = one('main');
    const games = Array.isArray(data.games) ? data.games : [];
    if (!main.liveUI) {
      const cards = all('.matchup-card', main);
      const heading = findLeaf(/^QUANT LIVE EDGE RADAR$/i, main);
      const radar = heading?.closest('.bg-surface-container');
      main.liveUI = { cards, base: cards.length, template: cards[0]?.cloneNode(true), filter: 'all', radar };
      all('[data-sport-filter]', main).forEach(button => {
        button.onclick = () => { main.liveUI.filter = button.dataset.sportFilter; bindLive(main.liveUI.data); };
      });
      leafElements(main).forEach(el => {
        if (/REALTIME EXECUTION MATRIX/i.test(el.textContent)) text(el, 'LIVEINTEL GAME STATE');
        if (/GLOBAL SLATE IN-PLAY/i.test(el.textContent)) main.liveUI.slate = el;
        if (/^BOOKMAKERS:/i.test(el.textContent)) text(el, 'MARKET DATA: SOURCE PENDING');
        if (/EV ARB DISCOVERY|STEAM >|HIGH LEVERAGE Q4/i.test(el.textContent)) text(el, 'MARKET SIGNAL: SOURCE PENDING');
      });
      const signalText = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
      while (signalText.nextNode()) {
        const node = signalText.currentNode;
        if (/EV ARB DISCOVERY|STEAM >|HIGH LEVERAGE Q4/.test(node.textContent)) node.textContent = ' MARKET SIGNAL: SOURCE PENDING';
      }
      const latency = all('span', main).find(el => /^LATENCY:/.test(el.textContent.trim()));
      text(latency, 'MARKET LATENCY: SOURCE PENDING');
      const intro = one('h1', main)?.nextElementSibling;
      text(intro, 'Recorded slate and game state. Verified market inputs remain source-pending.');
      if (radar) {
        all('p', radar).forEach(el => text(el, 'VERIFIED CURRENT LINE / PRICE / BOOK INPUTS REQUIRED'));
        leafElements(radar).filter(el => /^ACTIVE SCAN$/i.test(el.textContent.trim())).forEach(el => text(el, 'SOURCE PENDING'));
        all('button', radar).forEach(disableControl);
      }
    }
    const ui = main.liveUI; ui.data = data;
    // Only the radar is source-pending; the game workspace must remain usable.
    all('.li-source-pending', main).filter(el => el.querySelector('.matchup-card')).forEach(unpending);
    if (ui.radar) pending(ui.radar, 'EDGE / ARBITRAGE • VERIFIED MARKET SOURCE PENDING');
    text(ui.slate, `${games.length} RECORDED SLATE GAMES`);
    all('[data-sport-filter]', main).forEach(button => {
      const sport = button.dataset.sportFilter;
      const count = sport === 'all' ? games.length : games.filter(game => game.sport?.toLowerCase() === sport).length;
      text(one('span:last-child', button), count);
      button.disabled = false;
      button.classList.toggle('bg-surface-container-highest', ui.filter === sport);
      button.classList.toggle('text-on-surface', ui.filter === sport);
    });
    while (ui.cards.length < games.length && ui.template) {
      const card = ui.template.cloneNode(true); ui.cards[0].parentElement.appendChild(card); ui.cards.push(card);
    }
    while (ui.cards.length > Math.max(ui.base, games.length)) ui.cards.pop().remove();
    ui.cards.forEach((card, index) => {
      const game = games[index];
      if (!prepareDataRegion(card, Boolean(game), 'NO ADDITIONAL RECORDED GAME')) { card.liveFields = null; card.classList.remove('li-hidden'); return; }
      unpending(card); card.dataset.gameId = game.id; card.dataset.sport = String(game.sport || '').toLowerCase();
      card.classList.toggle('li-hidden', ui.filter !== 'all' && card.dataset.sport !== ui.filter);
      if (!card.liveFields) {
        const blocks = Array.from(card.children);
        const head = leafElements(blocks[0]);
        const teams = Array.from(blocks[1]?.children || []).slice(0, 2).map(row => {
          const leaves = leafElements(row);
          return { badge: leaves[0], name: leaves[1], score: leaves.at(-1), extra: leaves.slice(2, -1) };
        });
        const footer = blocks.at(-1);
        card.liveFields = { sport: head[0], date: head[1], status: head.at(-1), teams,
          metrics: Array.from(blocks[2]?.children || []),
          footer: leafElements(footer).find(el => !el.closest('button') && !el.matches('.material-symbols-outlined')),
          action: one('button', footer) };
        leafElements(card).filter(el => !el.matches('.material-symbols-outlined')).forEach(el => text(el, 'SOURCE PENDING'));
      }
      const refs = card.liveFields;
      text(refs.sport, game.sport); text(refs.date, game.slate_date || data.global?.slate_date || 'SLATE PENDING');
      text(refs.status, `${game.status || 'STATUS PENDING'}${game.status === 'LIVE' ? ` • ${game.period_label || 'PERIOD PENDING'} ${game.clock || ''}` : ''}`);
      const recorded = game.recorded || {};
      [[game.away, game.away_score ?? recorded.away_score], [game.home, game.home_score ?? recorded.home_score]].forEach(([team, score], i) => {
        text(refs.teams[i]?.badge, team); text(refs.teams[i]?.name, team);
        text(refs.teams[i]?.score, finiteInput(score) ? score : '—');
        refs.teams[i]?.extra.forEach(el => text(el, 'SEGMENTS PENDING'));
      });
      refs.metrics.forEach((metric, i) => {
        const leaves = leafElements(metric);
        text(leaves[0], i < 2 ? 'LIVE MARKET' : 'GAME STATE');
        text(leaves[1], i < 2 ? 'SOURCE PENDING' : gameSituation(game));
        if (i < 2) pending(metric, 'LIVE MARKET • DATA SOURCE PENDING'); else unpending(metric);
      });
      text(refs.footer, game.status === 'PRE' ? `START ${game.start_time || 'TIME PENDING'}` : `${game.status || 'RECORDED'} • RECORDED STATE`);
      if (refs.action) {
        const label = leafElements(refs.action).find(el => !el.matches('.material-symbols-outlined'));
        text(label, game.status === 'FINAL' ? 'RECAP & LOG' : 'GAMECAST');
        refs.action.dataset.liAction = 'gamecast'; refs.action.dataset.url = game.gamecast || '';
        refs.action.disabled = !/^https:\/\/www\.espn\.com\//.test(game.gamecast || '');
      }
    });
    text(one('#syncTimer'), '15s');
    all('header button').forEach(disableControl);
  }

  function playerMarket(player) {
    const priority = ['points', 'rebounds', 'assists', 'pass_yards', 'rush_yards', 'receiving_yards', 'receptions', 'three_pm', 'fga', 'minutes'];
    return priority.find(key => player.windows && player.windows[key] && player.windows[key].values.length) || Object.keys(player.windows || {})[0] || '';
  }
  function titleStat(key) { return key ? key.replaceAll('_', ' ').replace(/\b\w/g, char => char.toUpperCase()) : 'HISTORY NOT AVAILABLE'; }

  function bindPlayers(data) {
    const main = one('main');
    const players = Array.isArray(data.players) ? data.players : [];
    if (!main.playersUI) {
      const grid = all('div', main).find(el => /xl:grid-cols-12/.test(el.className) && !el.querySelector('table'));
      const footerText = leafElements(main);
      const chips = all('button', main).filter(button => /(?:BOS @ CHA|OKC @ MEM|PHX @ GSW)/.test(button.textContent));
      const ui = main.playersUI = { page: 0, role: 'all', sport: '', sort: 'source', selected: '',
        templates: Array.from(grid?.children || []).map(el => el.cloneNode(true)),
        search: one('#player-filter-input'), sortSelect: one('select', main), chips,
        count: footerText.find(el => /^SHOWING 7 OF 18/i.test(el.textContent)),
        age: footerText.find(el => /^MODEL UPDATE:/i.test(el.textContent)),
        pageLabel: footerText.find(el => /^PAGE 1 OF 3/i.test(el.textContent)),
        active: footerText.find(el => /ACTIVE PROPS IDENTIFIED/i.test(el.textContent)),
        prev: all('button', main).find(button => /PREV/.test(button.textContent)),
        next: all('button', main).find(button => /NEXT/.test(button.textContent)),
        roles: all('button', main).filter(button => /^(ALL|STARTERS|BENCH|ROTATION)$/.test(button.textContent.trim())),
        sports: all('button', main).filter(button => /^(NBA|WNBA|NFL|CFB)$/.test(button.textContent.trim())) };
      const refresh = () => bindPlayers(ui.data);
      ui.search.addEventListener('input', event => { event.stopImmediatePropagation(); ui.page = 0; refresh(); }, true);
      ui.sports.forEach(button => {
        button.dataset.playerSport = button.textContent.trim();
        button.onclick = () => { ui.sport = ui.sport === button.dataset.playerSport ? '' : button.dataset.playerSport; ui.page = 0; refresh(); };
      });
      ui.roles.forEach((button, index) => {
        button.dataset.playerRoleFilter = ['all', 'starter', 'bench', 'unknown'][index];
        text(button, ['ALL', 'STARTERS', 'BENCH / ROT', 'ROLE UNKNOWN'][index]);
        button.onclick = () => { ui.role = button.dataset.playerRoleFilter; ui.page = 0; refresh(); };
      });
      if (ui.sortSelect) {
        Array.from(ui.sortSelect.options).forEach((option, index) => {
          option.value = ['source', 'usage', 'minutes', 'l5'][index];
          text(option, ['SOURCE ORDER', 'USAGE • SOURCE PENDING', 'MINUTES', 'L5 AVG'][index]);
          option.disabled = index === 1;
        });
        ui.sortSelect.value = 'source';
        ui.sortSelect.onchange = () => { ui.sort = ui.sortSelect.value; ui.page = 0; refresh(); };
      }
      ui.prev.onclick = () => { ui.page = Math.max(0, ui.page - 1); refresh(); };
      ui.next.onclick = () => { ui.page++; refresh(); };
      const model = findLeaf(/^PRO QUANT V4$/i, main); text(model, 'RECORDED RESEARCH');
      text(findLeaf(/^⌘K$/i, main), 'SEARCH');
      const headers = all('thead th', main); text(headers[8], 'L10 AVG');
    }
    const ui = main.playersUI; ui.data = data;
    const supported = [...ui.roles, ...ui.sports, ui.prev, ui.next];
    all('button', main).filter(button => !supported.includes(button)).forEach(disableControl);
    const query = ui.search.value.trim().toLowerCase();
    const matches = players.filter(player => {
      const role = String(player.role || '').toLowerCase();
      return (!ui.sport || player.sport === ui.sport) && (!query || `${player.name} ${player.team} ${player.game || ''} ${titleStat(playerMarket(player))}`.toLowerCase().includes(query)) &&
        (ui.role === 'all' || (ui.role === 'starter' && role === 'starter') || (ui.role === 'bench' && /bench|rotation/.test(role)) || (ui.role === 'unknown' && (!role || /not supplied|unknown/.test(role))));
    });
    const statValue = player => ui.sort === 'minutes' ? player.minutes : (player.windows?.[playerMarket(player)] || {}).l5;
    if (ui.sort === 'minutes' || ui.sort === 'l5') matches.sort((a, b) => (finiteInput(statValue(b)) ? Number(statValue(b)) : -Infinity) - (finiteInput(statValue(a)) ? Number(statValue(a)) : -Infinity));
    const pageSize = all('tbody tr', main).length;
    ui.page = Math.max(0, Math.min(ui.page, Math.max(0, Math.ceil(matches.length / pageSize) - 1)));
    const pagePlayers = matches.slice(ui.page * pageSize, (ui.page + 1) * pageSize);
    const detailGrid = Array.from(document.querySelectorAll('main div')).find(element => /xl:grid-cols-12/.test(element.className || '') && !element.querySelector('table'));
    const detailPanels = detailGrid ? Array.from(detailGrid.children) : [];
    detailPanels.forEach((panel, i) => { const copy = ui.templates[i]?.cloneNode(true); if (copy) panel.replaceChildren(...Array.from(copy.childNodes)); });
    const playerDetail = detailPanels[0] || null;
    const rotationDetail = detailPanels[1] || null;
    if (playerDetail) pending(playerDetail, 'SELECTED PLAYER DETAIL • CHOOSE A REAL MATRIX ROW');
    if (rotationDetail) pending(rotationDetail, 'MATCHUP ROTATION DETAIL • REAL ROW SELECTION REQUIRED');
    detailPanels.slice(2).forEach(panel => pending(panel, 'REAL DATA BINDING PENDING'));
    const rows = all('tbody tr');
    rows.forEach((row, index) => {
      row.style.display = '';
      const player = pagePlayers[index];
      if (!prepareDataRegion(row, Boolean(player), 'NO ADDITIONAL CURRENT PLAYER')) return;
      const sourceRow = row.liDataTemplate.cloneNode(true);
      row.replaceChildren(...Array.from(sourceRow.childNodes));
      unpending(row); row.dataset.playerId = player.player_id; row.dataset.sport = player.sport; row.dataset.team = player.team; row.dataset.playerIndex = String(players.indexOf(player));
      const cells = Array.from(row.children); const market = playerMarket(player); const window = (player.windows || {})[market] || {};

      if (cells[0]) {
        const identity = leafElements(cells[0]);
        text(identity[0], player.name || 'PLAYER');
        text(identity[1], `${player.team || '—'}${player.game ? ` • ${player.game}` : ''}`);
      }

      const replaceCell = (cell, value) => {
        if (!cell) return;
        const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT), nodes = [];
        while (walker.nextNode()) if (walker.currentNode.textContent.trim()) nodes.push(walker.currentNode);
        nodes.forEach((node, i) => { node.textContent = i === 0 ? String(value) : ''; });
      };

      replaceCell(cells[1], player.position || '—');
      replaceCell(cells[2], player.role || 'ROLE NOT SUPPLIED');
      replaceCell(cells[3], player.minutes == null ? '—' : number(player.minutes, 1));
      replaceCell(cells[4], 'NOT DERIVABLE');
      replaceCell(cells[5], titleStat(market));
      replaceCell(cells[6], 'SOURCE PENDING');
      replaceCell(cells[7], window.l5 == null ? '—' : number(window.l5, 1));
      replaceCell(
        cells[8],
        `${window.l10 == null ? '—' : number(window.l10, 1)} • LINE REQUIRED`
      );
      replaceCell(cells[9], 'PRICE REQUIRED');
      const buttons = cells[10] ? all('button', cells[10]) : [];
      buttons.forEach((button, buttonIndex) => {
        button.dataset.playerId = player.player_id;
        button.dataset.playerName = player.name;
        button.dataset.team = player.team;
        button.dataset.sport = player.sport;

        if (buttonIndex === 0) {
          button.dataset.liAction = 'watch-add';
          button.disabled = false;
          text(button, 'WATCH');
        } else {
          delete button.dataset.liAction;
          button.disabled = true;
          button.dataset.market = titleStat(market);
          button.dataset.gameId = player.game_id || '';
          text(button, 'LINE REQUIRED');
        }
      });
    });

    const bindAvatar = (root, player) => {
      const holder = all('div', root).find(el => /\bw-(?:8|10|12|14|16|20|full)\b/.test(el.className) && /items-center/.test(el.className) && /justify-center/.test(el.className));
      if (!holder || one('img', holder)) return;
      const initials = String(player.name || '?').split(/\s+/).filter(Boolean).map(part => part[0]).join('').slice(0, 2).toUpperCase();
      const value = leafElements(holder).find(el => !el.matches('.material-symbols-outlined'));
      text(value || holder, initials);
    };

    const renderRotationDetail = selectedPlayer => {
      if (!rotationDetail || !selectedPlayer) return;

      unpending(rotationDetail);

      const peers = players
        .filter(player =>
          player &&
          String(player.game_id || '') === String(selectedPlayer.game_id || '') &&
          String(player.player_id || '') !== String(selectedPlayer.player_id || '')
        )
        .sort((a, b) => {
          const aSame = a.team === selectedPlayer.team ? 0 : 1;
          const bSame = b.team === selectedPlayer.team ? 0 : 1;
          if (aSame !== bSame) return aSame - bSame;

          const roleRank = role =>
            role === 'STARTER' ? 0 :
            role === 'BENCH / ROTATION' ? 1 : 2;

          const roleDiff = roleRank(a.role) - roleRank(b.role);
          if (roleDiff) return roleDiff;

          return (Number(b.minutes) || -1) - (Number(a.minutes) || -1);
        })
        .slice(0, 4);

      const cards = Array.from(rotationDetail.children);

      cards.forEach((card, index) => {
        const player = peers[index];

        if (!prepareDataRegion(card, Boolean(player), 'NO ADDITIONAL CURRENT PLAYER')) return;

        unpending(card);
        card.dataset.playerId = player.player_id || '';
        card.dataset.team = player.team || '';
        card.dataset.sport = player.sport || '';

        const market = playerMarket(player);
        const window = (player.windows || {})[market] || {};
        const values = (window.values || []).slice(0, 5);
        const statName = titleStat(market);

        const leaves = leafElements(card);
        bindAvatar(card, player);

        const nameElement = leaves.find(element =>
          /font-headline-sm/.test(element.className || '')
        );
        text(nameElement, player.name || 'PLAYER');

        if (nameElement?.parentElement) {
          const positionElement = Array.from(nameElement.parentElement.children).find(
            element => element !== nameElement && element.tagName === 'SPAN'
          );
          text(positionElement, player.position || '—');
        }

        const identityElement = leaves.find(element =>
          /•/.test(element.textContent) &&
          /font-label-mono-micro/.test(element.className || '')
        );
        text(
          identityElement,
          `${player.team || '—'} • ${player.role || 'ROLE NOT SUPPLIED'}`
        );

        const headerBadge = Array.from(card.querySelectorAll('span')).find(element =>
          /10\/10 HIT|\+EV EDGE|HOT BENCH|ROTATION/i.test(element.textContent.trim())
        );
        text(headerBadge, player.role || 'ROLE NOT SUPPLIED');

        const image = card.querySelector('img');
        if (image) {
          image.removeAttribute('src');
          image.style.display = 'none';

          const holder = image.parentElement;
          if (holder) {
            let initials = holder.querySelector('[data-li-rotation-initials]');
            if (!initials) {
              initials = document.createElement('span');
              initials.dataset.liRotationInitials = 'true';
              initials.className = 'font-label-mono-sm text-label-mono-sm text-on-surface font-bold';
              holder.insertBefore(initials, holder.firstChild);
            }
            initials.textContent = String(player.name || '?')
              .split(/\s+/)
              .filter(Boolean)
              .map(part => part[0])
              .join('')
              .slice(0, 2)
              .toUpperCase();
          }
        }

        const metricGrid = Array.from(card.querySelectorAll('div')).find(element =>
          /grid-cols-3/.test(element.className || '')
        );

        const metricBoxes = metricGrid ? Array.from(metricGrid.children) : [];

        const setMetric = (box, label, value) => {
          if (!box) return;
          const boxLeaves = leafElements(box);
          if (boxLeaves[0]) text(boxLeaves[0], label);
          if (boxLeaves[1]) text(boxLeaves[1], value);
        };

        setMetric(
          metricBoxes[0],
          'MIN',
          player.minutes == null ? '—' : number(player.minutes, 1)
        );

        setMetric(
          metricBoxes[1],
          `${statName} L5`,
          window.l5 == null ? '—' : number(window.l5, 1)
        );

        setMetric(
          metricBoxes[2],
          'MARKET',
          'SOURCE PENDING'
        );

        const logLine = leaves.find(element =>
          /^L5(?: Log)?:/i.test(element.textContent.trim())
        );

        text(
          logLine,
          `L5: [${values.length ? values.map(value => number(value, 1)).join(', ') : 'NO HISTORY'}]`
        );

        const lineOrHit = leaves.find(element =>
          /^Line:|Over$|\d+\/\d+ Over|\d+% Over/i.test(element.textContent.trim())
        );
        text(lineOrHit, 'LINE REQUIRED');

        const footerText = leaves.find(element =>
          /EV vs|Rim vuln|role expansion|rested = high usage/i.test(element.textContent)
        );
        text(footerText, 'RECORDED PLAYER HISTORY • MARKET SOURCE PENDING');

        const slipButton = all('button', card).find(button =>
          /SLIP/i.test(button.textContent)
        );

        if (slipButton) {
          delete slipButton.dataset.liAction;
          slipButton.disabled = true;
          text(slipButton, 'LINE REQUIRED');
        }

        const progress = Array.from(card.querySelectorAll('div')).find(element =>
          /h-1\.5/.test(element.className || '') &&
          /overflow-hidden/.test(element.className || '')
        );

        if (progress) all('div', progress).forEach(bar => { bar.style.width = '0%'; });
      });
    };

    const renderPlayerDetail = player => {
      if (!playerDetail || !player) return;

      renderRotationDetail(player);

      unpending(playerDetail);
      playerDetail.dataset.playerId = player.player_id || '';
      playerDetail.dataset.sport = player.sport || '';
      playerDetail.dataset.team = player.team || '';

      const market = playerMarket(player);
      const window = (player.windows || {})[market] || {};
      const values = (window.values || []).slice(0, 5);
      const statName = titleStat(market);
      const leaves = leafElements(playerDetail);
      bindAvatar(playerDetail, player);

      const nameElement = leaves.find(element =>
        /font-headline-lg/.test(element.className || '') &&
        /tracking-tight/.test(element.className || '') &&
        !element.dataset.liPlayerInitials
      );
      text(nameElement, player.name || 'PLAYER');

      if (nameElement && nameElement.parentElement) {
        const roleElement = Array.from(nameElement.parentElement.children).find(
          element => element !== nameElement && element.tagName === 'SPAN'
        );
        text(roleElement, player.role || 'ROLE NOT SUPPLIED');
      }

      const identityElement = leaves.find(element =>
        /^[A-Z0-9]{2,5}\s*•/.test(element.textContent.trim())
      );
      text(
        identityElement,
        `${player.team || '—'} • ${player.position || 'POSITION NOT SUPPLIED'}`
      );

      const matchupElement = leaves.find(element =>
        /^(?:vs|@)\s/i.test(element.textContent.trim())
      );
      text(matchupElement, player.game || 'MATCHUP NOT AVAILABLE');

      const paceElement = leaves.find(element =>
        /^PACE\s/i.test(element.textContent.trim())
      );
      text(paceElement, 'PACE: DATA NOT DERIVED');

      const edgeElement = Array.from(playerDetail.querySelectorAll('span')).find(element =>
        /EDGE/i.test(element.textContent.trim()) &&
        /font-label-mono-sm/.test(element.className || '')
      );
      if (edgeElement) {
        edgeElement.replaceChildren(document.createTextNode('MARKET EDGE: PRICE REQUIRED'));
      }

      const defenseElement = leaves.find(element =>
        /PERIMETER DEF|DEF \(#/i.test(element.textContent.trim())
      );
      text(defenseElement, 'MATCHUP DEFENSE: SOURCE PENDING');

      const image = playerDetail.querySelector('img');
      if (image) {
        image.removeAttribute('src');
        image.style.display = 'none';
        image.alt = `${player.name || 'Player'} portrait unavailable`;

        const holder = image.parentElement;
        if (holder) {
          let initials = holder.querySelector('[data-li-player-initials]');
          if (!initials) {
            initials = document.createElement('span');
            initials.dataset.liPlayerInitials = 'true';
            initials.className = 'font-headline-lg text-headline-lg text-on-surface font-bold';
            holder.insertBefore(initials, holder.firstChild);
          }
          initials.textContent = String(player.name || '?')
            .split(/\s+/)
            .filter(Boolean)
            .map(part => part[0])
            .join('')
            .slice(0, 2)
            .toUpperCase();
        }
      }

      const metricGrid = Array.from(playerDetail.querySelectorAll('div')).find(element =>
        /grid-cols-2/.test(element.className || '') &&
        /sm:grid-cols-4/.test(element.className || '')
      );

      const metricBoxes = metricGrid ? Array.from(metricGrid.children) : [];

      const setMetric = (box, items) => {
        if (!box) return;
        const boxLeaves = leafElements(box);
        items.forEach((value, index) => {
          if (boxLeaves[index]) text(boxLeaves[index], value);
        });
      };

      setMetric(metricBoxes[0], [
        'RECORDED MIN',
        player.minutes == null ? '—' : number(player.minutes, 1),
        'CURRENT GAME',
        `ROLE: ${player.role || 'NOT SUPPLIED'}`
      ]);

      setMetric(metricBoxes[1], [
        'USAGE',
        'NOT DERIVABLE',
        '—',
        'SOURCE NOT RECORDED'
      ]);

      setMetric(metricBoxes[2], [
        `${statName} L5 AVG`,
        window.l5 == null ? '—' : number(window.l5, 1),
        window.l10 == null ? 'L10 —' : `L10 ${number(window.l10, 1)}`,
        'RECORDED HISTORY'
      ]);

      setMetric(metricBoxes[3], [
        `${statName} HISTORY`,
        `${values.length} GAMES`,
        'RECORDED',
        'LINE REQUIRED'
      ]);

      const logHeading = leaves.find(element =>
        /LOG \(LAST 5 (?:GAMES|RECORDED)\)/i.test(element.textContent.trim())
      );

      const logPanel = logHeading?.parentElement?.parentElement?.parentElement || null;

      text(logHeading, `${statName} LOG (LAST 5 RECORDED)`);

      if (logPanel) {
        const logLeaves = leafElements(logPanel);

        const lineElement = logLeaves.find(element =>
          /^LINE:/i.test(element.textContent.trim())
        );
        text(lineElement, 'LINE: SOURCE PENDING');

        const averageElement = logLeaves.find(element =>
          /^AVG\s/i.test(element.textContent.trim())
        );
        text(
          averageElement,
          window.l5 == null ? 'AVG —' : `AVG ${number(window.l5, 1)} ${statName}`
        );

        const logGrid = Array.from(logPanel.querySelectorAll('div')).find(element =>
          /grid-cols-5/.test(element.className || '')
        );

        if (logGrid) {
          const boxes = Array.from(logGrid.children);
          const numericValues = values
            .filter(finiteInput).map(Number);
          const maximum = Math.max(...numericValues, 1);

          boxes.forEach((box, index) => {
            const boxLeaves = leafElements(box);
            const value = Number(values[index]);
            const available = finiteInput(values[index]);

            if (boxLeaves[0]) text(boxLeaves[0], available ? number(value, 1) : '—');
            if (boxLeaves[boxLeaves.length - 1]) text(boxLeaves[boxLeaves.length - 1], `G${index + 1}`);

            const bar = Array.from(box.children).find(element =>
              /bg-primary\/20|bg-surface-container-highest/.test(element.className || '')
            );

            if (bar) {
              bar.style.height = available
                ? `${Math.max(8, Math.round(100 * value / maximum))}%`
                : '8%';
            }
          });
        }

        const thresholdElement = logLeaves.find(element =>
          /^Threshold:/i.test(element.textContent.trim())
        );
        text(thresholdElement, 'Threshold: LINE REQUIRED');

        const oddsElement = logLeaves.find(element =>
          /Over Odds:/i.test(element.textContent.trim())
        );
        text(oddsElement, 'Price: SOURCE PENDING');
      }

      const detailButtons = all('button', playerDetail);

      const watchButton = detailButtons.find(button =>
        /ADD TO WATCH/i.test(button.textContent)
      );

      if (watchButton) {
        watchButton.dataset.playerId = player.player_id || '';
        watchButton.dataset.playerName = player.name || '';
        watchButton.dataset.team = player.team || '';
        watchButton.dataset.sport = player.sport || '';
        watchButton.dataset.liAction = 'watch-add';
        watchButton.disabled = false;
      }

      const ticketButton = detailButtons.find(button =>
        /ADD TO TICKET|TICKET:/i.test(button.textContent)
      );

      if (ticketButton) {
        delete ticketButton.dataset.liAction;
        ticketButton.disabled = true;
        const ticketLabel = leafElements(ticketButton).find(element =>
          !/add_circle/i.test(element.textContent)
        );
        text(ticketLabel, 'TICKET: LINE / PRICE REQUIRED');
      }

      rows.forEach(row => row.classList.remove('bg-surface-container'));
      const selectedRow = rows.find(
        row => row.dataset.playerId === String(player.player_id || '')
      );
      if (selectedRow) selectedRow.classList.add('bg-surface-container');
    };

    rows.forEach(row => {
      row.onclick = event => {
        if (event.target.closest('button') || !row.dataset.playerId) return;
        ui.selected = row.dataset.playerId; bindPlayers(ui.data);
      };
    });
    const selected = pagePlayers.find(player => String(player.player_id) === ui.selected) || pagePlayers[0];
    if (selected) { ui.selected = String(selected.player_id); renderPlayerDetail(selected); }
    else [playerDetail, rotationDetail].filter(Boolean).forEach(panel => { neutralizeData(panel, 'NO MATCHING PLAYER'); pending(panel, 'NO MATCHING RECORDED PLAYER'); });
    ui.search.disabled = false;
    ui.roles.forEach(button => { button.disabled = false; button.classList.toggle('li-active-role', button.dataset.playerRoleFilter === ui.role); });
    ui.sports.forEach(button => { button.disabled = false; button.classList.toggle('bg-surface-container-highest', button.dataset.playerSport === ui.sport); });
    if (ui.sortSelect) ui.sortSelect.disabled = false;
    ui.prev.disabled = ui.page === 0; ui.next.disabled = (ui.page + 1) * pageSize >= matches.length;
    text(ui.count, `SHOWING ${matches.length ? ui.page * pageSize + 1 : 0}–${Math.min((ui.page + 1) * pageSize, matches.length)} OF ${matches.length} MATCHING PLAYERS`);
    text(ui.pageLabel, `PAGE ${matches.length ? ui.page + 1 : 0} OF ${Math.ceil(matches.length / pageSize)}`);
    text(ui.age, data.generated_at ? `SOURCE: ${data.generated_at}` : 'SOURCE TIMESTAMP PENDING');
    text(ui.active, `LIVE SLATE: ${players.length} CURRENT PLAYERS • PROP SOURCE PENDING`);
    const games = Array.isArray(data.games) ? data.games : [];
    ui.chips.forEach((button, i) => {
      const game = games[i];
      const leaves = leafElements(button).filter(el => !el.matches('.material-symbols-outlined'));
      text(leaves[0], game ? `${game.away} @ ${game.home}` : 'MATCHUP PENDING');
      text(leaves[1], game?.start_time || 'TIME PENDING');
    });
    all('header button').forEach(disableControl);
  }

  function bindHeat(data) {
    const main = one('main');
    const heat = data.heat || {};
    const labelControl = (control, label) => {
      if (!control) return;
      const walker = document.createTreeWalker(control, NodeFilter.SHOW_TEXT), nodes = [];
      while (walker.nextNode()) if (walker.currentNode.textContent.trim() && !walker.currentNode.parentElement.closest('.material-symbols-outlined')) nodes.push(walker.currentNode);
      nodes.forEach((node, i) => { node.textContent = i === 0 ? label : ''; });
    };
    if (!main.heatUI) {
      const ui = main.heatUI = { mode: 'players', sport: '', search: one('input[type="text"]', main),
        grids: { players: one('#players-grid'), teams: one('#teams-grid') },
        detected: findLeaf(/^412 DETECTED$/i, main), segments: [one('#seg-btn-players'), one('#seg-btn-teams')], sports: all('#sport-selector button') };
      // Capture references before clearing exported identities and market claims.
      Object.values(ui.grids).forEach(grid => Array.from(grid.children).forEach(el => {
        const header = el.children[0], content = el.children[1];
        const leaves = leafElements(content), head = leafElements(header);
        const after = pattern => { const i = leaves.findIndex(e => pattern.test(e.textContent.trim())); return i < 0 ? null : leaves[i + 1]; };
        el.heatFields = { name: one('span.font-headline-sm', header), initials: head[0],
          team: head.find(e => /•/.test(e.textContent)), matchup: head.find(e => /(?:vs|@)\s/i.test(e.textContent)), market: head.at(-1),
          line: after(/^CURRENT LINE$/i), price: after(/^CONSENSUS$/i), rate: after(/REC HIT PROB/i),
          rateLabel: leaves.find(e => /REC HIT PROB/i.test(e.textContent)),
          streak: leaves.find(e => /^\d+\s*\/\s*\d+/.test(e.textContent)), average: leaves.find(e => /^AVG:/i.test(e.textContent)),
          log: all('div', content).find(e => /grid-cols-(?:7|8|10)/.test(e.className)), buttons: all('button', el.children[2]) };
        const keep = /^(CURRENT LINE|CONSENSUS|REC HIT PROB|LAST \d+|GAME LOG|RECENT RESULTS|HIT RATE|PATTERN)$/i;
        const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
        while (walker.nextNode()) {
          const node = walker.currentNode;
          if (node.textContent.trim() && !node.parentElement.closest('.material-symbols-outlined') && !keep.test(node.textContent.trim())) node.textContent = 'SOURCE PENDING';
        }
        all('[onclick]', el).forEach(e => e.removeAttribute('onclick'));
        all('[style*="width:"]', el).forEach(e => { e.style.width = '0%'; });
        all('svg polyline,svg path,svg line', el).forEach(e => { e.style.visibility = 'hidden'; });
      }));
      // These components require live market/covariance inputs the history API does not supply.
      ['#total-ou-correlation-bar', '#sgp-correlation-engine', '#sgp-betslip-drawer'].forEach(selector => {
        const component = one(selector);
        if (component) { neutralizeData(component, 'MARKET INPUTS PENDING'); unpending(component); }
      });
      [one('#btn-toggle-betslip-drawer'), one('#btn-open-sgp-drawer-bar')].forEach(button => {
        if (button) { disableControl(button); labelControl(button, 'SGP • VERIFIED INPUTS REQUIRED'); }
      });
      // Clear unsupported aggregate, ranking, feed, age and synchronization narratives.
      const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        const node = walker.currentNode, parent = node.parentElement;
        if (parent.closest('#players-grid,#teams-grid,.material-symbols-outlined,script,style')) continue;
        const value = node.textContent.trim();
        if (/14 PLAYS|10\/10 RATE|18\.4%|98\.2%|\(252\)|\(14\)|\(39\)|\(68\)|\(87\)|PERFECT|CORR|COVARIANCE|SYNCHRON|ARBITRAGE|HIGH.CONFIDENCE|LOGICAL VERIFICATION|BOOK.*(?:SYNC|REPAIR|MULTI)|REPAIR.*STATUS|412 DETECTED|DATA CADENCE: REAL.TIME|\d+\s*(?:ERRORS|NODES)|0\.082|REAL BOOK|LINE DISCREPANCY|SLATE EFFICIENCY/i.test(value)) node.textContent = 'SOURCE PENDING';
      }
      text(findLeaf(/^HEAT ENGINE v4.1$/i, main), 'RECORDED HEAT HISTORY');
      text(findLeaf(/^HISTORIC TRUTH PROTOCOL$/i, main), 'RECORDED RESULTS');
      text(findLeaf(/STRICTLY HISTORICAL/i, main), 'RECORDED HISTORY • CURRENT MARKET DATA PENDING');
      text(one('#mins-readout-badge'), 'MINUTES FILTER PENDING');
      text(one('#pace-readout-badge'), 'PACE FILTER PENDING');
      all('button,input,select', main).forEach(disableControl);
      ui.segments.forEach((button, i) => { button.onclick = () => { ui.mode = i ? 'teams' : 'players'; bindHeat(ui.data); }; });
      ui.sports.forEach(button => {
        button.dataset.heatSport = button.textContent.trim();
        button.onclick = () => { ui.sport = ui.sport === button.dataset.heatSport ? '' : button.dataset.heatSport; bindHeat(ui.data); };
      });
      ui.search.oninput = () => bindHeat(ui.data);
    }
    const ui = main.heatUI; ui.data = data;
    const source = Array.isArray(heat.cards) ? heat.cards : [];
    const query = ui.search.value.trim().toLowerCase();
    for (const [kind, grid] of Object.entries(ui.grids)) {
      const records = source.filter(record => (record.kind === 'team' ? 'teams' : 'players') === kind);
      const filtered = records.filter(record => (!ui.sport || record.sport === ui.sport) && (!query || `${record.player || ''} ${record.team || ''} ${record.market || ''}`.toLowerCase().includes(query)));
      const slots = Array.from(grid.children);
      slots.forEach((el, index) => {
        const record = filtered[index], refs = el.heatFields;
        all('button', el).forEach(disableControl);
        delete el.dataset.playerId;
        if (!record) {
          pending(el, 'NO ADDITIONAL RECORDED HISTORY PATTERN');
          [refs.name, refs.initials, refs.team, refs.matchup, refs.market, refs.line, refs.price, refs.rate, refs.streak, refs.average].forEach(ref => text(ref, 'SOURCE PENDING'));
          if (refs.log) leafElements(refs.log).forEach(ref => text(ref, '—'));
          all('svg polyline,svg path', el).forEach(e => { e.style.visibility = 'hidden'; });
          return;
        }
        unpending(el);
        const name = record.player || record.team || 'IDENTITY PENDING';
        el.dataset.playerId = record.player_id || ''; el.dataset.sport = record.sport || ''; el.dataset.team = record.team || '';
        text(refs.name, name); text(refs.initials, name.split(/\s+/).map(s => s[0]).join('').slice(0, 2).toUpperCase());
        text(refs.team, `${record.team || 'TEAM PENDING'} • ${record.sport || 'SPORT PENDING'}`);
        text(refs.matchup, 'RECORDED HISTORY'); text(refs.market, record.market || 'STAT PENDING');
        text(refs.line, 'SOURCE PENDING'); text(refs.price, 'SOURCE PENDING');
        const validCount = finiteInput(record.hits) && finiteInput(record.total) && Number(record.total) > 0 && Number(record.hits) >= 0 && Number(record.hits) <= Number(record.total);
        text(refs.rateLabel, 'HISTORICAL FREQUENCY'); text(refs.rate, validCount ? `${number(100 * record.hits / record.total, 1)}% HIST` : 'SOURCE PENDING');
        text(refs.streak, validCount ? `${record.hits} / ${record.total} RECORDED` : 'RECORD PENDING');
        const values = (record.values || []).filter(finiteInput).map(Number);
        text(refs.average, values.length ? `AVG: ${number(values.reduce((a, b) => a + b, 0) / values.length, 1)} ${record.market || ''}` : 'AVG: SOURCE PENDING');
        const curve = one('svg polyline', el);
        if (curve && values.length > 1 && values.length === (record.values || []).length) {
          const low = Math.min(...values), range = Math.max(...values) - low;
          curve.setAttribute('points', values.map((value, i) => `${100 * i / (values.length - 1)},${range ? 21 - 18 * (value - low) / range : 12}`).join(' '));
          curve.style.visibility = 'visible';
        } else if (curve) curve.style.visibility = 'hidden';
        if (refs.log) Array.from(refs.log.children).forEach((box, i) => {
          const leaves = leafElements(box); text(leaves[0], record.dates?.[i]?.slice(5) || `G${i + 1}`);
          text(leaves[1], finiteInput(record.values?.[i]) ? record.values[i] : '—');
        });
        refs.buttons.forEach((button, i) => {
          labelControl(button, i === 0 ? 'WATCH TARGET' : i === 1 ? 'SGP: INPUTS REQUIRED' : 'TICKET: PRICE REQUIRED');
          if (i === 0 && record.player_id) {
            button.disabled = false; button.removeAttribute('aria-disabled');
            Object.assign(button.dataset, { liAction: 'watch-add', playerId: record.player_id, playerName: name, team: record.team || '', sport: record.sport || '' });
          }
        });
      });
      grid.classList.toggle('hidden', ui.mode !== kind);
      text(one(kind === 'players' ? '#player-active-count-badge' : '#team-active-count-badge'), `SHOWING ${Math.min(slots.length, filtered.length)} OF ${filtered.length} LOADED ${kind.toUpperCase()} PATTERNS`);
      const segment = ui.segments[kind === 'players' ? 0 : 1];
      labelControl(segment, `${kind.toUpperCase()} `);
      text(one('span:last-child', segment), `(${records.length} LOADED)`);
      segment.disabled = false; segment.removeAttribute('aria-disabled');
      segment.classList.toggle('bg-surface-container-highest', ui.mode === kind);
      one(kind === 'players' ? '#player-filters-bar' : '#team-filters-bar')?.classList.toggle('hidden', ui.mode !== kind);
    }
    text(ui.detected, `${source.length} LOADED`);
    text(one('#cluster-count-label'), finiteInput(heat.total) ? `${heat.total} RECORDED HISTORY PATTERNS • ${source.length} LOADED` : `${source.length} LOADED HISTORY PATTERNS`);
    ui.search.disabled = false; ui.search.removeAttribute('aria-disabled');
    ui.sports.forEach(button => { button.disabled = false; button.removeAttribute('aria-disabled'); });
    all('header button').forEach(disableControl);
  }

  function researchTeams(data) {
    const cards = data.research && Array.isArray(data.research.cards)
      ? data.research.cards
      : [];

    const teamCards = cards.filter(card =>
      card &&
      card.team &&
      card.game_id &&
      /team score/i.test(card.market || '')
    );

    for (const card of teamCards) {
      const mate = teamCards.find(other =>
        other !== card &&
        String(other.game_id) === String(card.game_id) &&
        other.team !== card.team
      );

      if (mate) return [card, mate];
    }

    return teamCards.slice(0, 2);
  }

  function finiteInput(value) {
    return (typeof value === 'number' || typeof value === 'string') && String(value).trim() !== '' && Number.isFinite(Number(value));
  }

  function disableControl(control) {
    control.disabled = true;
    control.removeAttribute('onclick');
    control.removeAttribute('oninput');
    delete control.dataset.liAction;
    control.title = 'Required data / handler not connected';
  }

  function bindMath(data) {
    const main = one('main');
    if (!main) return;
    // Capture the exported DOM once, before changing any names or values.
    if (!main.mathBindings) {
      const labels = leafElements(main);
      main.mathBindings = labels.filter(el => /^RECENT FULL-GAME AVERAGE$/i.test(el.textContent.trim())).map(label => {
        const panel = label.parentElement.parentElement.parentElement;
        const heading = findLeaf(/TEAM PROJECTION$/i, panel);
        const projection = findLeaf(/^MODEL PROJ$/i, panel);
        const history = findLeaf(/^L5 Sequence Scores/i, panel);
        return { panel, heading, average: label.parentElement.children[1],
          projection: projection && projection.nextElementSibling,
          historyLabel: history, history: history && all('.grid > div', history.parentElement) };
      });
      main.mathBindings.names = labels.filter(el => /^(Atlanta Dream|Golden State)$/i.test(el.textContent.trim()));
      main.mathBindings.badges = labels.filter(el => /^(ATL|GS)$/i.test(el.textContent.trim()));
      main.mathBindings.matchup = findLeaf(/^ATL @ GS$/i, main);
      main.mathBindings.cadenceTeams = all('tbody tr', main).slice(0, 2).map(row => row.children[0]);
      main.mathBindings.sport = findLeaf(/^WNBA$/i, main);

      // Keep every component in position, but remove all illustrative values
      // and narratives before explicitly binding supported recorded research.
      labels.forEach(el => {
        if (el.matches('.material-symbols-outlined')) return;
        const value = el.textContent.trim();
        if (/\d|ATL|GOLDEN STATE|\bGS\b|Rhyne Howard|Allisha Gray|BULLISH|UNDER BIAS|LOW STABILITY|ELEVATED|Adjust game dynamics|Bayesian|Regressor|Syndicate|SGP ALPHA|COPULA|PEARSON/i.test(value)) {
          text(el, 'SOURCE PENDING');
        }
      });
      // Numeric parent text (e.g. an average with a nested Last 5 label)
      // must be cleared at the value-cell level as well.
      main.mathBindings.forEach(binding => {
        text(binding.average, 'RECORDED AVERAGE UNAVAILABLE');
        all('span', binding.panel).forEach(el => {
          if (el.children.length && /\d/.test(Array.from(el.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent).join(''))) text(el, 'SOURCE PENDING');
        });
      });
      all('select', main).forEach(select => {
        all('option', select).forEach(option => text(option, 'MODEL INPUTS REQUIRED'));
      });
      const footer = one('footer');
      if (footer) all('span', footer).filter(el => /SOCKET:|LATENCY:|SYS HOLD:|INSTITUTIONAL ODDS ENGINE/.test(el.textContent)).forEach(el => text(el, 'SOURCE PENDING'));
      text(findLeaf(/^SIMULATED REAL-TIME EDGE:$/i, main), 'SIMULATION: INPUTS REQUIRED');
    }
    const teams = researchTeams(data);
    const bindings = main.mathBindings;
    bindings.forEach((binding, index) => {
      const team = teams[index];
      text(binding.heading, team ? `${team.team} • TEAM PROJECTION` : 'TEAM RESEARCH UNAVAILABLE');
      text(binding.average, team && finiteInput(team.average) ? `${number(team.average, 1)} PPG (RECORDED)` : 'RECORDED AVERAGE UNAVAILABLE');
      const rawProjection = team && team.math && team.math.projection;
      const side = team && team.game && (team.team === team.game.away ? 0 : team.team === team.game.home ? 1 : -1);
      const projection = Array.isArray(rawProjection) ? rawProjection[side] : rawProjection;
      text(binding.projection, finiteInput(projection) ? `${number(projection, 1)} DESCRIPTIVE` : 'INPUTS PENDING');
      text(binding.historyLabel, team && (team.values || []).some(finiteInput) ? 'RECORDED FULL-GAME HISTORY' : 'HISTORY UNAVAILABLE');
      (binding.history || []).forEach(cell => {
        const leaves = leafElements(cell);
        const value = team && (team.values || [])[Array.from(binding.history).indexOf(cell)];
        text(leaves[0], finiteInput(value) ? number(value, 1) : '—');
        text(leaves[1], finiteInput(value) ? 'RECORDED' : 'HISTORY PENDING');
      });
    });
    bindings.cadenceTeams.forEach((el, i) => text(el, teams[i] ? teams[i].team : '—'));
    text(bindings.sport, teams[0] && teams[0].sport || 'SPORT PENDING');
    bindings.names.forEach((el, i) => text(el, teams[i] ? teams[i].team : 'TEAM UNAVAILABLE'));
    bindings.badges.forEach((el, i) => text(el, teams[i] ? teams[i].team : '—'));
    text(bindings.matchup, teams.length ? teams.map(team => team.team).join(' / ') : 'RESEARCH UNAVAILABLE');
    all('button,input,select', main).forEach(disableControl);
    all('header button').forEach(disableControl);
  }


  function bindWatch(data) {
    const watch = data.watch || [];
    const ticketData = data.tickets || {};
    const main = one('main');

    for (let index = 0; index < 5; index += 1) {
      const element =
        one(`#cardSlot0${index + 1}`) ||
        all('main [class*="grid-cols-5"] > div')[index];

      if (!element) continue;

      if (!element.watchBindings) {
        const name = one('.font-headline-sm', element);
        const team = name && name.nextElementSibling;
        const sport = findLeaf(/^(NBA|CFB|NFL|NCAAB|WNBA|MLB|NHL)$/i, element);
        const line = one(`#card${index + 1}LinePrice`, element);
        const market = line && line.previousElementSibling;
        const score = one('.font-headline-xl', element);
        const leaves = leafElements(element);
        leaves.forEach(leaf => {
          if (leaf.matches('.material-symbols-outlined') || leaf.closest('button') || /^SLOT \d+$/i.test(leaf.textContent.trim())) return;
          text(leaf, 'SOURCE PENDING');
        });
        all('[style*="width"]', element).forEach(bar => { bar.style.width = '0%'; });
        // Retain chart containers without depicting fabricated probability trends.
        all('.items-end > div', element).forEach(bar => { bar.style.height = '0px'; });
        // Some exported claims are text nodes alongside an icon.
        all('div', element).forEach(el => Array.from(el.childNodes).forEach(node => {
          if (node.nodeType === 3 && /LOCKED IN TICKET/i.test(node.textContent)) node.textContent = 'TICKET STATUS: NOT SUPPLIED';
        }));
        element.watchBindings = { name, team, sport, market, score };
      }
      const target = watch[index];
      if (!target) {
        pending(element, 'EMPTY WATCH SLOT');
        element.draggable = false;
        delete element.dataset.watchIndex;
        delete element.dataset.playerId;
        Object.values(element.watchBindings).forEach(el => text(el, '—'));
        all('button', element).forEach(disableControl);
        continue;
      }
      unpending(element);
      element.draggable = true;
      element.dataset.watchIndex = index;
      element.dataset.playerId = target.player_id || target.id || '';
      const values = (target.values || []).filter(finiteInput).map(Number);
      const latest = values.length ? values[0] : null;
      const average = finiteInput(target.average) ? Number(target.average) : values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
      const refs = element.watchBindings;
      text(refs.name, target.player_name || target.name || 'WATCH TARGET');
      text(refs.team, target.team || 'TEAM NOT SUPPLIED');
      text(refs.sport, target.sport || 'SPORT NOT SUPPLIED');
      text(refs.market, target.market || 'RECORDED HISTORY');
      text(refs.score, latest == null ? '—' : number(latest, 1));

      // Normalize card-level frozen demo claims.
      leafElements(element).forEach(leaf => {
        const value = leaf.textContent.trim();

        if (/^(NBA|CFB|NFL|NCAAB|WNBA|MLB|NHL)$/i.test(value)) {
          text(leaf, target.sport || 'SPORT');

        } else if (
          /^(BOS|CHA|HOU|OKC|NCAA|MIZZ|ARK|MEM|NYJ)\s*•/i.test(value)
        ) {
          text(leaf, `${target.team || '—'} • ROLE NOT SUPPLIED`);

        } else if (
          /\b(?:BOS|CHA|HOU|NYJ|OKC|MEM|MIZZ|ARK)\b.*(?:@|VS)/i.test(value) ||
          /\b(?:@|VS)\b.*\b(?:BOS|CHA|HOU|NYJ|OKC|MEM|MIZZ|ARK)\b/i.test(value)
        ) {
          text(leaf, 'GAME CONTEXT NOT SUPPLIED');

        } else if (/^(IN-PLAY|PRE-LOCK)\s*\/\//i.test(value)) {
          text(leaf, 'GAME STATE NOT SUPPLIED');

        } else if (/^[+-]?\d+(?:\.\d+)?%\s*(EDGE|FLOW)$/i.test(value)) {
          text(leaf, 'MARKET SIGNAL: SOURCE PENDING');

        } else if (/^WEATHER HIT$/i.test(value)) {
          text(leaf, 'WEATHER: SOURCE PENDING');

        } else if (/^\d+\/\d+\s*STREAK$/i.test(value)) {
          text(leaf, `${values.length} RECORDED GAMES`);

        } else if (/^LINE:/i.test(value)) {
          text(leaf, 'LINE: DATA SOURCE PENDING');

        } else if (/STEAM DRIFT:/i.test(value)) {
          text(leaf, 'ODDS MOVEMENT: SOURCE PENDING');

        } else if (/SHARP ACTION STEAM/i.test(value)) {
          text(leaf, 'SHARP ACTION: SOURCE PENDING');

        } else if (/^\d+%\s*MONEY ON/i.test(value)) {
          text(leaf, 'MARKET SPLIT: SOURCE PENDING');

        } else if (/CRASH:\s*/i.test(value)) {
          text(leaf, 'LINE MOVEMENT: SOURCE PENDING');

        } else if (/^\+\d+(?:\.\d+)?\s*YD ADVANTAGE$/i.test(value)) {
          text(leaf, 'EDGE: LINE REQUIRED');

        } else if (/METLIFE TELEMETRY/i.test(value)) {
          text(leaf, 'WEATHER TELEMETRY: SOURCE PENDING');

        } else if (/\d+MPH\s*CROSSWIND/i.test(value)) {
          text(leaf, 'WIND: SOURCE PENDING');

        } else if (/VOLUME IMPACT/i.test(value)) {
          text(leaf, 'VOLUME IMPACT: INPUTS REQUIRED');

        } else if (/[-+]\d+(?:\.\d+)?%\s*PASS VOL/i.test(value)) {
          text(leaf, 'VOLUME CHANGE: INPUTS REQUIRED');

        } else if (/USAGE DELTA|USAGE RATE/i.test(value)) {
          text(leaf, 'USAGE: SOURCE PENDING');

        } else if (/TEAM USG|BUMP$/i.test(value)) {
          text(leaf, 'USAGE VALUE: SOURCE PENDING');

        } else if (/ROTATION$/i.test(value)) {
          text(leaf, 'ROLE / ROTATION: SOURCE PENDING');

        } else if (/MINS|MPG RUNTIME/i.test(value)) {
          text(leaf, 'MINUTES: SOURCE PENDING');

        } else if (/REDZONE CONV/i.test(value)) {
          text(leaf, 'REDZONE: SOURCE PENDING');

        } else if (/DRIVE SUCCESS RATE/i.test(value)) {
          text(leaf, 'DRIVE RATE: SOURCE PENDING');

        } else if (/MATH LAB PACE:/i.test(value)) {
          text(leaf, 'PACE MODEL: INPUTS REQUIRED');

        } else if (/SHARP CEILING/i.test(value)) {
          text(leaf, 'CEILING: INPUTS REQUIRED');

        } else if (/MINS PROJECTION/i.test(value)) {
          text(leaf, 'MINUTES PROJECTION: INPUTS REQUIRED');

        } else if (/HIT IN \d+ CONSECUTIVE/i.test(value)) {
          text(leaf, `${values.length} RECORDED GAMES`);

        } else if (/^\d+%$/i.test(value)) {
          if (
            leaf.parentElement &&
            /HIT PROBABILITY|CASH REACH/i.test(
              leaf.parentElement.textContent || ''
            )
          ) {
            text(leaf, 'PROBABILITY NOT DERIVABLE');
          }

        } else if (/POTENTIAL ASSISTS/i.test(value)) {
          text(leaf, 'PLAYER CREATION: SOURCE PENDING');

        } else if (/^\d+\s*CREATED$/i.test(value)) {
          text(leaf, 'SOURCE PENDING');

        } else if (/LOCKED IN TICKET/i.test(value)) {
          text(leaf, 'TICKET STATUS: NOT SUPPLIED');

        } else if (/OPP MATCHUP/i.test(value)) {
          text(leaf, 'MATCHUP: SOURCE PENDING');

        } else if (/EDEY OUT|RIM VAC/i.test(value)) {
          text(leaf, 'AVAILABILITY: SOURCE PENDING');

        } else if (/PROJ:/i.test(value)) {
          text(leaf, 'PROJECTION: INPUTS REQUIRED');

        } else if (/^[+-]?\d+(?:\.\d+)?%\s*EV$/i.test(value)) {
          text(leaf, 'EV: PRICE REQUIRED');

        } else if (/REBOUND CHANCE CHUNKS/i.test(value)) {
          text(leaf, 'REBOUND MODEL: INPUTS REQUIRED');

        } else if (/PER 32M/i.test(value)) {
          text(leaf, 'RATE: INPUTS REQUIRED');

        } else if (/TARGET CASHED/i.test(value)) {
          text(leaf, `${values.length} RECORDED GAMES`);

        } else if (/HIT PROBABILITY/i.test(value)) {
          text(leaf, 'HISTORICAL SAMPLE');

        } else if (/CASH REACH/i.test(value)) {
          text(leaf, `N=${values.length} RECORDED`);
        }
      });

      // Bind supported Watch history.
      text(
        one(`#card${index + 1}LinePrice`),
        'LINE: DATA SOURCE PENDING'
      );

      text(
        one(`#card${index + 1}ScoreVal`),
        latest == null ? '—' : number(latest, 1)
      );

      text(
        one(`#card${index + 1}ScoreTarget`),
        target.market || 'RECORDED HISTORY'
      );

      text(
        one(`#card${index + 1}ScoreSub`),
        `${values.length} RECORDED GAMES`
      );

      text(
        one(`#card${index + 1}Rotation`),
        average == null
          ? 'HISTORICAL CONTEXT AVAILABLE'
          : `AVG ${number(average, 1)}`
      );

      text(
        one(`#card${index + 1}HitProb`),
        `HISTORY N=${values.length}`
      );

      const bar = one(`#card${index + 1}Bar`);
      if (bar) bar.style.width = '0%';

      pending(
        one(`#card${index + 1}SteamBox`),
        'ODDS MOVEMENT SOURCE PENDING'
      );

      // Only DROP TARGET is actionable with current Watch data.
      all('button', element).forEach(button => {
        const label = button.textContent.trim();

        Object.assign(button.dataset, {
          playerId: target.player_id || target.id || '',
          playerName: target.player_name || target.name || '',
          team: target.team || '',
          sport: target.sport || '',
          market: target.market || '',
          line: '',
          odds: '',
          book: '',
          side: 'OVER'
        });

        if (/DROP TARGET/i.test(label)) {
          button.dataset.liAction = 'watch-remove';
          button.disabled = false;
          return;
        }

        delete button.dataset.liAction;
        button.disabled = true;

        if (/ADD TICKET/i.test(label)) {
          text(button, 'TICKET • LINE / PRICE REQUIRED');

        } else if (/HEDGE SLIP/i.test(label)) {
          text(button, 'HEDGE • MARKET INPUTS REQUIRED');

        } else if (/DISPATCH/i.test(label)) {
          text(button, 'DISPATCH • MARKET INPUTS REQUIRED');

        } else if (/OPEN PROPS/i.test(label)) {
          text(button, 'PROPS • SOURCE PENDING');

        } else if (/GAMECAST/i.test(label)) {
          text(button, 'GAMECAST • GAME ID REQUIRED');

        } else if (/WEATHER/i.test(label)) {
          text(button, 'WEATHER • SOURCE PENDING');

        } else if (/COMPARE/i.test(label)) {
          text(button, 'COMPARE • INPUTS REQUIRED');

        } else if (/HEAT LOG/i.test(label)) {
          text(button, 'HISTORY • RECORDED DATA ONLY');
        }
      });
    }

    // Remove frozen ticket-lock banner that is plain text inside
    // a container rather than a leaf node.
    if (main) {
      const frozenTicketLocks = Array.from(main.querySelectorAll('div'))
        .filter(element =>
          /LOCKED IN TICKET\s*#?\d+/i.test(element.textContent.trim())
        )
        .sort((a, b) =>
          a.textContent.trim().length - b.textContent.trim().length
        );

      if (frozenTicketLocks[0]) {
        text(frozenTicketLocks[0], 'TICKET STATUS: NOT SUPPLIED');
      }
    }

    // Aggregate Watch 5 metrics.
    text(one('#totalEdgeMetric'), '—');

    text(one('#ribbonSteamCount'), 'MARKET FEED PENDING');
    text(one('#pinLatency'), 'PENDING');
    text(one('#circaLatency'), 'PENDING');

    const ticker = findLeaf(/LIVE LINE TICKER/i);

    if (ticker) {
      // Replace the exported fake sportsbook ticker contents with one
      // truthful source-pending panel while retaining the WATCH 5 layout.
      let tickerRegion = ticker.parentElement;

      while (
        tickerRegion &&
        tickerRegion !== main &&
        !/STEAM DETECTED|SHARP INFLOW|VALUE ARB|HIGH LIQUIDITY/i.test(
          tickerRegion.textContent || ''
        )
      ) {
        tickerRegion = tickerRegion.parentElement;
      }

      if (tickerRegion && tickerRegion !== main) {
        const title = document.createElement('div');
        title.className = 'font-headline-sm text-headline-sm uppercase text-on-surface';
        title.textContent = 'LIVE LINE TICKER';

        const status = document.createElement('div');
        status.className = 'font-label-mono-sm text-label-mono-sm text-outline py-space-md';
        status.textContent = 'MULTI-BOOK ODDS / MOVEMENT SOURCE PENDING';

        tickerRegion.replaceChildren(title, status);
        pending(tickerRegion, 'LIVE MULTI-BOOK TICKER • DATA SOURCE PENDING');
      }
    }

    setLabelValue(
      document,
      /LIVE SLATE TRIGGERS/i,
      `${watch.length} WATCHED`
    );

    setLabelValue(
      document,
      /TOTAL TARGET EDGE/i,
      'PRICE REQUIRED'
    );

    setLabelValue(
      document,
      /RAPID STEAM ALERTS|STEAM ALERTS/i,
      'SOURCE PENDING'
    );

    const ledger = findLeaf(
      /WATCH 5 RAPID DRIFT|RAPID EXECUTION ARBITRAGE|RAPID ARB LEDGER/i
    );

    if (ledger) {
      const section =
        ledger.closest('section') ||
        ledger.parentElement && ledger.parentElement.parentElement;

      const table = section && one('table', section);

      if (table) {
        const placeholder = document.createElement('div');
        placeholder.className =
          'p-space-md bg-surface-container-low text-outline font-label-mono-sm text-label-mono-sm text-center uppercase';

        placeholder.textContent =
          'MULTI-BOOK ODDS / MOVEMENT SOURCE PENDING';

        table.replaceWith(placeholder);
        pending(placeholder, 'MULTI-BOOK ODDS / MOVEMENT SOURCE PENDING');
      }

      // Neutralize exported ledger subtitles that claim live bookmakers.
      if (section) {
        leafElements(section).forEach(element => {
          const value = element.textContent.trim();

          if (/AUTO-POLLING PINNACLE\/CIRCA/i.test(value)) {
            text(element, 'MARKET POLLING • SOURCE PENDING');

          } else if (/COLOR KEY:.*BEST EXECUTION.*RAPID STEAM/i.test(value)) {
            text(element, 'MARKET COMPARISON • SOURCE PENDING');
          }
        });
      }
    }

    const exposure = findLeaf(/^MONITORED EXPOSURE$/i);
    const exposurePanel = exposure && exposure.parentElement.parentElement;
    if (exposurePanel) {
      const cells = exposurePanel.children[1] && exposurePanel.children[1].children;
      text(cells && cells[0], finiteInput(ticketData.active_exposure) ? money(ticketData.active_exposure) : '—');
      text(cells && cells[1], finiteInput(ticketData.open) ? `${ticketData.open} OPEN TICKETS` : 'TICKET COUNT UNAVAILABLE');
    }
    leafElements(main).forEach(el => {
      if (/^SLOTS:/.test(el.textContent.trim())) text(el, `SLOTS: ${watch.length} / 5 ACTIVE`);
      if (/^RADAR BUFFER SYNCHRONIZED$/.test(el.textContent.trim())) text(el, 'WATCH HISTORY • DATABASE READ');
    });
    leafElements(document).forEach(el => {
      if (/^GREEN =|^AMBER =|^KEY SHORTCUT:|^COLOR KEY:/.test(el.textContent.trim())) text(el, 'MARKET INPUTS REQUIRED');
    });
    all('header button').forEach(disableControl);
    text(one('#ribbonCycle'), 'POLL: 15 SECONDS');
    text(one('#engineLoad'), 'NOT SUPPLIED');
    all('button,input,select', main).forEach(control => {
      if (control.dataset.liAction !== 'watch-remove') disableControl(control);
    });

    // Remove the remaining exported demo odds ledger completely.
    // All residual fake player/book names live inside this one stale table.
    const staleBookHeader = Array.from(document.querySelectorAll('th')).find(
      element => /^PINNACLE \(SHARP\)$/i.test(element.textContent.trim())
    );

    if (staleBookHeader) {
      const staleTable = staleBookHeader.closest('table');

      if (staleTable) {
        const placeholder = document.createElement('div');

        placeholder.className =
          'p-space-md bg-surface-container-low text-outline font-label-mono-sm text-label-mono-sm text-center uppercase';

        placeholder.textContent =
          'MULTI-BOOK ODDS / MOVEMENT SOURCE PENDING';

        staleTable.replaceWith(placeholder);

        pending(
          placeholder,
          'MULTI-BOOK ODDS / MOVEMENT SOURCE PENDING'
        );
      }
    }

    // Neutralize frozen Watch-wide status copy that cannot be
    // derived from the current Watch records.
    leafElements(main).forEach(element => {
      const value = element.textContent.trim();

      if (/^\d+\s+IN-PLAY$/i.test(value)) {
        text(element, 'GAME STATE NOT SUPPLIED');

      } else if (/^\d+\s+PRE-LOCK$/i.test(value)) {
        text(element, 'GAME STATE NOT SUPPLIED');

      } else if (/^NEXT EVENT STARTS/i.test(value)) {
        text(element, 'SCHEDULE CONTEXT NOT SUPPLIED');

      } else if (/HEAVY OFFSHORE STEAM/i.test(value)) {
        text(element, 'MARKET MOVEMENT SOURCE PENDING');

      } else if (/^AVG EV$/i.test(value)) {
        text(element, 'EV REQUIRES PRICE');

      } else if (/QUANT MODEL VS SHARP CONSENSUS/i.test(value)) {
        text(element, 'VERIFIED MARKET INPUTS REQUIRED');

      } else if (/^2 ACTIVE$/i.test(value)) {
        text(element, 'SOURCE PENDING');

      } else if (/^<5M DELTA$/i.test(value)) {
        text(element, 'MOVEMENT DATA REQUIRED');

      } else if (/DEDICATED OWNER EXECUTION QUEUE/i.test(value)) {
        text(
          element,
          'OWNER WATCH QUEUE • RECORDED HISTORY BOUND • MARKET DATA FAILS CLOSED'
        );
      }
    });

    // Remove remaining frozen Watch-wide market claims.
    leafElements(document).forEach(element => {
      const value = element.textContent.trim();

      if (/^[+-]?\d+(?:\.\d+)?%\s*EDGE$/i.test(value)) {
        text(element, 'EDGE: PRICE REQUIRED');

      } else if (/SHARP ACTION STEAM/i.test(value)) {
        text(element, 'SHARP ACTION: SOURCE PENDING');

      } else if (/^\d+\s*STEAM EVENTS DETECTED$/i.test(value)) {
        text(element, 'STEAM EVENTS: SOURCE PENDING');
      }
    });

    // Neutralize remaining frozen Watch ledger development / EV cells.
    Array.from(document.querySelectorAll('[id$="-dev"]')).forEach(element => {
      const value = element.textContent.trim();

      if (/^[+-]?\d+(?:\.\d+)?%\s*EV$/i.test(value)) {
        text(element, 'EV: PRICE REQUIRED');
      }
    });

    // Defensive cleanup for frozen ticket-lock narratives.
    leafElements(document).forEach(element => {
      if (/LOCKED IN TICKET\s*#?\d+/i.test(element.textContent.trim())) {
        text(element, 'TICKET STATUS: NOT SUPPLIED');
      }
    });

    // Footer is outside main.
    const footer = one('footer');

    if (footer) {
      Array.from(footer.querySelectorAll('span')).forEach(element => {
        const value = element.textContent.trim();

        if (/^INSTITUTIONAL ODDS ENGINE$/i.test(value)) {
          text(element, 'MARKET DATA ENGINE • SOURCE PENDING');

        } else if (/^SOCKET:\s*CONNECTED$/i.test(value)) {
          text(element, 'SOCKET: NOT REQUIRED');

        } else if (/^LATENCY:\s*14MS$/i.test(value)) {
          text(element, 'INGEST: DATABASE CURRENT');

        } else if (/^SYS HOLD:\s*2\.41%$/i.test(value)) {
          text(element, 'MARKET HOLD: SOURCE PENDING');
        }
      });
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
    const main = one('main');
    if (!main) return;
    const cash = value => finiteInput(value) ? money(value) : 'PENDING';
    const record = owner => owner && finiteInput(owner.wins) && finiteInput(owner.losses) ? `${owner.wins}-${owner.losses}` : 'PENDING';
    const field = (root, pattern) => findLeaf(pattern, root);
    const sibling = (root, pattern) => field(root, pattern)?.nextElementSibling;
    const write = (root, pattern, value) => text(field(root, pattern), value);
    // Save stable references before replacing demo text. No components are removed.
    if (!main.picksBindings) {
      const owner = closestCardFromHeading(field(main, /^OWNER POSITION & TICKETS$/i));
      const ownerBalance = field(owner, /^OWNER BANKROLL$/i)?.closest('.bg-surface-container-lowest');
      const ownerTicket = field(owner, /^ACTIVE TICKET/i)?.closest('.bg-surface-container');
      const specialists = ['BASKETBALL_AI', 'CFB_AI', 'NFL_AI'].map(id => {
        const card = closestCardFromHeading(field(main, new RegExp(`^${id.replace('_', ' ')}$`, 'i')));
        const heading = field(card, new RegExp(`^${id.replace('_', ' ')}$`, 'i'));
        const area = field(card, /^AI TICKET SUBMISSION$/i)?.closest('.bg-surface-container');
        const detail = area?.children[1];
        return { id, card, area, detail, bankroll: sibling(card, /^BANKROLL$/i),
          goal: field(card, /^GOAL /i), record: field(card, /^RECORD:/i),
          exposure: heading?.parentElement.parentElement.children[1],
          badge: heading?.parentElement.parentElement.parentElement.lastElementChild,
          status: area?.children[0].lastElementChild,
          sample: field(area, /^\[SAMPLE DATA\]$/i),
          name: detail && one('.font-body-md', detail),
          market: field(detail, /^MARKET:/i), line: field(detail, /^LINE:/i),
          odds: field(detail, /^\+115$|^-110$/i)?.parentElement,
          stake: field(detail, /^STAKE:/i), payout: sibling(detail, /^POTENTIAL PAYOUT$/i),
          result: sibling(detail, /^RESULT$/i),
          empty: detail && leafElements(detail).filter(el => !el.matches('.material-symbols-outlined')) };
      });
      // Both labels contain nested values; select their full span explicitly.
      const aggregateSpan = all('span', main).find(el => /^AGGREGATE RECORD:/.test(el.textContent.trim()));
      const combinedSpan = all('span', main).find(el => /^COMBINED BANKROLL:/.test(el.textContent.trim()));
      main.picksBindings = { owner, ownerBalance, ownerTicket, specialists,
        balance: ownerBalance && one('.font-headline-xl', ownerBalance),
        today: field(ownerBalance, /TODAY$/i), ownerRecord: field(ownerBalance, /^VERIFIED RECORD:/i),
        exposure: sibling(owner, /^TOTAL EXPOSURE$/i), open: sibling(owner, /^OPEN TICKETS$/i),
        aggregate: aggregateSpan, combined: combinedSpan,
        goal: sibling(main, /^GOAL CAP$/i) };

      // Clear unsupported claims at text-node level, including parent text
      // around nested spans. Retain every panel, table, SVG and layout class.
      const scrub = (root, pattern) => {
        if (!root) return;
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        const nodes = [];
        while (walker.nextNode()) nodes.push(walker.currentNode);
        nodes.forEach(node => {
          if (!node.parentElement.closest('script,style,.material-symbols-outlined') && pattern.test(node.textContent.trim())) node.textContent = 'SOURCE PENDING';
        });
      };
      const ownerName = field(owner, /^BOBBY \(MANUAL DESK\)/i);
      const trajectoryTitle = field(main, /^CUMULATIVE BANKROLL VELOCITY/i);
      const ledgerTitle = field(main, /^PROVABLE LEDGER HASHES$/i);
      main.picksBindings.ledgerEntries = Array.from(closestCardFromHeading(ledgerTitle)?.children[1]?.children || []);
      scrub(main, /\d|SAMPLE DATA|AUDITED|VERIFIED|PASSED|SYNC PROTOCOL ACTIVE|ORACLE|ZERO.KNOWLEDGE|ZK.LOGGED|PROVABLE|CRYPTOGRAPHIC|CHAINLINK|CONSENSUS INTEGRITY|HIGH CONFIDENCE|High overlap|Joint expected|closing line edge|PRE.COMMITTED|CO.SIGNED|execution buffer|copied to your active slip|PROPRIETARY CAPITAL|PENDING TIP.OFF|PENDING KICKOFF|IN.FLIGHT|SUBMITTED$/i);
      // Static demo narratives without numbers also require explicit pending copy.
      write(main, /^SYNAPSE DRIFT:$/i, 'MODEL TELEMETRY:');
      write(main, /^CHALLENGE CONTRACT:$/i, 'LEDGER SOURCE:');
      write(main, /^ACTIVE AUDITOR:$/i, 'AUDIT SOURCE:');
      write(main, /^CURRENT VERIFICATION BLOCK:$/i, 'VERIFICATION:');
      text(ownerName, 'BOBBY • REAL OWNER LEDGER');
      text(ledgerTitle, 'CHALLENGE TICKET LEDGER • TEST');
      write(main, /^WNBA AI SPECIALIST$/i, 'WNBA • DATA PENDING');
      text(one('#tabBtnRolling span:last-child'), 'ROLLING 10-GAME HIT RATE');
      // No historical curves, probability bars or trajectory samples are supplied.
      all('svg path,svg circle,svg polyline,svg polygon', main).forEach(el => { el.style.visibility = 'hidden'; });
      all('[style*="width:"]', main).forEach(el => { el.style.width = '0%'; });
      const benchmark = one('#historicalWinRateSuite');
      write(benchmark, /^SOURCE PENDING$/i, 'HISTORY / CLV PENDING');
      const correlation = field(main, /^CROSS-SPECIALIST CORRELATION ALERT$/i);
      if (correlation) {
        text(one('p', correlation.parentElement.parentElement), 'CORRELATION PENDING • ALIGNED AUTHORITATIVE TICKET SAMPLE REQUIRED');
        text(correlation, 'CROSS-SPECIALIST CORRELATION • DATA PENDING');
      }
      text(trajectoryTitle, 'CUMULATIVE BANKROLL VELOCITY • HISTORY PENDING');
      // Co-signing has no supported mutation. Keep the controls in place.
      all('button,input,select', main).forEach(control => {
        if (!['ledgerModalTrigger', 'closeLedgerModal', 'closeLedgerModalBtn'].includes(control.id)) disableControl(control);
        else control.removeAttribute('onclick');
      });
      const ledgerButton = one('#ledgerModalTrigger');
      text(ledgerButton?.querySelector('span:last-child'), 'CHALLENGE DATA STATUS');
      const modal = one('#ledgerModal');
      text(one('h2', modal), 'CHALLENGE DATA STATUS');
      text(one('p', modal), 'LOCAL LEDGER READS • SPECIALISTS ARE TEST CHALLENGES');
      const oracleButton = all('button', main).find(button => /EXPLORE PUBLIC/.test(button.textContent));
      if (oracleButton) text(one('span:last-child', oracleButton), 'EXTERNAL AUDIT • NOT CONNECTED');
      const footer = one('footer');
      all('span', footer).filter(el => /INSTITUTIONAL ODDS ENGINE|SOCKET:|LATENCY:|SYS HOLD:/.test(el.textContent)).forEach(el => text(el, 'SOURCE PENDING'));
      all('header button').forEach(disableControl);
      // The exported co-sign function only animates a fictional submission.
      window.cosignPick = () => notify('Co-signing is not connected. No ticket was created.', true);
    }
    const refs = main.picksBindings;
    const bobby = data.bobby || {};
    const specialists = data.specialists || [];
    text(refs.balance, cash(bobby.bankroll));
    text(refs.today, finiteInput(bobby.today_pl) ? `${money(bobby.today_pl)} TODAY` : 'P/L PENDING');
    text(refs.ownerRecord, `RECORD: ${record(bobby)}`);
    text(refs.exposure, cash(bobby.exposure));
    text(refs.open, finiteInput(bobby.open_count) ? `${bobby.open_count} OPEN` : 'COUNT PENDING');
    const headerRoll = field(one('header'), /^ROLL$/i);
    text(headerRoll?.nextElementSibling, cash(bobby.bankroll));
    const headerPl = field(one('header'), /^P\/L$/i);
    text(headerPl?.nextElementSibling, cash(bobby.today_pl));

    // Bind the owner ticket without fabricating legs, prices or a parlay price.
    const ownerTicket = Array.isArray(bobby.tickets) ? bobby.tickets[0] : null;
    if (refs.ownerTicket) {
      const rows = refs.ownerTicket.children;
      text(one('span:last-child', rows[0]?.firstElementChild), ownerTicket ? `ACTIVE TICKET #${ownerTicket.id}` : 'NO RECORDED OWNER TICKET');
      text(rows[0]?.lastElementChild, ownerTicket?.group || 'NO TICKET');
      const legs = ownerTicket?.legs || [];
      Array.from(rows[1]?.children || []).forEach((row, i) => {
        const leg = legs[i];
        text(row.children[0], leg ? `${leg.player || leg.team || leg.game || 'GAME'} • ${leg.market || 'MARKET PENDING'} ${leg.side || ''} ${leg.line ?? ''}` : 'NO ADDITIONAL RECORDED LEG');
        text(row.children[1], leg && finiteInput(leg.odds) ? leg.odds : 'PRICE PENDING');
      });
      if (legs.length > 2) text(rows[1].children[1].children[0], legs.slice(1).map(leg => `${leg.player || leg.team || leg.game || 'GAME'} ${leg.market || ''} ${leg.side || ''} ${leg.line ?? ''}`).join(' • '));
      const cells = rows[2]?.children;
      text(cells?.[0]?.lastElementChild, cash(ownerTicket?.stake));
      text(cells?.[1]?.lastElementChild, 'PRICE PENDING');
      text(cells?.[2]?.lastElementChild, cash(ownerTicket?.payout));
    }
    let qualifiedCount = 0;
    refs.specialists.forEach(binding => {
      const specialist = specialists.find(item => item.id === binding.id && item.available === true);
      text(binding.bankroll, cash(specialist?.bankroll));
      text(binding.record, `RECORD: ${record(specialist)}`);
      text(binding.goal, finiteInput(specialist?.goal) ? `GOAL ${money(specialist.goal)}` : 'GOAL PENDING');
      text(binding.exposure, `TEST • EXPOSURE ${cash(specialist?.exposure)}`);
      text(binding.badge, specialist ? 'TEST LEDGER' : 'DATA PENDING');
      // Backend picks() alone qualifies current picks. Ledger history is never
      // promoted to a new pick or used to invent a confidence/qualification rule.
      const pick = specialist?.pick;
      const qualified = pick && finiteInput(pick.ticket_id) && Number(pick.ticket_id) > 0 && pick.book &&
        finiteInput(pick.stake) && Number(pick.stake) > 0 && finiteInput(pick.payout) && Number(pick.payout) > 0 &&
        ((finiteInput(pick.odds) && Number(pick.odds) > 1) || (finiteInput(pick.multiplier) && Number(pick.multiplier) > 0)) &&
        Array.isArray(pick.legs) && pick.legs.length && pick.legs.every(leg => leg.sport && leg.game_id &&
          (leg.player || leg.team || leg.game) && leg.market && finiteInput(leg.line) && leg.side);
      if (qualified) qualifiedCount += 1;
      text(binding.status, qualified ? `TEST TICKET #${pick.ticket_id}` : 'NO QUALIFIED TICKET');
      text(binding.sample, qualified ? `${pick.book} • ${pick.confidence || 'CONFIDENCE PENDING'}` : 'QUALIFIED INPUTS REQUIRED');
      if (binding.name) {
        text(binding.name, qualified ? pick.legs.map(leg => leg.player || leg.team || leg.game || 'GAME').join(' / ') : 'NO QUALIFIED CURRENT OPPORTUNITY');
        text(binding.market, qualified ? `MARKET: ${pick.legs.map(leg => leg.market).join(' / ')}` : 'MARKET: PENDING');
        text(binding.line, qualified ? `LINE: ${pick.legs.map(leg => `${leg.side} ${leg.line}`).join(' / ')}` : 'LINE: PENDING');
        text(binding.odds, qualified && finiteInput(pick.odds) ? `DECIMAL ODDS: ${pick.odds}` : qualified && finiteInput(pick.multiplier) ? `MULTIPLIER: ${pick.multiplier}` : 'ODDS: PENDING');
        text(binding.stake, qualified ? `STAKE: ${money(pick.stake)}` : 'STAKE: PENDING');
        text(binding.payout, qualified ? money(pick.payout) : 'PENDING');
        text(binding.result, qualified ? 'PENDING' : 'NO TICKET');
      } else {
        text(binding.empty?.[0], qualified ? pick.legs.map(leg => `${leg.player || leg.team || leg.game || 'GAME'} • ${leg.market} ${leg.side} ${leg.line}`).join(' / ') : 'NO QUALIFIED CURRENT OPPORTUNITY');
        text(binding.empty?.[1], qualified ? `${pick.book} • STAKE ${money(pick.stake)} • PAYOUT ${money(pick.payout)} • ${finiteInput(pick.odds) ? `DECIMAL ${pick.odds}` : `MULTIPLIER ${pick.multiplier}`} • ${pick.confidence || 'CONFIDENCE PENDING'}` : 'AUTHORITATIVE QUALIFIED TICKET REQUIRED');
      }
    });
    const participants = [data.bobby, ...['BASKETBALL_AI', 'CFB_AI', 'NFL_AI'].map(id => specialists.find(item => item.id === id && item.available === true))];
    const complete = key => participants.every(item => item && finiteInput(item[key]));
    text(refs.aggregate, complete('wins') && complete('losses') ? `AGGREGATE RECORD: ${participants.reduce((sum, item) => sum + Number(item.wins), 0)}-${participants.reduce((sum, item) => sum + Number(item.losses), 0)}` : 'AGGREGATE RECORD: PENDING');
    text(refs.combined, complete('bankroll') ? `COMBINED BANKROLL: ${money(participants.reduce((sum, item) => sum + Number(item.bankroll), 0))}` : 'COMBINED BANKROLL: PENDING');
    const goals = specialists.filter(item => item.available === true).map(item => item.goal);
    text(refs.goal, goals.length === 3 && goals.every(goal => finiteInput(goal) && Number(goal) === Number(goals[0])) ? `${money(goals[0])} • TEST GOAL` : 'GOAL PENDING');
    // Recorded TEST tickets are history, not evidence of a current qualified pick.
    (refs.ledgerEntries || []).forEach((entry, index) => {
      const id = ['CFB_AI', 'BASKETBALL_AI', 'NFL_AI'][index];
      const ledger = data.ai_tickets && data.ai_tickets[id];
      const ticket = Array.isArray(ledger) ? ledger.slice().sort((a, b) => Number(b.id) - Number(a.id))[0] : null;
      const rows = entry.children;
      text(rows[0]?.children[0], `${id.replace('_', ' ')} • ${ticket ? `TICKET #${ticket.id}` : 'NO RECORDED TICKET'}`);
      text(rows[0]?.children[1], ticket?.created_at || 'TIME PENDING');
      text(rows[1]?.children[0], ticket ? `${ticket.book || 'BOOK PENDING'} • ${ticket.ticket_type || 'TYPE PENDING'} • ${ticket.number_of_legs ?? '—'} LEGS` : 'NO RECORDED CHALLENGE TICKET');
      text(rows[1]?.children[1], ticket && finiteInput(ticket.stake_cents) ? `STAKE ${money(Number(ticket.stake_cents) / 100)}` : 'STAKE PENDING');
      text(rows[2], ticket ? `LOCAL TEST LEDGER • ${ticket.ticket_result || 'RESULT PENDING'}` : 'LOCAL TEST LEDGER • HISTORY PENDING');
    });
    // Keep hidden modal fields truthful as well as visible panels.
    const modal = one('#ledgerModal');
    const statusRows = modal?.children[0]?.children[1]?.children;
    [
      ['LEDGER SOURCE:', 'LOCAL TEST CHALLENGE LEDGER'],
      ['CURRENT QUALIFICATION:', participants.slice(1).every(Boolean) ? `${qualifiedCount} QUALIFIED CURRENT TICKETS` : 'DATA PENDING'],
      ['AGGREGATE RECORD:', refs.aggregate?.textContent.replace('AGGREGATE RECORD: ', '')],
      ['COMBINED BANKROLL:', refs.combined?.textContent.replace('COMBINED BANKROLL: ', '')]
    ].forEach(([label, value], i) => {
      text(statusRows?.[i]?.firstElementChild, label);
      text(statusRows?.[i]?.lastElementChild, value);
    });
  }

  function bindTickets(data) {
    const main = one('main');
    if (!main) return;
    const cash = value => finiteInput(value) ? money(value) : 'PENDING';
    const valueAfter = (root, pattern) => findLeaf(pattern, root)?.nextElementSibling;
    const copy = (element, value) => {
      if (!element) return;
      const nodes = Array.from(element.childNodes).filter(node => node.nodeType === 3);
      if (!nodes.length) { text(element, value); return; }
      const target = nodes.find(node => node.textContent.trim()) || nodes[0];
      nodes.forEach(node => { node.textContent = node === target ? String(value) : ''; });
    };
    const legRefs = row => ({ row, name: one('.font-headline-sm,.font-body-sm', row),
      game: all('.font-body-sm', row).find(el => el !== one('.font-headline-sm,.font-body-sm', row)),
      odds: findLeaf(/^\([+-]\d+\)$/i, row), sport: findLeaf(/^(NBA|CFB|NFL)$/i, row) });
    const cardRefs = card => {
      const id = findLeaf(/^#TK-/i, card);
      const header = id?.parentElement;
      const legs = all('.bg-surface-container-low', card).map(legRefs);
      const payoutLabel = findLeaf(/^(EST\. RETURN|EST\. PAYOUT|RETURN \(NET\)|NET P\/L)$/i, card);
      return { card, id, status: header?.children[0], book: findLeaf(/^(CIRCA SPORTS|PINNACLE LIVE|DRAFTKINGS)$/i, card),
        time: findLeaf(/^PLACED |^OCT \d/i, card), stake: valueAfter(card, /^STAKE$/i),
        odds: valueAfter(card, /^ODDS$/i), payout: payoutLabel?.nextElementSibling, payoutLabel, legs };
    };
    const neutralize = root => {
      // Replace every exported card claim, including text adjacent to icons.
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      const nodes = [];
      while (walker.nextNode()) nodes.push(walker.currentNode);
      nodes.forEach(node => {
        const parent = node.parentElement;
        if (node.textContent.trim() && !parent.closest('script,style,.material-symbols-outlined') &&
            (!parent.closest('button') || /\d/.test(node.textContent)) &&
            !/^(STAKE|ODDS|EST\. RETURN|EST\. PAYOUT|RETURN \(NET\)|NET P\/L|ARBITRAGE OPPORTUNITY:)$/i.test(node.textContent.trim())) node.textContent = 'SOURCE PENDING';
      });
      all('.material-symbols-outlined', root).filter(el => /^(check_circle|verified|cancel|close|autorenew)$/.test(el.textContent.trim())).forEach(el => text(el, 'help_outline'));
      all('[style*="width:"]', root).forEach(el => { el.style.width = '0%'; });
      all('button,input,select', root).forEach(disableControl);
    };
    if (!main.ticketBindings) {
      const metrics = all('section')[1]?.querySelector('.grid');
      const kpis = metrics ? Array.from(metrics.children).map(card => ({
        label: one('span', card), value: one('.font-headline-lg', card),
        detail: one('.font-headline-lg', card)?.nextElementSibling
      })) : [];
      const streamTitle = findLeaf(/^ACTIVE TICKETS & LEDGER STREAM$/i, main);
      const stream = streamTitle?.parentElement.parentElement.parentElement;
      const cards = stream ? Array.from(stream.children).slice(1).map(cardRefs) : [];
      const tabs = all('#ticket-tabs button');
      main.ticketBindings = { kpis, stream, cards, template: cards[0]?.card.cloneNode(true),
        count: streamTitle?.nextElementSibling, cadence: valueAfter(main, /^AUTO-REFRESH:$/i),
        tabs, filter: 'OPEN', data: {}, baseCards: cards.length };
      const telemetry = findLeaf(/^REAL-TIME TELEMETRY$/i, main);
      const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
      const nodes = [];
      while (walker.nextNode()) nodes.push(walker.currentNode);
      nodes.forEach(node => {
        if (!node.parentElement.closest('script,style,.material-symbols-outlined') &&
            /\d|CIRCA|PINNACLE|DRAFTKINGS|SIGNAL LIVE|\+EV|REAL-TIME TELEMETRY|CRYPTOGRAPHIC|HASH CHAIN/i.test(node.textContent.trim())) node.textContent = 'SOURCE PENDING';
      });
      cards.forEach(binding => neutralize(binding.card));
      all('svg path,svg circle,svg polyline,svg polygon', main).forEach(el => { el.style.visibility = 'hidden'; });
      all('[style*="width:"]', main).forEach(el => { el.style.width = '0%'; });
      all('.items-end > span', main).forEach(el => { el.style.height = '0px'; });
      all('button,input,select', main).forEach(disableControl);
      all('header button').forEach(disableControl);
      all('select option', main).forEach(option => {
        if (option.value !== 'all') text(option, 'FILTER DATA PENDING');
      });
      text(telemetry, 'RECORDED LEDGER DATA');
      const hedge = all('.font-headline-sm', main).find(el => /REAL-TIME HEDGE|KELLY HEDGE/i.test(el.textContent));
      const hedgePanel = hedge?.closest('.bg-surface-container');
      if (hedgePanel) text(one('p', hedgePanel), 'VALIDATED LIVE MARKET INPUTS REQUIRED');
      const footer = one('footer');
      all('span', footer).filter(el => /INSTITUTIONAL ODDS ENGINE|SOCKET:|LATENCY:|SYS HOLD:/.test(el.textContent)).forEach(el => text(el, 'SOURCE PENDING'));
      tabs.forEach((button, index) => {
        button.addEventListener('click', event => {
          event.preventDefault(); event.stopImmediatePropagation();
          main.ticketBindings.filter = ['ALL', 'OPEN', 'WON', 'LOST', 'VOID'][index];
          bindTickets(main.ticketBindings.data);
        }, true);
      });
    }
    const refs = main.ticketBindings;
    refs.data = data;
    const supplied = Array.isArray(data.tickets?.tickets);
    const tickets = supplied ? data.tickets.tickets : [];
    const groups = ['OPEN', 'WON', 'LOST', 'VOID', 'HISTORY'];
    const open = tickets.filter(ticket => ticket.group === 'OPEN');
    const settled = tickets.filter(ticket => ['WON', 'LOST', 'VOID'].includes(ticket.group));
    const amountsKnown = list => list.every(ticket => finiteInput(ticket.stake) && Number(ticket.stake) >= 0);
    const groupsKnown = tickets.every(ticket => groups.includes(ticket.group) && ticket.group !== 'HISTORY');
    const settlementsKnown = amountsKnown(settled) && settled.every(ticket => finiteInput(ticket.actual_payout) && Number(ticket.actual_payout) >= 0);
    const authoritative = data.tickets?.aggregates_authoritative === true;
    const exposure = authoritative ? data.tickets.active_exposure : supplied && groupsKnown && amountsKnown(open) ? open.reduce((sum, ticket) => sum + Number(ticket.stake), 0) : null;
    const settledPL = authoritative ? data.tickets.settled_pl : supplied && groupsKnown && settlementsKnown ? settled.reduce((sum, ticket) => sum + Number(ticket.actual_payout) - Number(ticket.stake), 0) : null;
    text(refs.kpis[0]?.value, cash(data.bobby?.bankroll));
    copy(refs.kpis[0]?.detail, finiteInput(data.bobby?.bankroll) ? 'OWNER ACCOUNT' : 'ACCOUNT DATA REQUIRED');
    text(refs.kpis[1]?.label, 'ACTIVE EXPOSURE (RECORDED OPEN)');
    text(refs.kpis[1]?.value, cash(exposure));
    text(refs.kpis[1]?.detail, supplied ? `${open.length} OPEN TICKETS` : 'COUNT PENDING');
    text(refs.kpis[2]?.label, 'SETTLED P/L (RECORDED)');
    text(refs.kpis[2]?.value, cash(settledPL));
    text(refs.kpis[2]?.detail, supplied ? `${settled.length} SETTLED` : 'SETTLEMENTS PENDING');
    // A descriptive rate needs at least 20 fully recorded decisive settlements.
    // Voids are excluded from the denominator. No confidence or CLV is inferred.
    const decisive = settled.filter(ticket => ticket.group !== 'VOID');
    const wins = decisive.filter(ticket => ticket.group === 'WON').length;
    text(refs.kpis[3]?.value, supplied && groupsKnown && settlementsKnown && decisive.length >= 20 ? `${number(wins / decisive.length * 100, 1)}%` : 'SAMPLE PENDING');
    text(refs.kpis[3]?.detail, supplied ? `${wins}W / ${decisive.length - wins}L • MIN 20` : 'RECORD PENDING');
    text(refs.kpis[4]?.value, 'SOURCE PENDING');
    text(refs.kpis[4]?.detail, 'VERIFIED CLV REQUIRED');
    const roll = findLeaf(/^ROLL$/i, one('header'));
    text(roll?.nextElementSibling, cash(data.bobby?.bankroll));
    const pl = findLeaf(/^P\/L$/i, one('header'));
    text(pl?.nextElementSibling, cash(data.bobby?.today_pl));
    text(refs.count, supplied ? `${open.length} OPEN / ${settled.length} SETTLED • ${tickets.length} RECORDED` : 'AUTHORITATIVE TICKET SOURCE PENDING');
    text(refs.cadence, '15 SECONDS');
    refs.tabs.forEach((button, index) => {
      const group = ['ALL', 'OPEN', 'WON', 'LOST', 'VOID'][index];
      const count = group === 'ALL' ? tickets.length : tickets.filter(ticket => ticket.group === group).length;
      const label = one('span:last-child', button) || button;
      text(label, `${group} (${supplied ? count : 'PENDING'})`);
      button.disabled = !supplied;
      button.classList.toggle('bg-surface-container-highest', refs.filter === group);
      button.classList.toggle('text-on-surface', refs.filter === group);
      button.classList.toggle('font-semibold', refs.filter === group);
      button.classList.toggle('bg-surface-container-high', refs.filter !== group);
      button.classList.toggle('text-on-surface-variant', refs.filter !== group);
    });
    const visible = tickets.filter(ticket => refs.filter === 'ALL' || ticket.group === refs.filter);
    // More records reuse the exported card design; never silently omit tickets.
    while (refs.cards.length < visible.length && refs.template) {
      const card = refs.template.cloneNode(true);
      refs.cards.push(cardRefs(card)); neutralize(card); refs.stream.appendChild(card);
    }
    while (refs.cards.length > Math.max(refs.baseCards, visible.length)) refs.cards.pop().card.remove();
    refs.cards.forEach((binding, index) => {
      const ticket = visible[index];
      binding.card.dataset.recordedTicketId = ticket ? String(ticket.id) : '';
      text(binding.id, ticket ? `TICKET #${ticket.id} • ${ticket.owner || 'OWNER PENDING'}` : supplied ? `NO ${refs.filter === 'ALL' ? '' : refs.filter + ' '}RECORDED TICKET` : 'TICKET SOURCE PENDING');
      copy(binding.status, ticket?.group || 'EMPTY SLOT');
      text(binding.book, ticket?.book || 'BOOK PENDING');
      text(binding.time, ticket?.placed || ticket?.settled || 'TIME PENDING');
      text(binding.stake, cash(ticket?.stake));
      const legs = Array.isArray(ticket?.legs) ? ticket.legs : [];
      text(binding.odds, legs.length === 1 && finiteInput(legs[0].odds) ? legs[0].odds : 'PRICE PENDING');
      const isSettled = ticket && ['WON', 'LOST', 'VOID'].includes(ticket.group);
      const netKnown = isSettled && finiteInput(ticket.stake) && finiteInput(ticket.actual_payout);
      text(binding.payoutLabel, isSettled ? 'RETURN / NET' : 'POTENTIAL PAYOUT');
      text(binding.payout, isSettled ? netKnown ? `${money(ticket.actual_payout)} / ${money(Number(ticket.actual_payout) - Number(ticket.stake))}` : 'SETTLEMENT PENDING' : cash(ticket?.payout));
      const baseLegs = binding.baseLegs || (binding.baseLegs = binding.legs.length);
      while (binding.legs.length > baseLegs) binding.legs.pop().row.remove();
      while (binding.legs.length < legs.length && binding.legs[0]) {
        const row = binding.legs[0].row.cloneNode(true);
        const original = binding.legs[0];
        // Copy field positions, since labels are already rebound.
        const locate = element => element ? row.querySelectorAll('*')[Array.from(original.row.querySelectorAll('*')).indexOf(element)] : null;
        const extra = { row, name: locate(original.name), game: locate(original.game), odds: locate(original.odds), sport: locate(original.sport) };
        original.row.parentElement.appendChild(row); binding.legs.push(extra);
      }
      binding.legs.forEach((legBinding, legIndex) => {
        const leg = legs[legIndex];
        text(legBinding.name, leg ? `${leg.player || leg.team || 'RECORDED GAME'} • ${leg.market || 'MARKET PENDING'} ${leg.side || ''} ${leg.line ?? ''}` : 'NO ADDITIONAL RECORDED LEG');
        text(legBinding.game, leg?.game || 'GAME / LEG RESULT PENDING');
        text(legBinding.odds, leg && finiteInput(leg.odds) ? leg.odds : 'PRICE PENDING');
        text(legBinding.sport, leg?.sport || 'SPORT PENDING');
      });
    });
  }

  function bindIntelligence(data) {
    const main = one('main');
    if (!main) return;
    const field = (root, pattern) => findLeaf(pattern, root);
    const valueAfter = (root, pattern) => field(root, pattern)?.nextElementSibling;
    const scrub = root => {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      const nodes = [];
      while (walker.nextNode()) nodes.push(walker.currentNode);
      nodes.forEach(node => {
        if (!node.textContent.trim() || node.parentElement.closest('script,style,.material-symbols-outlined')) return;
        if (/\d|MISSOURI|DERRICK|STROUD|PINNACLE|CIRCA|CRIS|DRAFTKINGS|CORRELATION|CONFIDENCE|MODEL EV|SYNCHRONIZED QUANT|POSITIVE FLOW|WIND EXPOSURE|SHARP VOLUME|HIGH CORR|IN PLAY|PRE-LOCK|QUANT STREAM|ZERO ARBITRAGE|NO ARB LOCK|CURRENTLY IN TICKET|HALF|HIT$|MISS$|STALL|FINISHER|SURGE|STEADY|BALANCED|CLUTCH|BLOWOUT/i.test(node.textContent)) node.textContent = 'SOURCE PENDING';
      });
      all('button,input,select', root).forEach(control => {
        disableControl(control);
        control.removeAttribute('onkeydown');
        control.removeAttribute('onchange');
      });
      all('svg path,svg circle', root).forEach(el => { el.style.visibility = 'hidden'; });
      all('[style*="width:"]', root).forEach(el => { el.style.width = '0%'; });
    };
    const cardRefs = card => {
      const line = field(card, /^CURRENT LINE$/i);
      const projection = field(card, /^AI PROJECTION$/i);
      const edge = field(card, /^PROJECTED EDGE$/i);
      return { card, title: one('h3', card), rationale: one('p', card),
        sport: card.children[0]?.children[0]?.children[0], context: card.children[0]?.children[0]?.children[1],
        signal: card.children[0]?.children[0]?.children[2], status: card.children[0]?.lastElementChild,
        line: line?.nextElementSibling, price: line?.nextElementSibling?.nextElementSibling,
        projectionLabel: projection, projection: projection?.nextElementSibling,
        confidence: projection?.nextElementSibling?.nextElementSibling, edgeLabel: edge, edge: edge?.nextElementSibling,
        ev: edge?.nextElementSibling?.nextElementSibling, sample: valueAfter(card, /^SAMPLE SIZE:$/i),
        hit: valueAfter(card, /^HISTORICAL HIT:$/i),
        stage: all('button', card).find(button => /ADD(?:ED)? TO TICKET/i.test(button.textContent)),
        inspect: all('button', card).find(button => /INSPECT GAME LOG/i.test(button.textContent)),
        log: one('[id^="log-"]', card) };
    };
    const legRefs = slot => ({ slot, sport: slot.children[0]?.children[0]?.children[0],
      name: slot.children[0]?.children[0]?.children[1], remove: one('button', slot),
      market: slot.children[1]?.children[0], odds: slot.children[1]?.children[1],
      edge: slot.children[2]?.children[0], book: slot.children[2]?.children[1] });
    const fingerprint = card => JSON.stringify([card.id, card.game_id, card.player_id, card.player, card.team, card.sport, card.market, card.side, Number(card.current_line), Number(card.odds), card.book]);
    if (!main.intelligenceBindings) {
      const feed = one('#active-targets-feed');
      const empty = one('#empty-state-container');
      const builder = one('h2', one('aside', main))?.closest('.bg-surface-container');
      const slots = builder ? Array.from(builder.children[1].children).map(legRefs) : [];
      const cards = Array.from(feed?.children || []).map(cardRefs);
      const refs = main.intelligenceBindings = { feed, empty, builder, slots, cards, template: cards[0]?.card.cloneNode(true),
        baseCards: cards.length, baseSlots: slots.length, selected: [], qualified: new Map(), data: {},
        search: one('#search-input'), model: one('#model-selector'), stake: one('#ticket-stake'),
        count: field(builder, /^2 LEGS$/i), clear: one('button[title="Clear all selections"]', builder),
        odds: valueAfter(builder, /^COMBINED ODDS$/i), profit: valueAfter(builder, /^EST\. PROFIT$/i),
        payout: valueAfter(builder, /^TOTAL PAYOUT$/i), record: all('button', builder).find(button => /PLACE \/ SAVE TICKET/i.test(button.textContent)),
        researchCount: valueAfter(main, /^HEAT DETECTED$/i), researchLabel: field(main, /^HEAT DETECTED$/i),
        emptyTitle: field(empty, /^NO QUALIFIED CURRENT TARGETS$/i), emptyText: one('p', empty) };
      scrub(main);
      // Clear hidden demo tables and narratives as well as the visible feed.
      const clearCard = binding => {
        const walker = document.createTreeWalker(binding.card, NodeFilter.SHOW_TEXT);
        const nodes = []; while (walker.nextNode()) nodes.push(walker.currentNode);
        nodes.forEach(node => {
          if (node.textContent.trim() && !node.parentElement.closest('button,script,style,.material-symbols-outlined') &&
              !/^(CURRENT LINE|AI PROJECTION|PROJECTED EDGE|SAMPLE SIZE:|HISTORICAL HIT:|DATE|OPPONENT|LOC|OPP DEF|MARKET|SUPPORTING DATA:)$/i.test(node.textContent.trim())) node.textContent = 'SOURCE PENDING';
        });
        all('[data-hit],[data-hit-1h],[data-hit-2h]', binding.card).forEach(row => Array.from(row.attributes).filter(attr => attr.name.startsWith('data-')).forEach(attr => row.removeAttribute(attr.name)));
      };
      refs.clearCard = clearCard;
      cards.forEach(clearCard);
      text(refs.researchLabel, 'RECORDED RESEARCH');
      text(refs.emptyText, 'No current targets have complete verified market terms and authoritative qualification. Research history alone does not qualify a ticket.');
      const correlation = one('aside > div:last-child p', main);
      text(correlation, 'ALIGNED HISTORY AND VERIFIED COMBINED MARKET TERMS REQUIRED');
      text(one('#toggle-btn-text'), 'QUALIFICATION IS DATA-DRIVEN');
      all('input[type="checkbox"]', main).forEach(input => { input.checked = false; });
      refs.stake.value = ''; refs.stake.placeholder = 'ENTER STAKE';
      refs.search.removeAttribute('onkeydown'); refs.search.placeholder = 'FILTER REAL TEAM, PLAYER, OR MARKET';
      refs.model.value = 'all';
      all('header button').forEach(disableControl);
      const footer = one('footer');
      all('span', footer).filter(el => /INSTITUTIONAL ODDS ENGINE|SOCKET:|LATENCY:|SYS HOLD:/.test(el.textContent)).forEach(el => text(el, 'SOURCE PENDING'));
      // Exported handlers only manipulate demo data. They are never authoritative.
      ['toggleEmptyStatePreview', 'applyLogFilter', 'filterVenue', 'toggleCardLog'].forEach(name => { window[name] = () => {}; });
      refs.search.addEventListener('input', () => bindIntelligence(refs.data));
      refs.model.addEventListener('change', () => bindIntelligence(refs.data));
      main.addEventListener('input', event => {
        if (event.target !== refs.stake) return;
        event.stopImmediatePropagation(); // Suppress the frozen 3.10 multiplier simulator.
        refs.renderBuilder();
      }, true);
      refs.renderBuilder = () => {
        const selected = refs.selected.map(item => refs.qualified.get(item.id)).filter(Boolean);
        text(refs.count, `${selected.length} LEGS`);
        refs.stake.disabled = !selected.length;
        refs.clear.disabled = !selected.length;
        refs.clear.dataset.intelAction = 'clear';
        text(refs.odds, selected.length === 1 ? `${number(selected[0].odds, 3)}x DECIMAL` : selected.length ? 'COMBINED QUOTE REQUIRED' : 'PRICE REQUIRED');
        const stake = finiteInput(refs.stake.value) && Number(refs.stake.value) > 0 ? Number(refs.stake.value) : null;
        const payout = selected.length === 1 && stake != null ? Math.round(stake * Number(selected[0].odds) * 100) / 100 : null;
        text(refs.profit, payout == null ? 'INPUTS REQUIRED' : money(payout - stake));
        text(refs.payout, payout == null ? 'INPUTS REQUIRED' : money(payout));
        const recordable = selected.length === 1 && payout != null && Number.isFinite(payout) && payout >= stake && payout > 0 && state.config?.writes_enabled === true && state.config?.database === 'isolated' && state.config?.wager_execution === false;
        refs.record.disabled = !recordable || Boolean(refs.recording);
        refs.record.dataset.intelAction = 'record';
        text(one('span:last-child', refs.record), recordable ? 'RECORD ONLY • NO WAGER' : 'RECORDING • VERIFIED INPUTS REQUIRED');
        while (refs.slots.length < selected.length) {
          const slot = refs.slots[0].slot.cloneNode(true);
          refs.slots[0].slot.parentElement.appendChild(slot); refs.slots.push(legRefs(slot));
        }
        while (refs.slots.length > Math.max(refs.baseSlots, selected.length)) refs.slots.pop().slot.remove();
        refs.slots.forEach((binding, index) => {
          const card = selected[index];
          text(binding.sport, card?.sport || 'NO SELECTION');
          text(binding.name, card?.player || card?.team || 'NO QUALIFIED LEG STAGED');
          text(binding.market, card ? `${card.market} • ${card.side} ${card.current_line}` : 'VERIFIED TARGET REQUIRED');
          text(binding.odds, card ? `${card.odds} DECIMAL` : 'PRICE REQUIRED');
          text(binding.edge, card && finiteInput(card.edge) ? `DESCRIPTIVE DELTA ${number(card.edge, 2)}` : 'DELTA PENDING');
          text(binding.book, card ? `BOOK: ${card.book}` : 'BOOK PENDING');
          binding.remove.disabled = !card;
          binding.remove.dataset.intelAction = 'remove'; binding.remove.dataset.targetId = card?.id || '';
        });
        const slip = findLeaf(/^SLIP\s*\(/i, one('header')); text(slip, `SLIP (${selected.length})`);
      };
      refs.action = async button => {
        if (button.disabled || refs.recording) return;
        const action = button.dataset.intelAction;
        if (action === 'stage') {
          const card = refs.qualified.get(button.dataset.targetId);
          if (card && !refs.selected.some(item => item.id === card.id)) refs.selected.push({ id: card.id, fingerprint: fingerprint(card) });
        } else if (action === 'clear') refs.selected = [];
        else if (action === 'remove') refs.selected = refs.selected.filter(item => item.id !== button.dataset.targetId);
        else if (action === 'inspect') {
          const binding = refs.cards.find(item => item.card.dataset.targetId === button.dataset.targetId);
          binding?.log?.classList.toggle('hidden');
        } else if (action === 'record') {
          refs.recording = true; refs.renderBuilder();
          try {
            // Recheck current qualification/terms before a record-only request.
            await refreshFull();
            const card = refs.selected.length === 1 && refs.qualified.get(refs.selected[0].id);
            const stake = finiteInput(refs.stake.value) ? Number(refs.stake.value) : 0;
            if (!card || stake <= 0 || state.config?.writes_enabled !== true || state.config?.database !== 'isolated' || state.config?.wager_execution !== false) throw new Error('Current verified target, positive stake and isolated record-only mode required');
            const decimal = Number(card.odds);
            const american = decimal >= 2 ? (decimal - 1) * 100 : -100 / (decimal - 1);
            const payout = Math.round(stake * decimal * 100) / 100;
            if (!Number.isFinite(payout) || payout < stake || payout <= 0) throw new Error('Valid verified payout terms required');
            const result = await post('/api/stitch/tickets/record', { owner_name: 'Bobby', book: card.book, stake,
              potential_payout: payout,
              legs: [{ sport: card.sport, game_id: card.game_id, player_id: card.player_id || '', player: card.player || '', team: card.team || '', market: card.market, line: Number(card.current_line), side: card.side, odds: american }] });
            if (result.wager_placed !== false || result.recorded !== true) throw new Error('Record-only confirmation missing');
            refs.selected = []; refs.stake.value = '';
            notify(`Ticket #${result.ticket_id} recorded in the isolated development ledger. No wager was placed.`);
          } catch (error) { bindIntelligence({}); notify(`Ticket failed closed: ${error.message}`, true); }
          finally { refs.recording = false; refs.renderBuilder(); }
        }
        refs.renderBuilder();
      };
    }
    const refs = main.intelligenceBindings;
    refs.data = data;
    const research = Array.isArray(data.research?.cards) ? data.research.cards : [];
    // These are decimal prices from scan_offers(), never American template odds.
    const qualified = research.filter(card => card && typeof card.id === 'string' && card.qualified === true &&
      card.sport && card.game_id && (card.player_id || card.team) && (card.player || card.team) && card.market &&
      card.game && String(card.game.id) === String(card.game_id) && card.game.sport === card.sport && ['PRE', 'LIVE'].includes(card.game.status) &&
      finiteInput(card.current_line) && finiteInput(card.odds) && Number(card.odds) > 1 &&
      typeof card.book === 'string' && card.book.trim() && !/^(manual|manual_entry|test manual|unknown)$/i.test(card.book.trim()) &&
      card.heat && card.heat.milestone === false && finiteInput(card.heat.hits) && finiteInput(card.heat.total) &&
      Number(card.heat.hits) >= 4 && Number(card.heat.total) >= Number(card.heat.hits) &&
      finiteInput(card.heat.current_line) && Number(card.heat.current_line) === Number(card.current_line) &&
      finiteInput(card.heat.odds) && Number(card.heat.odds) === Number(card.odds) && card.heat.book === card.book && card.heat.side === card.side &&
      Array.isArray(card.heat.values) && card.heat.values.length === Number(card.heat.total) && card.heat.values.every(finiteInput) &&
      finiteInput(card.projection) && finiteInput(card.edge) && ((['OVER', 'MORE'].includes(card.side) && Number(card.edge) > 0) || (['UNDER', 'LESS'].includes(card.side) && Number(card.edge) < 0)));
    const unique = new Map();
    qualified.forEach(card => { if (!unique.has(card.id)) unique.set(card.id, card); });
    refs.qualified = unique;
    refs.selected = refs.selected.filter(item => unique.has(item.id) && fingerprint(unique.get(item.id)) === item.fingerprint);
    if (!refs.selected.length) refs.stake.value = '';
    text(one('#intel-opp-count'), `${unique.size} OPPORTUNIT${unique.size === 1 ? 'Y' : 'IES'}`);
    text(refs.researchCount, data.research ? `${research.length} RESEARCH CARDS` : 'RESEARCH SOURCE PENDING');
    const models = Array.isArray(data.research?.ai_filters) ? data.research.ai_filters : [];
    all('option', refs.model).forEach((option, index) => {
      option.value = index ? models[index - 1] || `pending-${index}` : 'all';
      text(option, index ? models[index - 1]?.replaceAll('_', ' ') || 'MODEL DATA PENDING' : 'ALL SUPPLIED MODELS');
      option.disabled = index > models.length;
    });
    refs.model.disabled = !models.length; refs.search.disabled = false;
    if (!models.includes(refs.model.value)) refs.model.value = 'all';
    const query = refs.search.value.trim().toLowerCase();
    const visible = Array.from(unique.values()).filter(card => (refs.model.value === 'all' || card.ai_ids?.includes(refs.model.value)) &&
      `${card.player || ''} ${card.team || ''} ${card.market || ''} ${card.sport || ''}`.toLowerCase().includes(query));
    refs.feed.classList.toggle('li-hidden', !visible.length); refs.feed.classList.toggle('hidden', !visible.length);
    refs.empty.classList.toggle('hidden', Boolean(visible.length)); refs.empty.classList.toggle('flex', !visible.length);
    text(refs.emptyTitle, unique.size ? 'NO MATCHING QUALIFIED TARGETS' : 'NO QUALIFIED CURRENT TARGETS');
    while (refs.cards.length < visible.length && refs.template) {
      const card = refs.template.cloneNode(true);
      const binding = cardRefs(card); scrub(card); refs.clearCard(binding);
      all('[id]', card).forEach(el => el.removeAttribute('id'));
      refs.cards.push(binding); refs.feed.appendChild(card);
    }
    while (refs.cards.length > Math.max(refs.baseCards, visible.length)) refs.cards.pop().card.remove();
    refs.cards.forEach((binding, index) => {
      const card = visible[index];
      binding.card.classList.toggle('li-hidden', !card);
      binding.card.dataset.targetId = card?.id || '';
      if (!card) { refs.clearCard(binding); all('button', binding.card).forEach(disableControl); return; }
      text(binding.title, `${card.player || card.team} • ${card.market} ${card.side} ${card.current_line}`);
      text(binding.sport, card.sport); text(binding.context, card.game.away && card.game.home ? `${card.game.away} @ ${card.game.home} • ${card.game.status}` : card.game.status);
      text(binding.signal, 'RECORDED RESEARCH'); text(binding.status, 'QUALIFIED • VERIFIED TERMS');
      text(binding.rationale, card.ai_read || 'AUTHORITATIVE RESEARCH • ADDITIONAL RATIONALE PENDING');
      text(binding.line, card.current_line); text(binding.price, `${card.odds} DECIMAL • ${card.book}`);
      text(binding.projectionLabel, 'DESCRIPTIVE PROJECTION'); text(binding.projection, finiteInput(card.projection) ? number(card.projection, 2) : 'PROJECTION PENDING');
      text(binding.confidence, 'CONFIDENCE: SOURCE PENDING');
      text(binding.edgeLabel, 'DESCRIPTIVE DELTA'); text(binding.edge, number(card.edge, 2)); text(binding.ev, 'EV: INPUTS REQUIRED');
      const values = Array.isArray(card.values) ? card.values : [];
      text(binding.sample, `${values.filter(finiteInput).length} RECORDED GAMES`);
      text(binding.hit, `${card.heat.hits} / ${card.heat.total} HISTORICAL • NOT A FORECAST`);
      binding.stage.disabled = false; binding.stage.dataset.intelAction = 'stage'; binding.stage.dataset.targetId = card.id;
      text(one('span:last-child', binding.stage), 'STAGE VERIFIED TARGET');
      const rows = all('tbody tr', binding.log);
      const dates = Array.isArray(card.dates) ? card.dates : [];
      all('thead tr', binding.log).forEach(row => { text(row.children[0], 'DATE'); text(row.children[1], 'RECORDED VALUE'); });
      rows.forEach((row, i) => Array.from(row.children).forEach((cell, column) => text(cell, column === 0 ? dates[i] || 'DATE PENDING' : column === 1 && finiteInput(values[i]) ? number(values[i], 1) : 'SOURCE PENDING')));
      binding.inspect.disabled = !values.some(finiteInput); binding.inspect.dataset.intelAction = 'inspect'; binding.inspect.dataset.targetId = card.id;
    });
    const cash = value => finiteInput(value) ? money(value) : 'PENDING';
    text(field(one('header'), /^ROLL$/i)?.nextElementSibling, cash(data.bobby?.bankroll));
    text(field(one('header'), /^P\/L$/i)?.nextElementSibling, cash(data.bobby?.today_pl));
    refs.renderBuilder();
  }

  function bindScreen(data) {
    const dataset = state.screen === 'live_today_scoreboard' ? data.games :
      state.screen === 'players_research_rotation_matrix' ? data.players :
      state.screen === 'heat_s_on_repeat_performers' ? data.heat?.cards : null;
    if (['live_today_scoreboard', 'players_research_rotation_matrix', 'heat_s_on_repeat_performers'].includes(state.screen) && (!Array.isArray(dataset) || !dataset.length)) {
      showDataPending(Array.isArray(dataset) ? 'NO CURRENT RECORDED DATA' : 'DATA SOURCE PENDING');
      bindCommon(data); return;
    }
    restoreDataView();
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
    if (state.screen === 'intelligence_ticket_builder') { one('main')?.intelligenceBindings?.renderBuilder(); return; }
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
    const cards = all('[data-watch-index]');
    cards.forEach(card => {
      if (card.dataset.dragWired) return;
      card.dataset.dragWired = 'true';
      card.addEventListener('dragstart', () => { state.watchDragSource = Number(card.dataset.watchIndex); });
      card.addEventListener('dragover', event => event.preventDefault());
      card.addEventListener('drop', async event => {
        event.preventDefault(); const source = state.watchDragSource; state.watchDragSource = null; const destination = Number(card.dataset.watchIndex); if (source == null || source === destination || card.dataset.watchIndex == null) return;
        const ids = all('[data-watch-index]').map(item => item.dataset.playerId); const moved = ids.splice(source, 1)[0]; ids.splice(destination, 0, moved);
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
      if (state.screen === 'intelligence_ticket_builder') {
        if (button.closest('nav')) return;
        event.preventDefault(); event.stopImmediatePropagation();
        one('main')?.intelligenceBindings?.action(button);
        return;
      }
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
        const prob = (finiteInput(button.dataset.probability) ? Number(button.dataset.probability) : NaN);
        const odds = (finiteInput(button.dataset.odds) ? Number(button.dataset.odds) : NaN);
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
        const overOdds = (finiteInput(button.dataset.overOdds) ? Number(button.dataset.overOdds) : NaN);
        const underOdds = (finiteInput(button.dataset.underOdds) ? Number(button.dataset.underOdds) : NaN);
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
        const odds = (finiteInput(button.dataset.odds) ? Number(button.dataset.odds) : NaN);
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
      if (state.screen === 'intelligence_ticket_builder') return;
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
    const data = await api(`/api/stitch/bootstrap?screen=${encodeURIComponent(state.screen)}`);
    try { bindScreen(data); state.data = data; }
    catch (error) { showDataPending('DATA BINDING UNAVAILABLE'); throw error; }
  }
  function poll() {
    // Serialize refreshes; callers await their own round, not an older in-flight read.
    const next = (state.pollQueue || Promise.resolve()).then(pollOnce);
    state.pollQueue = next.catch(() => {});
    return next;
  }
  async function pollOnce() {
    try {
      if (!state.config) state.config = await api('/api/stitch/config');
      if (!state.data || state.screen !== 'live_today_scoreboard') {
        await refreshFull();
      } else {
        const data = await api('/api/stitch/updates');
        if (data.revision !== state.revision) {
          const next = Object.assign({}, state.data, data);
          bindScreen(next); state.data = next; state.revision = data.revision;
        }
      }
    } catch (_) {
      const closeQualification = ['picks_positions_specialist_challenge', 'intelligence_ticket_builder'].includes(state.screen) || state.screen.startsWith('tickets_');
      if (state.data && closeQualification) bindScreen({});
      else if (!state.data) showDataPending('DATA SOURCE UNAVAILABLE');
      const retained = state.data && !closeQualification;
      bindCommon(retained ? { ...state.data, global: { ...state.data.global, health: { state: 'DEGRADED' } } } : { global: { health: { state: 'DEGRADED' } } });
      text(one('#liveintel-development-state'), `DEVELOPMENT STITCH • LIVE UPDATE DEGRADED • ${retained ? 'LAST AUTHORITATIVE DATA RETAINED' : 'DATA PENDING'}`);
    }
  }

  async function init() {
    addEnvironmentNotice(); bindNavigation(); bindActions();
    bindCommon({});
    if (state.screen === 'intelligence_ticket_builder') bindIntelligence({});
    if (state.screen.startsWith('tickets_')) bindTickets({});
    if (state.screen === 'picks_positions_specialist_challenge') bindPicks({});
    showDataPending('DATA SOURCE PENDING');
    state.loading = true;
    try {
      state.config = await api('/api/stitch/config');
      await refreshFull(); state.revision = state.data.generated_at;
    } catch (error) {
      showDataPending('DATA SOURCE UNAVAILABLE'); bindCommon({});
      notify(`LiveIntel data binding failed safely: ${error.message}`, true);
    } finally {
      state.loading = false;
      // Start polling after either success or failure, so cold failures recover.
      setInterval(poll, 15000);
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true }); else init();
}());
