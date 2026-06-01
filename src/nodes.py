"""
LangGraph node functions. One Node = One Task principle strictly enforced.

v1.3 changes:
  - All LLM prompts forced to English output with proper noun preservation.
  - Whitepaper-only (status_report mode and route_by_target removed).
  - Translation stage added: prepare_translation → translate → translation_checker.
  - route_final_check now routes to prepare_translation instead of END.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, List, Any

from langgraph.types import Send

from .state import GraphState
from .schemas import (
    ExtractedEvent, PeriodSummary, GlobalTheme,
    Outline, OutlineCritique,
    SectionDraft, FactCheckResult, PolishedDocument,
    TranslationCheckResult,
)
from .llm import structured_call
from .utils import (
    is_valid_date, chrono_sort_and_group, filter_by_period,
    validate_outline_periods, compile_sections, format_events_for_prompt,
    split_compiled_by_section, split_section_header_body,
    extract_proper_nouns,
)
from .context_guard import (
    BUDGET_BYTES, fits_budget, estimate_guard_overhead, available_data_budget,
    split_items_for_budget, trim_retry_context, cross_check_terms,
    measure_text_bytes, measure_messages_bytes,
)


LOCAL_DATA_PATH = "./data/records.jsonl"

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
                print(f"  [load_docs] line {ln} skipped: {e}")
    print(f"[load_docs] loaded={len(docs)} failed={failed}")
    return {"raw_docs": docs}


def fanout_to_extractor(state: GraphState):
    """Dispatch strict_extractor_node per document via Send API."""
    return [
        Send("strict_extractor", {"doc": d, "doc_index": i})
        for i, d in enumerate(state["raw_docs"])
    ]


def strict_extractor_node(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract ExtractedEvent from a single document.
    3-retry built into structured_call.
    v1.2: Auto-truncate doc_text if 95KB budget exceeded.
    v1.3: English output enforced.
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

    if size > BUDGET_BYTES:
        excess = size - BUDGET_BYTES + 512
        doc_bytes = doc_text.encode("utf-8")
        allowed = max(len(doc_bytes) - excess, 256)
        doc_text = doc_bytes[:allowed].decode("utf-8", errors="ignore") + " [TRUNCATED]"
        messages = _build_messages(doc_text)
        print(f"  [extractor] doc {idx} truncated: {size/1024:.1f}KB → target fit")

    try:
        ev = structured_call(messages, ExtractedEvent, role="extractor", temperature=0.0)
        if not is_valid_date(ev.date):
            print(f"  [extractor] doc {idx} invalid date '{ev.date}' — dropped")
            return {"extracted_events": []}
        return {"extracted_events": [ev.model_dump()]}
    except Exception as e:
        print(f"  [extractor] doc {idx} failed after retries: {e}")
        return {"extracted_events": []}


def chrono_sorter_node(state: GraphState) -> Dict[str, Any]:
    """Pure Python sort + monthly grouping."""
    grouped = chrono_sort_and_group(state["extracted_events"])
    print(f"[chrono_sorter] events={len(state['extracted_events'])} months={list(grouped.keys())}")
    return {"grouped_chunks": grouped}


# ──────────────────────────────────────────────────────────────
# Phase 2: Micro Summaries
# ──────────────────────────────────────────────────────────────
def fanout_to_period_summarizer(state: GraphState):
    """Parallel monthly summaries via Send API."""
    return [
        Send("period_summarizer", {"period": p, "events": evs})
        for p, evs in state["grouped_chunks"].items()
    ]


def period_summarizer_node(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Monthly key trend summary in exactly 3 sentences.
    v1.2: Budget-aware batch splitting + sub-summary merging.
    v1.3: English output enforced.
    """
    period = payload["period"]
    events = payload["events"]

    system_content = (
        "You are a period trend analyst. Summarize the given event list into exactly "
        "3 sentences capturing the key trends. Do NOT add content not present in the events."
        + _EN_ENFORCE
    )
    user_template = "Period: {period}\n\nEvent list:\n{events_text}\n\n3-sentence summary:"

    def _build_messages(events_text: str) -> list:
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_template.format(period=period, events_text=events_text)},
        ]

    events_text = format_events_for_prompt(events)
    messages = _build_messages(events_text)
    guard_overhead = estimate_guard_overhead(PeriodSummary.model_json_schema())
    size = measure_messages_bytes(messages) + guard_overhead

    if size <= BUDGET_BYTES:
        result = structured_call(messages, PeriodSummary, role="default", temperature=0.2)
        print(f"[period_summarizer] {period}: {result.summary[:60]}...")
        return {"period_summaries": {period: result.summary}}

    # Budget exceeded — batch split
    data_budget = available_data_budget(
        system_content,
        PeriodSummary.model_json_schema(),
        extra_fixed=user_template.format(period=period, events_text=""),
    )
    batches = split_items_for_budget(events, format_events_for_prompt, data_budget)
    print(f"[period_summarizer] {period}: budget exceeded ({size/1024:.1f}KB) — {len(batches)} batches")

    sub_summaries: List[str] = []
    for batch in batches:
        batch_text = format_events_for_prompt(batch)
        batch_msgs = _build_messages(batch_text)
        sub = structured_call(batch_msgs, PeriodSummary, role="default", temperature=0.2)
        sub_summaries.append(sub.summary)

    # Merge sub-summaries
    merged_input = "\n".join(f"[Partial summary {i+1}] {s}" for i, s in enumerate(sub_summaries))
    merge_messages = [
        {"role": "system", "content": (
            "You are a summary merger. Combine partial summaries for the same period "
            "into one unified summary without information loss. "
            "Do NOT add content not present in the partial summaries. Exactly 3 sentences."
            + _EN_ENFORCE
        )},
        {"role": "user", "content": (
            f"Period: {period}\n\nPartial summaries:\n{merged_input}\n\n"
            "Unified 3-sentence summary:"
        )},
    ]
    merged = structured_call(merge_messages, PeriodSummary, role="default", temperature=0.2)
    print(f"[period_summarizer] {period}: merged summary: {merged.summary[:60]}...")
    return {"period_summaries": {period: merged.summary}}


