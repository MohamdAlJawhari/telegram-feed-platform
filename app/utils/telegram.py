def normalize_username(username: str) -> str:
    """
    Accept:
        testRssN8n
        @testRssN8n
        https://t.me/testRssN8n
        https://telegram.me/testRssN8n
    """

    username = username.strip()

    if username.startswith("https://t.me/"):
        username = username.replace("https://t.me/", "")

    if username.startswith("https://telegram.me/"):
        username = username.replace("https://telegram.me/", "")

    if username.startswith("@"):
        username = username[1:]

    return username