"""
LangGraph node functions — v3.0.
One Node = One Task principle strictly enforced.

v3 graph flow:
  START → load_docs → [fanout] extract(×N) → chrono_sorter
       → [fanout] period_digest(×M) → strategic_analyst
       → init_writing → section_writer → fact_checker → route(retry/save/next)
       → assembler → polish → translate
       → docx_builder → END
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Dict, List, Any

from langgraph.types import Send

from .state import GraphState
from .schemas import (
    ExtractedEvent, PeriodDigest,
    SectionPlan, DocumentBlueprint,
    SectionDraft, FactCheckResult, PolishedDocument,
)
from .llm import structured_call, Timeout504Error, effective_budget, _504_MAX_STEPS
from .utils import (
    is_valid_date, chrono_sort_and_group, filter_by_period,
    format_events_for_prompt, extract_proper_nouns,
)
from .logger import plog, psub
from .context_guard import (
    estimate_guard_overhead, available_data_budget,
    split_items_for_budget, trim_retry_context, cross_check_terms,
    measure_messages_bytes,
)
from .prompt_config import (
    get_summary_context, get_strategic_context,
    get_writing_context, get_translation_context, get_docx_meta,
)
from .artifacts import save_json, save_text
import functools


LOCAL_DATA_PATH = "./data/records.jsonl"

# ════════════════════════════════════════════════════════════════
# Fact-check skip mode (--skip-fact-check)
# ════════════════════════════════════════════════════════════════
# 활성화 시:
#   - fact_checker_node: LLM 호출 생략, 자동 승인
#   - 집필 루프가 1회로 줄어 속도 대폭 향상
#   - ⚠️ 환각/사실오류가 미검증 상태로 최종 문서에 포함될 수 있음

_skip_fact_check: bool = False


def set_skip_fact_check(skip: bool) -> None:
    """main.py에서 호출. --skip-fact-check 플래그 설정."""
    global _skip_fact_check
    _skip_fact_check = skip


def get_skip_fact_check() -> bool:
    return _skip_fact_check

# ══════════════════════════════════════════════════════════════
# 504 retry decorator: re-runs the entire node with reduced budget
# ══════════════════════════════════════════════════════════════

def retry_on_504(fn):
    """Decorator: on Timeout504Error, re-run the entire node function.

    504 reduction is LOCAL to this node only:
      - reset_504_state() at entry (clean slate)
      - On 504: structured_call reduces budget, raises Timeout504Error
      - Decorator re-runs the node with reduced effective_budget()
      - On success OR exhaustion: reset_504_state() restores full budget

    Subsequent nodes always start with the original full budget/tokens
    to preserve maximum output quality.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        from .llm import reset_504_state
        reset_504_state()  # clean slate for this node
        try:
            for attempt in range(_504_MAX_STEPS):
                try:
                    return fn(*args, **kwargs)
                except Timeout504Error:
                    psub("504_retry",
                         f"{fn.__name__} — node re-run ({attempt + 1}/{_504_MAX_STEPS}), "
                         f"budget now {effective_budget() // 1024}KB")
            return fn(*args, **kwargs)  # final attempt, let exception propagate
        finally:
            reset_504_state()  # always restore full budget for next node
    return wrapper


# ══════════════════════════════════════════════════════════════
# Common English enforcement suffix appended to key system prompts
# ══════════════════════════════════════════════════════════════
_EN_ENFORCE = (
    " Respond in English only. "
    "Preserve all proper nouns (company names, project names, personal names, "
    "place names, technical terms, abbreviations) in their original form."
)


# ──────────────────────────────────────────────────────────────
# Phase 1: Extraction
# ──────────────────────────────────────────────────────────────
def load_docs_node(state: GraphState) -> Dict[str, Any]:
    """Load JSONL file into raw_docs."""
    docs: List[Dict] = []
    failed = 0
    path = Path(LOCAL_DATA_PATH)
    with path.open("r", encoding="utf-8-sig") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                docs.append(json.loads(line))
            except json.JSONDecodeError as e:
                failed += 1
                psub("load_docs", f"line {ln} skipped: {e}")
    plog("load_docs", f"loaded={len(docs)} failed={failed}")
    return {"raw_docs": docs}


