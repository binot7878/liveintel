// Browser regression: run against the local Stitch development server.
// API mutations are intercepted; the database is never written.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const fs = require('node:fs');
const base = 'http://127.0.0.1:8122';

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    for (const route of ['math-lab', 'watch-5']) {
      const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      // Keep normal polling and expose its callback for data-transition checks.
      await page.addInitScript(() => {
        const interval = window.setInterval;
        window.setInterval = (callback, ms, ...args) => {
          if (ms === 15000) window.verifyPoll = callback;
          return interval(callback, ms, ...args);
        };
      });
      const requests = [];
      let fixture = null;
      let bootstraps = 0;
      await page.route('**/api/stitch/bootstrap?*', async request => {
        bootstraps += 1;
        if (fixture) return request.fulfill({ json: fixture });
        return request.continue();
      });
      await page.route('**/api/stitch/watch/**', async request => {
        requests.push({ path: new URL(request.request().url()).pathname, body: request.request().postDataJSON() });
        return request.fulfill({ json: { success: true } });
      });
      await page.goto(`${base}/${route}`, { waitUntil: 'domcontentloaded' });
      await page.waitForFunction(() => window.verifyPoll);
      await page.waitForTimeout(16000); // Real elapsed time exceeds the demo timer window.
      assert.ok(bootstraps >= 2, `${route}: normal polling must refresh bootstrap`);
      const actual = await page.evaluate(async () => (await fetch(`/api/stitch/bootstrap?screen=${window.__STITCH_SCREEN__}`)).json());
      const visible = await page.locator('body').innerText();
      assert.doesNotMatch(visible, /500MS|SEQ #|14\.2%|78\.5|72\.2|80\.8|18-14 SU|20-12 ATS|Bayesian|Syndicate Consensus/);
      fs.writeFileSync(`/tmp/${route}-verified.txt`, visible);
      await page.screenshot({ path: `/tmp/${route}-verified.png`, fullPage: true });
      if (route === 'math-lab') {
        assert.equal(await page.locator('main input:enabled, main select:enabled, main button:enabled').count(), 0);
        // Dispatch even synthetic input events: no exported simulator may survive.
        await page.locator('#pace-slider').evaluate(el => { el.value = 88; el.dispatchEvent(new Event('input')); });
        assert.equal(await page.locator('#sim-atl-score').textContent(), 'SOURCE PENDING');
        const team = (name, projection) => ({ team: name, sport: 'NBA', game_id: 'verify', market: 'Team score', average: 101, values: [100, 102], math: { projection } });
        fixture = { ...actual, research: { cards: [team('VERIFY AWAY', 109.5), team('VERIFY HOME', 112)] } };
        await page.evaluate(() => window.verifyPoll());
        assert.deepEqual(await page.evaluate(() => document.querySelector('main').mathBindings.map(x => x.projection.textContent)), ['109.5 DESCRIPTIVE', '112.0 DESCRIPTIVE']);
        fixture.research.cards.forEach(card => { card.math.projection = [98, 104]; card.game = { away: 'VERIFY AWAY', home: 'VERIFY HOME' }; });
        await page.evaluate(() => window.verifyPoll());
        assert.deepEqual(await page.evaluate(() => document.querySelector('main').mathBindings.map(x => x.projection.textContent)), ['98.0 DESCRIPTIVE', '104.0 DESCRIPTIVE']);
        fixture = { ...actual, research: null };
        await page.evaluate(() => window.verifyPoll());
        assert.deepEqual(await page.evaluate(() => document.querySelector('main').mathBindings.map(x => x.projection.textContent)), ['INPUTS PENDING', 'INPUTS PENDING']);
        assert.doesNotMatch(await page.locator('main').innerText(), /PPG|123\.4|RECORDED\n|VERIFY AWAY|ATL DREAM|GS WARRIORS/);
        // Calculator handlers accept only complete explicit numeric inputs.
        await page.evaluate(() => {
          const button = document.createElement('button'); button.id = 'verify-calculator';
          document.querySelector('main').appendChild(button);
        });
        const calculations = [];
        await page.route('**/api/stitch/calculate', async request => {
          calculations.push(request.request().postDataJSON());
          return request.fulfill({ json: { available: false, fail_closed: true } });
        });
        for (const [action, inputs, expected] of [
          ['calc-kelly', { probability: '0.6', odds: '-110' }, 'kelly'],
          ['calc-no-vig', { overOdds: '-110', underOdds: '-105' }, 'no_vig_probability'],
          ['calc-implied', { odds: '+120' }, 'implied_probability']
        ]) {
          const count = calculations.length;
          await page.evaluate(({ action }) => { const b = document.querySelector('#verify-calculator'); for (const key of Object.keys(b.dataset)) delete b.dataset[key]; b.dataset.liAction = action; b.click(); }, { action });
          await page.waitForTimeout(100);
          assert.equal(calculations.length, count, 'missing calculator inputs must fail closed');
          await page.evaluate(({ action, inputs }) => { const b = document.querySelector('#verify-calculator'); Object.assign(b.dataset, inputs, { liAction: action }); b.click(); }, { action, inputs });
          await page.waitForTimeout(100);
          assert.equal(calculations.at(-1).kind, expected);
        }
      } else {
        assert.ok(await page.locator('[data-watch-index] .items-end > div').evaluateAll(bars => bars.length > 0 && bars.every(bar => getComputedStyle(bar).height === '0px')));
        fixture = { ...actual, watch: [
          { player_id: 'verify-a', name: 'Target Alpha', team: 'AAA', sport: 'NFL', market: 'Passing Yards', values: [210, 200] },
          { player_id: 'verify-b', name: 'Target Beta', team: 'BBB', sport: 'NBA', market: 'Points', values: [22, 26] }
        ], tickets: { active_exposure: 37.25, open: 2 } };
        await page.evaluate(() => window.verifyPoll());
        assert.match(await page.locator('main').innerText(), /\$37\.25\n2 OPEN TICKETS/);
        const identities = () => page.evaluate(() => Array.from(document.querySelectorAll('[data-watch-index]')).map(card => [card.watchBindings.name.textContent, card.watchBindings.team.textContent, card.watchBindings.market.textContent]));
        assert.deepEqual(await identities(), [['Target Alpha', 'AAA', 'Passing Yards'], ['Target Beta', 'BBB', 'Points']]);
        fixture.watch.reverse();
        await page.evaluate(() => window.verifyPoll());
        assert.deepEqual(await identities(), [['Target Beta', 'BBB', 'Points'], ['Target Alpha', 'AAA', 'Passing Yards']]);
        // Repeated refreshes must not duplicate drag listeners.
        await page.evaluate(() => window.verifyPoll());
        await page.locator('#cardSlot01').dispatchEvent('dragstart');
        await page.locator('#cardSlot02').dispatchEvent('drop');
        await page.waitForTimeout(200);
        assert.deepEqual(requests.filter(x => x.path.endsWith('/reorder')).map(x => x.body.player_ids), [['verify-a', 'verify-b']]);
        await page.locator('#cardSlot01 button[data-li-action="watch-remove"]').click();
        await page.waitForTimeout(200);
        assert.equal(requests.find(x => x.path.endsWith('/remove')).body.player_id, 'verify-b');
        assert.equal(await page.locator('main button:enabled').count(), 2);
        assert.equal(await page.locator('header button:enabled').count(), 0);
        fixture.watch = [];
        fixture.tickets = {};
        await page.evaluate(() => window.verifyPoll());
        assert.equal(await page.locator('[data-watch-index]').count(), 0);
        assert.equal(await page.locator('main button:enabled').count(), 0);
      }
      assert.deepEqual(errors, [], `${route} page errors`);
      console.log(`PASS ${route}: 16 seconds, polling, supported data, missing inputs, controls and transitions; no page errors`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
