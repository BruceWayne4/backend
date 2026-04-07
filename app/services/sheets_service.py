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
        "%d %b, %Y",   # "8 Jan, 2026" / "26 Jan, 2026"  (day MonAbbr, YYYY)
        "%d %B, %Y",   # "8 January, 2026"               (day FullMonth, YYYY)
        "%m/%d/%Y",    # "01/20/2025"
        "%m/%d/%y",    # "01/20/25"
        "%Y-%m-%d",    # "2025-01-20"
        "%d/%m/%Y",    # "20/01/2025"
    ]
    for candidate_val in (val_norm, val) if val_norm != val else (val,):
        for fmt in formats:
            try:
                result = _dt.strptime(candidate_val, fmt).date().isoformat()
                log.debug("CENTRAL_DATE: %r matched fmt=%r → %r", val, fmt, result)
                return result
            except ValueError:
                continue

    # Partial date: "Mar 9" / "Apr 15" / "Jun 14" — month + day, no year.
    # Also handles day-first partials: "29 September" / "25 Sep" / "20 October".
    # Infer the year: use the current year; if the resulting date is more than
    # 6 months in the past, bump to next year (avoids stale dates for future tasks).
    partial_formats = [
        "%b %d",   # "Mar 9", "Apr 15"
        "%B %d",   # "March 9", "April 15"
        "%b %-d",  # same but explicit no-padding (Linux only; harmless fallback)
        "%d %b",   # "25 Sep", "9 Mar"  (day-first abbreviated month, no year)
        "%d %B",   # "29 September", "20 October"  (day-first full month, no year)
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

    # ── Column layout detection ───────────────────────────────────────────────
    # Standard layout: A=Division, B=Project, C=Task, D=Start Date, E=End Date,
    #                  F=Duration, G=Resource1, H=Resource2, I=Resource3, J=Stage
    #
    # Some company tabs have an extra leading column (e.g. "notion_page_id") that
    # shifts every subsequent column right by 1.  We detect the header row and
    # resolve each column index by name so the parser is layout-agnostic.
    #
    # Default column indices (0-based) — used when no header row is present:
    COL_DIVISION    = 0
    COL_PROJECT     = 1
    COL_TASK        = 2
    COL_START       = 3
    COL_END         = 4
    COL_DURATION    = 5
    COL_RES1        = 6
    COL_RES2        = 7
    COL_RES3        = 8
    COL_STAGE       = 9
    COL_COMPLETION  = -1   # sentinel: absent unless found in header

    # Keywords used to identify each column by header name (case-insensitive).
    # Each list is ordered most-specific → least-specific.
    _COL_KEYWORDS: dict[str, list[str]] = {
        "division":         ["division", "div"],
        "project":          ["project", "proj"],
        "task":             ["task", "tasks", "item"],
        "start_date":       ["start date", "start_date", "start"],
        "end_date":         ["end date", "end_date", "due date", "due", "end"],
        "duration":         ["duration", "days", "dur"],
        "resource_1":       ["resource 1", "resource1"],
        "resource_2":       ["resource 2", "resource2"],
        "resource_3":       ["resource 3", "resource3"],
        "stage":            ["stage", "status", "state"],
        "completion_date":  ["completion date", "completion_date", "completed", "completion"],
    }

    # Sentinel value returned by _find_col when a column is absent from the header.
    _COL_ABSENT = -1

    def _find_col(
        header_row: list,
        keywords: list[str],
        default: int,
        optional: bool = False,
    ) -> int:
        """
        Find the column index whose header matches any keyword (case-insensitive).

        Scans the full header row.  Returns:
          - The matched index (with a COL_SHIFT warning if it differs from `default`)
          - `default` if not found and optional=False  (with a COL_MISSING warning)
          - `_COL_ABSENT` (-1) if not found and optional=True  (no warning — absence is expected)
        """
        header_lower = [str(c).strip().lower() for c in header_row]
        for idx, cell in enumerate(header_lower):
            if cell in keywords:
                if idx != default:
                    log.warning(
                        "COL_SHIFT company=%r: column %r found at index %d (expected %d) — remapping",
                        company_name, keywords[0], idx, default,
                    )
                else:
                    log.debug(
                        "COL_OK company=%r: column %r at expected index %d",
                        company_name, keywords[0], idx,
                    )
                return idx
        # Column not found in header
        if optional:
            log.debug(
                "COL_ABSENT company=%r: optional column %r not in header — skipping",
                company_name, keywords[0],
            )
            return _COL_ABSENT
        log.warning(
            "COL_MISSING company=%r: required column %r not found in header %r — using default index %d",
            company_name, keywords[0], header_lower[:14], default,
        )
        return default

    # Detect if first row is a header and resolve column indices from it.
    # A row is treated as a header when ≥2 of its first 8 cells match a known keyword.
    start_idx = 0
    first_row_cells = [str(c).strip().lower() for c in (raw_rows[0] if raw_rows else [])]
    _all_keywords = {kw for kws in _COL_KEYWORDS.values() for kw in kws}
    _header_hits = sum(1 for c in first_row_cells[:8] if c in _all_keywords)
    if _header_hits >= 2:
        start_idx = 1
        header_row = list(raw_rows[0]) + [""] * (14 - len(raw_rows[0]))
        log.info(
            "CENTRAL_FETCH: header row detected (%d keyword hits) for company=%r — header=%r",
            _header_hits, company_name, [str(c).strip() for c in header_row[:14]],
        )
        COL_DIVISION   = _find_col(header_row, _COL_KEYWORDS["division"],        0)
        COL_PROJECT    = _find_col(header_row, _COL_KEYWORDS["project"],         1)
        COL_TASK       = _find_col(header_row, _COL_KEYWORDS["task"],            2)
        COL_START      = _find_col(header_row, _COL_KEYWORDS["start_date"],      3)
        COL_END        = _find_col(header_row, _COL_KEYWORDS["end_date"],        4)
        COL_DURATION   = _find_col(header_row, _COL_KEYWORDS["duration"],        5)
        COL_RES1       = _find_col(header_row, _COL_KEYWORDS["resource_1"],      6)
        COL_RES2       = _find_col(header_row, _COL_KEYWORDS["resource_2"],      7)
        COL_RES3       = _find_col(header_row, _COL_KEYWORDS["resource_3"],      8)
        COL_STAGE      = _find_col(header_row, _COL_KEYWORDS["stage"],           9)
        COL_COMPLETION = _find_col(header_row, _COL_KEYWORDS["completion_date"], -1, optional=True)
        log.info(
            "COL_MAP company=%r: division=%d project=%d task=%d start=%d end=%d "
            "duration=%d res1=%d res2=%d res3=%d stage=%d completion=%d",
            company_name,
            COL_DIVISION, COL_PROJECT, COL_TASK, COL_START, COL_END,
            COL_DURATION, COL_RES1, COL_RES2, COL_RES3, COL_STAGE, COL_COMPLETION,
        )
    else:
        log.info(
            "CENTRAL_FETCH: no header row detected for company=%r (hits=%d) — using default column indices",
            company_name, _header_hits,
        )

    date_parse_failures: list[tuple[int, str, str, str]] = []  # (row_idx, task_name, raw_start, raw_end)

    # Pre-compute the minimum row length needed (exclude _COL_ABSENT sentinels)
    _required_cols = [
        c for c in (COL_DIVISION, COL_PROJECT, COL_TASK, COL_START, COL_END,
                    COL_DURATION, COL_RES1, COL_RES2, COL_RES3, COL_STAGE,
                    COL_COMPLETION)
        if c != _COL_ABSENT
    ]
    _min_row_len = (max(_required_cols) + 1) if _required_cols else 10

    for row_idx, row in enumerate(raw_rows[start_idx:], start=start_idx + 1):
        # Pad row to cover all required column indices
        row = list(row) + [""] * (_min_row_len - len(row))

        division  = str(row[COL_DIVISION]).strip() or None
        project   = str(row[COL_PROJECT]).strip()  or None
        task_name = str(row[COL_TASK]).strip()     or None

        # Read start/end dates — use empty string when column is absent (sentinel -1)
        raw_start_val = row[COL_START]   if COL_START   != _COL_ABSENT else ""
        raw_end_val   = row[COL_END]     if COL_END     != _COL_ABSENT else ""
        start_date    = _parse_central_date(raw_start_val)
        end_date      = _parse_central_date(raw_end_val)
        duration_days = _safe_int(row[COL_DURATION]) if COL_DURATION != _COL_ABSENT else None
        resource_1    = (str(row[COL_RES1]).strip() or None) if COL_RES1 != _COL_ABSENT else None
        resource_2    = (str(row[COL_RES2]).strip() or None) if COL_RES2 != _COL_ABSENT else None
        resource_3    = (str(row[COL_RES3]).strip() or None) if COL_RES3 != _COL_ABSENT else None

        raw_stage = (str(row[COL_STAGE]).strip() if COL_STAGE != _COL_ABSENT else "")
        stage = _strip_stage_prefix(raw_stage) if raw_stage else None

        # Read completion_date when the column exists in this sheet's layout
        raw_completion = (
            row[COL_COMPLETION] if COL_COMPLETION != _COL_ABSENT and COL_COMPLETION < len(row)
            else ""
        )
        completion_date = _parse_central_date(raw_completion) if raw_completion else None

        # Skip completely empty rows (visual separators in the sheet)
        if not any([division, project, task_name]):
            skipped_empty += 1
            continue

        # Track rows where date parsing failed — logged as a summary after the loop.
        # Only flag when a raw value was present but couldn't be parsed; absent columns
        # (sentinel -1) produce empty raw values and are intentionally excluded.
        if (raw_start_val or raw_end_val) and (start_date is None or end_date is None):
            date_parse_failures.append((
                row_idx,
                task_name or "",
                str(raw_start_val) if raw_start_val else "",
                str(raw_end_val) if raw_end_val else "",
            ))

        if len(tasks) < 5:
            log.info(
                "CENTRAL_TASK[%d]: div=%r project=%r task=%r start=%r end=%r stage=%r completion=%r",
                row_idx, division, project, task_name, start_date, end_date, stage, completion_date,
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
        "CENTRAL_FETCH: tasks parsed=%d skipped_empty=%d for company=%r",
        len(tasks), skipped_empty, company_name,
    )

    # Log a single consolidated warning listing every row where date parsing broke.
    # This makes it trivial to grep logs for "PARSE_FAIL" and see which companies
    # have bad date formats in their sheet tabs.
    if date_parse_failures:
        log.warning(
            "PARSE_FAIL company=%r — %d row(s) with unparseable dates:",
            company_name, len(date_parse_failures),
        )
        for row_idx, task_nm, raw_s, raw_e in date_parse_failures:
            log.warning(
                "  PARSE_FAIL company=%r row=%d task=%r raw_start=%r raw_end=%r",
                company_name, row_idx, task_nm, raw_s, raw_e,
            )
    else:
        log.info("PARSE_FAIL company=%r — 0 date parse failures (all dates OK)", company_name)

    # ── 2. Metrics from Overall_Gantt summary tab ─────────────────────────────
    shipping_velocity: Optional[float] = None
    execution_speed: Optional[float] = None
    planning_depth: Optional[float] = None
    sheet_task_count: Optional[int] = None  # total count from Overall_Gantt — validation anchor

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
