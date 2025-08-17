// MJPEG-Stream-Verwaltung und Watchdog
const STREAM_URL = streamUrl();
let streamInitialized = false;
let streamRetryTimer = null;
let placeholderObserver = null;
let placeholderTransitionTimer = null;
let lastPlaceholderVisible = null;
let lastStreamLoadTs = 0;

function ensureStream(active) {
    const img = document.getElementById('stream-img');
    const placeholder = document.getElementById('stream-placeholder');
    if (!img || !placeholder) return;

    if (active) {
        if (!streamInitialized || !img.src || !img.src.startsWith(STREAM_URL)) {
            img.onload = function () {
                placeholder.style.display = 'none';
                lastStreamLoadTs = Date.now();
                streamInitialized = true;
            };
            img.onerror = function () {
                placeholder.style.display = 'flex';
                streamInitialized = false;
                if (streamRetryTimer) clearTimeout(streamRetryTimer);
                streamRetryTimer = setTimeout(() => {
                    img.src = STREAM_URL + '?r=' + Date.now();
                }, 2000);
            };
            img.src = STREAM_URL;
        }
    } else {
        if (streamRetryTimer) { clearTimeout(streamRetryTimer); streamRetryTimer = null; }
        img.removeAttribute('src');
        placeholder.style.display = 'flex';
        streamInitialized = false;
    }
}

function startStreamWatchdog() {
    if (placeholderObserver) return;
    const placeholder = document.getElementById('stream-placeholder');
    const img = document.getElementById('stream-img');
    if (!img || !placeholder) return;
    lastPlaceholderVisible = (placeholder.style.display !== 'none');
    placeholderObserver = new MutationObserver(() => {
        const visible = (placeholder.style.display !== 'none');
        if (lastPlaceholderVisible === true && visible === false) {
            if (placeholderTransitionTimer) clearTimeout(placeholderTransitionTimer);
            placeholderTransitionTimer = setTimeout(() => {
                try {
                    img.removeAttribute('src');
                    img.src = STREAM_URL + '?r=' + Date.now();
                } catch (_) { }
            }, 2000);
        }
        lastPlaceholderVisible = visible;
    });
    placeholderObserver.observe(placeholder, { attributes: true, attributeFilter: ['style'] });
}
function stopStreamWatchdog() {
    if (placeholderObserver) { try { placeholderObserver.disconnect(); } catch (_) { } placeholderObserver = null; }
    if (placeholderTransitionTimer) { clearTimeout(placeholderTransitionTimer); placeholderTransitionTimer = null; }
    lastPlaceholderVisible = null;
}