def fanout_to_extractor(state: GraphState):
    """Dispatch strict_extractor_node per document via Send API."""
    return [
        Send("strict_extractor", {"doc": d, "doc_index": i})
        for i, d in enumerate(state["raw_docs"])
    ]


@retry_on_504
def strict_extractor_node(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Extract ExtractedEvent from a single document.

    95KB 초과 시 문서 자동 절단. 영어 출력 강제. 3회 retry 내장.
    """
    doc = payload["doc"]
    idx = payload["doc_index"]
    doc_text = json.dumps(doc, ensure_ascii=False)

    system_content = (
        "You are a document analyst. Extract key facts from the given source document. "
        "The date field MUST be in YYYY-MM-DD format. "
        "NEVER fabricate information not explicitly stated in the source."
        + _EN_ENFORCE
    )
    user_prefix = "Source document:\n"
    user_suffix = "\n\nExtract date/issue/action from the above document."

    def _build_messages(text: str) -> list:
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": f"{user_prefix}{text}{user_suffix}"},
        ]

    messages = _build_messages(doc_text)
    guard_overhead = estimate_guard_overhead(ExtractedEvent.model_json_schema())
    size = measure_messages_bytes(messages) + guard_overhead

    if size > effective_budget():
        excess = size - effective_budget() + 512
        doc_bytes = doc_text.encode("utf-8")
        allowed = max(len(doc_bytes) - excess, 256)
        doc_text = doc_bytes[:allowed].decode("utf-8", errors="ignore") + " [TRUNCATED]"
        messages = _build_messages(doc_text)
        psub("extractor", f"doc {idx} truncated: {size/1024:.1f}KB → target fit")

    try:
        ev = structured_call(messages, ExtractedEvent, role="extractor", temperature=0.0)
        if not is_valid_date(ev.date):
            psub("extractor", f"doc {idx} invalid date '{ev.date}' — dropped")
            return {"extracted_events": []}
        return {"extracted_events": [ev.model_dump()]}
    except Exception as e:
        psub("extractor", f"doc {idx} failed after retries: {e}")
        return {"extracted_events": []}


def chrono_sorter_node(state: GraphState) -> Dict[str, Any]:
    """Pure Python sort + monthly grouping."""
    grouped = chrono_sort_and_group(state["extracted_events"])
    plog("chrono_sorter", f"events={len(state['extracted_events'])} months={list(grouped.keys())}")
    save_json("phase1_extracted_events.json", state["extracted_events"])
    save_json("phase1_grouped_chunks.json", grouped)
    return {"grouped_chunks": grouped}


# ──────────────────────────────────────────────────────────────
# Phase 2: Period Digests (Compression)
# ──────────────────────────────────────────────────────────────
def fanout_to_period_digest(state: GraphState):
    """Parallel period digest via Send API."""
    return [
        Send("period_digest", {"period": p, "events": evs})
        for p, evs in state["grouped_chunks"].items()
    ]


@retry_on_504
def period_digest_node(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Produce a 1-2 sentence digest + event count + key metrics for a period.

    Budget management: if events exceed budget, batch split → sub-digests → merge.
    """
    period = payload["period"]
    events = payload["events"]

    system_content = (
        f"You are a data compressor. Given events for period {period}, "
        "produce a 1-2 sentence factual digest. Extract numeric KPIs. "
        "Do NOT add information not in the events."
        + get_summary_context()
        + _EN_ENFORCE
    )
    user_template = (
        "Period: {period}\n\n"
        "Event list:\n{events_text}\n\n"
        "Produce a digest with event count and key metrics:"
    )

    def _build_messages(events_text: str) -> list:
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_template.format(
                period=period, events_text=events_text,
            )},
        ]

    events_text = format_events_for_prompt(events)
    messages = _build_messages(events_text)
    guard_overhead = estimate_guard_overhead(PeriodDigest.model_json_schema())
    size = measure_messages_bytes(messages) + guard_overhead

    if size <= effective_budget():
        result = structured_call(
            messages, PeriodDigest, role="default", temperature=0.2,
        )
        digest_text = (
            f"{result.digest} "
            f"[Events: {result.event_count}, "
            f"KPIs: {', '.join(result.key_metrics)}]"
        )
        plog("period_digest", f"{period}: {result.digest[:60]}...")
        return {"period_digests": {period: digest_text}}

    # Budget exceeded — batch split
    data_budget = available_data_budget(
        system_content,
        PeriodDigest.model_json_schema(),
        extra_fixed=user_template.format(period=period, events_text=""),
        budget_override=effective_budget(),
    )
    batches = split_items_for_budget(events, format_events_for_prompt, data_budget)
    plog("period_digest", f"{period}: budget exceeded ({size/1024:.1f}KB) — {len(batches)} batches")

    sub_digests: List[str] = []
    for batch in batches:
        batch_text = format_events_for_prompt(batch)
        batch_msgs = _build_messages(batch_text)
        sub = structured_call(
            batch_msgs, PeriodDigest, role="default", temperature=0.2,
        )
        sub_digests.append(
            f"{sub.digest} "
            f"[Events: {sub.event_count}, "
            f"KPIs: {', '.join(sub.key_metrics)}]"
        )

    # Merge sub-digests
    merged_input = "\n".join(
        f"[Partial digest {i+1}] {s}" for i, s in enumerate(sub_digests)
    )
    merge_messages = [
        {"role": "system", "content": (
            "You are a summary merger. Combine partial digests for the same period "
            "into one unified 1-2 sentence digest with total event count and key metrics. "
            "Do NOT add content not present in the partial digests."
            + _EN_ENFORCE
        )},
        {"role": "user", "content": (
            f"Period: {period}\n\nPartial digests:\n{merged_input}\n\n"
            "Unified digest:"
        )},
    ]
    merged = structured_call(
        merge_messages, PeriodDigest, role="default", temperature=0.2,
    )
    digest_text = (
        f"{merged.digest} "
        f"[Events: {merged.event_count}, "
        f"KPIs: {', '.join(merged.key_metrics)}]"
    )
    plog("period_digest", f"{period}: merged digest: {merged.digest[:60]}...")
    return {"period_digests": {period: digest_text}}


