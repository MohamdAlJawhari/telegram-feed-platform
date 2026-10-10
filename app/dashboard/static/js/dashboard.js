(() => {
    const searchInput = document.getElementById("search");
    const cards = [...document.querySelectorAll(".channel-card")];
    const noResults = document.getElementById("no-search-results");
    const toast = document.getElementById("copy-toast");
    const translationDialog = document.getElementById("translation-channels-dialog");
    const translationTrigger = document.getElementById("translation-channels-trigger");
    translationTrigger.addEventListener("click", () => {
        translationDialog.showModal();
        translationTrigger.setAttribute("aria-expanded", "true");
    });
    document.getElementById("translation-channels-close").addEventListener("click", () => translationDialog.close());
    translationDialog.addEventListener("close", () => {
        translationTrigger.setAttribute("aria-expanded", "false");
        translationTrigger.focus();
    });
    translationDialog.addEventListener("keydown", event => {
        if (event.key !== "Tab") return;
        const targets = [...translationDialog.querySelectorAll("button, a[href]")];
        const first = targets[0], last = targets[targets.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault(); last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault(); first.focus();
        }
    });
    let toastTimer;
    let latestPostsRefreshId = 0;
    const relativeTime = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

    function updatePostTime(element, value) {
        const date = new Date(value);
        if (!value || Number.isNaN(date.getTime())) {
            element.textContent = "";
            element.removeAttribute("datetime");
            element.removeAttribute("title");
            return;
        }
        const seconds = (date.getTime() - Date.now()) / 1000;
        const [unit, size] = Math.abs(seconds) < 60 ? ["second", 1]
            : Math.abs(seconds) < 3600 ? ["minute", 60]
            : Math.abs(seconds) < 86400 ? ["hour", 3600] : ["day", 86400];
        element.textContent = relativeTime.format(Math.round(seconds / size), unit);
        element.dateTime = date.toISOString();
        element.title = date.toLocaleString();
    }

    function searchChannels() {
        const query = searchInput.value.trim().toLocaleLowerCase();
        let visibleCards = 0;

        cards.forEach(card => {
            const matches = card.dataset.search.toLocaleLowerCase().includes(query);
            card.hidden = !matches;
            if (matches) visibleCards += 1;
        });

        noResults.hidden = visibleCards > 0 || cards.length === 0;
    }

    function showToast(message) {
        toast.textContent = message;
        toast.classList.add("is-visible");
        window.clearTimeout(toastTimer);
        toastTimer = window.setTimeout(() => toast.classList.remove("is-visible"), 2200);
    }

    async function copyLink(path) {
        const text = window.location.origin + path;

        try {

            // Modern Clipboard API
            if (navigator.clipboard && window.isSecureContext) {
                await navigator.clipboard.writeText(text);
            }

            // Fallback for HTTP / older browsers
            else {
                const textarea = document.createElement("textarea");

                textarea.value = text;
                textarea.style.position = "fixed";
                textarea.style.left = "-9999px";

                document.body.appendChild(textarea);

                textarea.focus();
                textarea.select();

                const success = document.execCommand("copy");

                document.body.removeChild(textarea);

                if (!success) {
                    throw new Error("Fallback copy failed");
                }
            }

            showToast("Link copied to clipboard");

        } catch (error) {
            console.error("Failed to copy link:", error);
            showToast("Could not copy the link");
        }
    }

    async function refreshLatestPosts() {
        const refreshId = ++latestPostsRefreshId;

        try {
            const response = await fetch("/telegram-feed/channel-summary");
            if (!response.ok) throw new Error(`Request failed: ${response.status}`);
            const channels = await response.json();
            if (refreshId !== latestPostsRefreshId) return;

            channels.forEach(channel => {
                const element = document.getElementById(`latest-${channel.channel_username}`);
                if (element) {
                    element.textContent = channel.latest_post || (channel.latest_post_date ? "Post without text" : "No posts yet");
                    const time = document.getElementById(`latest-time-${channel.channel_username}`);
                    if (time) updatePostTime(time, channel.latest_post_date);
                }
            });
        } catch (error) {
            console.error("Could not refresh latest posts:", error);
            cards.forEach(card => {
                const preview = card.querySelector(".latest-post");
                if (preview.textContent === "Loading latest post...")
                    preview.textContent = "Preview unavailable. Open posts to view this channel.";
            });
        }
    }

    searchInput.addEventListener("input", searchChannels);
    document.querySelectorAll(".copy-button").forEach(button => {
        button.addEventListener("click", () => copyLink(button.dataset.copyPath));
    });

    refreshLatestPosts();
    window.setInterval(refreshLatestPosts, 5000);
})();
