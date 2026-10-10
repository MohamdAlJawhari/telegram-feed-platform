(() => {
    const panel = document.querySelector(".translation-activity");
    if (!panel) return;
    const status = document.getElementById("translation-activity-status");
    const note = document.getElementById("translation-memory-note");
    let stopped = false;

    async function refresh() {
        try {
            const response = await fetch(panel.dataset.activityUrl, {
                cache: "no-store", signal: AbortSignal.timeout(8000)
            });
            if (!response.ok) throw new Error("Activity check failed");
            const data = await response.json();
            status.textContent = data.message;
            note.textContent = data.memory_note || "";
            panel.dataset.state = data.state;
            panel.dataset.memoryState = data.memory_state || "unknown";
        } catch (error) {
            status.textContent = "Could not check translation activity. RSS remains available.";
            note.textContent = "";
            panel.dataset.state = "unavailable";
        } finally {
            // Schedule after completion so requests never overlap.
            if (!stopped) window.setTimeout(refresh, 3000);
        }
    }
    window.addEventListener("pagehide", () => { stopped = true; });
    refresh();
})();