# ──────────────────────────────────────────────────────────────
# Phase 3: Strategic Analysis (core v3 innovation)
# ──────────────────────────────────────────────────────────────
@retry_on_504
def strategic_analyst_node(state: GraphState) -> Dict[str, Any]:
    """Create a document blueprint with 2 narrative-based sections.

    Replaces theme_analyzer + draft_planner from v2.
    Input: period_digests (all months), grouped_chunks keys (available periods)
    Output: blueprint (DocumentBlueprint as dict)
    """
    digests = state["period_digests"]
    sorted_periods = sorted(digests.keys())

    if not sorted_periods:
        raise ValueError("No period digests available for strategic analysis")

    date_range = f"{sorted_periods[0]} to {sorted_periods[-1]}"
    available_periods = ", ".join(sorted_periods)
    # Approx target words per section (~1 printed page)
    target_words = 400

    system_content = (
        f"You are a strategic business analyst. Given monthly digests spanning {date_range}, "
        "create a document blueprint for a concise 3-page whitepaper (1 cover + 2 body pages).\n\n"
        "CRITICAL CONSTRAINTS:\n"
        "- Identify exactly 2 key narratives that provide the most business insight\n"
        "- Narratives are THEMATIC, not chronological — each can span multiple months\n"
        f"- Each narrative will become one body page (~{target_words} words)\n"
        "- evidence_periods: list YYYY-MM keys that support each narrative "
        f"(from available: {available_periods})\n"
        "- key_points: 3-5 specific facts/metrics from the digests that MUST appear\n"
        "- doc_title: compelling title capturing the overarching theme\n"
        "- doc_subtitle: include the data period range\n\n"
        f"Available period keys: {sorted_periods}\n"
        "ONLY use periods from this list in evidence_periods."
        + get_strategic_context()
        + _EN_ENFORCE
    )

    digests_text = "\n".join(
        f"[{p}] {digests[p]}" for p in sorted_periods
    )

    def _build_messages(d_text: str) -> list:
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": f"Monthly digests:\n{d_text}\n\nCreate document blueprint:"},
        ]

    messages = _build_messages(digests_text)
    guard_overhead = estimate_guard_overhead(DocumentBlueprint.model_json_schema())
    size = measure_messages_bytes(messages) + guard_overhead

    # Budget guard: trim digests if needed
    if size > effective_budget():
        trimmed_text = "\n".join(
            f"[{p}] {digests[p][:150]}..." for p in sorted_periods
        )
        messages = _build_messages(trimmed_text)
        new_size = measure_messages_bytes(messages) + guard_overhead
        plog("strategic_analyst",
             f"budget exceeded ({size/1024:.1f}KB → {new_size/1024:.1f}KB) — digests trimmed")

    result = structured_call(
        messages, DocumentBlueprint, role="default", temperature=0.3,
    )

    blueprint = result.model_dump()
    plog("strategic_analyst",
         f"blueprint: '{result.doc_title}' | "
         f"s1='{result.section_1.title}' ({len(result.section_1.evidence_periods)} periods) | "
         f"s2='{result.section_2.title}' ({len(result.section_2.evidence_periods)} periods)")

    save_json("phase3_blueprint.json", blueprint)
    return {"blueprint": blueprint}