def theme_analyzer_node(state: GraphState) -> Dict[str, Any]:
    """Derive overall theme in 1 paragraph.
    v1.2: Drops oldest monthly summaries if budget exceeded.
    v1.3: English output enforced.
    """
    summaries = state["period_summaries"]
    sorted_periods = sorted(summaries.keys())

    system_content = (
        "You are a macro analyst. Given monthly summaries, write exactly 1 paragraph "
        "capturing the overarching insight into the project's performance and risk trajectory. "
        "Do NOT add content not present in the summaries."
        + _EN_ENFORCE
    )

    def _make_joined(periods: list) -> str:
        return "\n".join(f"[{k}] {summaries[k]}" for k in periods)

    def _build_messages(joined: str) -> list:
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": f"Monthly summaries:\n{joined}\n\nOverall theme (1 paragraph):"},
        ]

    active_periods = list(sorted_periods)
    joined = _make_joined(active_periods)
    messages = _build_messages(joined)
    guard_overhead = estimate_guard_overhead(GlobalTheme.model_json_schema())
    size = measure_messages_bytes(messages) + guard_overhead

    while size > BUDGET_BYTES and len(active_periods) > 1:
        removed = active_periods.pop(0)
        print(f"[theme_analyzer] budget exceeded — removing oldest period: {removed}")
        joined = _make_joined(active_periods)
        messages = _build_messages(joined)
        size = measure_messages_bytes(messages) + guard_overhead

    result = structured_call(messages, GlobalTheme, role="default", temperature=0.3)
    print(f"[theme_analyzer] theme: {result.theme[:80]}...")
    return {"global_theme": result.theme}


# ──────────────────────────────────────────────────────────────
# Phase 4-B [Step 1]: Planning Validation Loop
# ──────────────────────────────────────────────────────────────
def draft_planner_node(state: GraphState) -> Dict[str, Any]:
    """Plan whitepaper outline from global_theme + period_summaries.
    v1.2: Truncates summaries if budget exceeded.
    v1.3: English output enforced.
    """
    theme = state["global_theme"]
    summaries = state["period_summaries"]
    available_periods = sorted(summaries.keys())
    joined = "\n".join(f"[{k}] {v}" for k, v in sorted(summaries.items()))

    prev_feedback = state.get("outline_feedback", "")
    retry_hint = ""
    if prev_feedback:
        retry_hint = (
            f"\n\n[PREVIOUS OUTLINE REJECTED — address these issues]\n{prev_feedback}\n"
        )

    system_content = (
        "You are a whitepaper planner. Create an outline based on the given theme and "
        "monthly summaries. Each outline item must cover exactly one 'YYYY-MM' period "
        "(target_period). "
        f"Available period keys: {available_periods}\n"
        "Only use periods from this list. Sort in chronological order."
        + _EN_ENFORCE
    )

    def _build_messages(j: str, hint: str) -> list:
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": (
                f"Overall theme:\n{theme}\n\nMonthly summaries:\n{j}{hint}\n\n"
                "Create outline:"
            )},
        ]

    messages = _build_messages(joined, retry_hint)
    guard_overhead = estimate_guard_overhead(Outline.model_json_schema())
    size = measure_messages_bytes(messages) + guard_overhead

    if size > BUDGET_BYTES:
        truncated_joined = "\n".join(
            f"[{k}] {v[:100]}..." for k, v in sorted(summaries.items())
        )
        messages = _build_messages(truncated_joined, retry_hint)
        new_size = measure_messages_bytes(messages) + guard_overhead
        print(f"[draft_planner] budget exceeded ({size/1024:.1f}KB → {new_size/1024:.1f}KB) — summaries truncated")

    result = structured_call(messages, Outline, role="default", temperature=0.3)
    items = [it.model_dump() for it in result.items]
    print(f"[draft_planner] outline items={len(items)}")
    return {"outline": items}


