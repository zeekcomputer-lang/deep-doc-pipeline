"""
파이프라인 실행 진입점 — v3.0.

사용법:
    python -m main                              # 전체 실행
    python -m main --skip-fact-check            # 팩트체크 생략
    python -m main --output report.md           # 한글 백서 추가 저장
    python -m main --output-docx report.docx    # DOCX 추가 저장

사전 준비:
    1. cp .env.example .env (필요 시 수정)
    2. python -m scripts.gen_dummy   # ./data/records.jsonl 없을 때만
    3. pip install -r requirements.txt
"""
from __future__ import annotations
import argparse
import os
import shutil
import sys
import traceback as _tb
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from src.graph import build_graph
from src.nodes import LOCAL_DATA_PATH, set_skip_fact_check
from src.logger import reset_stats, summary, log_error
from src.llm import reset_504_state, set_default_reasoning
from src.artifacts import init_run_dir


def parse_args():
    p = argparse.ArgumentParser(
        description="Deep Doc Pipeline v3.0 — Whitepaper Generator (EN→KR+DOCX)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--output", default=None,
                   help="최종 한글 백서 저장 경로 (기본: run_dir/phase5_output_kr.md)")
    p.add_argument("--output-docx", default=None,
                   help="DOCX 추가 저장 경로 (기본: run_dir/output.docx)")
    p.add_argument("--reasoning", choices=["high", "medium"], default="high",
                   help="LLM 추론 강도 (high: 기본, medium: 빠른 응답/낮은 품질)")
    p.add_argument("--skip-fact-check", action="store_true",
                   help="팩트체크/환각 검증 생략 (빠른 실행, 품질 검증 미수행)")
    return p.parse_args()


def main():
    args = parse_args()

    # ── 공통 초기화 ──
    reset_stats()
    reset_504_state()
    set_default_reasoning(args.reasoning)
    set_skip_fact_check(args.skip_fact_check)

    if not Path(LOCAL_DATA_PATH).exists():
        print(f"[ERROR] {LOCAL_DATA_PATH} 가 없습니다.")
        print("  먼저 실행: python -m scripts.gen_dummy")
        sys.exit(1)

    run_dir = init_run_dir()

    initial_state = {
        "raw_docs": [],
        "extracted_events": [],
        "completed_sections": {},
        "hallucinated_tokens": [],
        "section_retry_count": 0,
    }
    graph = build_graph()

    print("=" * 70)
    print("Deep Doc Pipeline v3.0 — Whitepaper Generator (EN→KR+DOCX)")
    print(f"모델: {os.getenv('OPENAI_MODEL', 'gpt-oss:20b')} @ "
          f"{os.getenv('OPENAI_BASE_URL', 'http://localhost:11434/v1')}")
    skip_fc = "⚠️ 팩트체크 생략" if args.skip_fact_check else "팩트체크 ON"
    print(f"추론: {args.reasoning} | {skip_fc} | 504 2회 초과 시 medium 자동 전환")
    print(f"산출물: {run_dir}/")
    print("=" * 70)

    # ── 그래프 실행 ──
    try:
        final_state = graph.invoke(initial_state, config={"recursion_limit": 200})
    except Exception as _e:
        log_error("graph.invoke", _e, _tb.format_exc())
        print(f"\n[ERROR] 파이프라인 실행 실패: {_e}")
        sys.exit(1)

    final = final_state.get("final_output", "(빈 결과)")
    english = final_state.get("english_output", "")
    blueprint = final_state.get("blueprint", {})
    docx_path = final_state.get("docx_path", "")

    # ── 최종 파일 저장 (--output 지정 시 추가 복사) ──
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(final, encoding="utf-8")
        print(f"  추가 저장 → {out_path.resolve()}")

    if args.output_docx and docx_path:
        docx_dest = Path(args.output_docx)
        docx_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(docx_path, docx_dest)
        print(f"  DOCX 추가 저장 → {docx_dest.resolve()}")

    # ── 실행 통계 ──
    stats = summary()

    print()
    print("=" * 70)
    print("✅ 파이프라인 완료")
    print("=" * 70)
    print(f"  총 소요 시간 : {stats['elapsed']}")
    print(f"  완료 작업 수 : {stats['nodes']}건")
    print(f"  LLM API 호출 : {stats['llm_calls']}건")
    print("-" * 70)
    print(f"  추출 이벤트  : {len(final_state.get('extracted_events', []))}건")
    print(f"  블루프린트   : {blueprint.get('doc_title', '(없음)')}")
    print(f"  섹션 수      : 2")
    if english:
        print(f"  영문 원본    : {len(english):,} chars")
    print(f"  한글 번역    : {len(final):,} chars")
    if docx_path:
        print(f"  DOCX 경로    : {docx_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