# ──────────────────────────────────────────────────────────────
# Phase 4: Writing Loop
# ──────────────────────────────────────────────────────────────
def init_writing_node(state: GraphState) -> Dict[str, Any]:
    """Initialize writing loop state."""
    return {
        "current_section_index": 0,
        "section_retry_count": 0,
        "previous_draft": "",
        "current_draft": "",
    }


@retry_on_504
def section_writer_node(state: GraphState) -> Dict[str, Any]:
    """Write section body from source events across multiple months.

    v3 KEY CHANGE: pulls events from MULTIPLE months via
    blueprint.sections[idx].evidence_periods (cross-month narratives).
    """
    blueprint = state["blueprint"]
    idx = state["current_section_index"]
    sections_list = [blueprint["section_1"], blueprint["section_2"]]
    plan = sections_list[idx]
    grouped = state["grouped_chunks"]
    retry = state.get("section_retry_count", 0)
    target_words = 400

    # Gather events from ALL evidence periods (cross-month)
    events: List[Dict] = []
    for period in plan["evidence_periods"]:
        events.extend(filter_by_period(grouped, period))

    # Step 1: Build retry extras
    extra = ""
    if retry > 0:
        prev = state.get("previous_draft", "")
        bad_tokens = state.get("hallucinated_tokens", [])
        feedback = state.get("draft_feedback", "")
        retry_budget = max(effective_budget() // 5, 4 * 1024)
        prev, feedback, bad_tokens = trim_retry_context(
            prev, feedback, bad_tokens, budget_bytes=retry_budget,
        )
        extra = (
            f"\n\n[PREVIOUS REJECTED DRAFT — DO NOT repeat this]\n{prev}\n"
            f"\n[BANNED TOKENS — hallucinated terms not in source]\n{bad_tokens}\n"
            f"\n[REVISION INSTRUCTIONS]\n{feedback}\n"
        )

    system_content = (
        "You are a whitepaper writer. Write a section for a 3-page business report. "
        "Use ONLY the provided source events as evidence. NEVER fabricate facts. "
        f"Target length: ~{target_words} words (approximately 1 printed page). "
        "Structure the content as a cohesive narrative, NOT a chronological list. "
        "Use markdown: headers, bold for key metrics, bullet lists for enumerations."
        + get_writing_context()
        + _EN_ENFORCE
    )

    user_prefix = (
        f"Section title: {plan['title']}\n"
        f"Narrative focus: {plan['narrative']}\n"
        f"Key points to include: {', '.join(plan['key_points'])}\n"
        f"Evidence periods: {', '.join(plan['evidence_periods'])}\n\n"
        f"Source events (use ONLY this data):\n"
    )
    user_suffix = f"{extra}\n\nWrite section body:"

    def _build_messages(events_text: str) -> list:
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": f"{user_prefix}{events_text}{user_suffix}"},
        ]

    events_text = format_events_for_prompt(events)
    messages = _build_messages(events_text)
    guard_overhead = estimate_guard_overhead(SectionDraft.model_json_schema())
    size = measure_messages_bytes(messages) + guard_overhead

    if size <= effective_budget():
        result = structured_call(
            messages, SectionDraft, role="writer", temperature=0.3,
        )
        plog("section_writer",
             f"idx={idx} periods={plan['evidence_periods']} "
             f"retry={retry} len={len(result.content)}")
        return {"current_draft": result.content}

    # Budget exceeded — batch split
    data_budget = available_data_budget(
        system_content,
        SectionDraft.model_json_schema(),
        extra_fixed=user_prefix + user_suffix,
        budget_override=effective_budget(),
    )
    batches = split_items_for_budget(events, format_events_for_prompt, data_budget)
    plog("section_writer",
         f"idx={idx} budget exceeded ({size/1024:.1f}KB) — {len(batches)} batches")

    partial_system = (
        "You are a whitepaper writer. Write body text covering the key content of the "
        "provided events. Do NOT add information not in the source. "
        "This is a partial batch — content will be merged later."
        + _EN_ENFORCE
    )
    partial_drafts: List[str] = []
    for batch in batches:
        batch_text = format_events_for_prompt(batch)
        batch_msgs = [
            {"role": "system", "content": partial_system},
            {"role": "user", "content": (
                f"Section title: {plan['title']}\n"
                f"Narrative: {plan['narrative']}\n\n"
                f"Source events (this batch):\n{batch_text}{user_suffix}"
            )},
        ]
        part = structured_call(
            batch_msgs, SectionDraft, role="writer", temperature=0.3,
        )
        partial_drafts.append(part.content)

    # Merge partial drafts
    merge_input = "\n\n---\n\n".join(
        f"[Partial draft {i+1}]\n{d}" for i, d in enumerate(partial_drafts)
    )
    merge_msgs = [
        {"role": "system", "content": (
            "You are a whitepaper editor. Merge partial drafts for the same section into "
            "one smooth body text. Include all factual information from each partial draft. "
            "Do NOT add new information. Remove duplicates but preserve meaningful details."
            + _EN_ENFORCE
        )},
        {"role": "user", "content": (
            f"Section title: {plan['title']}\n\n"
            f"Partial drafts:\n{merge_input}\n\nMerged body:"
        )},
    ]
    merge_guard = estimate_guard_overhead(SectionDraft.model_json_schema())
    merge_size = measure_messages_bytes(merge_msgs) + merge_guard

    if merge_size <= effective_budget():
        merged = structured_call(
            merge_msgs, SectionDraft, role="writer", temperature=0.3,
        )
        content = merged.content
    else:
        plog("section_writer",
             f"idx={idx} merge also exceeded budget — concatenating")
        content = "\n\n".join(partial_drafts)

    plog("section_writer",
         f"idx={idx} periods={plan['evidence_periods']} "
         f"retry={retry} len={len(content)}")
    return {"current_draft": content}


