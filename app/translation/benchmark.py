"""Measure a ten-channel translation burst using the real background worker."""

import argparse
import asyncio
import io
import json
import logging
import math
import statistics
import sys
import tempfile
import time
from contextlib import closing, redirect_stdout
from dataclasses import asdict
from pathlib import Path

from app.database import database
from app.database.models import save_post, update_channel_settings, upsert_channel
from app.dashboard.translation_errors import translation_error_message
from app.llm.ollama_client import OllamaClient
from app.translation.check_workers import DemoClient
from app.translation.languages import normalize_language
from app.translation.service import TranslationConfig, TranslationService
from app.translation.worker import TranslationWorker, WorkerConfig


# Invented news samples of different lengths; no live news or project data is read.
SAMPLE_POSTS = [
    "The city library will reopen on Monday after repairs to its roof. Opening hours remain unchanged.",
    "Rail services between North Station and the airport resumed at 08:30 after engineers repaired a signal fault. Passengers should check the departure board because some morning services remain delayed.",
    "The local council approved a new public park beside the river. Construction is expected to begin in March, and the first section should open before the end of the year. The proposal includes accessible paths, shaded seating and a children's play area.",
    "Heavy rain is forecast overnight. Residents in low-lying areas have been asked to monitor local announcements and avoid roads covered by water. The transport office said it would publish any route changes before the morning commute.",
    "The regional hospital has opened an evening clinic for routine appointments. It will operate from 17:00 to 21:00 on weekdays. Patients must book in advance; emergency services will continue to operate around the clock.",
    "A new training programme will offer free computer classes to 120 residents.\n\nApplications open on Tuesday and close on 15 June. Participants can choose morning or evening sessions, and equipment will be provided during each class.",
    "The university announced a research partnership with three local schools. Teachers and students will measure air quality near busy roads over the next six months. The collected data will be published in a public report after the study ends.",
    "The national basketball team won its opening match 78–72 on Friday. The coach praised the team's defence in the final quarter. Its next game is scheduled for Sunday at 19:00.",
    "A small business grant programme will accept applications from 1 September. Eligible businesses can request support for equipment, staff training or energy-saving improvements. Applicants must provide a budget and explain how the proposed investment will benefit their business.",
    "The museum will host a weekend exhibition featuring photographs from the town's archives.\n\nAdmission is free. Guided tours begin at 10:00 and 14:00, and visitors are encouraged to book a place because each tour is limited to twenty people. The exhibition closes on Sunday evening.",
]


class BenchmarkError(Exception):
    pass


def load_posts(path=None):
    posts = SAMPLE_POSTS if path is None else json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(posts, list) or len(posts) != 10 or any(
        not isinstance(text, str) or not text.strip() for text in posts
    ):
        raise BenchmarkError("The input file must contain a JSON array of exactly ten nonempty news texts.")
    return list(posts)


class TimedWorker(TranslationWorker):
    """Observe job timings without changing scheduling, prompts or saved results."""

    def __init__(self, *, submitted_at, **kwargs):
        super().__init__(**kwargs)
        self.submitted_at = submitted_at
        self.timings = {}

    async def _run_job(self, job):
        started = time.perf_counter()
        self.timings[job["post_id"]] = {"queue_wait_seconds": started - self.submitted_at}
        try:
            await super()._run_job(job)
        finally:
            finished = time.perf_counter()
            self.timings[job["post_id"]].update(
                service_seconds=finished - started,
                finished_after_seconds=finished - self.submitted_at,
            )


def read_jobs():
    with closing(database.get_connection()) as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM translation_jobs ORDER BY post_id")]


