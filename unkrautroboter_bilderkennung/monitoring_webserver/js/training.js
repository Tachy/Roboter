(function () {
  const box = document.getElementById('training-box');
  if (!box) return;
  const statusEl = document.getElementById('training-status');
  const metaEl = document.getElementById('training-meta');
  const logEl = document.getElementById('training-log');
  const btn = document.getElementById('training-btn');
  const skipEl = document.getElementById('training-skip');
  const baseEl = document.getElementById('training-base');

  let running = false;

  function render(s) {
    running = !!s.running;
    const phase = s.running ? (s.phase === 'deploy' ? 'deployt …' : 'trainiert …') : 'bereit';
    statusEl.textContent = phase;
    statusEl.className = s.running ? 'run' : 'idle';
    const cand = s.cand_map != null ? s.cand_map.toFixed(3) : '–';
    const cur = s.cur_map != null ? s.cur_map.toFixed(3) : '–';
    metaEl.textContent =
      `Modell ${s.current_model || '–'} · letztes Gate ${s.gate || '–'} ` +
      `(cand ${cand} / current ${cur})` + (s.deployed ? ' · deployt ✅' : '');
    if (typeof s.log === 'string') {
      const atBottom = logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 20;
      logEl.textContent = s.log.slice(-6000);
      if (atBottom) logEl.scrollTop = logEl.scrollHeight;
    }
    btn.disabled = s.running;
  }

  async function poll() {
    try {
      const r = await fetch('train.php?status=1', { cache: 'no-store' });
      const s = await r.json();
      if (s.error) { statusEl.textContent = s.error; statusEl.className = 'run'; }
      else render(s);
    } catch (e) {
      statusEl.textContent = '.17 nicht erreichbar';
      statusEl.className = 'run';
    }
  }

  btn.addEventListener('click', async () => {
    if (running) return;
    const switching = baseEl && baseEl.checked;
    const msg = switching
      ? 'ERSTUMSTIEG: Modell komplett neu von YOLO26s aufsetzen (verwirft die v8m-Basis) '
        + 'und bei bestandenem Gate an den Roboter deployen?'
      : 'Modell neu trainieren und bei bestandenem Gate an den Roboter deployen?';
    if (!confirm(msg)) return;
    btn.disabled = true;
    statusEl.textContent = 'starte …';
    const body = new URLSearchParams({
      train: '1', deploy: '1',
      token: (typeof CONFIG !== 'undefined' && CONFIG.CONTROL_TOKEN) || '',
    });
    if (skipEl && skipEl.checked) body.set('skip_export', '1');
    if (switching) body.set('base', 'yolo26s.pt');
    try {
      const r = await fetch('train.php', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: body.toString(),
      });
      const j = await r.json();
      if (!r.ok || j.error) statusEl.textContent = 'Fehler: ' + (j.error || r.status);
    } catch (e) {
      statusEl.textContent = 'Fehler beim Start';
    }
    setTimeout(poll, 500);
  });

  poll();
  setInterval(poll, 4000);
})();
