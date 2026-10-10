"""Deterministic sample channel: no database writes, model calls or publication."""

from app.dashboard.post_status import present_post


def get_demo_posts():
    samples = [
        ("Translation completed", "completed", "The new community library opens on Monday.", "تُفتتح مكتبة الحي الجديدة يوم الاثنين.", "", {}),
        ("Waiting in the queue", "pending", "The council will discuss improvements to the park this evening.", None, "", {}),
        ("Translation in progress", "running", "New bus routes will connect the city centre with the university.", None, "", {}),
        ("Channel paused", "pending", "The weekend market will return to the town square.", None, "", {"channel_enabled": False}),
        ("Service temporarily paused", "pending", "Registration for the summer programme opens next week.", None, "", {"background": {"state": "paused"}}),
        ("Translation turned off", "disabled", "The swimming pool will close at six today.", None, "", {}),
        ("Same source and target language", "skipped", "A new walking trail opens this weekend.", None, "", {}),
        ("Service unavailable", "unavailable", "Local volunteers will plant trees along the river.", None, "We couldn't reach Ollama.", {}),
        ("Translation setup incomplete", "unavailable", "The annual science fair will take place on Thursday.", None, "The selected model isn't installed.", {}),
        ("Language settings need attention", "unavailable", "The museum has announced extended opening hours.", None, "Check the channel's translation languages.", {}),
        ("Translation timed out", "failed", "The rail operator will publish an updated timetable tomorrow.", None, "Ollama is taking longer than expected.", {}),
        ("Incomplete translation", "failed", "Students will present their projects at the exhibition.", None, "The translation could not be completed.", {}),
        ("Published before translation was ready", "source_final", "The city's new public garden is now open.", None, "Translation wasn't ready when RSS published this post.", {}),
        ("Published during a service outage", "source_final", "The harbour ferry will resume its normal schedule on Friday.", None, "We couldn't reach Ollama.", {}),
        ("Invalid text rule", "processing_error", "The road will reopen after maintenance is completed.", None, "A replacement rule is invalid.", {}),
        ("Photo with a translated caption", "completed", "A view of the city at sunset.", "منظر للمدينة عند غروب الشمس.", "", {}),
        ("Photo without a caption", "skipped", "", None, "", {}),
        ("Video with an original-language caption", "source_final", "Highlights from the community concert.", None, "Translation wasn't ready when RSS published this post.", {}),
        ("Document with translation disabled", "disabled", "The full event programme is attached.", None, "", {}),
        ("All news text removed by a rule", "skipped", "ADVERTISEMENT", None, "", {}),
        ("Translation service busy", "unavailable", "The community centre is accepting bookings for autumn events.", None, "Ollama is busy right now.", {}),
        ("Service configuration needs attention", "unavailable", "The theatre has announced its new season.", None, "Translation isn't set up correctly.", {}),
        ("Published with a text-rule warning", "source_final", "The lakeside walking route reopens on Saturday.", None, "A replacement rule is invalid.", {}),
    ]
    posts = []
    for index, (title, state, original, translated, error, context) in enumerate(samples, 1):
        body = "" if index == 20 else translated if translated is not None else original
        media_type = "photo" if index in (16, 17) else "video" if index == 18 else "document" if index == 19 else "text"
        media_url = "/static/demo/city.svg" if media_type == "photo" else "/static/demo/programme.txt" if media_type == "document" else None
        post = dict(id=index, message_id=index, channel_username="demo_news", date=f"2026-10-11T09:{index:02d}:00+00:00",
                    text=original, body_text=body, output_text=f"Latest news\n\n{body}\n\nCommunity updates" if body else "Community updates",
                    translation_status=state, translation_label="Translated · Arabic" if state == "completed" else state,
                    translation_message=error, message_type=media_type, media_path=None, media_url=media_url,
                    demo_title=title, demo_video=media_type == "video")
        posts.append(present_post(post, **context))
    return posts