async def measure_round(client, config, worker_config, posts, source, target, *,
                        concurrency, round_timeout, progress=None):
    """A fresh temporary database per round prevents cached jobs skipping generation."""
    original_database = database.DATABASE_NAME
    worker = None
    with tempfile.TemporaryDirectory(prefix="translation-benchmark-") as directory:
        database.DATABASE_NAME = str(Path(directory) / "benchmark.db")
        try:
            # Populate the burst before starting its clock. Silence save_post's CLI output.
            with redirect_stdout(io.StringIO()):
                database.create_database()
                for index, text in enumerate(posts, 1):
                    channel = str(index)
                    upsert_channel(channel, f"Benchmark {index}", f"benchmark_{index}")
                    update_channel_settings(
                        channel, "", "", "", "", translation_enabled=True,
                        source_language=source, target_language=target,
                    )
                    save_post(channel, f"Benchmark {index}", f"benchmark_{index}", 1,
                              text, "text", None, "2026-10-09")
            submitted_at = time.perf_counter()
            worker = TimedWorker(
                client=client, translation_config=config, submitted_at=submitted_at,
                worker_config=WorkerConfig(concurrency, worker_config.poll_seconds, worker_config.job_timeout),
            )
            await worker.start()
            last_progress = submitted_at
            abort_reason = None
            while True:
                jobs = await asyncio.to_thread(read_jobs)
                terminal = sum(job["status"] in ("completed", "failed") for job in jobs)
                elapsed = time.perf_counter() - submitted_at
                # Wait for observers too: completion can commit just before the task returns.
                if len(jobs) == len(posts) and terminal == len(posts) and all(
                    "finished_after_seconds" in worker.timings.get(job["post_id"], {}) for job in jobs
                ):
                    break
                if worker.error_message:
                    abort_reason = worker.error_message
                    break
                if worker.task.done():
                    abort_reason = "The worker stopped unexpectedly. Check its warnings above."
                    break
                if elapsed >= round_timeout:
                    abort_reason = f"The round exceeded its {round_timeout:g}-second time limit."
                    break
                if progress and time.perf_counter() - last_progress >= 10:
                    progress(f"  {terminal}/{len(posts)} finished after {elapsed:.1f}s")
                    last_progress = time.perf_counter()
                await asyncio.sleep(0.05)
            elapsed = time.perf_counter() - submitted_at
            await worker.stop()
            jobs = await asyncio.to_thread(read_jobs)
            completed = [job for job in jobs if job["status"] == "completed"]
            records = []
            for index, text in enumerate(posts, 1):
                job = next((job for job in jobs if job["post_id"] == index), None)
                records.append({
                    "channel": index, "original_text": text,
                    "status": job["status"] if job else "not_queued",
                    "translated_text": job["output_text"] if job else None,
                    "error_type": job["error_type"] if job else None,
                    **worker.timings.get(index, {}),
                })
            waits = [record["queue_wait_seconds"] for record in records if "queue_wait_seconds" in record]
            finishes = [record["finished_after_seconds"] for record in records if record["status"] == "completed"]
            valid = len(completed) == len(posts) and abort_reason is None
            return {
                "concurrency": concurrency, "successful": valid, "completed": len(completed),
                "failed": sum(job["status"] == "failed" for job in jobs),
                "unfinished": len(posts) - len(completed) - sum(job["status"] == "failed" for job in jobs),
                "abort_reason": abort_reason, "total_seconds": elapsed,
                "average_queue_wait_seconds": statistics.mean(waits) if waits else None,
                "longest_queue_wait_seconds": max(waits) if waits else None,
                "average_completion_seconds": statistics.mean(finishes) if finishes else None,
                "last_completion_seconds": max(finishes) if finishes else None,
                "posts_per_minute": len(completed) * 60 / elapsed if valid else None,
                "posts": records,
            }
        finally:
            try:
                if worker is not None:
                    await worker.stop()
            finally:
                database.DATABASE_NAME = original_database


def format_seconds(value):
    return "—" if value is None else f"{value:.1f}"


def print_results(report):
    print("\nSIMULATED RESULTS — these do not measure your computer's model speed." if report["simulated"]
          else "\nMeasured results (seconds; ten posts submitted together):")
    print("Slots  Run  OK  Fail  Unfinished  Total   Avg wait  Max wait  Last ready  Posts/min")
    for result in report["rounds"]:
        print(f"{result['concurrency']:>5}  {result['repeat']:>3}  {result['completed']:>2}"
              f"  {result['failed']:>4}  {result['unfinished']:>10}"
              f"  {result['total_seconds']:>5.1f}  {format_seconds(result['average_queue_wait_seconds']):>9}"
              f"  {format_seconds(result['longest_queue_wait_seconds']):>8}"
              f"  {format_seconds(result['last_completion_seconds']):>10}"
              f"  {format_seconds(result['posts_per_minute']):>9}")
        if result["abort_reason"]:
            print("  Stopped: " + result["abort_reason"])
    print("Wait = submission to application job start; includes scheduling/polling.")
    print("Last ready = submission to last successful job finish; includes Ollama's own queue.")
    print("Actual parallel inference depends on Ollama's server capacity and available memory.")
    print("These results measure speed and delivery delay, not translation accuracy.")


