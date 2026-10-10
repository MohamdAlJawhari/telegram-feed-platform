(() => {
    const form = document.getElementById("channel-settings-form");
    const connectionStatus = document.getElementById("translation-connection-status");
    const connectionButton = document.getElementById("check-translation-connection");
    const previewButton = document.getElementById("preview-translation");
    const previewStatus = document.getElementById("translation-preview-status");
    const results = document.getElementById("translation-preview-results");
    const processedOutput = document.getElementById("translation-processed-output");
    const translatedOutput = document.getElementById("translation-translated-output");
    const connectionLabel = document.getElementById("translation-connection-label");
    const connectionDetails = document.getElementById("translation-connection-details");
    const connectionCard = document.querySelector(".connection-card");
    const enabled = document.getElementById("translation-enabled");
    enabled.addEventListener("change", () => {
        document.getElementById("translation-toggle-label").textContent = enabled.checked ? "Enabled" : "Disabled";
    });

    function friendlyServiceError(message) {
        // Keep input/rule validation messages intact; provider instructions belong in details.
        if (!/ollama|model|OLLAMA_/i.test(message)) return message;
        if (/not installed|isn't installed|isn’t installed|missing|not found|set up correctly/i.test(message))
            return "Translation needs setup. Please contact an administrator.";
        if (/longer|timeout|too long/i.test(message))
            return "The translation service is taking longer than expected. Try again in a moment.";
        if (/busy|memory/i.test(message))
            return "The translation service is temporarily busy. Try again in a moment.";
        return "The translation service is temporarily unavailable. Contact an administrator if this continues.";
    }

    function showConnection(state, label, detail) {
        connectionCard.dataset.state = state;
        connectionLabel.textContent = label;
        connectionDetails.textContent = detail;
        connectionStatus.querySelector("use").setAttribute("href", `#icon-${state === "ready" ? "check" : state === "checking" ? "clock" : "alert"}`);
    }

    function currentInput() {
        const values = new FormData(form);
        return {
            text: document.getElementById("translation-preview-text").value,
            prefix: values.get("prefix") || "",
            suffix: values.get("suffix") || "",
            remove_keywords: values.get("remove_keywords") || "",
            replace_words: values.get("replace_words") || "",
            translation_enabled: document.getElementById("translation-enabled").checked,
            source_language: values.get("source_language"),
            target_language: values.get("target_language"),
        };
    }

    async function readResponse(response) {
        let data;
        try {
            data = await response.json();
        } catch {
            throw new Error("The server returned an unreadable response. Please try again.");
        }
        if (!response.ok) {
            const validationMessage = Array.isArray(data.detail)
                ? "Check your input lengths and language selections, then try again."
                : data.detail;
            throw new Error(data.message || validationMessage || "The request failed. Please try again.");
        }
        return data;
    }

    async function checkConnection() {
        connectionButton.disabled = true;
        showConnection("checking", "Checking connection…", "Checking the translation service.");
        try {
            const data = await readResponse(await fetch(form.dataset.statusUrl));
            showConnection(data.ready ? "ready" : "unavailable", data.ready ? "Connected" : "Translation unavailable", data.message);
        } catch (error) {
            const message = error instanceof TypeError
                ? "Cannot reach the application. Check that it is running, then try again."
                : error.message;
            showConnection("unavailable", friendlyServiceError(message), message);
        } finally {
            connectionButton.disabled = false;
        }
    }

    previewButton.addEventListener("click", async () => {
        const input = currentInput();
        const snapshot = JSON.stringify(input);
        previewButton.disabled = true;
        results.hidden = true;
        previewStatus.classList.remove("error-text");
        previewStatus.textContent = "Preparing preview… The translation service may need a moment to get ready.";
        try {
            const data = await readResponse(await fetch(form.dataset.previewUrl, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: snapshot,
            }));
            if (snapshot !== JSON.stringify(currentInput())) {
                previewStatus.textContent = "Your settings or sample text changed. Preview again to see the latest values.";
                return;
            }
            // Treat model output as text, never as HTML.
            processedOutput.textContent = data.processed_text || "(No text)";
            translatedOutput.textContent = data.translated_text || "(No text)";
            results.hidden = false;
            previewStatus.textContent = data.translated
                ? "Translation preview ready. Your settings have not been saved."
                : "Showing final text without translation. Translation is off, the languages match, or the news body is empty.";
        } catch (error) {
            previewStatus.textContent = error instanceof TypeError
                ? "Cannot reach the application. Your entries are unchanged; please try again."
                : friendlyServiceError(error.message);
            previewStatus.classList.add("error-text");
        } finally {
            previewButton.disabled = false;
        }
    });

    function invalidatePreview() {
        if (!results.hidden) {
            results.hidden = true;
            previewStatus.textContent = "Settings or sample text changed. Preview again to update the output.";
        }
    }
    form.addEventListener("input", invalidatePreview);
    form.addEventListener("change", invalidatePreview);
    // Keyword/replacement chips update hidden fields through their own script.
    form.addEventListener("click", event => {
        if (event.target.closest(".remove-button, #add-keyword, #add-replacement")) invalidatePreview();
    });
    connectionButton.addEventListener("click", checkConnection);
    checkConnection();
})();
