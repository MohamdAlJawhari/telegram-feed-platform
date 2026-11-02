"""Preview or remove old downloaded media, optionally including old posts.

Stop TelegramFeed before --apply. No network access or third-party dependencies.
"""

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3


def is_link(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def cleanup(project, days=7, *, apply=False, delete_posts=False):
    if days < 1:
        raise ValueError("Days must be at least 1")
    project = Path(project).resolve(strict=True)
    database = project / "telegram.db"
    media = project / "data" / "media"
    # Never follow a media root redirected outside the selected project.
    if is_link(media) or not media.resolve().is_relative_to(project):
        raise ValueError("Media root must be an ordinary directory inside the project")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    mode = "rw" if apply else "ro"
    with closing(sqlite3.connect(database.as_uri() + f"?mode={mode}", uri=True)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        rows = connection.execute(
            "SELECT id, media_path, julianday(date) < julianday(?) FROM posts",
            (cutoff.isoformat(),),
        ).fetchall()
        old_ids = [post_id for post_id, _, old in rows if old]
        # Unparseable dates are retained. A file referenced by any retained post
        # must not be removed just because its filesystem timestamp is old.
        referenced = {}
        paths_by_post = {}
        for post_id, relative, old in rows:
            if not relative:
                continue
            target = (media / relative).resolve()
            if not target.is_relative_to(media.resolve()):
                raise ValueError(f"Post {post_id} has a media path outside the media directory")
            referenced.setdefault(target, []).append(bool(old))
            paths_by_post[post_id] = target

        candidates = []
        if media.exists():
            # Do not traverse symlinks or Windows junctions.
            pending = [media]
            while pending:
                for path in pending.pop().iterdir():
                    if is_link(path):
                        continue
                    resolved = path.resolve()
                    if not resolved.is_relative_to(media.resolve()):
                        raise ValueError("Media file points outside the media directory")
                    if path.is_dir():
                        # Profile photos have their own refresh lifecycle.
                        if path != media / "channel-profiles":
                            pending.append(path)
                    elif path.is_file():
                        ages = referenced.get(resolved)
                        old = all(ages) if ages else path.stat().st_mtime < cutoff.timestamp()
                        if old:
                            candidates.append(path)

        total_bytes = sum(path.stat().st_size for path in candidates)
        print(f"Cutoff (UTC): {cutoff.isoformat()}")
        print(f"Media files: {len(candidates)} ({total_bytes / 1024**3:.3f} GiB)")
        print(f"Posts to delete: {len(old_ids) if delete_posts else 0}")
        if not apply:
            print("PREVIEW ONLY. Stop TelegramFeed, then rerun with --apply to delete.")
            return

        # Commit link removal first. If a file cannot be deleted, it is an orphan
        # that a later run can retry, rather than a broken dashboard/RSS link.
        selected = {path.resolve() for path in candidates}
        connection.executemany(
            "UPDATE posts SET media_path=NULL WHERE id=?",
            [(post_id,) for post_id, target in paths_by_post.items() if target in selected or not target.exists()],
        )
        if delete_posts:
            for table in ("post_translations", "translation_requests", "rss_source_fallbacks", "translation_jobs"):
                connection.executemany(f"DELETE FROM {table} WHERE post_id=?", [(value,) for value in old_ids])
            connection.executemany("DELETE FROM posts WHERE id=?", [(value,) for value in old_ids])
        connection.commit()
        failures = []
        for path in candidates:
            try:
                path.unlink()
            except OSError as error:
                failures.append(str(error))
        # Reclaim database pages only when posts were deleted. Media-only cleanup
        # saves disk space directly without needing to rebuild the database.
        if delete_posts:
            connection.execute("VACUUM")
        if failures:
            raise OSError("Some files could not be deleted; their database links were cleared. Inspect these errors:\n" + "\n".join(failures))
        print("Cleanup completed. Channel settings, login session and backups were retained.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--apply", action="store_true", help="Delete selected data; stop TelegramFeed first")
    parser.add_argument("--delete-posts", action="store_true", help="Also delete old posts and their translation records")
    arguments = parser.parse_args()
    cleanup(arguments.project, arguments.days, apply=arguments.apply, delete_posts=arguments.delete_posts)


if __name__ == "__main__":
    main()
