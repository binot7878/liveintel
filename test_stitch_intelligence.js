// Intelligence browser regression. All mutations are intercepted; no DB writes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
const base = 'http://127.0.0.1:8122';
(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } });
    const errors = [], posts = [];
    let fixture = null, config = null, fail = false, bootstraps = 0;
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      const interval = window.setInterval;
      window.setInterval = (callback, ms, ...args) => {
        if (ms === 15000) window.verifyIntelPoll = callback;
        return interval(callback, ms, ...args);
      };
    });
    await page.route('**/api/stitch/bootstrap?*', async route => {
      bootstraps++;
      if (fail) return route.fulfill({ status: 503, json: { error: 'Fixture unavailable' } });
      return fixture ? route.fulfill({ json: fixture }) : route.continue();
    });
    await page.route('**/api/stitch/config', route => config ? route.fulfill({ json: config }) : route.continue());
    await page.route('**/api/stitch/tickets/record', async route => {
      posts.push(route.request().postDataJSON());
      return route.fulfill({ json: { ticket_id: 701, recorded: true, wager_placed: false } });
    });
    const values = () => page.evaluate(() => {
      const r = document.querySelector('main').intelligenceBindings;
      return { count: document.querySelector('#intel-opp-count').textContent, qualified: r.qualified.size,
        selected: r.selected.map(item => item.id), odds: r.odds.textContent, payout: r.payout.textContent,
        profit: r.profit.textContent, recordDisabled: r.record.disabled, stakeDisabled: r.stake.disabled,
        visible: r.cards.filter(c => !c.card.classList.contains('li-hidden')).map(c => ({ id: c.card.dataset.targetId, text: c.card.innerText })),
        empty: !r.empty.classList.contains('hidden') };
    });
    const ready = () => page.waitForFunction(() => window.verifyIntelPoll);
    await page.goto(`${base}/intelligence`, { waitUntil: 'domcontentloaded' }); await ready();
    await page.waitForTimeout(16000);
    assert.ok(bootstraps >= 2, 'polling refreshes authoritative research through bootstrap');
    const actual = await page.evaluate(async () => (await fetch(`/api/stitch/bootstrap?screen=${window.__STITCH_SCREEN__}`)).json());
    const actualConfig = await page.evaluate(async () => (await fetch('/api/stitch/config')).json());
    assert.equal((await values()).qualified, 0);
    assert.equal((await values()).count, '0 OPPORTUNITIES');
    assert.equal((await values()).empty, true);
    assert.equal((await values()).stakeDisabled, true);
    assert.equal((await values()).recordDisabled, true);
    const body = await page.locator('body').innerText();
    assert.doesNotMatch(body, /MISSOURI|DERRICK WHITE|STRoud|PINNACLE|CIRCA|CRIS|DRAFTKINGS|432 NODES|4\.28%|0\.038|0\.14 R|3\.10|\$155|99\.8|84%|89%|79%|5\.8%|6\.2%|14S AGO|14 MARKETS/i);
    fs.writeFileSync('/tmp/intelligence-verified.txt', body);
    await page.screenshot({ path: '/tmp/intelligence-verified.png', fullPage: true });
    const html = await (await page.request.get(`${base}/intelligence`)).text();
    assert.ok(await page.evaluate(html => {
      const source = new DOMParser().parseFromString(html, 'text/html').querySelector('main');
      return ['section', 'div', 'aside', 'button', 'input', 'select', 'table', 'tr'].every(tag => source.querySelectorAll(tag).length === document.querySelector('main').querySelectorAll(tag).length);
    }, html), 'all exported component containers remain');
    // Even synthetic stake input and exported preview functions cannot run demo math.
    await page.locator('#ticket-stake').evaluate(el => { el.value = '100'; el.dispatchEvent(new Event('input', { bubbles: true })); });
    await page.evaluate(() => window.toggleEmptyStatePreview());
    assert.equal((await values()).count, '0 OPPORTUNITIES');
    assert.equal((await values()).payout, 'INPUTS REQUIRED');
    const target = (id, sport = 'NBA', ai = 'BASKETBALL_AI') => ({ id, qualified: true, kind: 'player', player_id: id,
      player: `Verified Player ${id}`, team: `TEAM${id}`, sport, game_id: `game-${id}`, market: 'Points', side: 'OVER', current_line: 20.5,
      odds: 2.5, book: 'Verified Fixture Book', projection: 28, edge: 7.5, ai_ids: [ai],
      game: { id: `game-${id}`, sport, status: 'PRE', away: 'AAA', home: 'BBB' },
      heat: { milestone: false, hits: 4, total: 4, current_line: 20.5, odds: 2.5, book: 'Verified Fixture Book', side: 'OVER', values: [30, 29, 28, 27] },
      values: [30, 29, 28, 27], dates: ['2026-10-30', '2026-10-28', '2026-10-26', '2026-10-24'],
      ai_read: 'Recorded scoring history; descriptive baseline only.' });
    fixture = { ...actual, research: { ai_filters: ['BASKETBALL_AI', 'NFL_AI', 'CFB_AI'], cards: [target('a'), target('b', 'NFL', 'NFL_AI')] } };
    const invalids = [
      c => { c.qualified = false; }, c => { c.current_line = null; }, c => { c.odds = null; }, c => { c.odds = -110; },
      c => { c.book = ''; }, c => { c.heat = null; }, c => { c.heat.milestone = true; }, c => { c.edge = null; },
      c => { c.game.status = 'FINAL'; }, c => { c.projection = null; }, c => { c.heat.current_line = 99; }, c => { c.heat.values = []; }
    ];
    invalids.forEach((change, i) => { const c = target(`invalid-${i}`); change(c); fixture.research.cards.push(c); });
    await page.evaluate(() => window.verifyIntelPoll());
    let rendered = await values();
    assert.equal(rendered.qualified, 2); assert.equal(rendered.empty, false); assert.equal(rendered.visible.length, 2);
    assert.ok(rendered.visible.every(c => /Verified Player/.test(c.text) && /2.5 DECIMAL/.test(c.text)));
    assert.ok(rendered.visible.every(c => /CONFIDENCE: SOURCE PENDING/.test(c.text) && /EV: INPUTS REQUIRED/.test(c.text)));
    await page.locator('#search-input').fill('Player a'); assert.equal((await values()).visible.length, 1);
    await page.locator('#search-input').fill('no-match'); assert.equal((await values()).empty, true);
    await page.locator('#search-input').fill('');
    await page.locator('#model-selector').selectOption('NFL_AI'); assert.equal((await values()).visible[0].id, 'b');
    await page.locator('#model-selector').selectOption('all');
    await page.locator('[data-intel-action="inspect"][data-target-id="a"]').click();
    assert.match(await page.locator('#active-targets-feed').innerText(), /2026-10-30/);
    assert.doesNotMatch(await page.locator('#active-targets-feed').innerText(), /MISSOURI|ALABAMA|7 OF LAST 8|89%|HIGH CORRELATION/i);
    await page.locator('[data-intel-action="stage"][data-target-id="a"]').click();
    await page.locator('#ticket-stake').fill('10');
    rendered = await values(); assert.equal(rendered.payout, '$25.00'); assert.equal(rendered.profit, '$15.00');
    assert.equal(rendered.recordDisabled, actualConfig.writes_enabled !== true || actualConfig.database !== 'isolated');
    await page.locator('[data-intel-action="stage"][data-target-id="b"]').click();
    assert.equal((await values()).odds, 'COMBINED QUOTE REQUIRED'); assert.equal((await values()).payout, 'INPUTS REQUIRED');
    assert.equal((await values()).recordDisabled, true);
    await page.locator('[data-intel-action="remove"][data-target-id="b"]').click();
    assert.equal((await values()).payout, '$25.00');
    // Quote changes revoke the staged terms rather than silently repricing them.
    fixture.research.cards[0].odds = 2; fixture.research.cards[0].heat.odds = 2;
    await page.evaluate(() => window.verifyIntelPoll()); assert.deepEqual((await values()).selected, []);
    await page.locator('[data-intel-action="stage"][data-target-id="a"]').click();
    fixture.research.cards[0].qualified = false;
    await page.evaluate(() => window.verifyIntelPoll()); assert.deepEqual((await values()).selected, []);
    assert.equal((await values()).qualified, 1);
    fail = true; await page.evaluate(() => window.verifyIntelPoll());
    assert.equal((await values()).qualified, 0); assert.equal((await values()).visible.length, 0); assert.equal((await values()).empty, true);
    fail = false;
    fixture.research.cards = [target('a'), target('b'), target('c'), target('d')];
    config = { ...actualConfig, writes_enabled: true, database: 'isolated', wager_execution: false };
    await page.reload({ waitUntil: 'domcontentloaded' }); await ready();
    assert.equal((await values()).visible.length, 4);
    await page.locator('[data-intel-action="stage"][data-target-id="a"]').click();
    for (const invalid of ['', '0', '-1', '10junk']) {
      await page.locator('#ticket-stake').fill(invalid); assert.equal((await values()).recordDisabled, true);
    }
    await page.locator('#ticket-stake').fill('10'); assert.equal((await values()).recordDisabled, false);
    await page.locator('[data-intel-action="record"]').click();
    await page.waitForFunction(() => document.querySelector('main').intelligenceBindings.selected.length === 0);
    assert.equal(posts.length, 1); assert.equal(posts[0].stake, 10); assert.equal(posts[0].potential_payout, 25);
    assert.equal(posts[0].book, 'Verified Fixture Book'); assert.equal(posts[0].legs[0].player_id, 'a');
    assert.equal(posts[0].legs[0].odds, 150, 'verified decimal quote converts to American storage format');
    // Revalidation at recording time also fails closed on lost qualification.
    await page.locator('[data-intel-action="stage"][data-target-id="b"]').click();
    await page.locator('#ticket-stake').fill('10'); fail = true;
    await page.locator('[data-intel-action="record"]').click();
    await page.waitForFunction(() => document.querySelector('main').intelligenceBindings.qualified.size === 0);
    assert.equal(posts.length, 1); fail = false;
    fixture = { ...actual, research: null };
    await page.evaluate(() => window.verifyIntelPoll());
    assert.equal((await values()).qualified, 0); assert.equal((await values()).visible.length, 0); assert.deepEqual((await values()).selected, []);
    assert.equal((await values()).payout, 'INPUTS REQUIRED');
    assert.doesNotMatch(await page.locator('main').innerText(), /Verified Player|Verified Fixture Book|\$25\.00|\$15\.00/i);
    await page.evaluate(() => document.body.dispatchEvent(new ClipboardEvent('paste', { bubbles: true, clipboardData: new DataTransfer() })));
    assert.deepEqual((await values()).selected, []);
    assert.equal(await page.locator('main button:enabled').count(), 0);
    assert.deepEqual(errors, []);
    console.log('PASS INTELLIGENCE: 16 seconds; no page errors; real qualification, full components, decimal pricing, filters/history, staging, record-only guards, polling and stale-data clearing; POST intercepted');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
