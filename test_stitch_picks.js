// PICKS-only browser regression. Fixtures intercept reads; no database writes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
const base = 'http://127.0.0.1:8122';
(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } });
    const errors = [];
    const mutations = [];
    let fixture = null;
    let bootstraps = 0;
    let failBootstrap = false;
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (request.method() === 'POST') mutations.push(request.url()); });
    await page.addInitScript(() => {
      const interval = window.setInterval;
      window.setInterval = (callback, ms, ...args) => {
        if (ms === 15000) window.verifyPicksPoll = callback;
        return interval(callback, ms, ...args);
      };
    });
    await page.route('**/api/stitch/bootstrap?*', async route => {
      bootstraps += 1;
      if (failBootstrap) return route.fulfill({ status: 503, json: { error: 'Fixture source unavailable' } });
      return fixture ? route.fulfill({ json: fixture }) : route.continue();
    });
    await page.goto(`${base}/picks`, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => window.verifyPicksPoll);
    await page.waitForTimeout(16000);
    assert.ok(bootstraps >= 2, 'PICKS polls fresh Bobby and challenge bootstrap data');
    const actual = await page.evaluate(async () => (await fetch(`/api/stitch/bootstrap?screen=${window.__STITCH_SCREEN__}`)).json());
    const actualText = await page.locator('body').innerText();
    assert.doesNotMatch(actualText, /115-63|34-19|41-21|22-14|18-9-1|1,240|2,850|3,420|1,980|9,490|Derrick White|Missouri Tigers|Mizzou|71\.2|64\.2|28\.4|78\.2|0\.941|2\.84|0x[\da-f]+|ORACLE|ZK-LOGGED|ZERO-KNOWLEDGE|CRYPTOGRAPHIC|Joint expected|AUDITED DATASET|SAMPLE DATA/i);
    const values = () => page.evaluate(() => {
      const r = document.querySelector('main').picksBindings;
      return { bankroll: r.balance.textContent, exposure: r.exposure.textContent, open: r.open.textContent,
        aggregate: r.aggregate.textContent, combined: r.combined.textContent,
        specialists: r.specialists.map(s => ({ id: s.id, bankroll: s.bankroll.textContent, record: s.record.textContent,
          exposure: s.exposure.textContent, status: s.status.textContent, detail: s.detail.textContent })) };
    });
    const initial = await values();
    assert.equal(initial.bankroll, actual.bobby.bankroll == null ? 'PENDING' : Number(actual.bobby.bankroll).toLocaleString('en-US', { style: 'currency', currency: 'USD' }));
    for (const specialist of actual.specialists.filter(s => s.available)) {
      const rendered = initial.specialists.find(s => s.id === specialist.id);
      assert.equal(rendered.bankroll, Number(specialist.bankroll).toLocaleString('en-US', { style: 'currency', currency: 'USD' }));
      assert.equal(rendered.record, `RECORD: ${specialist.wins}-${specialist.losses}`);
      if (!specialist.pick) assert.equal(rendered.status, 'NO QUALIFIED TICKET');
    }
    assert.ok(await page.locator('main svg path, main svg circle').evaluateAll(nodes => nodes.every(n => getComputedStyle(n).visibility === 'hidden')));
    assert.ok(await page.locator('main [style*="width:"]').evaluateAll(nodes => nodes.every(n => n.style.width === '0%')));
    // All component containers and controls remain; only their contents bind.
    const servedHtml = await (await page.request.get(`${base}/picks`)).text();
    assert.ok(await page.evaluate(html => {
      const template = new DOMParser().parseFromString(html, 'text/html');
      return ['div', 'table', 'svg', 'button', 'section'].every(tag => document.querySelector('main').querySelectorAll(tag).length === template.querySelector('main').querySelectorAll(tag).length);
    }, servedHtml));
    fs.writeFileSync('/tmp/picks-verified.txt', actualText);
    await page.screenshot({ path: '/tmp/picks-verified.png', fullPage: true });

    fixture = structuredClone(actual);
    fixture.bobby = { name: 'BOBBY', bankroll: 525.5, exposure: 25, open_count: 1, today_pl: -3, wins: 2, losses: 1,
      tickets: [{ id: 71, group: 'OPEN', stake: 25, payout: 47, legs: [{ player: 'Owner Fixture', market: 'Points', line: 21.5, side: 'OVER', odds: -110 }] }] };
    fixture.specialists.forEach((s, i) => Object.assign(s, { available: true, bankroll: 100 + i * 10, exposure: 5 + i, wins: i, losses: 1, pick: null }));
    await page.evaluate(() => window.verifyPicksPoll());
    let rendered = await values();
    assert.equal(rendered.bankroll, '$525.50');
    assert.equal(rendered.exposure, '$25.00');
    assert.equal(rendered.open, '1 OPEN');
    assert.equal(rendered.aggregate, 'AGGREGATE RECORD: 5-4');
    assert.equal(rendered.combined, 'COMBINED BANKROLL: $855.50');
    assert.match(await page.locator('main').innerText(), /Owner Fixture/);
    assert.match(await page.locator('main').innerText(), /TEST • EXPOSURE \$5\.00/);
    // Historical pending ledger entries must not become qualified current picks.
    fixture.ai_tickets.BASKETBALL_AI = [{ id: 92, ticket_result: 'PENDING', stake_cents: 1500, created_at: '2026-11-01T12:00:00Z', book: 'Fixture Book', ticket_type: 'STRAIGHT', number_of_legs: 1 }];
    await page.evaluate(() => window.verifyPicksPoll());
    rendered = await values();
    assert.ok(rendered.specialists.every(s => s.status === 'NO QUALIFIED TICKET'));
    assert.match(await page.locator('main').innerText(), /TICKET #92/);
    const qualified = id => ({ ticket_id: id, book: 'Fixture Book', stake: 15, payout: 28.5, odds: '1.9', confidence: 'NORMAL',
      legs: [{ player: 'Qualified Fixture', sport: 'NBA', game_id: 'fixture', market: 'Points', side: 'OVER', line: 22.5 }] });
    fixture.specialists.find(s => s.id === 'BASKETBALL_AI').pick = qualified(92);
    fixture.specialists.find(s => s.id === 'NFL_AI').pick = { ...qualified(93), odds: null, multiplier: '2.5' };
    await page.evaluate(() => window.verifyPicksPoll());
    rendered = await values();
    assert.equal(rendered.specialists.find(s => s.id === 'BASKETBALL_AI').status, 'TEST TICKET #92');
    assert.match(rendered.specialists.find(s => s.id === 'BASKETBALL_AI').detail, /DECIMAL ODDS: 1\.9/);
    assert.match(rendered.specialists.find(s => s.id === 'NFL_AI').detail, /MULTIPLIER 2\.5/);
    fixture.specialists.find(s => s.id === 'BASKETBALL_AI').pick.odds = '';
    await page.evaluate(() => window.verifyPicksPoll());
    assert.equal((await values()).specialists.find(s => s.id === 'BASKETBALL_AI').status, 'NO QUALIFIED TICKET');
    failBootstrap = true;
    await page.evaluate(() => window.verifyPicksPoll());
    assert.equal((await values()).bankroll, 'PENDING');
    assert.ok((await values()).specialists.every(s => s.status === 'NO QUALIFIED TICKET'));
    assert.match(await page.locator('#liveintel-development-state').textContent(), /DATA PENDING/);
    failBootstrap = false;
    fixture = { ...actual, bobby: {}, specialists: [], ai_tickets: {} };
    await page.evaluate(() => window.verifyPicksPoll());
    rendered = await values();
    assert.equal(rendered.bankroll, 'PENDING');
    assert.equal(rendered.exposure, 'PENDING');
    assert.equal(rendered.open, 'COUNT PENDING');
    assert.equal(rendered.aggregate, 'AGGREGATE RECORD: PENDING');
    assert.equal(rendered.combined, 'COMBINED BANKROLL: PENDING');
    assert.ok(rendered.specialists.every(s => s.bankroll === 'PENDING' && s.record === 'RECORD: PENDING' && s.status === 'NO QUALIFIED TICKET'));
    assert.doesNotMatch(await page.locator('main').innerText(), /Qualified Fixture|Owner Fixture|\$525\.50|TEST TICKET #92/);
    assert.equal(await page.locator('header button:enabled').count(), 0);
    assert.deepEqual(await page.locator('main button:enabled').evaluateAll(buttons => buttons.map(b => b.id).sort()), ['closeLedgerModal', 'closeLedgerModalBtn', 'ledgerModalTrigger']);
    await page.locator('#ledgerModalTrigger').click();
    await page.locator('#ledgerModal').waitFor({ state: 'visible' });
    assert.doesNotMatch(await page.locator('#ledgerModal').innerText(), /ORACLE|CHAINLINK|0x|SHA-256|CRYPTOGRAPHIC|COHERENT/);
    await page.locator('#closeLedgerModalBtn').click();
    await page.locator('#ledgerModal').waitFor({ state: 'hidden' });
    await page.evaluate(() => { document.querySelector('[onclick]')?.click(); window.cosignPick(); });
    assert.deepEqual(mutations, []);
    assert.deepEqual(errors, []);
    console.log('PASS PICKS: 16 seconds, no page errors, real data, full component structure, missing-data clearing, qualification, ledger history, polling and safe actions');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
