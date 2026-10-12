"""Online server backups using Python's standard library; never stop services."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED


ARCHIVE_PATTERN = re.compile(r"telegram-feed-\d{8}T\d{12}Z-[0-9a-f]{8}\.zip")


def snapshot_database(source, destination, timeout=120):
    deadline = time.monotonic() + timeout

    def progress(status, remaining, total):
        if time.monotonic() > deadline:
            raise TimeoutError("Database backup exceeded its time limit")

    uri = source.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as reader, \
            closing(sqlite3.connect(destination)) as writer:
        reader.backup(writer, pages=256, progress=progress, sleep=0.1)
        if writer.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise sqlite3.DatabaseError("Backup database integrity check failed")


def add_file(archive, root, relative, prefix="", required=False):
    source = root / relative
    if not source.is_file():
        if required:
            raise FileNotFoundError(f"Required backup file is missing: {relative}")
        return
    if not source.resolve().is_relative_to(root.resolve()):
        raise ValueError("Backup source points outside its selected folder")
    archive.write(source, f"{prefix}{Path(relative).as_posix()}")


def add_tree(archive, root, relative, prefix=""):
    directory = root / relative
    if not directory.exists():
        return
    for source in sorted(directory.rglob("*")):
        if "__pycache__" in source.parts or source.suffix in (".pyc", ".pyo"):
            continue
        if source.is_file():
            add_file(archive, root, source.relative_to(root), prefix)


def verify_archive(path):
    with ZipFile(path) as archive:
        manifest = json.loads(archive.read("backup-manifest.json"))
        if manifest.get("format") != "telegram-feed-backup-v1":
            raise ValueError("Unrecognized backup format")
        required = {"project/telegram.db", "project/.env",
                    "project/sessions/telegram_session.session"}
        if not required.issubset(archive.namelist()):
            raise ValueError("Backup is missing required application data")
        if archive.testzip() is not None:
            raise ValueError("Backup archive integrity check failed")


def create_backup(project, output, caddy, ollama, dns, keep=7):
    project, output, caddy, ollama, dns = map(Path, (project, output, caddy, ollama, dns))
    if keep < 1:
        raise ValueError("Keep must be at least one")
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    filename = f"telegram-feed-{stamp}-{uuid4().hex[:8]}.zip"
    final = output / filename
    partial = output / (filename + ".partial")
    try:
        # Temporary SQLite copies are inside the protected backup folder.
        with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=output) as stage_name:
            stage = Path(stage_name)
            snapshot_database(project / "telegram.db", stage / "telegram.db")
            session = project / "sessions/telegram_session.session"
            snapshot_database(session, stage / "telegram_session.session")
            with ZipFile(partial, "w", ZIP_DEFLATED, compresslevel=1) as archive:
                archive.write(stage / "telegram.db", "project/telegram.db")
                archive.write(stage / "telegram_session.session",
                              "project/sessions/telegram_session.session")
                for name in (".env", "main.py", "requirements-production.txt"):
                    add_file(archive, project, name, "project/", required=True)
                for name in ("requirements.txt", "README.md", "DEPLOYMENT.md"):
                    add_file(archive, project, name, "project/")
                for name in ("app", "scripts", "data/media"):
                    add_tree(archive, project, name, "project/")
                add_file(archive, project, "Services/TelegramFeedService.xml",
                         "project/", required=True)
                for name in ("Caddyfile", "CaddyService.xml"):
                    add_file(archive, caddy, name, "caddy/", required=True)
                add_tree(archive, caddy, "storage", "caddy/")
                add_file(archive, ollama, "OllamaService.xml", "ollama/", required=True)
                for name in ("update.ps1", "token.txt"):
                    add_file(archive, dns, name, "dns/", required=True)
                archive.writestr("backup-manifest.json", json.dumps({
                    "format": "telegram-feed-backup-v1",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "python": sys.version.split()[0],
                    "contains_credentials": True,
                    "sqlite_snapshots": True,
                    "notes": "Online database/session snapshots. Other files are copied during the run; this is not a single transaction across all files. Models, binaries, virtual environments and logs are excluded.",
                }, indent=2))
        verify_archive(partial)
        partial.replace(final)
    finally:
        if partial.exists():
            partial.unlink()

    # Delete only this tool's directly contained backup files, after success.
    archives = sorted((p for p in output.iterdir()
                       if ARCHIVE_PATTERN.fullmatch(p.name) and p.is_file()
                       and not p.is_symlink()), key=lambda p: p.name, reverse=True)
    for old in archives[keep:]:
        if old.resolve().parent != output.resolve():
            raise ValueError("Retention target is outside the backup folder")
        old.unlink()
    return final


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path("C:/TelegramFeed"))
    parser.add_argument("--output", type=Path, default=Path("C:/TelegramFeedBackups"))
    parser.add_argument("--caddy", type=Path, default=Path("C:/Caddy"))
    parser.add_argument("--ollama", type=Path, default=Path("C:/Ollama"))
    parser.add_argument("--dns", type=Path, default=Path("C:/TelegramFeed-DNS"))
    parser.add_argument("--keep", type=int, default=7)
    parser.add_argument("--verify", type=Path, help="Verify an existing archive without creating a backup")
    args = parser.parse_args(argv)
    if args.verify:
        verify_archive(args.verify)
        print("PASS: Backup archive contents and CRC checks are valid.")
        return 0
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        archive = create_backup(args.project, args.output, args.caddy,
                                args.ollama, args.dns, args.keep)
        result = {"status": "ok", "archive": str(archive),
                  "bytes": archive.stat().st_size}
        exit_code = 0
    except Exception as error:
        # Do not print file contents, credentials, or arbitrary exception text.
        result = {"status": "failed", "error_type": type(error).__name__,
                  "message": "Backup failed. Check source files, folder access and available disk space. Existing completed backups were retained."}
        exit_code = 1
    result["checked_at"] = datetime.now(timezone.utc).isoformat()
    (args.output / "last-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
