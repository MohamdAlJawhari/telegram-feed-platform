"""Plain-language post presentation, independent of translation/storage decisions."""

from contextlib import closing
from app.database.database import get_connection
from app.translation.activity import get_activity


STATUS_GUIDE = (
    ("Translated", "Translation is ready and RSS uses it."),
    ("Waiting for translation", "The post is queued. RSS can still publish its original language."),
    ("Translating", "Translation is in progress. RSS can still publish its original language."),
    ("Translation paused", "The channel or translation service is paused. RSS remains available."),
    ("Translation disabled", "Translation is turned off in this channel's settings."),
    ("Translation not needed", "The languages are the same or there is no text to translate."),
    ("Translation unavailable", "The service cannot translate right now. RSS uses the original language."),
    ("Translation needs setup", "An administrator needs to finish setting up the translation service."),
    ("Translation failed", "This post could not be translated. Its original-language output is final."),
    ("Published without translation", "RSS already published the original language. This post will stay unchanged."),
    ("Content processing warning", "A text rule could not be applied. RSS still provides the post."),
)

# Presentation only: English definitions above remain the source of truth.
STATUS_APPEARANCE = {
    "Translated": ("success", "check", "تمت الترجمة", "الترجمة جاهزة ويستخدمها RSS."),
    "Waiting for translation": ("progress", "clock", "بانتظار الترجمة", "المنشور في قائمة الانتظار. يمكن لـ RSS نشره بلغته الأصلية."),
    "Translating": ("progress", "refresh", "جارٍ الترجمة", "الترجمة قيد التنفيذ. يمكن لـ RSS نشر المنشور بلغته الأصلية."),
    "Translation paused": ("warning", "pause", "الترجمة متوقفة مؤقتًا", "القناة أو خدمة الترجمة متوقفة مؤقتًا. يظل RSS متاحًا."),
    "Translation disabled": ("neutral", "minus", "الترجمة معطّلة", "الترجمة معطّلة في إعدادات هذه القناة."),
    "Translation not needed": ("neutral", "text", "لا حاجة للترجمة", "اللغتان متماثلتان أو لا يوجد نص لترجمته."),
    "Translation unavailable": ("warning", "unavailable", "الترجمة غير متاحة", "لا تستطيع الخدمة الترجمة حاليًا. يستخدم RSS اللغة الأصلية."),
    "Translation needs setup": ("warning", "settings", "الترجمة تحتاج إلى إعداد", "يحتاج المسؤول إلى إكمال إعداد خدمة الترجمة."),
    "Translation failed": ("error", "alert", "فشلت الترجمة", "تعذّرت ترجمة هذا المنشور. مخرجاته باللغة الأصلية نهائية."),
    "Published without translation": ("warning", "text", "نُشر دون ترجمة", "نشر RSS المنشور بالفعل بلغته الأصلية. سيبقى هذا المنشور دون تغيير."),
    "Content processing warning": ("warning", "alert", "تحذير معالجة المحتوى", "تعذّر تطبيق إحدى قواعد النص. يظل المنشور متاحًا عبر RSS."),
}


def service_reason(message):
    """Classify known failures without displaying provider names or raw details."""
    message = (message or "").lower()
    if "model" in message and any(word in message for word in ("installed", "missing", "not found")):
        return "The translation service needs to be set up by an administrator.", "setup"
    if any(word in message for word in ("set up correctly", "translation languages", "language settings")):
        return "The translation settings need attention. Check the selected languages or contact an administrator.", "setup"
    if "rule" in message:
        return "A text rule needs attention. Check the channel's text rules.", "processing"
    if any(word in message for word in ("longer", "timeout", "taking too long")):
        return "The translation took too long to complete.", "timeout"
    if any(word in message for word in ("busy", "memory", "capacity")):
        return "The translation service is temporarily busy.", "busy"
    if any(word in message for word in ("reach", "connect", "responding", "start", "stopped", "unavailable", "verify")):
        return "The translation service is temporarily unavailable.", "unavailable"
    return "The translation could not be completed.", "failed"


