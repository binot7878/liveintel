// Cross-route safety regression; API writes are blocked/intercepted.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const base = 'http://127.0.0.1:8122';
const screens = {
  live: 'live_today_scoreboard', players: 'players_research_rotation_matrix', heat: 'heat_s_on_repeat_performers',
  math: 'math_lab_quantitative_edge_engine', watch: 'watch_5_rapid_target_radar_2',
  picks: 'picks_positions_specialist_challenge', tickets: 'tickets_settlement_portfolio_ledger', intelligence: 'intelligence_ticket_builder'
};
(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  const errors = [], writes = [];
  const create = async (name, source, failure = false) => {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } });
    const control = { page, source, configFail: failure, bootstrapFail: failure, bootstraps: 0, updates: 0 };
    page.on('pageerror', error => errors.push(`${name}: ${error.message}`));
    await page.addInitScript(() => {
      const interval = window.setInterval;
      window.setInterval = (callback, ms, ...args) => {
        if (ms === 15000) window.sharedPoll = callback;
        return interval(callback, ms, ...args);
      };
    });
    await page.route('**/api/stitch/**', async route => {
      if (route.request().method() !== 'GET') {
        writes.push(route.request().url());
        return route.fulfill({ status: 403, json: { error: 'Read-only shared regression' } });
      }
      if (route.request().url().endsWith('/config') && control.configFail) return route.fulfill({ status: 503, json: { error: 'Config unavailable' } });
      if (route.request().url().includes('/bootstrap?')) {
        control.bootstraps++;
        return route.fulfill({ status: control.bootstrapFail ? 503 : 200, json: control.bootstrapFail ? { error: 'Bootstrap unavailable' } : control.source });
      }
      if (route.request().url().includes('/updates')) {
        return route.fulfill({ status: control.bootstrapFail ? 503 : 200, json: control.bootstrapFail ? { error: 'Updates unavailable' } : { ...control.source, revision: `shared-${++control.updates}` } });
      }
      return route.continue();
    });
    await page.goto(`${base}/${name}`, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => window.sharedPoll);
    return control;
  };
  const poll = control => control.page.evaluate(() => window.sharedPoll());
  const header = page => page.evaluate(() => {
    const refs = document.querySelector('header');
    const leaf = pattern => Array.from(refs.querySelectorAll('*')).find(el => !el.children.length && pattern.test(el.textContent.trim()));
    return { roll: leaf(/^ROLL$/i)?.nextElementSibling?.textContent, pl: leaf(/^P\/L$/i)?.nextElementSibling?.textContent, text: refs.innerText };
  });
  const safe = async page => {
    const body = await page.locator('body').innerText();
    assert.doesNotMatch(body, /MISSOURI|DERRICK WHITE|LAMELO BALL|CHET HOLMGREN|C\.J\. STROUD|ATLANTA DREAM|GOLDEN STATE|PINNACLE|CIRCA|DRAFTKINGS|FANDUEL|14MS|18ms|2\.41%|99\.8%|\$100\.00|\+310|80\.8|72\.2|64\.8%|500MS|SEQ #|100,000 ITER|CONFIDENCE\s*\d|SHA-256/i);
    assert.equal((await header(page)).roll, 'PENDING'); assert.equal((await header(page)).pl, 'PENDING');
    assert.doesNotMatch((await header(page)).text, /FEED LIVE|DB CURRENT|OCT 31/);
    assert.equal(await page.locator('button:enabled,input:enabled,select:enabled').count(), 0);
    assert.equal(await page.locator('nav a[href]').count(), 8);
  };
  try {
    const sources = {};
    const requests = await browser.newContext();
    for (const [name, screen] of Object.entries(screens)) {
      sources[name] = await (await requests.request.get(`${base}/api/stitch/bootstrap?screen=${screen}`)).json();
      sources[name].global = { ...sources[name].global, bankroll: null, pl: null };
      sources[name].bobby = { ...sources[name].bobby, bankroll: null, today_pl: null };
    }
    await requests.close();
    const names = Object.keys(screens);
    for (let start = 0; start < names.length; start += 2) {
      await Promise.all(names.slice(start, start + 2).map(async name => {
        const control = await create(name, sources[name], true);
        await control.page.waitForTimeout(16000); // Timers must not regenerate startup values.
        await safe(control.page);
        control.configFail = false;
        await poll(control); await safe(control.page); // Config succeeds, bootstrap still fails.
        control.bootstrapFail = false;
        await poll(control);
        assert.equal((await header(control.page)).roll, 'PENDING'); assert.equal((await header(control.page)).pl, 'PENDING');
        assert.doesNotMatch(await control.page.locator('footer').innerText(), /14MS|2\.41%|CONNECTED\b/);
        assert.ok(control.bootstraps >= 2, `${name}: initial failure retries and recovers`);
        control.source.global.bankroll = control.source.bobby.bankroll = 0;
        control.source.global.pl = control.source.bobby.today_pl = 0;
        await poll(control);
        assert.equal((await header(control.page)).roll, '$0.00'); assert.equal((await header(control.page)).pl, '$0.00');
        control.source.global.bankroll = control.source.bobby.bankroll = 777.25;
        control.source.global.pl = control.source.bobby.today_pl = -4.5;
        await poll(control);
        assert.equal((await header(control.page)).roll, '$777.25'); assert.equal((await header(control.page)).pl, '-$4.50');
        control.source.global.bankroll = control.source.bobby.bankroll = null;
        control.source.global.pl = control.source.bobby.today_pl = null;
        await poll(control);
        assert.equal((await header(control.page)).roll, 'PENDING'); assert.equal((await header(control.page)).pl, 'PENDING');
        console.log(`PASS ${name}: initial config/bootstrap failures, 16 seconds, retry recovery and truthful shared header/footer`);
        await control.page.close();
      }));
    }
    for (const name of ['players', 'heat']) {
      const source = structuredClone(sources[name]);
      const entries = (name === 'players' ? source.players : source.heat.cards).slice(0, 2);
      entries.forEach((entry, i) => { entry.name = `Shared ${i ? 'Beta' : 'Alpha'}`; entry.player = entry.name; });
      if (name === 'players') source.players = entries;
      else source.heat = { ...source.heat, cards: entries, total: 2 };
      const control = await create(name, source);
      // Wait until the first bootstrap has actually bound, independent of the interval hook.
      await control.page.waitForFunction(() => document.querySelector('main').textContent.includes('Shared Alpha'));
      const before = control.bootstraps;
      entries[0].name = entries[0].player = 'Shared Gamma';
      if (name === 'players') source.players = [entries[0]];
      else source.heat.cards = [entries[0]];
      await poll(control);
      assert.ok(control.bootstraps > before, `${name}: polls bootstrap without relying on revision changes`);
      assert.match(await control.page.locator('main').innerText(), /Shared Gamma/i);
      assert.doesNotMatch(await control.page.locator('main').innerText(), /Shared Beta/i);
      if (name === 'players') source.players = [];
      else source.heat.cards = [];
      await poll(control);
      assert.doesNotMatch(await control.page.locator('main').innerText(), /Shared Alpha|Shared Beta|Shared Gamma/i);
      assert.match(await control.page.locator('main').innerText(), /NO CURRENT RECORDED DATA/);
      if (name === 'players') source.players = entries;
      else source.heat.cards = entries;
      await poll(control);
      assert.match(await control.page.locator('main').innerText(), /Shared Gamma/i);
      assert.match(await control.page.locator('main').innerText(), /Shared Beta/i);
      control.bootstrapFail = true; await poll(control);
      assert.match(await control.page.locator('main').innerText(), /Shared Gamma/i);
      assert.doesNotMatch((await header(control.page)).text, /FEED LIVE|DB CURRENT/);
      console.log(`PASS ${name}: bootstrap refresh, shrinking/disappearing datasets, recovery and last-authoritative-data retention`);
      await control.page.close();
    }
    // Consumers use corrected backend aggregates, including nullable/incomplete sums.
    for (const name of ['watch', 'tickets', 'picks']) {
      const source = structuredClone(sources[name]);
      source.tickets = { tickets: [], aggregates_authoritative: true, open: 1, open_count: 1, settled: 1, active_exposure: 25, settled_pl: 40 };
      source.bobby = { ...source.bobby, exposure: 25, open_count: 1, settled_pl: 40 };
      const control = await create(name, source);
      await control.page.waitForFunction(() => document.querySelector('main').textContent.includes('$25.00'));
      const text = await control.page.locator('main').innerText(); assert.match(text, /\$25\.00/);
      if (name === 'tickets') assert.match(text, /\$40\.00/);
      else assert.match(text, /1 OPEN/);
      source.tickets.active_exposure = null; source.tickets.settled_pl = null; source.bobby.exposure = null;
      await poll(control);
      assert.doesNotMatch(await control.page.locator('main').innerText(), /\$25\.00|\$40\.00/);
      await control.page.close();
    }
    assert.deepEqual(errors, []); assert.deepEqual(writes, []);
    console.log('PASS shared cross-route safety, polling and aggregate consumers; no page errors or writes');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
