(() => {
    const page = document.querySelector(".posts-page");
    const modeButtons = [...document.querySelectorAll(".content-mode-button")];
    const postContents = [...document.querySelectorAll(".post-content")];
    const helpText = document.getElementById("content-mode-help");

    if (!modeButtons.length || !postContents.length) return;

    const originalPosts = new Map(
        postContents.map(element => [element.dataset.messageId, element.textContent])
    );
    let processedPosts = null;
    let processedPostsRequest = null;
    let requestedMode = "original";

    function selectModeButton(mode) {
        modeButtons.forEach(button => {
            const selected = button.dataset.contentMode === mode;
            button.classList.toggle("is-selected", selected);
            button.setAttribute("aria-pressed", String(selected));
        });
    }

    function parseProcessedPosts(xmlText) {
        const documentNode = new DOMParser().parseFromString(xmlText, "application/xml");
        if (documentNode.querySelector("parsererror")) {
            throw new Error("The RSS response is not valid XML.");
        }

        const posts = new Map();
        documentNode.querySelectorAll("item").forEach(item => {
            const link = item.querySelector("link")?.textContent.trim() || "";
            const messageId = link.match(/\/(\d+)\/?$/)?.[1];
            const description = item.querySelector("description")?.textContent ?? "";
            if (messageId) posts.set(messageId, description);
        });
        return posts;
    }

    async function loadProcessedPosts() {
        if (processedPosts) return processedPosts;
        if (processedPostsRequest) return processedPostsRequest;

        const channelUsername = page.dataset.channelUsername;
        processedPostsRequest = fetch(`/rss/${encodeURIComponent(channelUsername)}`)
            .then(response => {
                if (!response.ok) throw new Error(`RSS request failed: ${response.status}`);
                return response.text();
            })
            .then(parseProcessedPosts)
            .then(posts => {
                processedPosts = posts;
                return posts;
            })
            .finally(() => {
                processedPostsRequest = null;
            });

        return processedPostsRequest;
    }

    function showOriginalPosts() {
        requestedMode = "original";
        postContents.forEach(element => {
            element.textContent = originalPosts.get(element.dataset.messageId) ?? "";
        });
        selectModeButton("original");
        helpText.textContent = "Showing the original message received from Telegram.";
    }

    async function showProcessedPosts() {
        requestedMode = "processed";
        const processedButton = modeButtons.find(button => button.dataset.contentMode === "processed");
        processedButton.disabled = true;
        helpText.textContent = "Loading the processed content from the RSS feed...";

        try {
            const posts = await loadProcessedPosts();
            if (requestedMode !== "processed") return;
            let unavailableCount = 0;

            postContents.forEach(element => {
                const messageId = element.dataset.messageId;
                if (posts.has(messageId)) {
                    element.textContent = posts.get(messageId);
                } else {
                    element.textContent = originalPosts.get(messageId) ?? "";
                    unavailableCount += 1;
                }
            });

            selectModeButton("processed");
            helpText.textContent = unavailableCount
                ? `Showing processed RSS content. ${unavailableCount} older post(s) were not available in the feed.`
                : "Showing the content after keyword removal, replacements, prefix, and suffix processing.";
        } catch (error) {
            console.error("Could not load processed posts:", error);
            showOriginalPosts();
            helpText.textContent = "Processed content could not be loaded. Showing the original messages.";
        } finally {
            processedButton.disabled = false;
        }
    }

    modeButtons.forEach(button => {
        button.addEventListener("click", () => {
            if (button.dataset.contentMode === "processed") {
                showProcessedPosts();
            } else {
                showOriginalPosts();
            }
        });
    });
})();
