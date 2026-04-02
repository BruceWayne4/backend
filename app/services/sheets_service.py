"""
Google Sheets API v4 integration.

Two modes of operation:

1. CENTRAL SPREADSHEET MODE (new, preferred)
   A single "Gantt_Overall" spreadsheet contains one tab per portfolio company.
   Tab names match company names (e.g. "GaadiMech", "Thread Factory").
   The "Overall_Gantt" tab holds aggregate metrics for all companies.

   Use:
     fetch_central_sheet_data(company_name)  — reads per-company tab + Overall_Gantt metrics
     fetch_overall_metrics()                 — reads Overall_Gantt summary tab only

   Configured via GANTT_SPREADSHEET_ID env var.

2. PER-COMPANY SPREADSHEET MODE (legacy, kept for backward compatibility)
   Each company has its own Google Sheet with a Task_List tab and Gantt_Scorecard tab.

   Use:
     fetch_sheet_data(sheets_url)            — reads Task_List + Gantt_Scorecard tabs

   Configured via company.sheets_url field.

Both modes share the same append_task_to_sheet() for writing suggestions back.
"""

import logging
import os
import re
import time
from datetime import date, timedelta
from typing import Optional
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2 import service_account
from fastapi import HTTPException, status
from app.config import settings

log = logging.getLogger(__name__)

# Read-write scope required for append_task_to_sheet()
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Regex to strip "N - " numeric prefix from Excel stage labels
# e.g. "1 - Yet to Start" → "Yet to Start", "3 - In Progress" → "In Progress"
_STAGE_PREFIX_RE = re.compile(r"^\d+\s*-\s*")


def _get_service():
    creds_file = settings.GOOGLE_CREDENTIALS_FILE.strip()
    if not os.path.exists(creds_file):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google credentials not configured",
        )
    creds = service_account.Credentials.from_service_account_file(
        creds_file.strip(), scopes=SCOPES
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _sheets_get_with_retry(request, max_retries: int = 3) -> dict:
    """
    Execute a Google Sheets API read request with exponential backoff on HTTP 429.

    Retries up to `max_retries` times with delays of 1s, 2s, 4s before giving up.
    All other errors are re-raised immediately.
    """
    delay = 1.0
    last_exc: Exception = RuntimeError("_sheets_get_with_retry: no attempts made")
    for attempt in range(max_retries + 1):
        try:
            return request.execute()
        except HttpError as e:
            last_exc = e
            if e.resp.status == 429 and attempt < max_retries:
                log.warning(
                    "SHEETS_RETRY: HTTP 429 rate-limited, retrying in %.0fs (attempt %d/%d)",
                    delay, attempt + 1, max_retries,
                )
                time.sleep(delay)
                delay *= 2
            else:
                raise
    raise last_exc  # unreachable in practice; satisfies type checkers


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


def _parse_central_date(val) -> Optional[str]:
    """
    Parse date strings from the central Gantt_Overall spreadsheet.
    Dates are stored as human-readable strings like "Jan 20, 2025" or "Feb 10, 2025".
    Also handles:
      - "11 Apr 2026"  (DD Mon YYYY, day-first no comma)
      - "8-Apr-2026"   (D-Mon-YYYY, hyphen-separated)
      - "April 7 2026" (full month name + day + year)
      - "Dec 1 ,2025"  (irregular comma spacing — normalised before parsing)
      - "Dec 15 , 2025" (same)
      - "Mar 9"        (abbreviated month + day, no year — year inferred)
      - "Apr 15"       (same)
    Returns ISO date string (YYYY-MM-DD) or None.
    """
    if val is None or val == "":
        return None
    val = str(val).strip()
    if not val:
        return None

    from datetime import datetime as _dt
    import re as _re

    # Normalise irregular comma spacing: "Dec 1 ,2025" / "Dec 15 , 2025"
    # → "Dec 1, 2025"  (collapse any spaces around a comma into ", ")
    val_norm = _re.sub(r"\s*,\s*", ", ", val).strip()

    # Try common date formats used in the central sheet
    formats = [
        "%b %d, %Y",   # "Jan 20, 2025"
        "%B %d, %Y",   # "January 20, 2025"
        "%b %d %Y",    # "Jan 20 2025"
        "%B %d %Y",    # "January 20 2025"  / "April 7 2026"
        "%d %b %Y",    # "11 Apr 2026"  (day first, no comma)
        "%d %B %Y",    # "11 April 2026"
        "%d-%b-%Y",    # "8-Apr-2026"  (day-MonAbbr-year, no leading zero handled by %d)
        "%d-%B-%Y",    # "8-April-2026" (day-FullMonth-year)
        "%m/%d/%Y",    # "01/20/2025"
        "%m/%d/%y",    # "01/20/25"
        "%Y-%m-%d",    # "2025-01-20"
        "%d/%m/%Y",    # "20/01/2025"
    ]
    for candidate_val in (val_norm, val) if val_norm != val else (val,):
        for fmt in formats:
            try:
                return _dt.strptime(candidate_val, fmt).date().isoformat()
            except ValueError:
                continue

    # Partial date: "Mar 9" / "Apr 15" / "Jun 14" — month + day, no year.
    # Infer the year: use the current year; if the resulting date is more than
    # 6 months in the past, bump to next year (avoids stale dates for future tasks).
    partial_formats = [
        "%b %d",   # "Mar 9", "Apr 15"
        "%B %d",   # "March 9", "April 15"
        "%b %-d",  # same but explicit no-padding (Linux only; harmless fallback)
    ]
    for fmt in partial_formats:
        try:
            parsed = _dt.strptime(val, fmt)
            today = date.today()
            # Try current year first
            candidate = parsed.replace(year=today.year).date()
            # If candidate is more than ~180 days in the past, use next year
            if (today - candidate).days > 180:
                candidate = parsed.replace(year=today.year + 1).date()
            log.debug("CENTRAL_DATE: partial %r → %r (inferred year)", val, candidate.isoformat())
            return candidate.isoformat()
        except ValueError:
            continue

    # Try serial number fallback
    try:
        serial = int(float(val))
        if serial >= 1000:
            base = date(1899, 12, 30)
            return (base + timedelta(days=serial)).isoformat()
    except (ValueError, TypeError):
        pass

    log.warning("CENTRAL_DATE: could not parse %r", val)
    return None


# ── CENTRAL SPREADSHEET MODE ──────────────────────────────────────────────────

def _get_central_spreadsheet_id() -> str:
    """
    Return the central Gantt spreadsheet ID from config.
    Raises HTTPException 503 if not configured.
    """
    raw = settings.GANTT_SPREADSHEET_ID
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GANTT_SPREADSHEET_ID not configured",
        )
    return _extract_sheet_id(raw)


