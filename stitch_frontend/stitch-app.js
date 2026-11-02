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
    const detailPanels = detailGrid ? Array.from(detailGrid.children) : [];
    const playerDetail = detailPanels[0] || null;
    const rotationDetail = detailPanels[1] || null;
    if (playerDetail) pending(playerDetail, 'SELECTED PLAYER DETAIL • CHOOSE A REAL MATRIX ROW');
    if (rotationDetail) pending(rotationDetail, 'MATCHUP ROTATION DETAIL • REAL ROW SELECTION REQUIRED');
    detailPanels.slice(2).forEach(panel => pending(panel, 'REAL DATA BINDING PENDING'));
    const rows = all('tbody tr');
    rows.forEach((row, index) => {
      const player = players[index];
      if (!player) { pending(row, 'NO ADDITIONAL CURRENT PLAYER'); return; }
      unpending(row); row.dataset.playerId = player.player_id; row.dataset.sport = player.sport; row.dataset.team = player.team; row.dataset.playerIndex = String(index);
      const cells = Array.from(row.children); const market = playerMarket(player); const window = player.windows[market] || {};

      if (cells[0]) {
        cells[0].replaceChildren();

        const primary = document.createElement('div');
        primary.textContent = player.name || 'PLAYER';

        const secondary = document.createElement('div');
        secondary.textContent = `${player.team || '—'}${player.game ? ` • ${player.game}` : ''}`;

        cells[0].append(primary, secondary);
      }

      const replaceCell = (cell, value) => {
        if (!cell) return;
        cell.replaceChildren(document.createTextNode(String(value)));
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

        if (!player) {
          pending(card, 'NO ADDITIONAL CURRENT PLAYER');
          return;
        }

        unpending(card);
        card.dataset.playerId = player.player_id || '';
        card.dataset.team = player.team || '';
        card.dataset.sport = player.sport || '';

        const market = playerMarket(player);
        const window = (player.windows || {})[market] || {};
        const values = (window.values || []).slice(0, 5);
        const statName = titleStat(market);

        const leaves = leafElements(card);

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

        if (progress) {
          progress.replaceChildren();

          const bar = document.createElement('div');
          bar.className = 'bg-primary h-full';
          bar.style.width = values.length ? '100%' : '0%';
          progress.appendChild(bar);
        }
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
            .map(value => Number(value))
            .filter(value => Number.isFinite(value));
          const maximum = Math.max(...numericValues, 1);

          boxes.forEach((box, index) => {
            const boxLeaves = leafElements(box);
            const value = Number(values[index]);
            const available = Number.isFinite(value);

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
      row.addEventListener('click', event => {
        if (event.target.closest('button')) return;
        const index = Number(row.dataset.playerIndex);
        if (Number.isInteger(index) && players[index]) {
          renderPlayerDetail(players[index]);
        }
      });
    });

    if (players.length) renderPlayerDetail(players[0]);

    const applyPlayerFilters = () => {
      const query = (one('#player-filter-input')?.value || '').trim().toLowerCase();
      const activeRole = document.querySelector('[data-player-role-filter].li-active-role')?.dataset.playerRoleFilter || 'all';

      rows.forEach(row => {
        const matchesText = !query || row.textContent.toLowerCase().includes(query);
        const role = (row.dataset.playerRole || '').toLowerCase();
        const matchesRole =
          activeRole === 'all' ||
          (activeRole === 'starter' && role === 'starter') ||
          (activeRole === 'bench-rotation' && role === 'bench / rotation') ||
          (activeRole === 'unknown' && role === 'role not supplied');
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
        label === 'bench' ? 'bench-rotation' :
        label === 'rotation' ? 'unknown' : 'all';

      if (label === 'bench') button.textContent = 'BENCH / ROT';
      if (label === 'rotation') button.textContent = 'ROLE UNKNOWN';

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

    const sortSelect = Array.from(document.querySelectorAll('select')).find(select =>
      Array.from(select.options).some(option => /PROP HEAT \(\+EV\)|USAGE RATE|MINUTES SHIFT|L5 HIT RATE/i.test(option.textContent))
    );

    if (sortSelect) {
      Array.from(sortSelect.options).forEach(option => {
        if (/PROP HEAT \(\+EV\)|USAGE RATE/i.test(option.textContent)) {
          option.disabled = true;
          option.textContent = option.textContent.replace(/\s*\(\+EV\)/i, '') + ' — SOURCE PENDING';
        } else if (/MINUTES SHIFT/i.test(option.textContent)) {
          option.value = 'minutes';
          option.textContent = 'MINUTES';
        } else if (/L5 HIT RATE/i.test(option.textContent)) {
          option.value = 'l5';
          option.textContent = 'L5 AVG';
        }
      });

      const firstSupported = Array.from(sortSelect.options).find(option => !option.disabled);
      if (firstSupported) sortSelect.value = firstSupported.value;

      sortSelect.addEventListener('change', () => {
        const mode = sortSelect.value;
        const tbody = rows[0]?.parentElement;
        if (!tbody) return;

        rows
          .slice()
          .sort((a, b) => {
            const ai = Number(a.dataset.playerIndex || 0);
            const bi = Number(b.dataset.playerIndex || 0);
            const ap = players[ai] || {};
            const bp = players[bi] || {};

            if (mode === 'minutes') {
              return (Number(bp.minutes) || -1) - (Number(ap.minutes) || -1);
            }

            if (mode === 'l5') {
              const am = playerMarket(ap);
              const bm = playerMarket(bp);
              const av = Number((ap.windows?.[am] || {}).l5);
              const bv = Number((bp.windows?.[bm] || {}).l5);
              return (Number.isFinite(bv) ? bv : -1) - (Number.isFinite(av) ? av : -1);
            }

            return ai - bi;
          })
          .forEach(row => tbody.appendChild(row));

        applyPlayerFilters();
      });
    }

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
    text(
      one('#player-active-count-badge'),
      `SHOWING ${Math.min(cards.length, heat.cards.length)} OF ${heat.total} RECORDED HISTORY PATTERNS`
    );

    // Remove frozen demo sportsbook / pattern-count claims from the shell.
    leafElements(document.querySelector('main')).forEach(element => {
      const value = element.textContent.trim();

      if (/REAL BOOK SYNC:/i.test(value)) {
        text(element, 'MARKET PRICES: SOURCE PENDING');
      } else if (/^STREAKS \(\d+\)$/i.test(value)) {
        text(element, `RECORDED PATTERNS (${heat.total || 0})`);
      } else if (/^BOOK: MULTI-SYNTHESIS$/i.test(value)) {
        text(element, 'BOOK: SOURCE PENDING');
      }
    });

    // The frozen correlation bar contains illustrative market totals/covariance.
    // Keep the component, but remove claims until verified market inputs exist.
    const correlationBar = one('#total-ou-correlation-bar');
    if (correlationBar) {
      pending(correlationBar, 'MARKET CORRELATION • MULTI-BOOK SOURCE PENDING');

      leafElements(correlationBar).forEach(element => {
        const value = element.textContent.trim();

        if (/GAME TOTAL O\/U CORRELATION:/i.test(value)) {
          text(element, 'GAME TOTAL / HISTORY CORRELATION');
        } else if (/CLUSTERS GAME-TOTAL CORRELATED/i.test(value)) {
          text(element, 'CORRELATION: VERIFIED MARKET INPUTS REQUIRED');
        } else if (/GAME TOTAL:|TOTAL OU:/i.test(value)) {
          text(element, 'GAME TOTAL: MARKET SOURCE PENDING');
        } else if (/MARKET DENSITY:/i.test(value)) {
          text(element, 'MARKET DENSITY: SOURCE PENDING');
        } else if (/PINNACLE|CRIS|SYNDICATE LINE HOOK/i.test(value)) {
          text(element, 'MARKET SOURCE: PENDING');
        }
      });

      all('button', correlationBar).forEach(button => {
        button.disabled = true;
        button.removeAttribute('onclick');
      });
    }

    // The frozen SGP engine is illustrative only. Do not expose fake odds,
    // edge, covariance, presets, or actionable sportsbook controls.
    const sgpEngine = one('#sgp-correlation-engine');
    if (sgpEngine) {
      pending(sgpEngine, 'SGP MARKET / PRICE / ALIGNED CORRELATION INPUTS PENDING');

      leafElements(sgpEngine).forEach(element => {
        const value = element.textContent.trim();

        if (/AUTOMATED SGP CORRELATION ENGINE/i.test(value)) {
          text(element, 'SGP CORRELATION ENGINE • INPUTS PENDING');
        } else if (/QUANT MULTI-LEG SYNERGY CALCULATOR/i.test(value)) {
          text(element, 'VERIFIED MARKET / COVARIANCE INPUTS REQUIRED');
        } else if (/ACTIVE SGP SLIP:/i.test(value)) {
          text(element, 'SGP SLIP: MARKET INPUTS REQUIRED');
        } else if (/EDGE|FAIR ODDS|BOOK|ACTIVE CLUSTERS|COVARIANCE/i.test(value)) {
          text(element, 'SOURCE PENDING');
        }
      });

      all('button', sgpEngine).forEach(button => {
        button.disabled = true;
        button.removeAttribute('onclick');
      });
    }

    // Player-card correlation/SGP/footer claims are frozen demo content.
    cards.forEach(cardElement => {
      const content = cardElement.children[1];

      if (content) {
        Array.from(content.querySelectorAll('span, div')).forEach(element => {
          const value = element.textContent.trim();

          if (/TOTAL O\/U CORR:/i.test(value) && element.children.length <= 1) {
            text(element, 'MARKET CORRELATION: SOURCE PENDING');
          }
        });

        leafElements(content).forEach(element => {
          const value = element.textContent.trim();

          if (/TOTAL O\/U CORR:/i.test(value)) {
            text(element, 'MARKET CORRELATION: SOURCE PENDING');
          } else if (/BULLISH OVER|DEF COVARIANCE|BEARISH UNDER/i.test(value)) {
            text(element, 'CORRELATION PENDING');
          } else if (/hits occurred when|overs hit when|hits accompanied|defensive slogs/i.test(value)) {
            text(element, 'VERIFIED MARKET HISTORY REQUIRED');
          } else if (/\+\d+(?:\.\d+)?% EDGE|\+EV EDGE|PLUS MONEY EV|STEAM DETECTED/i.test(value)) {
            text(element, 'MARKET EDGE: PRICE REQUIRED');
          } else if (/VS OPP RANK|PERIMETER USAGE|REB DEFENSE|PACE UP MATCHUP|STARTING ROLE \(/i.test(value)) {
            text(element, 'MATCHUP DERIVATION: SOURCE PENDING');
          }
        });
      }

      const actionBar = cardElement.children[2];
      const buttons = actionBar ? all('button', actionBar) : [];

      buttons.forEach((button, buttonIndex) => {
        button.removeAttribute('onclick');

        if (buttonIndex === 0) {
          button.dataset.liAction = 'watch-add';
        } else {
          delete button.dataset.liAction;
          button.disabled = true;
          text(button, buttonIndex === 1 ? 'SGP: INPUTS REQUIRED' : 'TICKET: LINE / PRICE REQUIRED');
        }
      });
    });

    // Remove remaining frozen SGP preset narratives and top/floating demo claims.
    if (sgpEngine) {
      Array.from(sgpEngine.querySelectorAll('p')).forEach(element => {
        if (/LaMelo|Derrick White|Chet Holmgren|Over \d|Under \d/i.test(element.textContent)) {
          text(element, 'PRESET LEGS UNAVAILABLE • VERIFIED MARKET INPUTS REQUIRED');
        }
      });

      Array.from(sgpEngine.querySelectorAll('span')).forEach(element => {
        const value = element.textContent.trim();

        if (/FAIR ODDS:/i.test(value)) {
          text(element, 'ODDS: SOURCE PENDING');
        } else if (/\+\d+(?:\.\d+)?% EDGE/i.test(value)) {
          text(element, 'EDGE: PRICE REQUIRED');
        }
      });
    }

    const sgpBarButton = one('#btn-open-sgp-drawer-bar');
    if (sgpBarButton) {
      sgpBarButton.disabled = true;
      sgpBarButton.removeAttribute('onclick');
      text(sgpBarButton, 'SGP SLIP • VERIFIED MARKET INPUTS REQUIRED');
    }

    const floatingSgpButton = one('#btn-toggle-betslip-drawer');
    if (floatingSgpButton) {
      floatingSgpButton.disabled = true;
      floatingSgpButton.removeAttribute('onclick');
      text(floatingSgpButton, 'SGP DRAFT • INPUTS REQUIRED');
    }

    const drawer = one('#sgp-betslip-drawer');
    if (drawer) {
      drawer.dataset.liveintelDraft = 'true';

      const wagerInput = one('#sgp-wager-input');
      if (wagerInput) {
        wagerInput.disabled = true;
        wagerInput.value = '';
        wagerInput.placeholder = 'PRICE REQUIRED';
      }

      leafElements(drawer).forEach(element => {
        const value = element.textContent.trim();

        if (/QUANT PROTOCOL ACTIVE|AUTO-PAIRING/i.test(value)) {
          text(element, 'MARKET INPUTS PENDING');
        } else if (/CORRELATED EDGE|JOINT TRUE PROB|IMP:|PAIRWISE CORRELATION MATRIX|SYNCHRONIZED/i.test(value)) {
          text(element, 'SOURCE PENDING');
        } else if (/LaMelo Ball|Derrick White|Chet Holmgren|Game Total/i.test(value)) {
          text(element, 'DEMO LEG REMOVED');
        } else if (/Over \d|Under \d|[-+]\d{3}|CORR EDGE|SGP COVARIANCE|r =/i.test(value)) {
          text(element, 'SOURCE PENDING');
        } else if (/COMBINED PARLAY ODDS:/i.test(value)) {
          text(element, 'COMBINED ODDS: SOURCE PENDING');
        } else if (/EST\. TOTAL PAYOUT:/i.test(value)) {
          text(element, 'EST. PAYOUT: PRICE REQUIRED');
        } else if (/NET QUANT EDGE VALUE:/i.test(value)) {
          text(element, 'EDGE VALUE: PRICE REQUIRED');
        }
      });

      all('button', drawer).forEach(button => {
        if (/TRANSMIT ORDER TO SPORTSBOOK/i.test(button.textContent)) {
          button.disabled = true;
          button.removeAttribute('onclick');
          text(button, 'SPORTSBOOK TRANSMISSION NOT CONNECTED');
        }
      });
    }
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
      if (state.screen === 'picks_positions_specialist_challenge') {
        try { await refreshFull(); }
        catch (_) {
          bindPicks({});
          text(one('#liveintel-development-state'), 'DEVELOPMENT STITCH • PICKS UPDATE DEGRADED • DATA PENDING');
        }
        return;
      }
      if (/^(math_lab_|watch_5_)/.test(state.screen)) { await refreshFull(); return; }
      const data = await api('/api/stitch/updates'); if (data.revision !== state.revision) { state.revision = data.revision; state.data = Object.assign({}, state.data, data); bindScreen(state.data); }
    } catch (_) { const notice = one('#liveintel-development-state'); if (notice) notice.textContent = 'DEVELOPMENT STITCH • LIVE UPDATE DEGRADED • LAST DATA RETAINED'; }
  }

  async function init() {
    addEnvironmentNotice(); bindNavigation(); bindActions();
    if (state.screen === 'picks_positions_specialist_challenge') bindPicks({});
    try {
      [state.config, state.data] = await Promise.all([api('/api/stitch/config'), api(`/api/stitch/bootstrap?screen=${encodeURIComponent(state.screen)}`)]);
      bindScreen(state.data); state.revision = state.data.generated_at;
      setInterval(poll, 15000);
    } catch (error) { notify(`LiveIntel data binding failed safely: ${error.message}`, true); }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true }); else init();
}());
