(() => {
    const form = document.getElementById("channel-settings-form");
    const connectionStatus = document.getElementById("translation-connection-status");
    const connectionButton = document.getElementById("check-translation-connection");
    const previewButton = document.getElementById("preview-translation");
    const previewStatus = document.getElementById("translation-preview-status");
    const results = document.getElementById("translation-preview-results");
    const processedOutput = document.getElementById("translation-processed-output");
    const translatedOutput = document.getElementById("translation-translated-output");

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
        connectionStatus.textContent = "Checking Ollama…";
        connectionStatus.classList.remove("error-text");
        try {
            const data = await readResponse(await fetch(form.dataset.statusUrl));
            connectionStatus.textContent = data.message;
        } catch (error) {
            connectionStatus.textContent = error instanceof TypeError
                ? "Cannot reach the application. Check that it is running, then try again."
                : error.message;
            connectionStatus.classList.add("error-text");
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
        previewStatus.textContent = "Preparing preview… Ollama may need time to load the model.";
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
                : error.message;
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