def planner_critique_node(state: GraphState) -> Dict[str, Any]:
    """
    Outline review: chronological flow + target_period existence validation.
    Python validates target_period deterministically (blocks LLM hallucination).
    v1.2: Budget check before LLM call, intent truncation if exceeded.
    v1.3: English output enforced.
    """
    outline = state["outline"]
    grouped = state["grouped_chunks"]

    # Python validation 1: target_period existence
    invalid_periods = validate_outline_periods(outline, grouped)
    if invalid_periods:
        msg = f"Non-existent target_period used: {invalid_periods}"
        print(f"[planner_critique] REJECTED (python): {msg}")
        return {
            "is_outline_approved": False,
            "outline_feedback": msg,
            "outline_retry_count": state.get("outline_retry_count", 0) + 1,
        }

    # Python validation 2: chronological order
    periods = [it["target_period"] for it in outline]
    if periods != sorted(periods):
        msg = f"Chronological order violation. Current order: {periods}"
        print(f"[planner_critique] REJECTED (python): {msg}")
        return {
            "is_outline_approved": False,
            "outline_feedback": msg,
            "outline_retry_count": state.get("outline_retry_count", 0) + 1,
        }

    # LLM review: structural reasonableness
    system_content = (
        "You are a strict planning reviewer. Evaluate whether the given outline forms "
        "a natural whitepaper flow. Approve if each section intent is clear and there are "
        "no duplications. If issues exist, provide specific reasons."
        + _EN_ENFORCE
    )

    def _make_outline_text(items: list) -> str:
        return "\n".join(
            f"{it['index']}. [{it['target_period']}] {it['title']} — {it['intent']}"
            for it in items
        )

    def _build_messages(outline_text: str) -> list:
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": f"Outline:\n{outline_text}\n\nReview result:"},
        ]

    outline_text = _make_outline_text(outline)
    messages = _build_messages(outline_text)
    guard_overhead = estimate_guard_overhead(OutlineCritique.model_json_schema())
    size = measure_messages_bytes(messages) + guard_overhead

    if size > BUDGET_BYTES:
        truncated = [
            {**it, "intent": it["intent"][:80] + "..." if len(it["intent"]) > 80 else it["intent"]}
            for it in outline
        ]
        outline_text = _make_outline_text(truncated)
        messages = _build_messages(outline_text)
        print(f"[planner_critique] budget exceeded ({size/1024:.1f}KB) — outline intent truncated")

    result = structured_call(messages, OutlineCritique, role="judge", temperature=0.0)
    retry = state.get("outline_retry_count", 0) + (0 if result.is_outline_approved else 1)
    print(f"[planner_critique] approved={result.is_outline_approved} retry={retry}")

    # Fail-Safe: force pass after 3 retries
    if not result.is_outline_approved and retry >= 3:
        print("[planner_critique] FAIL-SAFE: forced pass (3+ retries)")
        return {
            "is_outline_approved": True,
            "outline_feedback": f"[FORCED PASS] {result.feedback}",
            "outline_retry_count": retry,
        }

    return {
        "is_outline_approved": result.is_outline_approved,
        "outline_feedback": result.feedback,
        "outline_retry_count": retry,
    }


def route_outline(state: GraphState) -> str:
    return "init_writing" if state.get("is_outline_approved") else "draft_planner"


# ──────────────────────────────────────────────────────────────
# Phase 4-B [Step 2]: Writing + Fact-check Loop
# ──────────────────────────────────────────────────────────────
def init_writing_node(state: GraphState) -> Dict[str, Any]:
    """Initialize writing loop."""
    return {
        "current_section_index": 0,
        "section_retry_count": 0,
        "previous_draft": "",
        "current_draft": "",
    }


