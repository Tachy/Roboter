// On-screen joypad for MANUAL mode. Sends UDP-equivalent via PHP proxy every 500 ms
(function(){
  const ui = document.getElementById('joyui');
  const pad = document.getElementById('joypad');
  const btn = document.getElementById('joybtn');
  if (!ui || !pad || !btn) return;
  const stick = pad.querySelector('.stick');

  let dragging = false;
  let padRect = null;
  let vx = 0; // -100 .. 100
  let vy = 0; // -100 .. 100
  let btnLatched = false; // send BUTTON:1 once in next 500ms window
  let modeIsManual = false;

  // Called by status.js when mode changes
  window.__setJoypadMode = function(mode){
    modeIsManual = (mode === 'MANUAL');
    ui.style.display = modeIsManual ? 'block' : 'none';
    if (!modeIsManual) centerStick();
  };

  function centerStick(){
    vx = 0; vy = 0; dragging = false;
    if (stick) {
      stick.style.left = '50%';
      stick.style.top = '50%';
      stick.style.transform = 'translate(-50%, -50%)';
    }
  }

  function updateFromPoint(clientX, clientY){
    if (!padRect) padRect = pad.getBoundingClientRect();
    const x = clientX - padRect.left;
    const y = clientY - padRect.top;
    const size = Math.min(padRect.width, padRect.height);
    const half = size / 2;
    const cx = half; const cy = half;
    // relative -1..1 range
    let rx = (x - cx) / half;
    let ry = (y - cy) / half;
    // clamp
    rx = Math.max(-1, Math.min(1, rx));
    ry = Math.max(-1, Math.min(1, ry));
    // convert to -100..100, invert Y to match joystick up=negative/positive? Keep same as PC client (y_axis)
    vx = Math.round(rx * 100);
    vy = Math.round(ry * 100);
    // move stick visually within bounds
    const px = cx + rx * (half - 18); // minus stick radius
    const py = cy + ry * (half - 18);
    stick.style.left = px + 'px';
    stick.style.top = py + 'px';
    stick.style.transform = 'translate(-50%, -50%)';
  }

  function onPress(e){
    if (!modeIsManual) return;
    dragging = true;
    padRect = pad.getBoundingClientRect();
    if (e.touches && e.touches[0]) updateFromPoint(e.touches[0].clientX, e.touches[0].clientY);
    else updateFromPoint(e.clientX, e.clientY);
    e.preventDefault();
  }
  function onMove(e){
    if (!dragging) return;
    if (e.touches && e.touches[0]) updateFromPoint(e.touches[0].clientX, e.touches[0].clientY);
    else updateFromPoint(e.clientX, e.clientY);
    e.preventDefault();
  }
  function onRelease(){
    if (!dragging) return;
    dragging = false;
    centerStick();
  }

  pad.addEventListener('mousedown', onPress);
  window.addEventListener('mousemove', onMove);
  window.addEventListener('mouseup', onRelease);

  pad.addEventListener('touchstart', onPress, {passive:false});
  window.addEventListener('touchmove', onMove, {passive:false});
  window.addEventListener('touchend', onRelease, {passive:false});
  window.addEventListener('touchcancel', onRelease, {passive:false});

  // Separate capture button: set latch to include BUTTON:1 in the next send
  btn.addEventListener('click', () => { if (modeIsManual) btnLatched = true; });

  // 500 ms sender like PC joystick client
  async function sendLoop(){
    try {
  if (modeIsManual){
        const form = new URLSearchParams();
        form.set('joy', '1');
        form.set('x', String(vx));
        form.set('y', String(vy));
        if (btnLatched) form.set('button', '1');
        await fetch('send_udp.php', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: form.toString() });
        btnLatched = false;
      }
    } catch (_) { /* ignore */ }
    setTimeout(sendLoop, 500);
  }
  sendLoop();

  // initialize hidden
  centerStick();
})();
