def process_text(
    text: str,
    settings: dict,
) -> str:
    """
    Apply all text processing rules and return the processed text.
    """

    if text is None:
        return ""

    processed = text

    # -------------------------
    # Remove keywords
    # -------------------------

    remove_keywords = settings.get(
        "remove_keywords",
        "",
    )

    if remove_keywords:

        for keyword in remove_keywords.split(","):

            keyword = keyword.strip()

            if keyword:

                processed = processed.replace(
                    keyword,
                    "",
                )

    # -------------------------
    # Replace words
    # -------------------------

    replace_words = settings.get(
        "replace_words",
        "",
    )

    if replace_words:

        for rule in replace_words.split(","):

            rule = rule.strip()

            if "=>" not in rule:
                continue

            old, new = rule.split(
                "=>",
                1,
            )

            processed = processed.replace(
                old.strip(),
                new.strip(),
            )

    # -------------------------
    # Prefix
    # -------------------------

    prefix = settings.get(
        "prefix",
        "",
    ).strip()

    if prefix:

        processed = (
            prefix
            + "\n\n"
            + processed
        )

    # -------------------------
    # Suffix
    # -------------------------

    suffix = settings.get(
        "suffix",
        "",
    ).strip()

    if suffix:

        processed = (
            processed
            + "\n\n"
            + suffix
        )

    return processed.strip()


if __name__ == "__main__":

    settings = {
        "remove_keywords": "BREAKING",
        "replace_words": "Israel=>Occupation",
        "prefix": "📰 Latest News",
        "suffix": "Source: Telegram",
    }

    text = """
BREAKING

Israel attacked.
"""

    print(
        process_text(
            text,
            settings,
        )
    )