def fetch_overall_metrics() -> dict:
    """
    Read the Overall_Gantt summary tab from the central spreadsheet.

    Returns a dict keyed by company name:
        {
            "GaadiMech": {
                "yet_to_start": int,
                "delayed": int,
                "in_progress": int,
                "done": int,
                "done_but_delayed": int,
                "total": int,
                "execution_speed": float | None,   # e.g. 0.30 for 30%
                "planning_depth": float | None,
                "shipping_velocity": float | None,
            },
            ...
        }
    """
    service = _get_service()
    sheet_id = _get_central_spreadsheet_id()

    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range="Overall_Gantt")
        .execute()
    )
    rows = result.get("values", [])
    log.info("OVERALL_METRICS: received %d rows from Overall_Gantt", len(rows))

    metrics: dict[str, dict] = {}

    def _pct_to_float(val) -> Optional[float]:
        """Convert '75%' or '0.75' to float 0.75, or None."""
        if val is None or val == "":
            return None
        s = str(val).strip().rstrip("%")
        try:
            f = float(s)
            # If it looks like a percentage integer (e.g. 75), divide by 100
            if "%" in str(val):
                return f / 100.0
            # If already a decimal fraction (e.g. 0.75), return as-is
            return f
        except (ValueError, TypeError):
            return None

    # Row 0 is the header: Company, 1-Yet to Start, 2-Delayed, 3-In Progress,
    #                       4-Done, 5-Done but Delayed, Total, Execution Speed,
    #                       Planning Depth, Shipping Velocity
    for row in rows[1:]:  # skip header
        if not row or not row[0]:
            continue
        row = row + [""] * (10 - len(row))  # pad to 10 cols
        company_name = str(row[0]).strip()
        if not company_name:
            continue
        metrics[company_name] = {
            "yet_to_start": _safe_int(row[1]) or 0,
            "delayed": _safe_int(row[2]) or 0,
            "in_progress": _safe_int(row[3]) or 0,
            "done": _safe_int(row[4]) or 0,
            "done_but_delayed": _safe_int(row[5]) or 0,
            "total": _safe_int(row[6]) or 0,
            "execution_speed": _pct_to_float(row[7]),
            "planning_depth": _pct_to_float(row[8]),
            "shipping_velocity": _pct_to_float(row[9]),
        }

    log.info("OVERALL_METRICS: parsed metrics for %d companies", len(metrics))
    return metrics


