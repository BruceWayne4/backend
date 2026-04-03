"""
Gantt parser, stage computation, metrics engine, and diff logic.
DEBUG logging enabled — remove once date format is confirmed.

All metric calculations (Stage, Execution Speed, Planning Depth, Shipping Velocity)
are computed server-side to minimise dependence on Google Sheets formula evaluation.
"""

import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional
from app.models.gantt import GanttSnapshot

log = logging.getLogger(__name__)

# ── Stage sort order for task display ────────────────────────────────────────
STAGE_ORDER = {
    "Delayed": 0,
    "Done but Delayed": 1,
    "In Progress": 2,
    "Yet to Start": 3,
    "Done": 4,
}

# Score assigned to each stage for Execution Speed calculation.
#
# Sourced directly from the sheet's own criteria table (Task_List col O):
#   Stage              Score
#   Yet to Start         0     ← excluded from ES denominator
#   Delayed             -0.5   ← NEGATIVE penalty for overdue tasks
#   In Progress          0.5   ← partial credit
#   Done                 1.0   ← full credit
#   Done but Delayed     0.75  ← partial credit (shipped late)
#
# Verified: with 7 Done + 9 Done-but-Delayed + 0 others in window →
#   ES = (7×1.0 + 9×0.75) / 16 = 13.75/16 = 0.8594 = 85.9% ✓
STAGE_SCORES = {
    "Yet to Start": 0.0,       # excluded from ES denominator
    "Delayed": -0.5,           # PENALTY — overdue, no completion
    "In Progress": 0.5,        # partial credit
    "Done": 1.0,               # full credit
    "Done but Delayed": 0.75,  # partial credit — late but completed
}

# Weights from Excel N20 / N21
EXEC_SPEED_WEIGHT = 0.7
PLAN_DEPTH_WEIGHT = 0.3
WINDOW_DAYS = 90  # ±90 days → 180-day window


# ── Date parsing helpers ──────────────────────────────────────────────────────

_DATE_FORMATS = [
    "%b %d, %Y",  # Google Sheets display format: "Feb 28, 2026"
    "%B %d, %Y",  # full month name: "February 28, 2026"
    "%m/%d/%Y",   # US locale: "2/28/2026"
    "%d/%m/%Y",   # alternate locale
    "%Y-%m-%d",   # ISO: "2026-02-28"
    "%m/%d/%y",   # 2-digit year
    "%d-%m-%Y",
]


def _parse_date(val: Optional[str]) -> Optional[date]:
    """Try to parse a date string with multiple common formats. Returns None on failure."""
    if not val:
        return None
    val = val.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(val, fmt).date()
        except ValueError:
            continue
    return None


# ── Core stage computation (replicates Excel Column J formula) ────────────────

def compute_stage(
    division: Optional[str],
    start_date_str: Optional[str],
    end_date_str: Optional[str],
    completion_date_str: Optional[str],
    today: Optional[date] = None,
) -> Optional[str]:
    """
    Replicate the Excel Column J formula:

        IF(ISBLANK(A{n}), "",
          IF(TODAY()<D{n}, "1 - Yet to Start",
            IF(ISBLANK(K{n}),
              IF(TODAY()<=E{n}, "3 - In Progress", "2 - Delayed"),
              IF(K{n}<=E{n}, "4 - Done", "5 - Done but Delayed")
            )
          )
        )

    Returns a clean stage label (no numeric prefix).
    Returns None if the row has no division (blank row suppression).
    Returns None if start/end dates cannot be parsed (caller uses sheet value as fallback).
    """
    if not division:
        return None

    today = today or date.today()

    start = _parse_date(start_date_str)
    end = _parse_date(end_date_str)

    if start is None or end is None:
        # Cannot compute — signal fallback to caller
        return None

    completion = _parse_date(completion_date_str)

    if today < start:
        return "Yet to Start"
    if completion is None:
        return "In Progress" if today <= end else "Delayed"
    return "Done" if completion <= end else "Done but Delayed"


# ── Metrics engine (replicates N3 / N4 / N1) ────────────────────────────────