@retry_on_504
def fact_checker_node(state: GraphState) -> Dict[str, Any]:
    """Fact-check section draft against source events.

    v3: gathers events from MULTIPLE months via blueprint evidence_periods.
    --skip-fact-check 시 자동 승인.
    """
    # ── 팩트체크 생략 모드: LLM 호출 없이 자동 승인 ──
    if _skip_fact_check:
        idx = state["current_section_index"]
        plog("fact_checker", f"idx={idx} SKIPPED (--skip-fact-check)")
        return {
            "is_draft_approved": True,
            "draft_feedback": "Fact-check skipped (--skip-fact-check)",
            "hallucinated_tokens": [],
        }

    blueprint = state["blueprint"]
    idx = state["current_section_index"]
    sections_list = [blueprint["section_1"], blueprint["section_2"]]
    plan = sections_list[idx]
    grouped = state["grouped_chunks"]

    # Gather events from ALL evidence periods
    events: List[Dict] = []
    for period in plan["evidence_periods"]:
        events.extend(filter_by_period(grouped, period))

    events_text = format_events_for_prompt(events)
    draft = state["current_draft"]

    system_content = (
        "You are a strict auditor. If the draft contains ANY proper noun, date, or number "
        "not present in the source events, you MUST set is_draft_approved=False. "
        "Extract the exact hallucinated tokens into the hallucinated_terms list. "
        "In feedback, specify exactly which parts are problematic."
        + _EN_ENFORCE
    )

    def _build_messages(ev_text: str) -> list:
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": (
                f"Source events (ground truth):\n{ev_text}\n\n"
                f"Draft under review:\n{draft}\n\nVerification result:"
            )},
        ]

    messages = _build_messages(events_text)
    guard_overhead = estimate_guard_overhead(FactCheckResult.model_json_schema())
    size = measure_messages_bytes(messages) + guard_overhead

    if size <= effective_budget():
        result = structured_call(
            messages, FactCheckResult, role="judge", temperature=0.0,
        )
        plog("fact_checker",
             f"idx={idx} approved={result.is_draft_approved} "
             f"halluc={result.hallucinated_terms[:3]}")
        return {
            "is_draft_approved": result.is_draft_approved,
            "draft_feedback": result.feedback,
            "hallucinated_tokens": (
                result.hallucinated_terms if not result.is_draft_approved else []
            ),
        }

    # Budget exceeded — batch split events (draft kept in each batch)
    data_budget = available_data_budget(
        system_content,
        FactCheckResult.model_json_schema(),
        extra_fixed=(
            f"Source events (ground truth):\n\n\n"
            f"Draft under review:\n{draft}\n\nVerification result:"
        ),
        budget_override=effective_budget(),
    )
    batches = split_items_for_budget(events, format_events_for_prompt, data_budget)

    batched_system = (
        "You are an auditor. Verify whether the draft content matches the provided event data. "
        "These events are a SUBSET of the full data. If information in the draft is absent "
        "from this batch, record it in hallucinated_terms (it may exist in other batches). "
        "Only set is_draft_approved=False if the draft clearly CONTRADICTS this batch."
        + _EN_ENFORCE
    )

    all_approved = True
    all_feedback: List[str] = []
    all_candidates: List[str] = []

    for batch in batches:
        batch_text = format_events_for_prompt(batch)
        batch_msgs = [
            {"role": "system", "content": batched_system},
            {"role": "user", "content": (
                f"Source events (this batch):\n{batch_text}\n\n"
                f"Draft under review:\n{draft}\n\nVerification result:"
            )},
        ]
        batch_result = structured_call(
            batch_msgs, FactCheckResult, role="judge", temperature=0.0,
        )
        if not batch_result.is_draft_approved:
            all_approved = False
            all_feedback.append(batch_result.feedback)
        all_candidates.extend(batch_result.hallucinated_terms)

    # Cross-check: only truly absent tokens are hallucinations
    truly_hallucinated = cross_check_terms(all_candidates, events)
    is_approved = len(truly_hallucinated) == 0
    feedback = "; ".join(all_feedback) if all_feedback else "Batch verification passed"
    plog("fact_checker",
         f"idx={idx} batched: {len(batches)} batches, "
         f"candidates={len(all_candidates)}, truly_halluc={len(truly_hallucinated)}")

    return {
        "is_draft_approved": is_approved,
        "draft_feedback": feedback,
        "hallucinated_tokens": truly_hallucinated if not is_approved else [],
    }