async def benchmark(args):
    posts = load_posts(args.file)
    source, target = normalize_language(args.source), normalize_language(args.target)
    if source == target:
        raise BenchmarkError("Choose different source and target languages to measure real translation work.")
    config = TranslationConfig.from_env()
    worker_config = WorkerConfig.from_env()
    client = DemoClient() if args.simulate else OllamaClient()
    report = {
        "simulated": args.simulate, "model": config.model,
        "source_language": source, "target_language": target,
        "declared_server_parallel": args.server_parallel,
        "worker_config": asdict(worker_config), "translation_config": asdict(config),
        "post_count": len(posts), "requested_runs": len(args.limits) * args.repeats, "rounds": [],
    }
    try:
        model = await client.require_model(config.model)
        digest = model.get("digest", "")
        if not isinstance(digest, str) or not digest.strip():
            raise BenchmarkError("Ollama did not return an installed model digest.")
        report["model_digest"] = digest
        print("SIMULATION: no real Ollama calls or hardware measurements." if args.simulate
              else f"Model: {config.model}; languages: {source} -> {target}", flush=True)
        print(f"Ten channels, one post each; limits: {', '.join(map(str, args.limits))}.")
        print(f"Application poll interval: {worker_config.poll_seconds:g}s; job timeout: {worker_config.job_timeout:g}s.")
        print("Ollama server capacity: " + (f"{args.server_parallel} (declared by you; not verified)"
                                             if args.server_parallel else "unknown; cannot be read through this API"))
        print("The benchmark does not change Ollama settings. Use an idle Ollama server for comparable results.")
        print("Warming the model once before timing the rounds...", flush=True)
        warm_started = time.perf_counter()
        await asyncio.wait_for(TranslationService(client, config).translate(posts[0], source, target),
                               timeout=worker_config.job_timeout)
        report["warmup_seconds"] = time.perf_counter() - warm_started
        print(f"Warm-up finished in {report['warmup_seconds']:.1f}s (excluded from rounds).", flush=True)
        for repeat in range(1, args.repeats + 1):
            # Reverse order on alternate repeats to reduce ordering bias.
            limits = args.limits if repeat % 2 else list(reversed(args.limits))
            for limit in limits:
                if (await client.require_model(config.model)).get("digest") != digest:
                    raise BenchmarkError("The installed model changed during the benchmark. Run it again.")
                print(f"\nStarting limit {limit}, run {repeat}/{args.repeats}...", flush=True)
                result = await measure_round(
                    client, config, worker_config, posts, source, target, concurrency=limit,
                    round_timeout=args.round_timeout, progress=lambda message: print(message, flush=True),
                )
                result["repeat"] = repeat
                report["rounds"].append(result)
                print(f"  {result['completed']}/10 completed; total {result['total_seconds']:.1f}s", flush=True)
                if result["abort_reason"]:
                    return report
        return report
    except Exception as error:
        if not report["rounds"]:
            raise
        report["abort_reason"] = str(error) if isinstance(error, BenchmarkError) else translation_error_message(error)
        return report
    finally:
        if not args.simulate:
            await client.aclose()


def positive_seconds(value):
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("Use positive finite seconds.")
    return result


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--limits", nargs="+", type=int, choices=range(1, 9), default=[1, 2, 4])
    result.add_argument("--repeats", type=int, choices=range(1, 6), default=1)
    result.add_argument("--source", default="en")
    result.add_argument("--target", default="fr")
    result.add_argument("--file", help="UTF-8 JSON array of exactly ten representative news texts.")
    result.add_argument("--output", help="Save a JSON report including source texts, translations and job timings.")
    result.add_argument("--server-parallel", type=int, choices=range(1, 9), help="Your declared Ollama parallel capacity; this does not configure or verify the server.")
    result.add_argument("--round-timeout", type=positive_seconds, default=1800)
    result.add_argument("--simulate", action="store_true", help="Check the benchmark logic offline; results are not hardware measurements.")
    return result


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING)
    args = parser().parse_args()
    args.limits = list(dict.fromkeys(args.limits))
    try:
        report = asyncio.run(benchmark(args))
        print_results(report)
        if report.get("abort_reason"):
            print("Benchmark stopped: " + report["abort_reason"])
        if args.output:
            Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"JSON report saved to {args.output}")
        print("Only temporary benchmark databases were used; project posts and settings are unchanged.")
        complete = len(report["rounds"]) == report["requested_runs"] and not report.get("abort_reason")
        return 0 if complete and all(result["successful"] for result in report["rounds"]) else 1
    except KeyboardInterrupt:
        print("Benchmark cancelled.", file=sys.stderr)
        return 130
    except (BenchmarkError, ValueError, OSError) as error:
        print(f"Benchmark failed: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"Benchmark failed: {translation_error_message(error)} ({type(error).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
