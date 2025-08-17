// On-screen joypad for MANUAL mode. Sends UDP-equivalent via PHP proxy every 500 ms
(function () {
    const ui = document.getElementById('joyui');
    const pad = document.getElementById('joypad');
    const btn = document.getElementById('joybtn');
    if (!ui || !pad || !btn) return;
    const stick = pad.querySelector('.stick');

    let dragging = false;
    let activePointerId = null;
    let padRect = null;
    let vx = 0; // -100 .. 100
    let vy = 0; // -100 .. 100
    let btnLatched = false; // send BUTTON:1 once in next 500ms window
    let modeIsManual = false;
    let centerSendPending = false; // send exactly once after re-center

    // Called by status.js when mode changes
    window.__setJoypadMode = function (mode) {
        modeIsManual = (mode === 'MANUAL');
        ui.style.display = modeIsManual ? 'block' : 'none';
        if (!modeIsManual) centerStick();
    };

    function centerStick() {
        vx = 0; vy = 0; dragging = false;
        if (stick) {
            stick.style.left = '50%';
            stick.style.top = '50%';
            stick.style.transform = 'translate(-50%, -50%)';
        }
    }

    function getPadContentMetrics() {
        if (!padRect) padRect = pad.getBoundingClientRect();
        const cs = window.getComputedStyle(pad);
        const bL = parseFloat(cs.borderLeftWidth) || 0;
        const bT = parseFloat(cs.borderTopWidth) || 0;
        const w = pad.clientWidth; // content width
        const h = pad.clientHeight; // content height
        const leftC = padRect.left + bL;
        const topC = padRect.top + bT;
        return { w, h, leftC, topC };
    }
    function updateFromPoint(clientX, clientY) {
        const m = getPadContentMetrics();
        const x = clientX - m.leftC;
        const y = clientY - m.topC;
        const halfX = m.w / 2;
        const halfY = m.h / 2;
        const cx = halfX; const cy = halfY;
        // relative -1..1 range (square so use respective axis halves)
        let rx = (x - cx) / halfX;
        let ry = (y - cy) / halfY;
        // clamp
        rx = Math.max(-1, Math.min(1, rx));
        ry = Math.max(-1, Math.min(1, ry));
        // convert to -100..100, invert Y to match joystick up=negative/positive? Keep same as PC client (y_axis)
        vx = Math.round(rx * 100);
        vy = Math.round(ry * 100);
        // move stick visually within bounds
        const px = cx + rx * halfX; // center within content box
        const py = cy + ry * halfY;
        stick.style.left = px + 'px';
        stick.style.top = py + 'px';
        stick.style.transform = 'translate(-50%, -50%)';
    }

    function onPress(e) {
        if (!modeIsManual) return;
        dragging = true;
        padRect = pad.getBoundingClientRect();
        if (e.touches && e.touches[0]) {
            updateFromPoint(e.touches[0].clientX, e.touches[0].clientY);
        } else {
            updateFromPoint(e.clientX, e.clientY);
        }
        e.preventDefault();
    }
    function onMove(e) {
        if (!dragging) return;
        if (e.touches && e.touches[0]) {
            updateFromPoint(e.touches[0].clientX, e.touches[0].clientY);
        } else {
            updateFromPoint(e.clientX, e.clientY);
        }
        e.preventDefault();
    }
    function onRelease() {
        if (!dragging) return;
        dragging = false;
        centerStick();
        centerSendPending = true; // schedule one 0/0 send
    }

    // Prefer Pointer Events if available for better capture outside bounds
    if (window.PointerEvent) {
        pad.addEventListener('pointerdown', (e) => {
            if (!modeIsManual) return;
            activePointerId = e.pointerId;
            try { pad.setPointerCapture(e.pointerId); } catch (_) { }
            onPress(e);
        });
        pad.addEventListener('pointermove', (e) => {
            if (activePointerId !== e.pointerId) return;
            onMove(e);
        });
        pad.addEventListener('pointerup', (e) => {
            if (activePointerId !== e.pointerId) return;
            try { pad.releasePointerCapture(e.pointerId); } catch (_) { }
            activePointerId = null;
            onRelease(e);
        });
        pad.addEventListener('pointercancel', (e) => {
            if (activePointerId !== e.pointerId) return;
            try { pad.releasePointerCapture(e.pointerId); } catch (_) { }
            activePointerId = null;
            onRelease(e);
        });
    } else {
        // Fallback: mouse + touch
        pad.addEventListener('mousedown', onPress);
        window.addEventListener('mousemove', onMove);
        window.addEventListener('mouseup', onRelease);
        pad.addEventListener('touchstart', onPress, { passive: false });
        window.addEventListener('touchmove', onMove, { passive: false });
        window.addEventListener('touchend', onRelease, { passive: false });
        window.addEventListener('touchcancel', onRelease, { passive: false });
    }

    // Keep padRect updated on resize/rotation
    window.addEventListener('resize', () => { padRect = null; });

    // Helper: perform one send
    async function doSend(forceButton = false) {
        const { vx: svx, vy: svy } = vectorFromDom();
        const form = new URLSearchParams();
        form.set('joy', '1');
        form.set('x', String(svx));
        form.set('y', String(svy));
        if (forceButton || btnLatched) form.set('button', '1');
        await fetch('send_udp.php', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: form.toString() });
        if (forceButton || btnLatched) btnLatched = false;
    }

    // Separate capture button: send immediately with BUTTON:1
    btn.addEventListener('click', (e) => {
        if (!modeIsManual) return;
        e.stopPropagation();
        doSend(true).catch(() => { });
    });

    // Compute current vector from DOM stick position (robust against lost events)
    function vectorFromDom() {
        try {
            const pr = pad.getBoundingClientRect();
            const sr = stick.getBoundingClientRect();
            const cs = window.getComputedStyle(pad);
            const bL = parseFloat(cs.borderLeftWidth) || 0;
            const bT = parseFloat(cs.borderTopWidth) || 0;
            const w = pad.clientWidth;
            const h = pad.clientHeight;
            const cx = pr.left + bL + w / 2;
            const cy = pr.top + bT + h / 2;
            const sx = sr.left + sr.width / 2;
            const sy = sr.top + sr.height / 2;
            let rx = (sx - cx) / (w / 2);
            let ry = (sy - cy) / (h / 2);
            rx = Math.max(-1, Math.min(1, rx));
            ry = Math.max(-1, Math.min(1, ry));
            return { vx: Math.round(rx * 100), vy: Math.round(ry * 100) };
        } catch (_) {
            // fallback to last known state
            return { vx, vy };
        }
    }

    // 500 ms sender like PC joystick client (only while dragging or on center event)
    async function sendLoop() {
        try {
            if (modeIsManual) {
                if (dragging || centerSendPending) {
                    await doSend(false);
                    if (centerSendPending) centerSendPending = false;
                }
            }
        } catch (_) { /* ignore */ }
        setTimeout(sendLoop, 500);
    }
    sendLoop();

    // initialize hidden
    centerStick();
})();
