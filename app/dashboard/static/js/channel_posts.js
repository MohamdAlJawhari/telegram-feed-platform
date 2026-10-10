(() => {
    const page = document.querySelector(".posts-page");
    const guide = document.getElementById("status-guide");
    const guideTrigger = document.getElementById("status-guide-trigger");
    if (guide && guideTrigger) {
        guideTrigger.addEventListener("click", () => {
            guide.showModal();
            guideTrigger.setAttribute("aria-expanded", "true");
        });
        document.getElementById("status-guide-close").addEventListener("click", () => guide.close());
        guide.addEventListener("close", () => {
            guideTrigger.setAttribute("aria-expanded", "false");
            guideTrigger.focus();
        });
        guide.addEventListener("keydown", event => {
            if (event.key !== "Tab") return;
            const targets = [...guide.querySelectorAll('button, [tabindex="0"]')];
            const first = targets[0], last = targets[targets.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault(); last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault(); first.focus();
            }
        });
        // Native dialog handles Escape and makes the background inert.
    }
    const buttons = [...document.querySelectorAll(".content-mode-button")];
    const cards = [...document.querySelectorAll(".post-card")];
    if (!page || !cards.length) return;
    const help = document.getElementById("content-mode-help");
    const refreshStatus = document.getElementById("post-output-refresh-status");
    const list = page.querySelector(".posts-list");
    const layoutButtons = [...page.querySelectorAll(".layout-button")];
    const byId = new Map(cards.map(card => [String(card.dataset.postId), card]));
    let stopped = false;
    const statusIcons = {
        "Translated": "check", "Waiting for translation": "clock", "Translating": "refresh",
        "Translation paused": "pause", "Translation disabled": "minus", "Translation not needed": "text",
        "Translation unavailable": "unavailable", "Translation needs setup": "settings",
        "Translation failed": "alert", "Published without translation": "text", "Content processing warning": "alert"
    };

    function selectMode(mode) {
        cards.forEach(card => {
            card.querySelector(".post-original").hidden = mode !== "original";
            card.querySelector(".post-published").hidden = mode !== "processed";
        });
        buttons.forEach(button => {
            const selected = button.dataset.contentMode === mode;
            button.classList.toggle("is-selected", selected);
            button.setAttribute("aria-pressed", String(selected));
        });
        help.textContent = mode === "original"
            ? "Showing the original message received from Telegram."
            : "Showing what RSS publishes: translations when ready, otherwise the original language with your text rules applied.";
    }

    function selectLayout(layout) {
        list.dataset.layout = layout === "cards" ? "cards" : "stacked";
        layoutButtons.forEach(button => {
            const selected = button.dataset.layout === list.dataset.layout;
            button.classList.toggle("is-selected", selected);
            button.setAttribute("aria-pressed", String(selected));
        });
        try { localStorage.setItem("posts-layout", list.dataset.layout); } catch (_) { /* Storage may be blocked. */ }
    }
    let savedLayout = "stacked";
    try { savedLayout = localStorage.getItem("posts-layout") || savedLayout; } catch (_) { /* Use the default. */ }
    selectLayout(savedLayout);
    layoutButtons.forEach(button => button.addEventListener("click", () => selectLayout(button.dataset.layout)));
    page.querySelectorAll("time[datetime]").forEach(element => {
        const date = new Date(element.dateTime);
        if (!Number.isNaN(date.getTime())) {
            element.textContent = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
            element.title = element.dateTime;
        }
    });

    async function refresh() {
        try {
            const response = await fetch(page.dataset.outputUrl, {
                cache: "no-store", signal: AbortSignal.timeout(8000)
            });
            if (!response.ok) throw new Error("Output check failed");
            const data = await response.json();
            data.posts.forEach(post => {
                const card = byId.get(String(post.id));
                if (!card) return;
                card.querySelector(".post-original").textContent = post.text || "(No caption)";
                card.querySelector(".post-published").textContent = post.output_text || "(No text)";
                card.querySelector(".translation-label").textContent = post.translation_label;
                card.querySelector(".translation-message").textContent = post.translation_message;
                card.querySelector(".translation-status").dataset.status = post.translation_status;
                card.querySelector(".translation-status").dataset.tone = post.translation_tone;
                const badge = card.querySelector(".translation-badge");
                badge.setAttribute("aria-label", `${post.translation_label}: show status details`);
                card.querySelector(".translation-icon use").setAttribute("href", `#icon-${statusIcons[post.translation_label.split(" · ")[0]] || "alert"}`);
                card.querySelector(".rss-label").textContent = post.rss_label;
            });
            refreshStatus.textContent = "";
        } catch (error) {
            refreshStatus.textContent = "Live updates are temporarily unavailable. Showing the last loaded posts.";
        } finally {
            if (!stopped) window.setTimeout(refresh, 5000);
        }
    }
    buttons.forEach(button => button.addEventListener("click", () => selectMode(button.dataset.contentMode)));
    window.addEventListener("pagehide", () => { stopped = true; });
    if (page.dataset.demo !== "true") refresh();
})();