def fetch_central_sheet_data(
    company_name: str,
    _prefetched_metrics: Optional[dict] = None,
) -> dict:
    """
    Fetch and parse data for a single company from the central Gantt_Overall spreadsheet.

    The company's tab name must match `company_name` exactly (case-sensitive).
    Metrics (shipping_velocity, execution_speed, planning_depth) are read from
    the Overall_Gantt summary tab.

    Column layout of per-company tabs:
        A: Division
        B: Project
        C: Task
        D: Start Date   (e.g. "Jan 20, 2025")
        E: End Date
        F: Duration     (integer days)
        G: Resource 1
        H: Resource 2
        I: Resource 3
        J: Stage        (e.g. "4 - Done", "2 - Delayed")

    Returns the same shape as fetch_sheet_data() for drop-in compatibility:
        {
            "tasks": [...],
            "shipping_velocity": float | None,
            "execution_speed": float | None,
            "planning_depth": float | None,
            "sheet_today": None,          # not available in central sheet
            "task_count": int,
            "scorecard_history": [],      # not available in central sheet
        }
    """
    log.info("CENTRAL_FETCH: starting fetch for company=%r", company_name)
    service = _get_service()
    sheet_id = _get_central_spreadsheet_id()
    spreadsheet = service.spreadsheets()

    # ── 1. Per-company tab — task rows ────────────────────────────────────────
    # Use FORMATTED_VALUE so dates come back as human-readable strings
    # (e.g. "Jan 20, 2025") which _parse_central_date() handles.
    try:
        task_result = _sheets_get_with_retry(
            spreadsheet.values().get(
                spreadsheetId=sheet_id,
                range=f"'{company_name}'",
                valueRenderOption="FORMATTED_VALUE",
            )
        )
    except HttpError as e:
        if e.resp.status == 404:
            log.error("CENTRAL_FETCH: tab not found for %r: %s", company_name, e)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No sheet tab found for company '{company_name}' in central spreadsheet",
            )
        raise
    except Exception as e:
        log.error("CENTRAL_FETCH: failed to read tab %r: %s", company_name, e)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No sheet tab found for company '{company_name}' in central spreadsheet",
        )

    raw_rows = task_result.get("values", [])
    log.info("CENTRAL_FETCH: raw rows received=%d for company=%r", len(raw_rows), company_name)

    tasks = []
    skipped_empty = 0

    # Detect if first row is a header (Division / Project / Task ...)
    start_idx = 0
    if raw_rows and raw_rows[0] and str(raw_rows[0][0]).strip().lower() in ("division", "div"):
        start_idx = 1
        log.debug("CENTRAL_FETCH: skipping header row")

    for row_idx, row in enumerate(raw_rows[start_idx:], start=start_idx + 1):
        # Pad row to at least 10 columns
        row = list(row) + [""] * (10 - len(row))

        division = str(row[0]).strip() or None
        project = str(row[1]).strip() or None
        task_name = str(row[2]).strip() or None
        start_date = _parse_central_date(row[3])
        end_date = _parse_central_date(row[4])
        duration_days = _safe_int(row[5])
        resource_1 = str(row[6]).strip() or None
        resource_2 = str(row[7]).strip() or None
        resource_3 = str(row[8]).strip() or None

        raw_stage = str(row[9]).strip()
        stage = _strip_stage_prefix(raw_stage) if raw_stage else None

        # Skip completely empty rows (visual separators in the sheet)
        if not any([division, project, task_name]):
            skipped_empty += 1
            continue

        if len(tasks) < 5:
            log.info(
                "CENTRAL_TASK[%d]: div=%r project=%r task=%r start=%r end=%r stage=%r",
                row_idx, division, project, task_name, start_date, end_date, stage,
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
                "completion_date": None,  # not present in central sheet
            }
        )

    log.info(
        "CENTRAL_FETCH: tasks parsed=%d skipped_empty=%d for company=%r",
        len(tasks), skipped_empty, company_name,
    )

    # ── 2. Metrics from Overall_Gantt summary tab ─────────────────────────────
    shipping_velocity: Optional[float] = None
    execution_speed: Optional[float] = None
    planning_depth: Optional[float] = None

    try:
        # Use pre-fetched metrics when available (bulk-pull passes these in to
        # avoid re-reading the Overall_Gantt tab once per company).
        all_metrics = (
            _prefetched_metrics
            if _prefetched_metrics is not None
            else fetch_overall_metrics()
        )
        company_metrics = all_metrics.get(company_name)
        if company_metrics:
            shipping_velocity = company_metrics.get("shipping_velocity")
            execution_speed = company_metrics.get("execution_speed")
            planning_depth = company_metrics.get("planning_depth")
            log.info(
                "CENTRAL_FETCH: metrics SV=%s ES=%s PD=%s for company=%r",
                shipping_velocity, execution_speed, planning_depth, company_name,
            )
        else:
            log.warning(
                "CENTRAL_FETCH: company %r not found in Overall_Gantt metrics", company_name
            )
    except Exception as e:
        log.warning("CENTRAL_FETCH: could not fetch overall metrics: %s", e)

    return {
        "tasks": tasks,
        "shipping_velocity": shipping_velocity,
        "execution_speed": execution_speed,
        "planning_depth": planning_depth,
        "sheet_today": None,       # not available in central sheet
        "task_count": len(tasks),
        "scorecard_history": [],   # not available in central sheet
    }


