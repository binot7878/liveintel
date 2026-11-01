const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const runtime = fs.readFileSync('/opt/liveintel/stitch_frontend/stitch-app.js', 'utf8');
const screens = fs.readdirSync('/opt/liveintel/stitch_frontend/screens').filter(name => name.endsWith('.html'));
assert.equal(screens.length, 15);
for (const token of [
  '/api/stitch/bootstrap', '/api/stitch/updates', '/api/stitch/watch/add',
  '/api/stitch/watch/remove', '/api/stitch/watch/reorder', '/api/stitch/tickets/record',
  'wager_placed', 'DATA SOURCE PENDING', 'setInterval(poll, 15000)',
  'externalAction', 'recordDraft', 'bindLive', 'bindPlayers', 'bindHeat', 'bindMath',
  'bindWatch', 'bindPicks', 'bindTickets', 'bindIntelligence'
]) assert.ok(runtime.includes(token), `missing runtime contract: ${token}`);

const sourceRoot = '/opt/liveintel/verification/stitch-master/20261101T150701Z/source';
for (const filename of screens) {
  const slug = filename.slice(0, -5);
  assert.deepEqual(
    fs.readFileSync(path.join('/opt/liveintel/stitch_frontend/screens', filename)),
    fs.readFileSync(path.join(sourceRoot, slug, 'code.html')),
    `${slug} code.html was modified`
  );
}
console.log('PASS: 15 code.html files frozen; shared data, action, update, fail-closed and page binding contracts present');