def present_post(post, *, channel_enabled=True, background=None):
    post = dict(post)
    state = post["translation_status"]
    if not channel_enabled and state == "unavailable":
        state = "pending"
    reason, kind = service_reason(post.get("translation_message"))
    tone, icon, rss = "neutral", "–", "RSS: Original language"
    if state == "completed":
        label = post["translation_label"]
        message, tone, icon, rss = "The translation is ready. RSS uses this version.", "success", "✓", "RSS: Translated"
    elif state == "disabled":
        label, message = "Translation disabled", "Translation is turned off for this channel. RSS uses its original language."
    elif state == "skipped":
        label = "Translation not needed"
        message = ("There is no news text to translate. Media and any added text still appear in RSS."
                   if not post.get("body_text", post.get("text")) else "The source and target languages are the same. RSS uses the original text.")
    elif state == "source_final":
        label, message, tone, icon = "Published without translation", "RSS published this post in its original language. Its published text will stay unchanged.", "warning", "!"
        rss = "RSS: Original language · Final"
        # Only show a reason if known; pending source publication is not a failure.
        if kind != "failed":
            message = reason + " " + message
    elif state == "failed":
        label, message, tone, icon = "Translation failed", reason + " RSS uses the original language. This post will not be retried.", "error", "!"
        rss = "RSS: Original language · Final"
    elif state == "processing_error":
        label, message, tone, icon = "Content processing warning", "A text replacement rule could not be applied. RSS uses the original text with any added opening or closing text. Check the channel's text rules.", "warning", "!"
    elif state == "unavailable":
        if kind == "busy":
            state, label = "paused", "Translation paused"
        else:
            label = "Translation needs setup" if kind == "setup" else "Translation unavailable"
        message = reason + " RSS uses the original language. Contact an administrator if this continues."
        tone, icon = "warning", "!"
    else:
        background = background or {}
        paused = (not channel_enabled or (background.get("state") == "paused" and not background.get("connection_error"))
                  or (background.get("memory_state") == "critical" and background.get("limit") == 0))
        if paused and state != "running":
            state, label, tone, icon = "paused", "Translation paused", "warning", "Ⅱ"
            message = ("This channel is paused. Resume it from the dashboard to translate unpublished posts."
                       if not channel_enabled else "Translation is temporarily paused. It will resume when the service is ready.")
            message += " RSS remains available in the original language."
        elif state == "running":
            label, message, tone, icon = "Translating", "Translation is in progress. RSS uses the original language until it is ready.", "progress", "◷"
        else:
            label, message, tone, icon = "Waiting for translation", "This post is waiting its turn. RSS uses the original language until translation is ready.", "progress", "◷"
    post.update(translation_status=state, translation_label=label, translation_message=message,
                translation_tone=tone, translation_icon=icon, rss_label=rss)
    return post


def present_posts(channel_username, posts):
    background = get_activity()
    with closing(get_connection()) as conn:
        row = conn.execute("SELECT enabled FROM channels WHERE lower(channel_username) = lower(?)", (channel_username,)).fetchone()
    return [present_post(post, channel_enabled=bool(row[0]) if row else True, background=background) for post in posts]


def present_activity(payload):
    result = dict(payload)
    state = payload.get("state")
    if payload.get("connection_error") or state in ("paused", "unavailable", "stopped"):
        reason, _ = service_reason(payload.get("connection_error") or payload.get("message"))
        result["message"] = reason + " RSS remains available."
    elif state == "checking":
        result["message"] = "Checking the translation service. RSS remains available."
    elif payload.get("running") or payload.get("waiting"):
        result["message"] = f"Translating {payload.get('running') or 0} posts; {payload.get('waiting') or 0} waiting."
    else:
        result["message"] = "The translation service is ready."
    result["memory_note"] = "Translations will resume when the service is ready." if payload.get("memory_state") == "critical" else ""
    result.pop("connection_error", None)
    return result
