"""
중간 산출물 저장/로드 모듈.

실행마다 output/YYYYMMDD_HHMMSS/ 디렉토리를 생성하고,
각 Phase 완료 시점의 산출물을 파일로 저장합니다.

--resume <run_dir> 로 이전 실행의 산출물을 로드하여
특정 Phase부터 재실행할 수 있습니다.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

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
    """외부에서 run_dir을 직접 설정 (resume 시 사용)."""
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


# ── 로드 함수 (resume용) ──────────────────────────────────────

def _load_json_safe(path: Path) -> Any:
    """JSON 파일 로드. 없으면 None."""
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_text_safe(path: Path) -> Optional[str]:
    """텍스트 파일 로드. 없으면 None."""
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def load_run_state(run_dir: str | Path) -> Dict[str, Any]:
    """이전 실행 디렉토리에서 GraphState 복원.

    존재하는 아티팩트만 로드. 없는 필드는 기본값으로 남김.
    resume_from 단계에 따라 필요한 데이터가 달라짐:
      - translate: english_output + proper_nouns (+ final_output as english)
      - polish:    final_compiled + outline + completed_sections + grouped_chunks
    """
    d = Path(run_dir)
    if not d.is_dir():
        raise FileNotFoundError(f"run directory not found: {d}")

    state: Dict[str, Any] = {
        "raw_docs": [],
        "extracted_events": [],
        "period_summaries": {},
        "completed_sections": {},
        "unverified_sections": [],
        "hallucinated_tokens": [],
        "outline_retry_count": 0,
        "section_retry_count": 0,
        "proper_nouns": [],
    }

    # Phase 1
    ev = _load_json_safe(d / "phase1_extracted_events.json")
    if ev is not None:
        state["extracted_events"] = ev

    gc = _load_json_safe(d / "phase1_grouped_chunks.json")
    if gc is not None:
        state["grouped_chunks"] = gc

    # Phase 2
    ps = _load_json_safe(d / "phase2_period_summaries.json")
    if ps is not None:
        state["period_summaries"] = ps

    gt = _load_text_safe(d / "phase2_global_theme.txt")
    if gt is not None:
        state["global_theme"] = gt

    # Phase 3
    ol = _load_json_safe(d / "phase3_outline.json")
    if ol is not None:
        state["outline"] = ol
        state["is_outline_approved"] = True

    # Phase 4 — sections
    sec_dir = d / "phase4_sections"
    if sec_dir.is_dir():
        sections: Dict[int, str] = {}
        for f in sorted(sec_dir.glob("section_*.md")):
            try:
                idx = int(f.stem.split("_")[1])
                sections[idx] = f.read_text(encoding="utf-8")
            except (ValueError, IndexError):
                pass
        if sections:
            state["completed_sections"] = sections

    compiled = _load_text_safe(d / "phase4_compiled_en.md")
    if compiled is not None:
        state["final_compiled"] = compiled

    polished = _load_text_safe(d / "phase4_polished_en.md")
    if polished is not None:
        state["final_output"] = polished

    # Phase 5
    en_out = _load_text_safe(d / "phase5_output_en.md")
    if en_out is not None:
        state["english_output"] = en_out

    nouns = _load_json_safe(d / "phase5_proper_nouns.json")
    if nouns is not None:
        state["proper_nouns"] = nouns

    kr_out = _load_text_safe(d / "phase5_output_kr.md")
    # kr_out is the final product; not loaded into state for resume

    plog("artifacts", f"loaded from {d}: "
         f"events={len(state.get('extracted_events', []))} "
         f"summaries={len(state.get('period_summaries', {}))} "
         f"sections={len(state.get('completed_sections', {}))} "
         f"english={'yes' if state.get('english_output') else 'no'}")

    return state


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