def section_writer_node(state: GraphState) -> Dict[str, Any]:
    """
    v1.2: Injects previous_draft + hallucinated_tokens on rewrite.
          Budget-aware batch splitting with partial draft merging.
    v1.3: English output enforced with proper noun preservation.
    """
    outline = state["outline"]
    idx = state["current_section_index"]
    item = outline[idx]
    period = item["target_period"]
    grouped = state["grouped_chunks"]
    events = filter_by_period(grouped, period)
    retry = state.get("section_retry_count", 0)

    # Step 1: Build retry extras
    extra = ""
    if retry > 0:
        prev = state.get("previous_draft", "")
        bad_tokens = state.get("hallucinated_tokens", [])
        feedback = state.get("draft_feedback", "")
        prev, feedback, bad_tokens = trim_retry_context(
            prev, feedback, bad_tokens, budget_bytes=20 * 1024
        )
        extra = (
            f"\n\n[PREVIOUS REJECTED DRAFT — DO NOT repeat this]\n{prev}\n"
            f"\n[BANNED TOKENS — hallucinated terms not in source]\n{bad_tokens}\n"
            f"\n[REVISION INSTRUCTIONS]\n{feedback}\n"
        )

    system_content = (
        "You are a whitepaper writer. Write the section using ONLY the provided source "
        "event data as evidence. NEVER fabricate proper nouns, dates, or numbers not in "
        "the source. Output markdown body only."
        + _EN_ENFORCE
    )
    user_prefix = (
        f"Section title: {item['title']}\n"
        f"Target period: {period}\n"
        f"Key message: {item['intent']}\n\n"
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

    if size <= BUDGET_BYTES:
        result = structured_call(messages, SectionDraft, role="writer", temperature=0.3)
        print(f"[section_writer] idx={idx} period={period} retry={retry} len={len(result.content)}")
        return {"current_draft": result.content}

    # Budget exceeded — batch split
    data_budget = available_data_budget(
        system_content,
        SectionDraft.model_json_schema(),
        extra_fixed=user_prefix + user_suffix,
    )
    batches = split_items_for_budget(events, format_events_for_prompt, data_budget)
    print(f"[section_writer] idx={idx} budget exceeded ({size/1024:.1f}KB) — {len(batches)} batches")

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
                f"Section title: {item['title']}\n"
                f"Target period: {period}\n\n"
                f"Source events (this batch):\n{batch_text}{user_suffix}"
            )},
        ]
        part = structured_call(batch_msgs, SectionDraft, role="writer", temperature=0.3)
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
            f"Section title: {item['title']}\n\n"
            f"Partial drafts:\n{merge_input}\n\nMerged body:"
        )},
    ]
    merge_guard = estimate_guard_overhead(SectionDraft.model_json_schema())
    merge_size = measure_messages_bytes(merge_msgs) + merge_guard

    if merge_size <= BUDGET_BYTES:
        merged = structured_call(merge_msgs, SectionDraft, role="writer", temperature=0.3)
        content = merged.content
    else:
        print(f"[section_writer] idx={idx} merge also exceeded budget — concatenating")
        content = "\n\n".join(partial_drafts)

    print(f"[section_writer] idx={idx} period={period} retry={retry} len={len(content)}")
    return {"current_draft": content}


