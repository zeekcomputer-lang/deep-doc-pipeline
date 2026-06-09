"""
중간 산출물 저장 모듈 — v3.0.

실행마다 output/YYYYMMDD_HHMMSS/ 디렉토리를 생성하고,
각 Phase 완료 시점의 산출물을 파일로 저장합니다.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .logger import plog

# ── 모듈 레벨 상태 ──────────────────────────────────────────
_run_dir: Optional[Path] = None


def init_run_dir(base: str = "output") -> Path:
    """실행 디렉토리 생성. Windows 호환 파일명 (YYYYMMDD_HHMMSS).

    Returns:
        생성된 디렉토리 경로 (예: output/20260604_162400/)
    """
    global _run_dir
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    _run_dir = Path(base) / ts
    _run_dir.mkdir(parents=True, exist_ok=True)
    plog("artifacts", f"run dir: {_run_dir}")
    return _run_dir


def set_run_dir(path: Path) -> None:
    """외부에서 run_dir을 직접 설정."""
    global _run_dir
    _run_dir = path


def get_run_dir() -> Optional[Path]:
    return _run_dir


# ── 저장 함수 ────────────────────────────────────────────────

def save_json(name: str, data: Any) -> None:
    """JSON 아티팩트 저장. run_dir 미설정 시 무시."""
    if _run_dir is None:
        return
    path = _run_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def save_text(name: str, text: str) -> None:
    """텍스트 아티팩트 저장. run_dir 미설정 시 무시."""
    if _run_dir is None:
        return
    path = _run_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def list_runs(base: str = "output") -> list[Path]:
    """output/ 하위 실행 디렉토리 목록 (최신순)."""
    base_dir = Path(base)
    if not base_dir.is_dir():
        return []
    runs = sorted(
        [d for d in base_dir.iterdir() if d.is_dir() and d.name[0].isdigit()],
        reverse=True,
    )
    return runs