def compute_metrics_from_tasks(
    tasks: list[dict],
    today: Optional[date] = None,
    company_name: Optional[str] = None,
) -> dict:
    """
    Compute Execution Speed (N3), Planning Depth (N4), and composite
    Shipping Velocity (N1) from the task list.

    Replicates the Excel formulas:
        Window: today ± WINDOW_DAYS (default ±60 days)
        N3 = SUMPRODUCT(counts[non-YTS], scores) / SUM(counts[non-YTS])
        N4 = MIN(count_yts / total_in_window * 2, 1.0)
        N1 = N3 * 0.7 + N4 * 0.3
    """
    today = today or date.today()
    window_start = today - timedelta(days=WINDOW_DAYS)
    window_end = today + timedelta(days=WINDOW_DAYS)

    # Count tasks in window by stage
    stage_counts: dict[str, int] = {s: 0 for s in STAGE_SCORES}

    unparseable_dates = 0
    out_of_window = 0

    for task in tasks:
        raw_start = task.get("start_date")
        start = _parse_date(raw_start)
        if start is None:
            unparseable_dates += 1
            continue
        # Filter: task start date must fall within the ±60-day window
        if not (window_start <= start <= window_end):
            out_of_window += 1
            continue
        stage = (task.get("stage") or "").strip()
        if stage in stage_counts:
            stage_counts[stage] += 1

    _company_tag = f" company={company_name!r}" if company_name else ""
    log.debug(
        "METRICS%s: total=%d window=%s..%s unparseable=%d out_of_window=%d stage_counts=%s",
        _company_tag, len(tasks), window_start, window_end, unparseable_dates, out_of_window, stage_counts,
    )
    if unparseable_dates:
        log.warning(
            "METRICS_PARSE_FAIL%s: %d/%d tasks had unparseable start_date — excluded from velocity window",
            _company_tag, unparseable_dates, len(tasks),
        )
    for t in tasks[:3]:
        log.debug("SAMPLE%s start_date=%r  end_date=%r  stage=%r", _company_tag, t.get("start_date"), t.get("end_date"), t.get("stage"))

    total_in_window = sum(stage_counts.values())
    if total_in_window == 0:
        return {
            "shipping_velocity": None,
            "execution_speed": None,
            "planning_depth": None,
        }

    # Execution Speed: excludes "Yet to Start" (Excel N14:N17, not N13)
    non_yts_stages = [s for s in STAGE_SCORES if s != "Yet to Start"]
    non_yts_total = sum(stage_counts[s] for s in non_yts_stages)

    if non_yts_total == 0:
        # All tasks are Yet to Start — no execution to measure
        execution_speed = 0.0
    else:
        weighted_sum = sum(
            stage_counts[s] * STAGE_SCORES[s] for s in non_yts_stages
        )
        execution_speed = weighted_sum / non_yts_total

    # Planning Depth: ratio of YTS tasks to total, doubled, capped at 1.0
    yts_count = stage_counts["Yet to Start"]
    planning_depth = min((yts_count / total_in_window) * 2, 1.0)

    # Composite Shipping Velocity
    shipping_velocity = (execution_speed * EXEC_SPEED_WEIGHT) + (planning_depth * PLAN_DEPTH_WEIGHT)

    return {
        "shipping_velocity": round(shipping_velocity, 4),
        "execution_speed": round(execution_speed, 4),
        "planning_depth": round(planning_depth, 4),
    }


# ── Main parse entry point ────────────────────────────────────────────────────

def parse_sheet_data(raw_data: dict, company_name: Optional[str] = None) -> dict:
    """
    Takes raw data returned by sheets_service.fetch_sheet_data() and returns
    a structured dict ready to be stored in a GanttSnapshot.

    Stage:
      Always taken directly from the spreadsheet (col J). The Stage column is
      a manually-set dropdown — it is the single authoritative source.
      Server-side recomputation is NOT performed.

    Metrics (Execution Speed, Planning Depth, Shipping Velocity):
      1. Server-computed (primary) — uses sheet_today anchor from N6 so the
         ±90-day window matches the sheet exactly.
      2. Sheet formula values (N1/N3/N4) — fallback only when server computation
         returns None (e.g. zero tasks fall inside the window).
    """
    tasks: list[dict] = raw_data.get("tasks", [])
    scorecard_history: list[dict] = raw_data.get("scorecard_history", [])

    _company_tag = f" company={company_name!r}" if company_name else ""
    log.info("PARSE%s: received tasks=%d scorecard_history=%d", _company_tag, len(tasks), len(scorecard_history))

    # ── Resolve "today" — prefer the sheet's own TODAY() anchor (N6) ─────────
    # This ensures stage computation + window filtering match the sheet exactly.
    raw_sheet_today: Optional[str] = raw_data.get("sheet_today")
    today: date = date.today()
    if raw_sheet_today:
        try:
            parsed_today = datetime.strptime(raw_sheet_today, "%Y-%m-%d").date()
            log.info("PARSE%s: using sheet_today=%s (server today=%s)", _company_tag, parsed_today, date.today())
            today = parsed_today
        except ValueError:
            log.warning("PARSE%s: could not parse sheet_today=%r, falling back to server today", _company_tag, raw_sheet_today)

    # ── 1. Stage — always use the spreadsheet value (col J) ──────────────────
    # The Stage column is the single authoritative source. It is manually set by
    # the user via a dropdown in the central Gantt_Overall spreadsheet.
    # Server-side recomputation is NOT performed — it would incorrectly override
    # user-set stages because the central sheet has no completion date column.
    log.info("PARSE%s: using spreadsheet stage values for all %d tasks (no server override)", _company_tag, len(tasks))

    # ── 2. Metrics — server-computed (primary); sheet N1/N3/N4 as fallback ────
    # Server computation uses the sheet_today anchor (N6) so the ±90-day window
    # matches the sheet exactly. Sheet formula values (N1/N3/N4) are kept only
    # as a safety net when server computation returns None (e.g. zero tasks fall
    # inside the window).
    sheet_sv = raw_data.get("shipping_velocity")
    sheet_es = raw_data.get("execution_speed")
    sheet_pd = raw_data.get("planning_depth")

    log.info(
        "PARSE%s: sheet metric values (reference only) SV=%s ES=%s PD=%s (sheet_today=%s)",
        _company_tag, sheet_sv, sheet_es, sheet_pd, today,
    )

    # Always run server computation
    computed_metrics = compute_metrics_from_tasks(tasks, today=today, company_name=company_name)
    log.info(
        "PARSE%s: server-computed (primary) SV=%s ES=%s PD=%s",
        _company_tag,
        computed_metrics["shipping_velocity"],
        computed_metrics["execution_speed"],
        computed_metrics["planning_depth"],
    )

    # Server value wins; sheet value is fallback when server returns None
    shipping_velocity = computed_metrics["shipping_velocity"] if computed_metrics["shipping_velocity"] is not None else sheet_sv
    execution_speed   = computed_metrics["execution_speed"]   if computed_metrics["execution_speed"]   is not None else sheet_es
    planning_depth    = computed_metrics["planning_depth"]    if computed_metrics["planning_depth"]    is not None else sheet_pd

    # ── 3. Planning quality score (task granularity + count health) ───────────
    planning_quality_score = compute_planning_quality_score(tasks)

    return {
        "tasks": tasks,
        "shipping_velocity": shipping_velocity,
        "execution_speed": execution_speed,
        "planning_depth": planning_depth,
        "planning_quality_score": planning_quality_score,
        "task_count": len(tasks),
        "scorecard_history": scorecard_history,
    }


