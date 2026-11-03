(() => {
    const searchInput = document.getElementById("search");
    const cards = [...document.querySelectorAll(".channel-card")];
    const channelList = document.querySelector('.channels');
    const sortSelect = document.getElementById('channel-sort');
    const cardsByUsername = new Map(cards.map(card => [card.dataset.username, card]));
    const nameCollator = new Intl.Collator(undefined, { sensitivity: 'base', numeric: true });
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

    function timestamp(value) {
        if (!value) return 0;
        // SQLite timestamps are UTC but omit the timezone suffix.
        let normalized = value.replace(' ', 'T');
        if (!/(Z|[+-]\d{2}:?\d{2})$/i.test(normalized)) normalized += 'Z';
        return Date.parse(normalized) || 0;
    }

    function sortChannels() {
        const byName = (a, b) => nameCollator.compare(a.dataset.title, b.dataset.title) ||
            nameCollator.compare(a.dataset.username, b.dataset.username);
        const byLatest = (a, b) => timestamp(b.dataset.latestPost) - timestamp(a.dataset.latestPost);
        const sorted = [...cards].sort((a, b) => {
            switch (sortSelect.value) {
                case 'newest': return timestamp(b.dataset.createdAt) - timestamp(a.dataset.createdAt) || byName(a, b);
                case 'name': return byName(a, b);
                case 'name-desc': return byName(b, a);
                case 'active': return Number(b.dataset.enabled === 'true') - Number(a.dataset.enabled === 'true') || byLatest(a, b) || byName(a, b);
                default: return byLatest(a, b) || byName(a, b);
            }
        });
        const current = [...channelList.querySelectorAll('.channel-card')];
        if (sorted.every((card, index) => card === current[index])) return;
        const focused = document.activeElement;
        sorted.forEach(card => channelList.appendChild(card));
        if (focused && channelList.contains(focused)) focused.focus({ preventScroll: true });
    }

    try {
        const saved = localStorage.getItem('channels-sort');
        if ([...sortSelect.options].some(option => option.value === saved)) sortSelect.value = saved;
    } catch (_) { /* Use the default when storage is unavailable. */ }
    sortChannels();
    sortSelect.addEventListener('change', () => {
        sortChannels();
        try { localStorage.setItem('channels-sort', sortSelect.value); } catch (_) { /* Storage may be blocked. */ }
    });

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
        const url = new URL(path, window.location.origin);
        if (url.hostname === "unews.duckdns.org" && url.pathname.startsWith("/rss/")) {
            url.protocol = "http:";
            url.port = "";
        }
        const text = url.href;

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
                const card = cardsByUsername.get(channel.channel_username);
                if (card) card.dataset.latestPost = channel.latest_post_date || '';
                window.ChannelAvatars.update(channel);
                const element = document.getElementById(`latest-${channel.channel_username}`);
                if (element) {
                    element.textContent = channel.latest_post || (channel.latest_post_date ? "Post without text" : "No posts yet");
                    const time = document.getElementById(`latest-time-${channel.channel_username}`);
                    if (time) updatePostTime(time, channel.latest_post_date);
                }
            });
            sortChannels();
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
