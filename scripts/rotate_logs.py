#!/usr/bin/env python3
"""
Log rotation for validated-observations.jsonl.

Strategy
--------
- Observations newer than --keep-days (default 30) stay in the main JSONL as-is.
- Observations between --thin-after-days (default 7) and --keep-days are thinned
  to one reading per hour (the latest in each hour bucket).
- Observations older than --keep-days are archived: grouped by UTC date and
  written to logs/archive/YYYY-MM-DD.jsonl.gz (appended if the file exists).
- The main JSONL is atomically rewritten with only the kept/thinned data.

Run manually:   .venv/bin/python scripts/rotate_logs.py
Via ssa:        ssa rotate [--keep-days N] [--thin-after-days N] [--dry-run]
Scheduled:      add to the watchdog systemd timer or a cron job.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
DEFAULT_LOG_PATH = ROOT_DIR / "logs" / "validated-observations.jsonl"
DEFAULT_ARCHIVE_DIR = ROOT_DIR / "logs" / "archive"


def _parse_observed_at(obs: dict) -> datetime | None:
    raw = obs.get("observedAt")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        normalized = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _hour_bucket(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def rotate(
    log_path: Path,
    archive_dir: Path,
    keep_days: int,
    thin_after_days: int,
    dry_run: bool,
) -> dict:
    if not log_path.exists():
        return {"skipped": True, "reason": "log file not found"}

    now = datetime.now(tz=timezone.utc)
    keep_cutoff = now - timedelta(days=keep_days)
    thin_cutoff = now - timedelta(days=thin_after_days)

    # Read all observations
    observations: list[dict] = []
    with log_path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                observations.append(json.loads(stripped))
            except json.JSONDecodeError:
                pass  # silently skip malformed lines

    total_in = len(observations)

    # Bucket into three groups
    recent: list[dict] = []              # keep verbatim (< thin_after_days old)
    thin_candidates: list[dict] = []     # thin to one-per-hour
    archive_candidates: list[dict] = []  # archive and remove from main log

    for obs in observations:
        dt = _parse_observed_at(obs)
        if dt is None:
            recent.append(obs)  # can't date it — keep as-is
            continue
        if dt >= thin_cutoff:
            recent.append(obs)
        elif dt >= keep_cutoff:
            thin_candidates.append(obs)
        else:
            archive_candidates.append(obs)

    # Thin: keep latest per hour bucket
    hour_map: dict[datetime, dict] = {}
    for obs in thin_candidates:
        dt = _parse_observed_at(obs)
        if dt is None:
            recent.append(obs)
            continue
        bucket = _hour_bucket(dt)
        hour_map[bucket] = obs  # later entries overwrite earlier ones
    thinned = list(hour_map.values())
    thinned.sort(key=lambda o: str(o.get("observedAt") or ""))

    # Archive: group by UTC date → logs/archive/YYYY-MM-DD.jsonl.gz
    by_date: dict[str, list[dict]] = defaultdict(list)
    for obs in archive_candidates:
        dt = _parse_observed_at(obs)
        date_key = dt.strftime("%Y-%m-%d") if dt else "undated"
        by_date[date_key].append(obs)

    archived_count = 0
    if not dry_run and by_date:
        archive_dir.mkdir(parents=True, exist_ok=True)
        for date_key, entries in sorted(by_date.items()):
            gz_path = archive_dir / f"{date_key}.jsonl.gz"
            mode = "ab" if gz_path.exists() else "wb"
            with gzip.open(gz_path, mode) as gz:
                for obs in entries:
                    gz.write((json.dumps(obs, separators=(",", ":")) + "\n").encode("utf-8"))
            archived_count += len(entries)

    # Rewrite main JSONL with recent + thinned data
    kept = recent + thinned
    kept.sort(key=lambda o: str(o.get("observedAt") or ""))
    total_out = len(kept)

    if not dry_run:
        tmp_fd, tmp_path = tempfile.mkstemp(dir=log_path.parent, suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                for obs in kept:
                    fh.write(json.dumps(obs, separators=(",", ":")) + "\n")
            os.replace(tmp_path, log_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    return {
        "dry_run": dry_run,
        "total_in": total_in,
        "kept_recent": len(recent),
        "kept_thinned": len(thinned),
        "archived": len(archive_candidates),
        "total_out": total_out,
        "removed": total_in - total_out,
        "archive_dates": sorted(by_date.keys()),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep-days", type=int, default=30,
                        help="Keep raw observations for this many days (default 30)")
    parser.add_argument("--thin-after-days", type=int, default=7,
                        help="Thin observations older than this to one-per-hour (default 7)")
    parser.add_argument("--log-file", default=str(DEFAULT_LOG_PATH))
    parser.add_argument("--archive-dir", default=str(DEFAULT_ARCHIVE_DIR))
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would happen without modifying any files")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.thin_after_days >= args.keep_days:
        print(
            f"Error: --thin-after-days ({args.thin_after_days}) must be less than "
            f"--keep-days ({args.keep_days})",
            file=sys.stderr,
        )
        return 1

    result = rotate(
        log_path=Path(args.log_file),
        archive_dir=Path(args.archive_dir),
        keep_days=args.keep_days,
        thin_after_days=args.thin_after_days,
        dry_run=args.dry_run,
    )

    if result.get("skipped"):
        print(f"Skipped: {result['reason']}")
        return 0

    prefix = "[DRY RUN] " if result["dry_run"] else ""
    print(f"{prefix}Rotation complete:")
    print(f"  Input observations : {result['total_in']:,}")
    print(f"  Kept (recent)      : {result['kept_recent']:,}")
    print(f"  Kept (thinned)     : {result['kept_thinned']:,}  (one-per-hour for days {args.thin_after_days}–{args.keep_days})")
    print(f"  Archived           : {result['archived']:,}  → {args.archive_dir}/")
    print(f"  Removed from main  : {result['removed']:,}")
    print(f"  Output observations: {result['total_out']:,}")
    if result["archive_dates"]:
        print(f"  Archive dates      : {', '.join(result['archive_dates'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