def route_section_draft(state: GraphState) -> str:
    """Route after fact-check:
    - approved → save_section
    - retry < 3 → retry_section
    - retry >= 3 → save_section_with_warning
    """
    if state.get("is_draft_approved"):
        return "save_section"
    if state.get("section_retry_count", 0) >= 3:
        return "save_section_with_warning"
    return "retry_section"


def retry_section_node(state: GraphState) -> Dict[str, Any]:
    """Prepare rewrite: update previous_draft + increment retry count."""
    return {
        "previous_draft": state.get("current_draft", ""),
        "section_retry_count": state.get("section_retry_count", 0) + 1,
    }


def save_section_node(state: GraphState) -> Dict[str, Any]:
    """Save approved section + advance index + reset scope."""
    idx = state["current_section_index"]
    draft = state["current_draft"]
    plog("save_section", f"idx={idx} APPROVED")
    save_text(f"phase4_sections/section_{idx:02d}.md", draft)
    return {
        "completed_sections": {idx: draft},
        "current_section_index": idx + 1,
        "section_retry_count": 0,
        "previous_draft": "",
    }


def save_section_with_warning_node(state: GraphState) -> Dict[str, Any]:
    """Fail-Safe forced pass: watermark + warning."""
    idx = state["current_section_index"]
    draft = state["current_draft"]
    feedback = state.get("draft_feedback", "(no reason recorded)")
    warned = (
        f"> ⚠️ **Unverified Section** — Automatic fact-check failed 3 times.\n"
        f"> Last rejection reason: {feedback}\n\n"
        f"{draft}"
    )
    plog("save_section_with_warning", f"idx={idx} FORCE-PASS")
    save_text(f"phase4_sections/section_{idx:02d}.md", warned)
    return {
        "completed_sections": {idx: warned},
        "current_section_index": idx + 1,
        "section_retry_count": 0,
        "previous_draft": "",
    }