# ── PER-COMPANY SPREADSHEET MODE (legacy) ────────────────────────────────────

def fetch_sheet_data(sheets_url: str) -> dict:
    """
    Fetch and parse data from the Google Sheet at sheets_url.

    LEGACY MODE: reads per-company sheets with Task_List + Gantt_Scorecard tabs.
    For the new central Gantt_Overall spreadsheet, use fetch_central_sheet_data().

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
    sheet_today: Optional[str] = None

    def _get_cell(rows: list, row_idx: int, col_idx: int) -> Optional[str]:
        try:
            return rows[row_idx][col_idx]
        except IndexError:
            return None

    shipping_velocity = _safe_float(_get_cell(metrics_rows, 0, 1))
    execution_speed = _safe_float(_get_cell(metrics_rows, 2, 1))
    planning_depth = _safe_float(_get_cell(metrics_rows, 3, 1))
    sheet_today = _parse_sheets_date(str(_get_cell(metrics_rows, 5, 1) or ""))

    log.info(
        "SHEETS_FETCH: metrics SV=%s ES=%s PD=%s sheet_today=%r",
        shipping_velocity, execution_speed, planning_depth, sheet_today,
    )

    # ── 3. Gantt_Scorecard A1:BZ2 — full velocity history ────────────────────
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
        date_row = scorecard_rows[0]
        vel_row = scorecard_rows[1]

        max_col = max(len(date_row), len(vel_row))
        for col_idx in range(2, max_col):
            raw_date = date_row[col_idx] if col_idx < len(date_row) else None
            raw_vel = vel_row[col_idx] if col_idx < len(vel_row) else None

            vel = _safe_float(raw_vel)
            if vel is None:
                log.debug("SCORECARD col=%d: no velocity, skipping", col_idx)
                continue

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
        "sheet_today": sheet_today,
        "task_count": len(tasks),
        "scorecard_history": scorecard_history,
    }


def append_task_to_sheet(sheets_url: str, task: dict) -> int:
    """
    Append a new task row to the Task_List tab of the Google Sheet.

    For the central Gantt_Overall spreadsheet, pass the full URL with the
    company tab name as the sheet name (handled by append_task_to_central_sheet).

    Writes columns A–H only (the input columns):
      A = division, B = project, C = task, D = start_date, E = end_date,
      F = duration (blank — sheet formula computes), G = resource_1, H = resource_2

    Columns J (stage) and K (completion_date) are formula-computed — left blank.

    Args:
        sheets_url: Google Sheets URL or spreadsheet ID
        task: dict with keys: task, project, division, resource,
              suggested_start_date (date|str|None), suggested_end_date (date|str|None)

    Returns:
        1-based row number of the appended row.

    Raises:
        HTTPException 503 if credentials not configured.
        googleapiclient.errors.HttpError on Sheets API failure.
    """
    creds_file = settings.GOOGLE_CREDENTIALS_FILE.strip()
    if not os.path.exists(creds_file):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google credentials not configured",
        )
    creds = service_account.Credentials.from_service_account_file(
        creds_file, scopes=SCOPES
    )
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    sheet_id = _extract_sheet_id(sheets_url)

    def _fmt_date(d) -> str:
        """Format date/str to MM/DD/YYYY for Sheets, or empty string."""
        if d is None:
            return ""
        if hasattr(d, "strftime"):
            return d.strftime("%m/%d/%Y")
        return str(d)

    row_values = [
        task.get("division") or "",
        task.get("project") or "",
        task.get("task") or "",
        _fmt_date(task.get("suggested_start_date")),
        _fmt_date(task.get("suggested_end_date")),
        "",  # F: duration — formula-computed
        task.get("resource") or "",
        "",  # H: resource_2 — leave blank
    ]

    body = {
        "values": [row_values],
    }

    result = (
        service.spreadsheets()
        .values()
        .append(
            spreadsheetId=sheet_id,
            range="Task_List!A:H",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body,
        )
        .execute()
    )

    updated_range = result.get("updates", {}).get("updatedRange", "")
    row_number = 0
    try:
        import re as _re
        match = _re.search(r"!A(\d+)", updated_range)
        if match:
            row_number = int(match.group(1))
    except Exception:
        pass

    log.info(
        "SHEETS_APPEND: task=%r written to row=%d (range=%s)",
        task.get("task"), row_number, updated_range,
    )
    return row_number


def append_task_to_central_sheet(company_name: str, task: dict) -> int:
    """
    Append a new task row to a company's tab in the central Gantt_Overall spreadsheet.

    Writes columns A–J:
      A = division, B = project, C = task, D = start_date, E = end_date,
      F = duration (blank), G = resource_1, H = resource_2, I = resource_3,
      J = stage (defaults to "1 - Yet to Start")

    Args:
        company_name: Exact tab name in the central spreadsheet (e.g. "GaadiMech")
        task: dict with keys: task, project, division, resource,
              suggested_start_date, suggested_end_date

    Returns:
        1-based row number of the appended row.
    """
    creds_file = settings.GOOGLE_CREDENTIALS_FILE.strip()
    if not os.path.exists(creds_file):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google credentials not configured",
        )
    creds = service_account.Credentials.from_service_account_file(
        creds_file, scopes=SCOPES
    )
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    sheet_id = _get_central_spreadsheet_id()

    def _fmt_date(d) -> str:
        if d is None:
            return ""
        if hasattr(d, "strftime"):
            return d.strftime("%b %-d, %Y")  # e.g. "Jan 5, 2026"
        return str(d)

    row_values = [
        task.get("division") or "",
        task.get("project") or "",
        task.get("task") or "",
        _fmt_date(task.get("suggested_start_date")),
        _fmt_date(task.get("suggested_end_date")),
        "",                        # F: duration — leave blank
        task.get("resource") or "",
        "",                        # H: resource_2
        "",                        # I: resource_3
        "1 - Yet to Start",        # J: stage default
    ]

    body = {"values": [row_values]}
    range_name = f"'{company_name}'!A:J"

    result = (
        service.spreadsheets()
        .values()
        .append(
            spreadsheetId=sheet_id,
            range=range_name,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body,
        )
        .execute()
    )

    updated_range = result.get("updates", {}).get("updatedRange", "")
    row_number = 0
    try:
        import re as _re
        match = _re.search(r"!A(\d+)", updated_range)
        if match:
            row_number = int(match.group(1))
    except Exception:
        pass

    log.info(
        "CENTRAL_APPEND: task=%r written to row=%d in tab=%r (range=%s)",
        task.get("task"), row_number, company_name, updated_range,
    )
    return row_number
