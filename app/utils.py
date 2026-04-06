"""
Shared utility helpers used across routers and services.
"""
from datetime import date, datetime
import logging

logger = logging.getLogger(__name__)


def parse_due_date(date_str) -> date | None:
    """
    Parse a date string (YYYY-MM-DD) into a Python date object.
    Also accepts an already-resolved ``date`` / ``datetime`` instance.
    Returns None if *date_str* is None or the value cannot be parsed.
    """
    if not date_str:
        return None
    try:
        if isinstance(date_str, datetime):
            return date_str.date()
        if isinstance(date_str, date):
            return date_str
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError) as exc:
        logger.warning("Failed to parse due_date %r: %s", date_str, exc)
        return None
