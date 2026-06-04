"""
Pipeline execution logger with timeline and statistics.

All pipeline output goes through this module to ensure consistent
timestamp prefixes and final summary statistics.

Usage:
    from .logger import plog, psub, count_llm, reset_stats, summary

    reset_stats()              # call once at pipeline start
    plog("node_name", "msg")   # [MM:SS] [node_name] msg
    psub("node_name", "msg")   # [MM:SS]   [node_name] msg (indented)
    count_llm()                # increment LLM call counter
    summary()                  # return stats dict
"""
from __future__ import annotations
import time
import traceback
from datetime import datetime
from threading import Lock

_start: float = 0.0
_node_count: int = 0
_llm_count: int = 0
_lock = Lock()


def reset_stats() -> None:
    """Reset all counters and start the clock. Call once before graph.invoke()."""
    global _start, _node_count, _llm_count
    _start = time.monotonic()
    _node_count = 0
    _llm_count = 0


def _ts() -> str:
    """Elapsed time as MM:SS."""
    if _start <= 0:
        return "00:00"
    e = time.monotonic() - _start
    m, s = divmod(int(e), 60)
    return f"{m:02d}:{s:02d}"


def plog(tag: str, msg: str) -> None:
    """Log a node-level event with timestamp. Increments node counter."""
    global _node_count
    with _lock:
        _node_count += 1
        n = _node_count
    print(f"[{_ts()}] #{n:<3d} [{tag}] {msg}")


def psub(tag: str, msg: str) -> None:
    """Log a sub-step (indented, no counter increment)."""
    print(f"[{_ts()}]      [{tag}] {msg}")


def count_llm() -> None:
    """Increment the LLM API call counter. Call after each successful API request."""
    global _llm_count
    with _lock:
        _llm_count += 1


def get_llm_count() -> int:
    """Current LLM call count (for inline display)."""
    return _llm_count


def summary() -> dict:
    """Return pipeline execution statistics."""
    e = time.monotonic() - _start if _start > 0 else 0
    m, s = divmod(int(e), 60)
    return {
        "elapsed": f"{m}분 {s}초",
        "elapsed_raw": e,
        "nodes": _node_count,
        "llm_calls": _llm_count,
    }


_LOG_FILE = "pipeline_error.log"


def log_error(
    node_name: str,
    error: Exception,
    stack_trace: str = "",
) -> None:
    """Append a node-level failure record to pipeline_error.log (append mode).

    Args:
        node_name:   Name of the failing node or call site.
        error:       The exception that was raised.
        stack_trace: Pre-formatted traceback string.
                     If omitted, uses traceback.format_exc().
    """
    timestamp = datetime.now().isoformat(timespec="seconds")
    error_type = type(error).__name__
    error_msg = str(error)
    tb_str = stack_trace if stack_trace else traceback.format_exc()

    entry = (
        f"[{timestamp}] node={node_name} error_type={error_type}\n"
        f"message: {error_msg}\n"
        f"traceback:\n{tb_str}"
        f"{'\u2500' * 60}\n"
    )

    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(entry)
    except OSError as oe:
        print(f"[WARN] {_LOG_FILE} 기록 실패: {oe}")
