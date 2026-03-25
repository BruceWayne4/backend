"""
Google Sheets API v4 integration.

Reads two tabs from a founder's Google Sheet:
  - Task_List   (columns A–K for tasks; M–R fallback metrics)
  - Gantt_Scorecard (8-week velocity history for sparkline seeding)
"""

import logging
import os
import re
from datetime import date, timedelta
from typing import Optional
from googleapiclient.discovery import build
from google.oauth2 import service_account
from fastapi import HTTPException, status
from app.config import settings

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Regex to strip "N - " numeric prefix from Excel stage labels
# e.g. "1 - Yet to Start" → "Yet to Start", "3 - In Progress" → "In Progress"
_STAGE_PREFIX_RE = re.compile(r"^\d+\s*-\s*")


def _get_service():
    creds_file = settings.GOOGLE_CREDENTIALS_FILE
    if not os.path.exists(creds_file):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google credentials not configured",
        )
    creds = service_account.Credentials.from_service_account_file(
        creds_file, scopes=SCOPES
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _extract_sheet_id(sheets_url: str) -> str:
    """Extract spreadsheet ID from a Google Sheets URL or return as-is if already an ID."""
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", sheets_url)
    if match:
        return match.group(1)
    return sheets_url


def _safe_float(val) -> Optional[float]:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _safe_int(val) -> Optional[int]:
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


def _strip_stage_prefix(raw: str) -> str:
    """Remove leading 'N - ' prefix from Excel stage labels."""
    stripped = _STAGE_PREFIX_RE.sub("", raw).strip()
    if stripped != raw:
        log.debug("STAGE_STRIP: %r → %r", raw, stripped)
    return stripped


def _parse_sheets_date(val, fallback_date: Optional[date] = None) -> Optional[str]:
    """
    Google Sheets returns date cells as formatted strings (e.g. "3/14/2026")
    or as serial numbers when unformatted (UNFORMATTED_VALUE render option).
    Accepts int, float, or str. Returns ISO date string or None on failure.

    Returns None for pure-text non-date values (e.g. label cells like "Today",
    "Date", "Shipping Velocity") so callers receive a clean None rather than
    a garbage string that downstream parsers will silently reject.
    """
    if val is None or val == "":
        return None
    # With UNFORMATTED_VALUE, date cells come back as int/float serials.
    # Cast everything to str for the strip + serial-detection path below.
    val = str(val).strip()
    if not val:
        return None
    # If it looks like a number (serial date), convert
    try:
        serial = int(float(val))
        # Sanity check: valid Sheets serials are > 1 (1 = 1899-12-31).
        # Serials below 1000 are almost certainly not real dates (e.g. a row
        # number or a boolean 1/0 leaked into a date cell).
        if serial < 1000:
            log.debug("DATE_SERIAL: %r serial=%d too small — treating as non-date", val, serial)
            return None
        # Excel/Sheets serial: days since 1899-12-30
        base = date(1899, 12, 30)
        d = base + timedelta(days=serial)
        result = d.isoformat()
        log.debug("DATE_SERIAL: %r → %r (serial=%d)", val, result, serial)
        return result
    except (ValueError, TypeError):
        pass
    # Plain date strings (e.g. "3/14/2026") are returned as-is for
    # gantt_service._parse_date() to handle.
    # BUT reject obvious non-date text labels (all-alpha strings like "Today",
    # "Date", column headers, etc.) — return None so callers get a clean signal.
    if val.replace("/", "").replace("-", "").replace(" ", "").replace(",", "").isalpha():
        log.warning("DATE_LABEL: %r looks like a text label, not a date — returning None", val)
        return None
    log.debug("DATE_RAW: %r returned as-is", val)
    return val


def fetch_sheet_data(sheets_url: str) -> dict:
    """
    Fetch and parse data from the Google Sheet at sheets_url.

    Returns:
        {
            "tasks": [...],
            "shipping_velocity": float | None,   # fallback from sheet cell N1
            "execution_speed": float | None,     # fallback from sheet cell N3
            "planning_depth": float | None,      # fallback from sheet cell N4
            "task_count": int,
            "scorecard_history": [               # for sparkline seeding on first pull
                {"date": "YYYY-MM-DD", "velocity": float},
                ...
            ],
        }
    """
    log.info("SHEETS_FETCH: starting fetch for url=%s", sheets_url[:60])
    service = _get_service()
    sheet_id = _extract_sheet_id(sheets_url)
    log.info("SHEETS_FETCH: resolved sheet_id=%s", sheet_id)
    spreadsheet = service.spreadsheets()

    # ── 1. Task_List A2:K — task rows ────────────────────────────────────────
    # UNFORMATTED_VALUE: dates come back as numeric serials (reliable parse),
    # numbers as floats, text as strings — avoids locale-formatted date strings.
    task_result = (
        spreadsheet.values()
        .get(spreadsheetId=sheet_id, range="Task_List!A2:K",
             valueRenderOption="UNFORMATTED_VALUE")
        .execute()
    )
    raw_tasks = task_result.get("values", [])
    log.info("SHEETS_FETCH: raw Task_List rows received=%d", len(raw_tasks))

    tasks = []
    skipped_empty = 0
    for row_idx, row in enumerate(raw_tasks):
        # Pad row to at least 11 columns
        row = row + [""] * (11 - len(row))
        division = row[0] or None
        project = row[1] or None
        task_name = row[2] or None
        start_date = _parse_sheets_date(row[3]) if row[3] else None
        end_date = _parse_sheets_date(row[4]) if row[4] else None
        duration_days = _safe_int(row[5])
        resource_1 = row[6] or None
        resource_2 = row[7] or None
        resource_3 = row[8] or None

        # Strip numeric prefix from stage (e.g. "1 - Yet to Start" → "Yet to Start")
        raw_stage = row[9] or ""
        stage = _strip_stage_prefix(raw_stage) if raw_stage else None

        completion_date = _parse_sheets_date(row[10]) if row[10] else None

        # Skip completely empty rows
        if not any([division, project, task_name]):
            skipped_empty += 1
            continue

        # Log first 5 tasks for debugging date formats
        if len(tasks) < 5:
            log.info(
                "TASK_ROW[%d]: div=%r project=%r task=%r raw_start=%r parsed_start=%r "
                "raw_end=%r parsed_end=%r raw_stage=%r stripped_stage=%r completion=%r",
                row_idx + 2, division, project, task_name,
                row[3], start_date, row[4], end_date,
                raw_stage, stage, completion_date,
            )

        tasks.append(
            {
                "division": division,
                "project": project,
                "task": task_name,
                "start_date": start_date,
                "end_date": end_date,
                "duration_days": duration_days,
                "resource_1": resource_1,
                "resource_2": resource_2,
                "resource_3": resource_3,
                "stage": stage,
                "completion_date": completion_date,
            }
        )

    log.info(
        "SHEETS_FETCH: tasks parsed=%d skipped_empty=%d",
        len(tasks), skipped_empty,
    )

    # ── 2. Task_List M1:R8 — metric cells + sheet's TODAY() anchor ────────────
    # Layout (0-indexed in the fetched sub-range, col M = index 0, col N = index 1):
    #   Row 0 (M1/N1): "Shipping Velocity" | composite SV formula result
    #   Row 2 (M3/N3): "Execution Speed"   | ES formula result
    #   Row 3 (M4/N4): "Planning Depth"    | PD formula result
    #   Row 5 (M6/N6): "Today"             | TODAY() serial (key anchor date)
    #   Row 6 (M7/N7): "Window Length"     | 120
    # UNFORMATTED_VALUE so formula results come back as raw floats and date
    # cells come back as numeric serials.
    metrics_result = (
        spreadsheet.values()
        .get(spreadsheetId=sheet_id, range="Task_List!M1:R8",
             valueRenderOption="UNFORMATTED_VALUE")
        .execute()
    )
    metrics_rows = metrics_result.get("values", [])
    log.info("SHEETS_FETCH: metrics rows received=%d", len(metrics_rows))

    shipping_velocity: Optional[float] = None
    execution_speed: Optional[float] = None
    planning_depth: Optional[float] = None
    sheet_today: Optional[str] = None   # ISO date string of sheet's TODAY() anchor

    def _get_cell(rows: list, row_idx: int, col_idx: int) -> Optional[str]:
        try:
            return rows[row_idx][col_idx]
        except IndexError:
            return None

    # N1 → row index 0, col index 1 (N is the 2nd column of the M:R sub-range)
    shipping_velocity = _safe_float(_get_cell(metrics_rows, 0, 1))
    # N3 → row index 2, col index 1
    execution_speed = _safe_float(_get_cell(metrics_rows, 2, 1))
    # N4 → row index 3, col index 1
    planning_depth = _safe_float(_get_cell(metrics_rows, 3, 1))
    # N6 → row index 5, col index 1 — the sheet's TODAY() value
    # With UNFORMATTED_VALUE this comes back as an Excel/Sheets serial number
    sheet_today = _parse_sheets_date(str(_get_cell(metrics_rows, 5, 1) or ""))

    log.info(
        "SHEETS_FETCH: metrics SV=%s ES=%s PD=%s sheet_today=%r",
        shipping_velocity, execution_speed, planning_depth, sheet_today,
    )

    # ── 3. Gantt_Scorecard A1:BZ2 — full velocity history (wide range, no cap)
    # Layout:
    #   Row 1: "Date"  | "Median" | 09/06 | 16/06 | ... (DD/MM weekly dates)
    #   Row 2: "Shipping Velocity" | <median%> | 0.41 | 0.40 | ...
    #   Column A (index 0) = row label  ("Date" / "Shipping Velocity")
    #   Column B (index 1) = "Median"   (aggregate, NOT a data point — skip)
    #   Column C onward    = weekly data points
    # UNFORMATTED_VALUE: date cells return as numeric serials → reliable ISO parse.
    scorecard_result = (
        spreadsheet.values()
        .get(spreadsheetId=sheet_id, range="Gantt_Scorecard!A1:BZ2",
             valueRenderOption="UNFORMATTED_VALUE")
        .execute()
    )
    scorecard_rows = scorecard_result.get("values", [])
    log.info("SHEETS_FETCH: scorecard rows received=%d", len(scorecard_rows))

    scorecard_history: list[dict] = []
    if len(scorecard_rows) >= 2:
        date_row = scorecard_rows[0]    # row 1: date headers
        vel_row = scorecard_rows[1]     # row 2: velocity values

        # Start at index 2 (skip col A = label, col B = Median)
        max_col = max(len(date_row), len(vel_row))
        for col_idx in range(2, max_col):
            raw_date = date_row[col_idx] if col_idx < len(date_row) else None
            raw_vel = vel_row[col_idx] if col_idx < len(vel_row) else None

            vel = _safe_float(raw_vel)
            if vel is None:
                log.debug("SCORECARD col=%d: no velocity, skipping", col_idx)
                continue

            # With UNFORMATTED_VALUE, date cells return as numeric serials
            parsed_date = _parse_sheets_date(str(raw_date)) if raw_date is not None else None
            log.info(
                "SCORECARD col=%d: raw_date=%r parsed_date=%r vel=%s",
                col_idx, raw_date, parsed_date, vel,
            )
            if parsed_date is None:
                log.warning("SCORECARD col=%d: unparseable date raw=%r, skipping", col_idx, raw_date)
                continue

            scorecard_history.append({"date": parsed_date, "velocity": vel})

    log.info("SHEETS_FETCH: scorecard_history entries built=%d", len(scorecard_history))

    return {
        "tasks": tasks,
        "shipping_velocity": shipping_velocity,
        "execution_speed": execution_speed,
        "planning_depth": planning_depth,
        "sheet_today": sheet_today,   # ISO string of the sheet's TODAY() anchor
        "task_count": len(tasks),
        "scorecard_history": scorecard_history,
    }
