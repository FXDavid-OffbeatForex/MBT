"""Extract allowlisted MT5 tester diagnostics without retaining private log lines."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

_MAX_BYTES = 2 * 1024 * 1024
_PASS = re.compile(r"\boptimization (finished|already processed), total passes (\d+)\b", re.I)
_FORWARD = re.compile(r"\bforward optimization (finished|already processed), total passes (\d+)\b", re.I)
_FORWARD_CACHE = re.compile(r"\breading of (\d+) forward result records from cache\b", re.I)
_GENETIC_OFF = re.compile(r"\bgenetic mode turned off due to (\d+) passes\b", re.I)
_GENETIC_FINISHED = re.compile(r"\bgenetic optimization finished on pass (\d+) \(of (\d+)\)", re.I)
_TASKS = re.compile(r"\blocal (\d+) tasks \([^)]*\), remote (\d+) tasks \([^)]*\), cloud (\d+) tasks\b", re.I)
_CACHE_USES = re.compile(r"\bresult cache used (\d+) times\b", re.I)
_CACHE_RECORDS = re.compile(r"\b(\d+) new records saved to cache file\b", re.I)


def tester_log_snapshot(data_dir: Path) -> tuple[Path, int]:
    """Record the current tester log size just before a controlled MT5 launch."""
    path = data_dir / "Tester" / "logs" / (datetime.now().strftime("%Y%m%d") + ".log")
    return path, path.stat().st_size if path.is_file() else 0


def parse_tester_log_lines(text: str) -> dict:
    """Parse only known diagnostics; no raw log text/account information escapes."""
    result: dict = {}
    for line in text.splitlines():
        if match := _FORWARD.search(line):
            result["forward_reported_passes"] = int(match.group(2))
            result["forward_cache_reused"] = match.group(1).lower() == "already processed"
        elif match := _PASS.search(line):
            result["mt5_reported_passes"] = int(match.group(2))
            result["optimization_cache_reused"] = match.group(1).lower() == "already processed"
        if match := _FORWARD_CACHE.search(line):
            result["forward_cached_records"] = int(match.group(1))
        if match := _GENETIC_OFF.search(line):
            result["genetic_disabled_small_grid"] = True
            result["genetic_disabled_passes"] = int(match.group(1))
        if match := _GENETIC_FINISHED.search(line):
            result["genetic_finished"] = True
            result["genetic_pass_counter"] = int(match.group(1))
            result["genetic_search_space"] = int(match.group(2))
        if match := _TASKS.search(line):
            result["local_tasks"] = int(match.group(1))
            result["remote_tasks"] = int(match.group(2))
            result["cloud_tasks"] = int(match.group(3))
        if match := _CACHE_USES.search(line):
            result["result_cache_uses"] = int(match.group(1))
        if match := _CACHE_RECORDS.search(line):
            result["new_cache_records"] = int(match.group(1))
        if "complete optimization started" in line.lower():
            result["effective_mode"] = "complete"
        elif "genetic optimization started" in line.lower():
            result["effective_mode"] = "genetic"
        if "optimization done in" in line.lower():
            result["statistics_done"] = True
    return result


def _tester_log_delta(snapshot: tuple[Path, int]) -> str | None:
    """Bounded private reader; raw appended log text must never be returned by tools."""
    path, offset = snapshot
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0 or not path.is_file():
        return None
    size = path.stat().st_size
    if size < offset or size - offset > _MAX_BYTES:
        return None
    with path.open("rb") as stream:
        prefix = stream.read(4)
        stream.seek(offset)
        raw = stream.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        return None
    if prefix.startswith(b"\xff\xfe"):
        text = raw.decode("utf-16-le", errors="replace")
    elif prefix.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16-be", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")
    return text


def tester_log_diagnostics(snapshot: tuple[Path, int]) -> dict:
    text = _tester_log_delta(snapshot)
    if text is None:
        return {"log_available": False}
    result = parse_tester_log_lines(text)
    result["log_available"] = True
    return result


def tester_log_commission(snapshot: tuple[Path, int], evidence: dict) -> dict:
    """Verify native profile application from this launch's appended log only.

    Returns just the exact profile name/hash and a verified flag. Missing,
    oversized, replaced or stale diagnostics cannot prove the native profile.
    """
    from .commission_profile import verify_commission_log
    text = _tester_log_delta(snapshot)
    if text is None:
        raise ValueError('Native commission application cannot be verified from this tester log delta')
    return verify_commission_log(text, evidence)
