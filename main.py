"""
파이프라인 실행 진입점.

사용법:
    python -m main                              # 전체 실행
    python -m main --skip-fact-check            # 팩트체크 생략
    python -m main --resume output/20260604_162400 --resume-from translate  # 번역만 재실행

사전 준비:
    1. cp .env.example .env (필요 시 수정)
    2. python -m scripts.gen_dummy   # ./data/records.jsonl 없을 때만
    3. pip install -r requirements.txt
"""
from __future__ import annotations
import argparse
import os
import sys
import traceback as _tb
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from src.graph import build_graph, build_resume_graph
from src.nodes import LOCAL_DATA_PATH, set_skip_fact_check
from src.logger import reset_stats, summary, log_error
from src.llm import reset_504_state, set_default_reasoning
from src.artifacts import init_run_dir, set_run_dir, load_run_state, list_runs


def parse_args():
    p = argparse.ArgumentParser(
        description="Deep Doc Pipeline — Whitepaper Generator (EN→KR)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "resume 예시:\n"
            "  python -m main --resume output/20260604_162400 --resume-from translate\n"
            "  python -m main --resume output/20260604_162400 --resume-from polish\n"
            "  python -m main --resume output/20260604_162400 --resume-from compile\n"
            "\n"
            "실행 디렉토리 조회:\n"
            "  python -m main --list-runs\n"
        ),
    )
    p.add_argument("--output", default=None,
                   help="최종 한글 백서 저장 경로 (기본: run_dir/phase5_output_kr.md)")
    p.add_argument("--output-en", default=None,
                   help="영문 원본 저장 경로 (기본: run_dir/phase5_output_en.md)")
    p.add_argument("--reasoning", choices=["high", "medium"], default="high",
                   help="LLM 추론 강도 (high: 기본, medium: 빠른 응답/낮은 품질)")
    p.add_argument("--skip-fact-check", action="store_true",
                   help="팩트체크/환각 검증 생략 (빠른 실행, 품질 검증 미수행)")
    p.add_argument("--resume", default=None, metavar="RUN_DIR",
                   help="이전 실행 디렉토리에서 재개 (예: output/20260604_162400)")
    p.add_argument("--resume-from", default="translate",
                   choices=["translate", "polish", "compile"],
                   help="재개 시작 단계 (기본: translate)")
    p.add_argument("--list-runs", action="store_true",
                   help="output/ 하위 실행 디렉토리 목록 표시 후 종료")
    return p.parse_args()


def main():
    args = parse_args()

    # ── --list-runs 모드 ──
    if args.list_runs:
        runs = list_runs()
        if not runs:
            print("실행 기록 없음 (output/ 디렉토리가 비어있거나 없음)")
        else:
            print(f"실행 기록 ({len(runs)}건, 최신순):")
            for r in runs:
                files = list(r.glob("phase*"))
                print(f"  {r.name}/  ({len(files)} artifacts)")
        return

    # ── 공통 초기화 ──
    reset_stats()
    reset_504_state()
    set_default_reasoning(args.reasoning)
    set_skip_fact_check(args.skip_fact_check)

    is_resume = args.resume is not None

    # ── Resume 모드 ──
    if is_resume:
        resume_dir = Path(args.resume)
        if not resume_dir.is_dir():
            print(f"[ERROR] 실행 디렉토리 없음: {resume_dir}")
            sys.exit(1)

        initial_state = load_run_state(resume_dir)
        # Resume 시 새 산출물은 동일 디렉토리에 덮어씀 (이전 결과 갱신)
        set_run_dir(resume_dir)
        graph = build_resume_graph(args.resume_from)

        print("=" * 70)
        print(f"Deep Doc Pipeline v2.0 — RESUME from {args.resume_from}")
        print(f"실행 디렉토리: {resume_dir}")
        print(f"모델: {os.getenv('OPENAI_MODEL', 'gpt-oss:20b')}")
        skip_fc = "⚠️ 팩트체크 생략" if args.skip_fact_check else "팩트체크 ON"
        print(f"추론: {args.reasoning} | {skip_fc}")
        print("=" * 70)

    # ── 전체 실행 모드 ──
    else:
        if not Path(LOCAL_DATA_PATH).exists():
            print(f"[ERROR] {LOCAL_DATA_PATH} 가 없습니다.")
            print("  먼저 실행: python -m scripts.gen_dummy")
            sys.exit(1)

        run_dir = init_run_dir()

        initial_state = {
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
        graph = build_graph()

        print("=" * 70)
        print("Deep Doc Pipeline v2.0 — Whitepaper Generator (EN→KR)")
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

    # ── 최종 파일 저장 (--output 지정 시 추가 복사) ──
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(final, encoding="utf-8")
        print(f"  추가 저장 → {out_path.resolve()}")

    if args.output_en and english:
        en_path = Path(args.output_en)
        en_path.parent.mkdir(parents=True, exist_ok=True)
        en_path.write_text(english, encoding="utf-8")
        print(f"  추가 저장 → {en_path.resolve()}")

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
    if not is_resume:
        print(f"  추출 이벤트  : {len(final_state.get('extracted_events', []))}건")
        print(f"  월별 그룹    : {list(final_state.get('grouped_chunks', {}).keys())}")
        print(f"  목차 항목    : {len(final_state.get('outline', []))}개")
        print(f"  완성 섹션    : {len(final_state.get('completed_sections', {}))}개")
        unv = final_state.get("unverified_sections", [])
        if unv:
            print(f"  ⚠️ 미검증 섹션 : {sorted(unv)}")
        nouns = final_state.get("proper_nouns", [])
        print(f"  고유명사 추출 : {len(nouns)}개")
    if english:
        print(f"  영문 원본    : {len(english):,} chars")
    print(f"  한글 번역    : {len(final):,} chars")
    print("=" * 70)


if __name__ == "__main__":
    main()