def route_next_section(state: GraphState) -> str:
    """All sections done → assembler, otherwise next section."""
    if state["current_section_index"] >= 2:  # Fixed: always 2 sections in v3
        return "assembler"
    return "section_writer"


# ──────────────────────────────────────────────────────────────
# Phase 4: Assembly → Polish
# ──────────────────────────────────────────────────────────────
def assembler_node(state: GraphState) -> Dict[str, Any]:
    """Pure Python assembly — no LLM calls. Combines completed sections."""
    completed = state.get("completed_sections", {})
    blueprint = state["blueprint"]
    sections_plans = [blueprint["section_1"], blueprint["section_2"]]

    parts = []
    for idx in range(2):
        plan = sections_plans[idx]
        body = completed.get(idx, "_(Section missing)_")
        parts.append(f"## {plan['title']}\n\n{body}")

    compiled = "\n\n---\n\n".join(parts)
    save_text("phase4_compiled_en.md", compiled)
    plog("assembler", f"sections={len(completed)} compiled_len={len(compiled)}")
    return {"final_compiled": compiled, "english_output": compiled}


@retry_on_504
def polish_node(state: GraphState) -> Dict[str, Any]:
    """Proofread polish — single LLM call for ~800 words.

    For v3's short output (~800 words total), a single call suffices.
    Falls back to no-polish if budget exceeded.
    """
    compiled = state["final_compiled"]

    system_prompt = (
        "You are a proofreading editor. Do NOT add, delete, or modify any factual "
        "information (dates, proper nouns, numbers, causal relationships). "
        "ONLY refine paragraph transitions, coherence, and awkward phrasing. "
        "NEVER fabricate new information. "
        "Maintain markdown structure (headers, lists, blockquote warnings)."
        + _EN_ENFORCE
    )
    guard_overhead = estimate_guard_overhead(PolishedDocument.model_json_schema())

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Body text:\n{compiled}\n\nPolished result:"},
    ]
    size = measure_messages_bytes(messages) + guard_overhead

    if size <= effective_budget():
        result = structured_call(
            messages, PolishedDocument, role="writer",
            temperature=0.1, stream=True,
        )
        plog("polish", f"done: len={len(result.content)}")
        save_text("phase4_polished_en.md", result.content)
        return {"final_compiled": result.content, "english_output": result.content}

    # Budget exceeded (unlikely for ~800 words) — skip polish
    plog("polish", f"budget exceeded ({size/1024:.1f}KB) — skipping polish")
    save_text("phase4_polished_en.md", compiled)
    return {"final_compiled": compiled, "english_output": compiled}


