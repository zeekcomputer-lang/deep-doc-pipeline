"""
파이프라인 실행 진입점.

사용법:
    python -m main --format whitepaper
    python -m main --format status_report

사전 준비:
    1. cp .env.example .env (필요 시 수정)
    2. python -m scripts.gen_dummy   # ./data/records.jsonl 없을 때만
    3. pip install -r requirements.txt
"""
from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path

# .env 자동 로드 (python-dotenv가 있으면)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from src.graph import build_graph
from src.nodes import LOCAL_DATA_PATH


def parse_args():
    p = argparse.ArgumentParser(description="Deep Doc Pipeline — Whitepaper Generator (EN→KR)")
    p.add_argument("--output", default="./output.md", help="최종 마크다운 저장 경로")
    return p.parse_args()


def main():
    args = parse_args()

    # 데이터 파일 존재 확인
    if not Path(LOCAL_DATA_PATH).exists():
        print(f"[ERROR] {LOCAL_DATA_PATH} 가 없습니다.")
        print("  먼저 실행: python -m scripts.gen_dummy")
        sys.exit(1)

    print("=" * 70)
    print("파이프라인 시작: whitepaper (EN→KR)")
    print(f"모델: {os.getenv('MODEL_NAME', 'gpt-oss:20b')} @ "
          f"{os.getenv('OPENAI_BASE_URL', 'http://localhost:11434/v1')}")
    print("=" * 70)

    graph = build_graph()

    initial_state = {
        "raw_docs": [],
        "extracted_events": [],
        "period_summaries": {},
        "completed_sections": {},
        "unverified_sections": [],
        "hallucinated_tokens": [],
        "outline_retry_count": 0,
        "section_retry_count": 0,
        "polish_retry_count": 0,
        "proper_nouns": [],
        "translation_retry_count": 0,
    }

    # recursion_limit를 충분히 늘려 루프 동작 보장
    final_state = graph.invoke(initial_state, config={"recursion_limit": 200})

    final = final_state.get("final_output", "(빈 결과)")
    out_path = Path(args.output)
    out_path.write_text(final, encoding="utf-8")

    print("=" * 70)
    print(f"완료. 결과: {out_path.resolve()}")
    print(f"  - 추출 이벤트: {len(final_state.get('extracted_events', []))}건")
    print(f"  - 월별 그룹: {list(final_state.get('grouped_chunks', {}).keys())}")
    print(f"  - 목차 항목: {len(final_state.get('outline', []))}개")
    print(f"  - 완성 섹션: {len(final_state.get('completed_sections', {}))}개")
    unv = final_state.get("unverified_sections", [])
    if unv:
        print(f"  - ⚠️ 검증 미완료 섹션: {sorted(unv)}")
    nouns = final_state.get("proper_nouns", [])
    print(f"  - 추출 고유명사: {len(nouns)}개")
    if final_state.get("english_output"):
        print(f"  - 영문 백서: {len(final_state['english_output'])} chars")
        print(f"  - 한글 번역: {len(final)} chars")
    print("=" * 70)


if __name__ == "__main__":
    main()