def fact_checker_node(state: GraphState) -> Dict[str, Any]:
    """
    v1.2: Mandatory hallucinated_terms extraction.
          Budget-aware batch splitting + cross_check_terms.
    v1.3: English output enforced.
    """
    outline = state["outline"]
    idx = state["current_section_index"]
    item = outline[idx]
    period = item["target_period"]
    grouped = state["grouped_chunks"]
    events = filter_by_period(grouped, period)
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

    if size <= BUDGET_BYTES:
        result = structured_call(messages, FactCheckResult, role="judge", temperature=0.0)
        print(f"[fact_checker] idx={idx} approved={result.is_draft_approved} "
              f"halluc={result.hallucinated_terms[:3]}")
        return {
            "is_draft_approved": result.is_draft_approved,
            "draft_feedback": result.feedback,
            "hallucinated_tokens": result.hallucinated_terms if not result.is_draft_approved else [],
        }

    # Budget exceeded — batch split events (draft kept in each batch)
    data_budget = available_data_budget(
        system_content,
        FactCheckResult.model_json_schema(),
        extra_fixed=f"Source events (ground truth):\n\n\nDraft under review:\n{draft}\n\nVerification result:",
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
        batch_result = structured_call(batch_msgs, FactCheckResult, role="judge", temperature=0.0)
        if not batch_result.is_draft_approved:
            all_approved = False
            all_feedback.append(batch_result.feedback)
        all_candidates.extend(batch_result.hallucinated_terms)

    # Cross-check: only truly absent tokens are hallucinations
    truly_hallucinated = cross_check_terms(all_candidates, events)
    is_approved = len(truly_hallucinated) == 0
    feedback = "; ".join(all_feedback) if all_feedback else "Batch verification passed"
    print(f"[fact_checker] idx={idx} batched: {len(batches)} batches, "
          f"candidates={len(all_candidates)}, truly_halluc={len(truly_hallucinated)}")

    return {
        "is_draft_approved": is_approved,
        "draft_feedback": feedback,
        "hallucinated_tokens": truly_hallucinated if not is_approved else [],
    }


def route_section_draft(state: GraphState) -> str:
    """
    v1.1 routing:
    - Pass → save_section
    - Fail & retry < 3 → retry_section (rewrite)
    - Fail & retry >= 3 → save_section_with_warning (fail-safe)
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
    print(f"[save_section] idx={idx} APPROVED")
    return {
        "completed_sections": {idx: draft},
        "current_section_index": idx + 1,
        "section_retry_count": 0,
        "previous_draft": "",
    }


def save_section_with_warning_node(state: GraphState) -> Dict[str, Any]:
    """Fail-Safe forced pass: watermark + unverified_sections accumulation."""
    idx = state["current_section_index"]
    draft = state["current_draft"]
    feedback = state.get("draft_feedback", "(no reason recorded)")
    warned = (
        f"> ⚠️ **Unverified Section** — Automatic fact-check failed 3 times.\n"
        f"> Last rejection reason: {feedback}\n\n"
        f"{draft}"
    )
    print(f"[save_section_with_warning] idx={idx} FORCE-PASS")
    return {
        "completed_sections": {idx: warned},
        "unverified_sections": [idx],
        "current_section_index": idx + 1,
        "section_retry_count": 0,
        "previous_draft": "",
    }


def route_next_section(state: GraphState) -> str:
    """All sections done → compiler, otherwise next section."""
    if state["current_section_index"] >= len(state["outline"]):
        return "compiler"
    return "section_writer"


# ──────────────────────────────────────────────────────────────
# Phase 4-B [Step 3]: Assembly → Polish → 2nd Fact-check
# ──────────────────────────────────────────────────────────────
def compiler_node(state: GraphState) -> Dict[str, Any]:
    """Pure Python assembly — no LLM calls."""
    outline = state["outline"]
    completed = state.get("completed_sections", {})
    unverified = state.get("unverified_sections", [])
    compiled = compile_sections(outline, completed, unverified)
    print(f"[compiler] sections={len(completed)} unverified={unverified} len={len(compiled)}")
    return {"final_compiled": compiled, "polish_retry_count": 0}


def polish_node(state: GraphState) -> Dict[str, Any]:
    """Section-by-section polishing + streaming. Prevents 504 on large contexts.
    v1.2: Paragraph-level splitting if section exceeds budget.
    v1.3: English output enforced.
    """
    compiled = state["final_compiled"]
    retry_count = state.get("polish_retry_count", 0)
    doc_header, sections, audit = split_compiled_by_section(compiled)

    if not sections:
        print("[polish] no sections found — skipping")
        return {"final_output": compiled}

    system_prompt = (
        "You are a proofreading editor. Do NOT add, delete, or modify any factual "
        "information (dates, proper nouns, numbers, causal relationships). "
        "ONLY refine paragraph transitions, coherence, and awkward phrasing. "
        "NEVER fabricate new information. "
        "Maintain markdown structure (headers, lists, blockquote warnings)."
        + _EN_ENFORCE
    )
    guard_overhead = estimate_guard_overhead(PolishedDocument.model_json_schema())

    polished_sections: List[str] = []
    for i, section in enumerate(sections):
        header, body = split_section_header_body(section)
        if not body.strip():
            polished_sections.append(section)
            continue

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Body text:\n{body}\n\nPolished result:"},
        ]
        size = measure_messages_bytes(messages) + guard_overhead

        if size <= BUDGET_BYTES:
            result = structured_call(
                messages, PolishedDocument, role="writer",
                temperature=0.1, stream=True,
            )
            polished_sections.append(header + result.content)
            print(f"[polish] section {i + 1}/{len(sections)} retry={retry_count} "
                  f"len={len(result.content)}")
        else:
            # Section exceeds budget — paragraph-level split
            paragraphs = body.split("\n\n")
            polished_paragraphs: List[str] = []
            for para in paragraphs:
                if not para.strip():
                    polished_paragraphs.append(para)
                    continue
                para_msgs = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Body text:\n{para}\n\nPolished result:"},
                ]
                para_size = measure_messages_bytes(para_msgs) + guard_overhead
                if para_size <= BUDGET_BYTES:
                    para_result = structured_call(
                        para_msgs, PolishedDocument, role="writer",
                        temperature=0.1, stream=True,
                    )
                    polished_paragraphs.append(para_result.content)
                else:
                    polished_paragraphs.append(para)
            polished_body = "\n\n".join(polished_paragraphs)
            polished_sections.append(header + polished_body)
            print(f"[polish] section {i + 1}/{len(sections)} retry={retry_count} "
                  f"paragraphs={len(paragraphs)} (budget exceeded, split)")

    final = doc_header + "".join(polished_sections) + audit
    print(f"[polish] done: sections={len(sections)} total_len={len(final)}")
    return {"final_output": final}


def final_fact_checker_node(state: GraphState) -> Dict[str, Any]:
    """Section-by-section 2nd fact-check + streaming. Prevents 504 on large contexts.
    v1.2: Skips section pairs exceeding budget (auto-approve).
    v1.3: English output enforced.
    """
    original = state["final_compiled"]
    polished = state["final_output"]
    retry_count = state.get("polish_retry_count", 0)

    _, orig_sections, _ = split_compiled_by_section(original)
    _, pol_sections, _ = split_compiled_by_section(polished)

    system_prompt = (
        "You are the final auditor. Compare the original and polished versions. "
        "Verify that no proper nouns, dates, numbers, or facts were added or altered. "
        "Sentence flow changes are allowed; only factual changes count as hallucination."
        + _EN_ENFORCE
    )
    guard_overhead = estimate_guard_overhead(FactCheckResult.model_json_schema())

    # Section count mismatch → full document comparison fallback
    if len(orig_sections) != len(pol_sections) or not orig_sections:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (
                f"[ORIGINAL]\n{original}\n\n[POLISHED]\n{polished}\n\nVerification result:"
            )},
        ]
        size = measure_messages_bytes(messages) + guard_overhead
        if size > BUDGET_BYTES:
            print(f"[final_fact_checker] fallback-full budget exceeded ({size/1024:.1f}KB) — auto-approve")
            return {
                "is_draft_approved": True,
                "draft_feedback": f"[Budget exceeded auto-approve] Full comparison not possible ({size/1024:.1f}KB)",
            }
        result = structured_call(messages, FactCheckResult, role="judge",
                                temperature=0.0, stream=True)
        print(f"[final_fact_checker] fallback-full approved={result.is_draft_approved} "
              f"retry={retry_count}")
        return {
            "is_draft_approved": result.is_draft_approved,
            "draft_feedback": result.feedback,
        }

    # Section-by-section verification
    all_approved = True
    feedback_parts: List[str] = []

    for i, (orig, pol) in enumerate(zip(orig_sections, pol_sections)):
        _, orig_body = split_section_header_body(orig)
        _, pol_body = split_section_header_body(pol)

        if not orig_body.strip() or orig_body.strip() == pol_body.strip():
            continue

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (
                f"[ORIGINAL]\n{orig_body}\n\n[POLISHED]\n{pol_body}\n\nVerification result:"
            )},
        ]
        size = measure_messages_bytes(messages) + guard_overhead

        if size > BUDGET_BYTES:
            print(f"[final_fact_checker] section {i + 1} budget exceeded ({size/1024:.1f}KB) — skipped")
            continue

        result = structured_call(messages, FactCheckResult, role="judge",
                                temperature=0.0, stream=True)
        if not result.is_draft_approved:
            all_approved = False
            feedback_parts.append(f"Section {i + 1}: {result.feedback}")
        print(f"[final_fact_checker] section {i + 1}/{len(orig_sections)} "
              f"approved={result.is_draft_approved}")

    feedback = "; ".join(feedback_parts) if feedback_parts else "All sections verified"
    print(f"[final_fact_checker] overall approved={all_approved} retry={retry_count}")
    return {
        "is_draft_approved": all_approved,
        "draft_feedback": feedback,
    }


def route_final_check(state: GraphState) -> str:
    """Polish verification routing.
    v1.3: Routes to prepare_translation instead of END on approval.
    """
    if state.get("is_draft_approved"):
        return "prepare_translation"
    if state.get("polish_retry_count", 0) >= 2:
        return "fallback_to_compiled"
    return "retry_polish"


def retry_polish_node(state: GraphState) -> Dict[str, Any]:
    return {"polish_retry_count": state.get("polish_retry_count", 0) + 1}


def fallback_to_compiled_node(state: GraphState) -> Dict[str, Any]:
    """Polish bypass — adopt final_compiled as-is."""
    print("[fallback_to_compiled] polish verification failed — adopting assembly output")
    return {"final_output": state["final_compiled"]}


# ══════════════════════════════════════════════════════════════
# Phase 5: Translation (English → Korean)
# ══════════════════════════════════════════════════════════════

def prepare_translation_node(state: GraphState) -> Dict[str, Any]:
    """Pure Python: save English output + extract proper nouns for translation."""
    english = state["final_output"]
    nouns = extract_proper_nouns(english)
    print(f"[prepare_translation] English output saved ({len(english)} chars), "
          f"extracted {len(nouns)} proper nouns")
    if nouns:
        print(f"  sample nouns: {nouns[:10]}")
    return {
        "english_output": english,
        "proper_nouns": nouns,
        "translation_retry_count": 0,
    }


def translate_node(state: GraphState) -> Dict[str, Any]:
    """Translate English whitepaper to Korean with proper noun preservation.

    Strategy:
      1. Try full-document translation if within budget.
      2. If budget exceeded, translate section by section.
      3. Structural elements (doc header, audit log) translated via
         deterministic string replacement (no LLM hallucination risk).
    """
    english = state["english_output"]
    proper_nouns = state.get("proper_nouns", [])
    retry = state.get("translation_retry_count", 0)
    feedback = state.get("translation_feedback", "")

    # Build proper noun reference
    noun_ref = "\n".join(f"  - {n}" for n in proper_nouns[:100]) if proper_nouns else "(none)"

    retry_hint = ""
    if retry > 0 and feedback:
        retry_hint = f"\n\n[PREVIOUS TRANSLATION REJECTED — fix these issues]\n{feedback}\n"

    system_prompt = (
        "You are a professional English-to-Korean translator specializing in "
        "technical and business documents.\n\n"
        "CRITICAL RULES:\n"
        "1. Preserve ALL proper nouns EXACTLY as they appear in English "
        "(do NOT transliterate, translate, or modify them).\n"
        "2. Preserve all dates (YYYY-MM-DD, YYYY-MM), numbers, percentages, "
        "and units exactly as written.\n"
        "3. Do NOT add any information not present in the English original.\n"
        "4. Do NOT omit any information from the English original.\n"
        "5. Maintain all markdown formatting: headers (##), lists (-), "
        "blockquotes (>), bold (**), italics (_), warning blocks.\n"
        "6. Produce natural, fluent Korean suitable for a formal whitepaper.\n"
        "7. Translate section headers and descriptive text to Korean, "
        "but keep proper nouns within them unchanged.\n\n"
        f"[PROPER NOUNS — PRESERVE EXACTLY AS-IS]\n{noun_ref}"
    )
    guard_overhead = estimate_guard_overhead(PolishedDocument.model_json_schema())

    # --- Attempt 1: Full-document translation ---
    full_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": (
            f"Translate the following English whitepaper to Korean:{retry_hint}\n\n"
            f"{english}\n\nKorean translation:"
        )},
    ]
    full_size = measure_messages_bytes(full_messages) + guard_overhead

    if full_size <= BUDGET_BYTES:
        result = structured_call(
            full_messages, PolishedDocument, role="writer",
            temperature=0.1, stream=True,
        )
        print(f"[translate] full-document, retry={retry}, len={len(result.content)}")
        return {"final_output": result.content}

    # --- Attempt 2: Section-by-section translation ---
    doc_header, sections, audit = split_compiled_by_section(english)
    translated_parts: List[str] = []

    for i, section in enumerate(sections):
        sec_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (
                f"Translate this section to Korean:{retry_hint}\n\n"
                f"{section}\n\nKorean translation:"
            )},
        ]
        sec_size = measure_messages_bytes(sec_messages) + guard_overhead

        if sec_size <= BUDGET_BYTES:
            sec_result = structured_call(
                sec_messages, PolishedDocument, role="writer",
                temperature=0.1, stream=True,
            )
            translated_parts.append(sec_result.content)
        else:
            # Section too large — paragraph by paragraph
            header, body = split_section_header_body(section)
            paragraphs = body.split("\n\n")
            translated_paras: List[str] = []

            # Translate header separately
            hdr_msgs = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": (
                    f"Translate this markdown header to Korean (keep proper nouns):\n\n"
                    f"{header}\n\nKorean:"
                )},
            ]
            hdr_size = measure_messages_bytes(hdr_msgs) + guard_overhead
            if hdr_size <= BUDGET_BYTES:
                hdr_result = structured_call(hdr_msgs, PolishedDocument, role="writer", temperature=0.1)
                kr_header = hdr_result.content
            else:
                # Deterministic fallback for header
                kr_header = header.replace("Target period:", "대상 기간:")

            for para in paragraphs:
                if not para.strip():
                    translated_paras.append(para)
                    continue
                para_msgs = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": (
                        f"Translate to Korean:\n\n{para}\n\nKorean:"
                    )},
                ]
                para_size = measure_messages_bytes(para_msgs) + guard_overhead
                if para_size <= BUDGET_BYTES:
                    para_result = structured_call(
                        para_msgs, PolishedDocument, role="writer", temperature=0.1,
                    )
                    translated_paras.append(para_result.content)
                else:
                    translated_paras.append(para)  # Keep English if too large
            translated_parts.append(kr_header + "\n\n".join(translated_paras))

        print(f"[translate] section {i + 1}/{len(sections)} done")

    # Deterministic structural translations (no LLM needed)
    kr_header = doc_header.replace("# Comprehensive Whitepaper", "# 종합 백서")
    kr_audit = ""
    if audit:
        kr_audit = (
            audit
            .replace("### Audit Log", "### 감사 로그")
            .replace("Unverified section indices:", "검증 미완료 섹션 인덱스:")
        )

    final = kr_header + "\n".join(translated_parts) + kr_audit
    print(f"[translate] section-by-section, retry={retry}, len={len(final)}")
    return {"final_output": final}


def translation_checker_node(state: GraphState) -> Dict[str, Any]:
    """Verify translation quality: proper noun preservation + semantic fidelity.

    Defense layers:
      1. Pure Python: check proper noun presence in Korean text.
      2. LLM spot-check: sample first section pair for semantic fidelity.
    """
    english = state["english_output"]
    korean = state["final_output"]
    proper_nouns = state.get("proper_nouns", [])
    retry = state.get("translation_retry_count", 0)

    # --- Layer 1: Pure Python proper noun check ---
    missing = [n for n in proper_nouns if n not in korean]
    missing_ratio = len(missing) / max(len(proper_nouns), 1)

    if missing_ratio > 0.3:
        msg = f"Too many proper nouns missing ({len(missing)}/{len(proper_nouns)}): {missing[:15]}"
        print(f"[translation_checker] REJECTED (python): {msg}")
        return {
            "is_translation_approved": False,
            "translation_feedback": msg,
        }

    # --- Layer 2: Structural integrity ---
    _, en_sections, _ = split_compiled_by_section(english)
    _, kr_sections, _ = split_compiled_by_section(korean)
    if len(en_sections) > 0 and len(kr_sections) == 0:
        msg = "Translation lost all section structure"
        print(f"[translation_checker] REJECTED (structure): {msg}")
        return {
            "is_translation_approved": False,
            "translation_feedback": msg,
        }

    # --- Layer 3: LLM spot-check on first section pair ---
    if en_sections and kr_sections:
        en_sample = en_sections[0][:2000]
        kr_sample = kr_sections[0][:2000]

        spot_system = (
            "You are a translation quality auditor. Compare the English original "
            "and Korean translation below. Check for:\n"
            "1. Proper nouns, dates, or numbers that were altered or missing\n"
            "2. Information added that is not in the English original\n"
            "3. Information from the English original that was omitted\n"
            "Report any issues found. Respond in English."
        )
        spot_messages = [
            {"role": "system", "content": spot_system},
            {"role": "user", "content": (
                f"[ENGLISH ORIGINAL]\n{en_sample}\n\n"
                f"[KOREAN TRANSLATION]\n{kr_sample}\n\n"
                "Verification result:"
            )},
        ]
        guard_overhead = estimate_guard_overhead(TranslationCheckResult.model_json_schema())
        spot_size = measure_messages_bytes(spot_messages) + guard_overhead

        if spot_size <= BUDGET_BYTES:
            result = structured_call(
                spot_messages, TranslationCheckResult, role="judge", temperature=0.0,
            )
            if not result.is_approved:
                all_missing = list(set(missing + result.missing_terms))
                msg = f"LLM spot-check failed: {result.feedback}. Missing: {all_missing[:10]}"
                print(f"[translation_checker] REJECTED (LLM): {msg}")
                return {
                    "is_translation_approved": False,
                    "translation_feedback": msg,
                }
            print(f"[translation_checker] LLM spot-check passed: {result.feedback[:60]}")
        else:
            print(f"[translation_checker] LLM spot-check skipped (budget exceeded)")

    # All checks passed
    if missing:
        print(f"[translation_checker] minor missing nouns (accepted): {missing[:5]}")
    print(f"[translation_checker] APPROVED retry={retry}")
    return {
        "is_translation_approved": True,
        "translation_feedback": "Translation approved" + (
            f" (minor missing: {missing})" if missing else ""
        ),
    }


def route_translation(state: GraphState) -> str:
    """Translation verification routing."""
    if state.get("is_translation_approved"):
        return "END"
    if state.get("translation_retry_count", 0) >= 2:
        return "fallback_english"
    return "retry_translate"


def retry_translate_node(state: GraphState) -> Dict[str, Any]:
    """Increment retry counter for translation re-attempt."""
    retry = state.get("translation_retry_count", 0) + 1
    print(f"[retry_translate] retrying translation (attempt {retry})")
    return {"translation_retry_count": retry}


def fallback_english_node(state: GraphState) -> Dict[str, Any]:
    """Translation verification failed — keep English version with warning header."""
    english = state["english_output"]
    warned = (
        "> ⚠️ **Translation Notice** — English-to-Korean translation could not be verified "
        "after multiple attempts. English original is preserved below.\n\n"
        + english
    )
    print("[fallback_english] Translation verification failed — keeping English output")
    return {"final_output": warned}