# ──────────────────────────────────────────────────────────────
# Phase 5: Translation (English → Korean)
# ──────────────────────────────────────────────────────────────
@retry_on_504
def translate_node(state: GraphState) -> Dict[str, Any]:
    """Translate English whitepaper into Korean — v3.0 simplified.

    For ~800 English words, a single LLM call is sufficient.
    No paragraph splitting, no completeness check, no source fallback.
    """
    english = state["english_output"]
    proper_nouns = extract_proper_nouns(english)

    proper_nouns_list = "\n".join(
        f"  - {n}" for n in proper_nouns[:100]
    ) if proper_nouns else "(없음)"

    system_content = (
        "당신은 영문 백서를 한국어로 충실하게 번역하는 전문 번역가입니다.\n\n"
        "핵심 원칙:\n"
        "1. 완전 번역: 원문의 모든 문장을 빠짐없이 번역. 요약·생략 금지.\n"
        "2. 고유명사: 회사명, 프로젝트명, 기술 용어는 원문 그대로 유지.\n"
        "3. 톤: 공식 백서 평어체 (~다, ~함, ~구축됨).\n"
        "4. 핵심 수치는 **볼드** 유지.\n"
        "5. 마크다운 구조 보존 (##, -, **, `)\n\n"
        f"[보존 대상 고유명사]\n{proper_nouns_list}"
        + get_translation_context()
    )

    guard_overhead = estimate_guard_overhead(PolishedDocument.model_json_schema())
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": (
            "다음 영문 백서를 한국어로 완전 번역하십시오.\n\n"
            f"---\n\n{english}"
        )},
    ]
    size = measure_messages_bytes(messages) + guard_overhead

    if size > effective_budget():
        plog("translate",
             f"budget exceeded ({size/1024:.1f}KB) — keeping English as fallback")
        save_text("phase5_output_kr.md", english)
        save_text("phase5_output_en.md", english)
        return {"final_output": english}

    result = structured_call(
        messages, PolishedDocument, role="writer",
        temperature=0.2, stream=True,
    )
    plog("translate",
         f"done: en={len(english)} kr={len(result.content)} "
         f"ratio={len(result.content)/max(len(english),1):.2f}")

    save_text("phase5_output_kr.md", result.content)
    save_text("phase5_output_en.md", english)
    return {"final_output": result.content}


# ──────────────────────────────────────────────────────────────
# Phase 6: DOCX Builder
# ──────────────────────────────────────────────────────────────
def _split_korean_sections(text: str) -> list:
    """Split translated Korean text into 2 sections."""
    # Try splitting on horizontal rule
    if "\n---\n" in text:
        parts = text.split("\n---\n", 1)
        return [p.strip() for p in parts]
    # Try splitting on ## heading
    parts = re.split(r'\n(?=## )', text, maxsplit=1)
    if len(parts) == 2:
        return [p.strip() for p in parts]
    # Fallback: split roughly in half by paragraphs
    paragraphs = text.split("\n\n")
    mid = len(paragraphs) // 2
    return [
        "\n\n".join(paragraphs[:mid]).strip(),
        "\n\n".join(paragraphs[mid:]).strip(),
    ]


def docx_builder_node(state: GraphState) -> Dict[str, Any]:
    """Build DOCX from blueprint + translated Korean sections."""
    from .docx_builder import DocxBuilder
    from .artifacts import get_run_dir

    blueprint = state["blueprint"]
    korean_text = state["final_output"]

    # Split Korean text into 2 sections
    sections = _split_korean_sections(korean_text)

    meta = get_docx_meta()
    builder = DocxBuilder(blueprint, sections, meta)

    run_dir = get_run_dir()
    output_path = str(Path(run_dir) / "output.docx") if run_dir else "output.docx"
    result_path = builder.build(output_path)

    plog("docx_builder", f"DOCX saved: {result_path}")
    return {"docx_path": result_path}
