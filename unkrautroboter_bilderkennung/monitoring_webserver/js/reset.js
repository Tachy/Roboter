(function(){
  const btn = document.getElementById('reset-btn');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    const label = btn.textContent;
    btn.textContent = 'RESET wird ausgelöst...';
    try {
      const res = await fetch('send_udp.php?reset=1');
      if (!res.ok) throw new Error('HTTP ' + res.status);
      btn.textContent = 'RESET ausgelöst.';
    } catch (e) {
      btn.textContent = 'Fehler beim Auslösen';
    }
    setTimeout(() => { btn.textContent = label; btn.disabled = false; }, 3000);
  });
})();
