import re

def split_rules(text: str) -> list[str]:
    """
    Split rules written either as comma-separated values
    or one rule per line.
    """

    rules = []

    for line in text.splitlines():

        for item in line.split(","):

            item = item.strip()

            if item:
                rules.append(item)

    return rules

def extract_title(text: str) -> str:
    """
    Return the first non-empty line of a Telegram message.
    """

    if not text:
        return "(No text)"

    for line in text.splitlines():

        line = line.strip()

        if line:
            return line

    return "(No text)"

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

        for keyword in split_rules(remove_keywords):

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

        for rule in split_rules(replace_words):

            rule = rule.strip()

            if "=>" not in rule:
                continue

            old, new = rule.split(
                "=>",
                1,
            )

            old = old.strip()
            new = new.strip()

            processed = re.sub(
                rf"\b{re.escape(old)}\b",
                new,
                processed,
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