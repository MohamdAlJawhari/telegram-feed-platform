(() => {
    const searchInput = document.getElementById("search");
    const cards = [...document.querySelectorAll(".channel-card")];
    const noResults = document.getElementById("no-search-results");
    const toast = document.getElementById("copy-toast");
    let toastTimer;
    let latestPostsRefreshId = 0;

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
            const response = await fetch("/channel-summary");
            if (!response.ok) throw new Error(`Request failed: ${response.status}`);
            const channels = await response.json();
            if (refreshId !== latestPostsRefreshId) return;

            channels.forEach(channel => {
                const element = document.getElementById(`latest-${channel.channel_username}`);
                if (element) element.textContent = channel.latest_post || "No posts yet";
            });
        } catch (error) {
            console.error("Could not refresh latest posts:", error);
        }
    }

    searchInput.addEventListener("input", searchChannels);
    document.querySelectorAll(".copy-button").forEach(button => {
        button.addEventListener("click", () => copyLink(button.dataset.copyPath));
    });

    refreshLatestPosts();
    window.setInterval(refreshLatestPosts, 5000);
})();