def diff_snapshots(
    prev_snapshot: Optional[GanttSnapshot], new_tasks: list[dict]
) -> Optional[dict]:
    """
    Compare new tasks against the previous snapshot.

    Returns:
        None if no previous snapshot exists (first pull).
        Otherwise returns:
            {
                "stage_changes": [{"task": ..., "project": ..., "division": ..., "from": ..., "to": ...}],
                "new_tasks": [...],
                "removed_tasks": [...]
            }
    """
    if prev_snapshot is None:
        return None

    prev_tasks: list[dict] = prev_snapshot.tasks or []

    def task_key(t: dict) -> tuple:
        return (
            (t.get("division") or "").strip(),
            (t.get("project") or "").strip(),
            (t.get("task") or "").strip(),
        )

    prev_map = {task_key(t): t for t in prev_tasks}
    new_map = {task_key(t): t for t in new_tasks}

    stage_changes = []
    for key, new_task in new_map.items():
        if key in prev_map:
            old_stage = (prev_map[key].get("stage") or "").strip()
            new_stage = (new_task.get("stage") or "").strip()
            if old_stage != new_stage:
                stage_changes.append(
                    {
                        "division": new_task.get("division"),
                        "project": new_task.get("project"),
                        "task": new_task.get("task"),
                        "from": old_stage,
                        "to": new_stage,
                    }
                )

    added_keys = set(new_map.keys()) - set(prev_map.keys())
    removed_keys = set(prev_map.keys()) - set(new_map.keys())

    new_task_list = [new_map[k] for k in added_keys]
    removed_task_list = [prev_map[k] for k in removed_keys]

    return {
        "stage_changes": stage_changes,
        "new_tasks": new_task_list,
        "removed_tasks": removed_task_list,
    }


def compute_planning_quality_score(tasks: list[dict]) -> float:
    """
    Compute a planning quality score (0.0 – 1.0) based on:
      - % of tasks with duration ≤ 14 days (ideal granularity per guideline M24)
      - Total task count relative to target range 100–140 (guideline M25)

    Returns:
        float between 0.0 and 1.0
    """
    if not tasks:
        return 0.0

    total = len(tasks)

    # Component 1: % of tasks with duration ≤ 14 days
    short_tasks = sum(
        1
        for t in tasks
        if isinstance(t.get("duration_days"), (int, float)) and t["duration_days"] <= 14
    )
    granularity_score = short_tasks / total

    # Component 2: task count score — ideal range is 100–140
    TARGET_LOW = 100
    TARGET_HIGH = 140
    if TARGET_LOW <= total <= TARGET_HIGH:
        count_score = 1.0
    elif total < TARGET_LOW:
        count_score = max(0.0, total / TARGET_LOW)
    else:
        count_score = max(0.0, 1.0 - (total - TARGET_HIGH) / TARGET_HIGH)

    # Weighted average: 60% granularity, 40% count
    score = 0.6 * granularity_score + 0.4 * count_score
    return round(min(1.0, max(0.0, score)), 4)


def sort_tasks(tasks: list[dict]) -> list[dict]:
    """
    Sort tasks: Delayed → Done but Delayed → In Progress → Yet to Start → Done.
    Unknown stages sorted last.
    """
    return sorted(
        tasks,
        key=lambda t: STAGE_ORDER.get((t.get("stage") or "").strip(), 99),
    )
