(function(){
  const btn = document.getElementById('reset-btn');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    const label = btn.textContent;
    btn.textContent = 'RESET wird ausgelöst...';
    try {
      const res = await fetch('send_udp.php', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: `reset=1&token=${encodeURIComponent(CONFIG.CONTROL_TOKEN)}`,
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      btn.textContent = 'RESET ausgelöst.';
    } catch (e) {
      btn.textContent = 'Fehler beim Auslösen';
    }
    setTimeout(() => { btn.textContent = label; btn.disabled = false; }, 3000);
  });
})();
