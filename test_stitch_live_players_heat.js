// UI acceptance for the three slate/history routes. All writes are intercepted.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const base = 'http://127.0.0.1:8122';
(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const errors = [], writes = [];
  try {
    for (const name of ['live', 'players', 'heat']) {
      const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } });
      let fixture, revision = 0, refreshes = 0;
      page.on('pageerror', error => errors.push(`${name}: ${error.message}`));
      page.on('console', message => { if (message.type() === 'error') errors.push(`${name}: ${message.text()}`); });
      await page.addInitScript(() => {
        const interval = window.setInterval;
        window.setInterval = (callback, ms, ...args) => { if (ms === 15000) window.uiPoll = callback; return interval(callback, ms, ...args); };
      });
      await page.route('**/api/stitch/**', async route => {
        if (route.request().method() !== 'GET') { writes.push(route.request().url()); return route.fulfill({ status: 403, json: { error: 'Read only' } }); }
        if (/\/bootstrap\?|\/updates/.test(route.request().url())) {
          refreshes++;
          if (fixture) return route.fulfill({ json: { ...fixture, revision: `ui-${++revision}` } });
        }
        return route.continue();
      });
      const response = await page.goto(`${base}/${name}`, { waitUntil: 'domcontentloaded' });
      assert.equal(response.status(), 200);
      await page.waitForFunction(name => document.querySelector('main')?.[`${name === 'players' ? 'players' : name === 'heat' ? 'heat' : 'live'}UI`] && window.uiPoll, name, { timeout: 90000 });
      const screen = await page.evaluate(() => window.__STITCH_SCREEN__);
      const actual = await (await page.request.get(`${base}/api/stitch/bootstrap?screen=${screen}`, { timeout: 90000 })).json();
      const countBefore = refreshes;
      await page.waitForTimeout(16500);
      assert.ok(refreshes > countBefore, `${name}: real polling remains active`);
      const body = await page.locator('body').innerText();
      assert.doesNotMatch(body, /48 SECONDS AGO|7 OF 18|14MS|2\.41%|PINNACLE|CRIS SYNCHRONIZED|STEAM > 1\.5|r = \+0\.71|\+11\.4% EDGE|Cross-book arbitrage check running|412 DETECTED|DATA CADENCE: REAL-TIME/);
      assert.equal(await page.locator('nav a[href]').count(), 8);
      assert.equal(await page.locator('main [onclick]').count(), 0);
      await page.screenshot({ path: `/tmp/${name}-real-ui-verified.png`, fullPage: true });
      if (name !== 'live') {
        const html = await (await page.request.get(`${base}/${name}`)).text();
        assert.ok(await page.evaluate(html => {
          const source = new DOMParser().parseFromString(html, 'text/html').querySelector('main');
          return ['section', 'div', 'button', 'select', 'input', 'svg'].every(tag => source.querySelectorAll(tag).length === document.querySelector('main').querySelectorAll(tag).length);
        }, html), `${name}: exported components remain`);
      }
      if (name === 'live') {
        assert.equal(await page.locator('.matchup-card[data-game-id]').count(), actual.games.length);
        assert.equal(await page.locator('.li-source-pending:has(.matchup-card)').count(), 0, 'market overlay cannot cover game workspace');
        assert.equal(await page.locator('#syncTimer').innerText(), '15s');
        await page.locator('[data-sport-filter="cfb"]').click();
        assert.equal(await page.locator('.matchup-card:visible[data-sport="nba"]').count(), 0);
        await page.locator('[data-sport-filter="all"]').click();
        assert.equal(await page.locator('.matchup-card:visible').count(), actual.games.length);
        fixture = structuredClone(actual); fixture.games = [{ ...actual.games[0], id: 'ui-game', away: 'UIA', home: 'UIB', status: 'LIVE', away_score: 17, home_score: 21 }];
        await page.evaluate(() => window.uiPoll());
        const card = await page.locator('.matchup-card[data-game-id="ui-game"]').innerText();
        assert.match(card, /UIA/); assert.match(card, /UIB/); assert.match(card, /17/); assert.match(card, /21/);
        assert.equal(await page.locator('.matchup-card[data-game-id]').count(), 1);
      } else if (name === 'players') {
        assert.match(body, new RegExp(`OF ${actual.players.length} MATCHING PLAYERS`));
        assert.ok(body.includes(`${actual.games[0].away} @ ${actual.games[0].home}`));
        assert.doesNotMatch(body, /\bAW\b|\bPP\b/);
        const next = page.getByRole('button', { name: /NEXT/ });
        await next.click(); assert.match(await page.locator('main').innerText(), /PAGE 2 OF/);
        await page.getByRole('button', { name: /PREV/ }).click(); assert.match(await page.locator('main').innerText(), /PAGE 1 OF/);
        const search = page.locator('#player-filter-input');
        await search.fill(actual.players.at(-1).name); assert.ok((await page.locator('tbody').innerText()).includes(actual.players.at(-1).name));
        await search.fill('');
        await page.locator('tbody tr').nth(3).click();
        assert.equal(await page.evaluate(() => document.querySelector('main').playersUI.selected), String(actual.players[3].player_id));
        fixture = structuredClone(actual); fixture.players = [{ ...actual.players[0], name: 'UI Recorded Player', team: 'UIA' }]; fixture.games = [];
        await page.evaluate(() => window.uiPoll());
        const updated = await page.locator('main').innerText();
        assert.match(updated, /UI Recorded Player/i); assert.match(updated, /OF 1 MATCHING PLAYERS/); assert.match(updated, /MATCHUP PENDING/);
        assert.doesNotMatch(updated, new RegExp(actual.players[0].name, 'i'));
        await search.fill('no matching player'); assert.match(await page.locator('main').innerText(), /OF 0 MATCHING PLAYERS/);
        await search.fill(''); assert.match(await page.locator('main').innerText(), /UI Recorded Player/i);
        assert.equal(await page.getByRole('button', { name: 'CSV', exact: true }).isDisabled(), true);
      } else {
        assert.ok((await page.locator('#players-grid').innerText()).includes(actual.heat.cards.find(c => c.kind !== 'team').player.toUpperCase()));
        await page.locator('#seg-btn-teams').click();
        assert.equal(await page.locator('#teams-grid').isVisible(), true);
        assert.doesNotMatch(await page.locator('#teams-grid').innerText(), /O 28\.5|-112|10\/10|r =|EDGE|BOSTON CELTICS/);
        assert.equal(await page.locator('#teams-grid button:enabled').count(), 0);
        fixture = structuredClone(actual);
        const team = { kind: 'team', team: 'UIT', player: 'UI Recorded Team', sport: 'NBA', market: 'Recorded wins', hits: 3, total: 4, values: [10, -2, 7, 4] };
        fixture.heat.cards = [actual.heat.cards[0], team]; fixture.heat.total = 2;
        await page.evaluate(() => window.uiPoll());
        const teamText = await page.locator('#teams-grid').innerText();
        assert.match(teamText, /UI Recorded Team/i); assert.match(teamText, /3 \/ 4 RECORDED/); assert.match(teamText, /75\.0% HIST/); assert.match(teamText, /AVG: 4\.8/);
        fixture.heat.cards = [actual.heat.cards[0]]; fixture.heat.total = 1;
        await page.evaluate(() => window.uiPoll()); assert.doesNotMatch(await page.locator('#teams-grid').innerText(), /UI Recorded Team/i);
        await page.locator('#seg-btn-players').click(); assert.equal(await page.locator('#players-grid').isVisible(), true);
        const drawerText = await page.locator('#sgp-betslip-drawer').textContent();
        assert.doesNotMatch(drawerText, /17\.1%|330|45\.54|14\.2%|0\.64|0\.71|LaMelo|Derrick White/);
        assert.equal(await page.locator('#sgp-betslip-drawer button:enabled,#sgp-betslip-drawer input:enabled,#sgp-correlation-engine button:enabled').count(), 0);
        assert.equal(await page.locator('#mins-range-slider').isDisabled(), true);
      }
      await page.screenshot({ path: `/tmp/${name}-ui-verified.png`, fullPage: true });
      console.log(`PASS ${name}: real data, 16.5s dwell, polling, controls, stale-value guards`);
      await page.close();
    }
    assert.deepEqual(errors, []); assert.deepEqual(writes, []);
    console.log('PASS LIVE / PLAYERS / HEAT UI acceptance; no page/console errors or writes');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
