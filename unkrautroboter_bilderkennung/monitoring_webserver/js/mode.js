// Modus-Wechsel an den PHP-Proxy senden
// Wird vom Select-Element (onchange) aufgerufen
async function changeMode() {
    const mode = document.getElementById("mode").value;
    try {
        await fetch("send_udp.php", {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: `mode=${mode}`,
        });
    } catch (error) {
        console.error(`Fehler beim Senden des Modus: ${error.message}`);
    }
}
