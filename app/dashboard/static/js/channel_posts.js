(() => {
    const page = document.querySelector(".posts-page");
    const buttons = [...document.querySelectorAll(".content-mode-button")];
    const cards = [...document.querySelectorAll(".post-card")];
    if (!page || !cards.length) return;
    const help = document.getElementById("content-mode-help");
    const refreshStatus = document.getElementById("post-output-refresh-status");
    const byId = new Map(cards.map(card => [String(card.dataset.postId), card]));
    let stopped = false;

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
            : "Showing RSS output: saved translations when ready, otherwise processed source text. Updates automatically.";
    }

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
            });
            refreshStatus.textContent = "";
        } catch (error) {
            refreshStatus.textContent = "Could not refresh post output. Showing the last loaded version; refresh the page to try again.";
        } finally {
            if (!stopped) window.setTimeout(refresh, 5000);
        }
    }
    buttons.forEach(button => button.addEventListener("click", () => selectMode(button.dataset.contentMode)));
    window.addEventListener("pagehide", () => { stopped = true; });
    refresh();
})();
