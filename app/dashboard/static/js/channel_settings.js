(() => {
    const keywordData = document.getElementById("remove-keywords-data");
    const keywordInput = document.getElementById("keyword-input");
    const keywordChips = document.getElementById("keyword-chips");
    const replacementData = document.getElementById("replace-words-data");
    const oldWordInput = document.getElementById("old-word-input");
    const newWordInput = document.getElementById("new-word-input");
    const replacementList = document.getElementById("replacement-list");

    const keywords = keywordData.value.split(/[\n,]+/).map(value => value.trim())
        .filter((value, index, values) => value && values.indexOf(value) === index);
    const replacements = replacementData.value.split(/[\n,]+/).map(rule => rule.split("=>"))
        .filter(parts => parts.length >= 2 && parts[0].trim() && parts.slice(1).join("=>").trim())
        .map(parts => ({ oldWord: parts[0].trim(), newWord: parts.slice(1).join("=>").trim() }));

    function removeButton(label, callback) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "remove-button";
        button.setAttribute("aria-label", label);
        button.textContent = "×";
        button.addEventListener("click", callback);
        return button;
    }

    function renderKeywords() {
        keywordChips.replaceChildren();
        keywords.forEach((keyword, index) => {
            const chip = document.createElement("span");
            chip.className = "keyword-chip";
            const label = document.createElement("span");
            label.textContent = keyword;
            chip.append(label, removeButton(`Remove ${keyword}`, () => {
                keywords.splice(index, 1);
                renderKeywords();
            }));
            keywordChips.append(chip);
        });
        keywordData.value = keywords.join("\n");
    }

    function addKeyword() {
        const keyword = keywordInput.value.trim();
        if (keyword && !keywords.includes(keyword)) keywords.push(keyword);
        keywordInput.value = "";
        renderKeywords();
        keywordInput.focus();
    }

    function renderReplacements() {
        replacementList.replaceChildren();
        replacements.forEach((rule, index) => {
            const row = document.createElement("div");
            row.className = "replacement-row";
            const oldWord = document.createElement("span");
            const arrow = document.createElement("span");
            const newWord = document.createElement("span");
            oldWord.textContent = rule.oldWord;
            arrow.className = "rule-arrow";
            arrow.textContent = "→";
            arrow.setAttribute("aria-hidden", "true");
            newWord.textContent = rule.newWord;
            row.append(oldWord, arrow, newWord, removeButton(`Remove ${rule.oldWord} replacement`, () => {
                replacements.splice(index, 1);
                renderReplacements();
            }));
            replacementList.append(row);
        });
        replacementData.value = replacements.map(rule => `${rule.oldWord}=>${rule.newWord}`).join("\n");
    }

    function addReplacement() {
        const oldWord = oldWordInput.value.trim();
        const newWord = newWordInput.value.trim();
        if (!oldWord || !newWord) return;
        replacements.push({ oldWord, newWord });
        oldWordInput.value = "";
        newWordInput.value = "";
        renderReplacements();
        oldWordInput.focus();
    }

    document.getElementById("add-keyword").addEventListener("click", addKeyword);
    keywordInput.addEventListener("keydown", event => {
        if (event.key === "Enter") { event.preventDefault(); addKeyword(); }
    });
    document.getElementById("add-replacement").addEventListener("click", addReplacement);
    [oldWordInput, newWordInput].forEach(input => input.addEventListener("keydown", event => {
        if (event.key === "Enter") { event.preventDefault(); addReplacement(); }
    }));

    renderKeywords();
    renderReplacements();
})();
