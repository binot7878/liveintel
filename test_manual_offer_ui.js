// Structural and intake contracts for the actual Stitch shell; behavior is covered by the isolated browser suite.
const fs=require('fs'),assert=require('node:assert/strict'),path=require('path');
const root=process.env.LIVEINTEL_TEST_DIR||__dirname;
const html=fs.readFileSync(path.join(root,'liveintel_v2.html'),'utf8'),js=fs.readFileSync(path.join(root,'board.js'),'utf8');
const routes=[...html.matchAll(/data-nav="([^"]+)"/g)].map(m=>m[1]);
assert.deepEqual(routes,['live','players','heat','math','watch','picks','tickets','intelligence']);
for(const id of ['player-hero','player-minis','player-rows','heat-cards','math-teams','math-segments','watch-cards','competitors','ticket-stream','qualified-targets','inline-builder','ticket-drawer'])assert.ok(html.includes('id="'+id+'"'),id);
assert.ok(html.includes('/board.css'));assert.ok(html.includes('/board.js'));
assert.ok(!/class="(?:game-grid|desk-card|workspace-grid)/.test(html));
for(const sample of ['9,490','78.4%','0.038 RMS','SYNAPSE DRIFT','100,000 ITER','Derrick White','Pinnacle'])assert.ok(!html.includes(sample),sample);
assert.ok(js.includes("'/api/manual-offer'"));assert.ok(js.includes("'/api/add-to-watchlist'"));assert.ok(js.includes("'/api/save-ticket'"));
assert.ok(js.includes("player_id:l.player_id"));assert.ok(js.includes("player_team:l.team"));
assert.ok(!/fetch\([^)]*(?:place-bet|settle-ticket|challenge-decision)/.test(js));
console.log('PASS: eight Stitch routes, distinct source-derived structures, visible builder, sample exclusions and existing intake endpoints');
