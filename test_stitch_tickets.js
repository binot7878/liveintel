// TICKETS browser regression. Fixture reads never mutate the database.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
const base = 'http://127.0.0.1:8122';
(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } });
    const errors = [], writes = [];
    let fixture = null, failBootstrap = false, bootstraps = 0;
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (request.method() !== 'GET') writes.push(request.url()); });
    await page.addInitScript(() => {
      const interval = window.setInterval;
      window.setInterval = (callback, ms, ...args) => {
        if (ms === 15000) window.verifyTicketPoll = callback;
        return interval(callback, ms, ...args);
      };
    });
    await page.route('**/api/stitch/bootstrap?*', async route => {
      bootstraps += 1;
      if (failBootstrap) return route.fulfill({ status: 503, json: { error: 'Fixture unavailable' } });
      return fixture ? route.fulfill({ json: fixture }) : route.continue();
    });
    await page.goto(`${base}/tickets`, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => window.verifyTicketPoll);
    await page.waitForTimeout(16000);
    assert.ok(bootstraps >= 2, 'normal polling must refresh bankroll and authoritative tickets');
    const actual = await page.evaluate(async () => (await fetch(`/api/stitch/bootstrap?screen=${window.__STITCH_SCREEN__}`)).json());
    const values = () => page.evaluate(() => {
      const r = document.querySelector('main').ticketBindings;
      return { kpis: r.kpis.map(k => k.value.textContent), count: r.count.textContent,
        cards: r.cards.map(c => ({ id: c.card.dataset.recordedTicketId, text: c.card.innerText })), tabs: r.tabs.map(b => b.textContent.trim()) };
    });
    const initial = await values();
    assert.equal(initial.kpis[0], actual.bobby.bankroll == null ? 'PENDING' : Number(actual.bobby.bankroll).toLocaleString('en-US', { style: 'currency', currency: 'USD' }));
    if (!actual.tickets.tickets.length) {
      assert.equal(initial.kpis[1], '$0.00');
      assert.equal(initial.kpis[2], '$0.00');
      assert.ok(initial.cards.every(c => !c.id && /NO OPEN RECORDED TICKET/.test(c.text)));
    }
    const visible = await page.locator('body').innerText();
    assert.doesNotMatch(visible, /10,480|1,840\.50|64\.8%|78\.4%|84920|84918|84882|84871|Derrick White|C\.J\. Stroud|LaMelo Ball|Aaron Wiggins|Minnesota Vikings|99\.8%|SHA-256|CRYPTOGRAPHIC|HASH CHAIN|SIGNAL LIVE|1 SEC|12\.4%|18\.2% ROI|112\.40/);
    assert.ok(await page.locator('main svg circle,main svg path').evaluateAll(nodes => nodes.every(n => getComputedStyle(n).visibility === 'hidden')));
    assert.ok(await page.locator('main [style*="width:"]').evaluateAll(nodes => nodes.every(n => n.style.width === '0%')));
    assert.ok(await page.locator('main .items-end > span').evaluateAll(nodes => nodes.every(n => getComputedStyle(n).height === '0px')));
    const html = await (await page.request.get(`${base}/tickets`)).text();
    assert.ok(await page.evaluate(html => {
      const source = new DOMParser().parseFromString(html, 'text/html').querySelector('main');
      return ['section', 'div', 'button', 'select', 'input', 'svg'].every(tag => source.querySelectorAll(tag).length === document.querySelector('main').querySelectorAll(tag).length);
    }, html), 'all exported components remain');
    fs.writeFileSync('/tmp/tickets-verified.txt', visible);
    await page.screenshot({ path: '/tmp/tickets-verified.png', fullPage: true });
    const leg = i => ({ player: `Recorded Player ${i}`, sport: 'NBA', game_id: `game-${i}`, market: 'Points', side: 'OVER', line: 20 + i, odds: -110, game: 'AAA @ BBB' });
    const ticket = (id, group, stake, payout, actual_payout) => ({ id, owner: 'Bobby', group, book: 'Recorded Book', stake, payout, actual_payout,
      placed: '2026-11-02T01:00:00Z', settled: group !== 'OPEN' ? '2026-11-02T02:00:00Z' : null, legs: [leg(id)] });
    fixture = { ...actual, bobby: { bankroll: 812.75, today_pl: 12 }, tickets: {
      // Deliberately inconsistent legacy aggregate values must not leak into UI.
      active_exposure: 99999, settled_pl: 88888,
      tickets: [ticket(1, 'OPEN', 25, 47, null), ticket(2, 'WON', 40, 80, 80), ticket(3, 'LOST', 20, 38, 0), ticket(4, 'VOID', 10, 19, 10)]
    } };
    await page.evaluate(() => window.verifyTicketPoll());
    let rendered = await values();
    assert.equal(rendered.kpis[0], '$812.75');
    assert.equal(rendered.kpis[1], '$25.00');
    assert.equal(rendered.kpis[2], '$20.00');
    assert.equal(rendered.kpis[3], 'SAMPLE PENDING');
    assert.equal(rendered.kpis[4], 'SOURCE PENDING');
    assert.deepEqual(rendered.cards.filter(c => c.id).map(c => c.id), ['1']);
    await page.locator('#ticket-tabs button').nth(0).click();
    rendered = await values();
    assert.deepEqual(rendered.cards.map(c => c.id), ['1', '2', '3', '4']);
    assert.ok(rendered.cards.every(c => /Recorded Player/i.test(c.text) && /Recorded Book/i.test(c.text)), JSON.stringify(rendered.cards));
    assert.match(rendered.cards[1].text, /\$80\.00 \/ \$40\.00/);
    assert.match(rendered.cards[2].text, /\$0\.00 \/ -\$20\.00/);
    await page.locator('#ticket-tabs button').nth(2).click();
    assert.deepEqual((await values()).cards.filter(c => c.id).map(c => c.id), ['2']);
    // Overflow tickets and multi-leg records reuse the same exported design.
    fixture.tickets.tickets = Array.from({ length: 6 }, (_, i) => ticket(10 + i, 'OPEN', 5, 9.5, null));
    fixture.tickets.tickets[0].legs = [leg(1), leg(2), leg(3)];
    await page.locator('#ticket-tabs button').nth(0).click();
    await page.evaluate(() => window.verifyTicketPoll());
    rendered = await values();
    assert.equal(rendered.cards.filter(c => c.id).length, 6);
    assert.match(rendered.cards[0].text, /Recorded Player 3/);
    assert.doesNotMatch(await page.locator('main').innerText(), /112\.40|84920|Derrick White|Missouri/);
    fixture.tickets.tickets.reverse();
    await page.evaluate(() => window.verifyTicketPoll());
    assert.deepEqual((await values()).cards.map(c => c.id), ['15', '14', '13', '12', '11', '10']);
    assert.doesNotMatch((await values()).cards[0].text, /Recorded Player 3\b/);
    // A minimum of 20 complete decisive settlements; voids excluded from rate.
    fixture.tickets.tickets = Array.from({ length: 20 }, (_, i) => ticket(100 + i, i < 12 ? 'WON' : 'LOST', 10, 20, i < 12 ? 20 : 0));
    fixture.tickets.tickets.push(ticket(200, 'VOID', 10, 20, 10));
    await page.evaluate(() => window.verifyTicketPoll());
    rendered = await values();
    assert.equal(rendered.kpis[1], '$0.00');
    assert.equal(rendered.kpis[2], '$40.00');
    assert.equal(rendered.kpis[3], '60.0%');
    fixture.tickets.tickets[0].actual_payout = null;
    await page.evaluate(() => window.verifyTicketPoll());
    assert.equal((await values()).kpis[2], 'PENDING');
    assert.equal((await values()).kpis[3], 'SAMPLE PENDING');
    fixture.tickets.tickets = [ticket(300, 'OPEN', null, null, null)];
    await page.evaluate(() => window.verifyTicketPoll());
    assert.equal((await values()).kpis[1], 'PENDING');
    failBootstrap = true;
    await page.evaluate(() => window.verifyTicketPoll());
    assert.ok((await values()).cards.every(c => !c.id));
    assert.equal((await values()).kpis[0], 'PENDING');
    assert.equal((await values()).kpis[1], 'PENDING');
    assert.match(await page.locator('#liveintel-development-state').textContent(), /DATA PENDING/);
    failBootstrap = false;
    fixture = { ...actual, bobby: {}, tickets: { tickets: [] } };
    await page.evaluate(() => window.verifyTicketPoll());
    rendered = await values();
    assert.equal(rendered.cards.length, 4, 'exported empty cards remain, generated overflow is cleared');
    assert.ok(rendered.cards.every(c => !c.id));
    assert.equal(rendered.kpis[0], 'PENDING');
    assert.equal(rendered.kpis[1], '$0.00');
    assert.equal(rendered.kpis[2], '$0.00');
    assert.doesNotMatch(await page.locator('main').innerText(), /Recorded Player|Recorded Book|812\.75|99999|88888/);
    assert.equal(await page.locator('main button:enabled').count(), 5);
    assert.equal(await page.locator('header button:enabled').count(), 0);
    assert.equal(await page.locator('main input:enabled,main select:enabled').count(), 0);
    assert.ok(await page.locator('main button:enabled').evaluateAll(buttons => buttons.every(b => b.closest('#ticket-tabs'))));
    await page.locator('main button').evaluateAll(buttons => buttons.filter(b => b.disabled).forEach(b => b.click()));
    assert.deepEqual(writes, []);
    assert.deepEqual(errors, []);
    console.log('PASS TICKETS: 16 seconds; no page errors or writes; component preservation, authoritative records, normalized exposure/P&L, sample threshold, tabs, overflow, polling and fail-closed checks');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
