(() => {
    const panel = document.querySelector(".translation-activity");
    if (!panel) return;
    const status = document.getElementById("translation-activity-status");
    const note = document.getElementById("translation-memory-note");
    const label = document.getElementById("translation-service-label");
    let stopped = false;

    function showSummary(data) {
        if (data.state === "checking") {
            label.textContent = "Checking";
            status.textContent = "Checking the translation service.";
        } else if (["paused", "unavailable", "stopped"].includes(data.state)) {
            label.textContent = "Temporarily unavailable";
            status.textContent = "News continues through RSS in its original language.";
        } else if (data.running || data.waiting) {
            label.textContent = "Translating";
            const running = data.running || 0, waiting = data.waiting || 0;
            status.textContent = `${running} ${running === 1 ? "post" : "posts"} in progress, ${waiting} waiting.`;
        } else {
            label.textContent = "Ready";
            status.textContent = "Translation is available.";
        }
    }

    async function refresh() {
        try {
            const response = await fetch(panel.dataset.activityUrl, {
                cache: "no-store", signal: AbortSignal.timeout(8000)
            });
            if (!response.ok) throw new Error("Activity check failed");
            const data = await response.json();
            if (panel.dataset.summary === "true") showSummary(data);
            else status.textContent = data.message;
            if (note) note.textContent = data.memory_note || "";
            panel.dataset.state = data.state;
            panel.dataset.memoryState = data.memory_state || "unknown";
        } catch (error) {
            if (panel.dataset.summary === "true") showSummary({state: "unavailable"});
            else status.textContent = "Could not check translation activity. RSS remains available.";
            if (note) note.textContent = "";
            panel.dataset.state = "unavailable";
        } finally {
            // Schedule after completion so requests never overlap.
            if (!stopped) window.setTimeout(refresh, 3000);
        }
    }
    window.addEventListener("pagehide", () => { stopped = true; });
    refresh();
})();
