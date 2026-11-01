(() => {
  const routes = {
    live: 'live_today_scoreboard',
    players: 'players_research_rotation_matrix',
    'heats-on': 'heat_s_on_repeat_performers',
    'math-lab': 'math_lab_quantitative_edge_engine',
    'watch-5': 'watch_5_rapid_target_radar_2',
    picks: 'picks_positions_specialist_challenge',
    tickets: 'tickets_settlement_portfolio_ledger',
    intelligence: 'intelligence_ticket_builder'
  };

  document.querySelectorAll('nav a[data-path]').forEach((link) => {
    const screen = routes[link.dataset.path];
    if (screen) link.href = '/?screen=' + encodeURIComponent(screen);
  });

  const notice = document.createElement('div');
  notice.id = 'stitch-development-data-notice';
  notice.textContent = 'Development / sample data · live wiring inventory in progress';
  document.body.append(notice);

  const status = document.createElement('div');
  status.id = 'stitch-action-status';
  status.setAttribute('role', 'status');
  document.body.append(status);

  const unwiredWrite = /TRANSMIT|PLACE \/ SAVE|SYNC TO BOOKMAKER|EXECUTE AUTOMATED|LOCK DUAL-LEG|CO-SIGN TICKET/i;
  document.addEventListener('click', (event) => {
    const control = event.target.closest('button, a');
    if (!control || !unwiredWrite.test(control.textContent || '')) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    status.textContent = 'ACTION WIRING NEEDED · This Stitch control is preserved but does not write or transmit during Phase 1.';
    status.dataset.open = 'true';
    window.setTimeout(() => { status.dataset.open = 'false'; }, 4200);
  }, true);
})();
