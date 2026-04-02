"""
Claude API service — thin wrapper around the Anthropic SDK.
Uses claude-3-5-sonnet-20241022 as resolved in decision D4.
"""
import json
import logging
from typing import Any, Dict

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.CLAUDE_API_KEY)
    return _client


SYSTEM_PROMPT = """\
You are a go-to-market strategist advising an early-stage VC fund manager.
You will be given:
1. A GTM playbook — the fund's recommended GTM framework
2. Meeting notes from the past 6 weeks with a specific portfolio company

Produce a structured GTM action plan for THIS company grounded ONLY in what is stated in the meeting notes and the playbook.
Return a valid JSON object that exactly matches the schema provided in the user message.
Do NOT include any text outside the JSON object — no markdown fences, no explanation, no preamble.
"""

# JSON schema description embedded in the user turn
RESPONSE_SCHEMA = """{
  "gtm_stage": "<one of: Pre-PMF | Early GTM | Growth | Scale>",
  "sentiment_trend": "<one of: Declining | Flat | Improving | Strong>",
  "focus_this_week": "<single most important action this week, one sentence>",
  "target_customer": {
    "segment": "<primary segment>",
    "icp": "<ideal customer profile description>",
    "negative_icp": "<who NOT to sell to>"
  },
  "current_gtm_approach": {
    "whats_working": ["<string>"],
    "not_working": ["<string>"],
    "primary_channel": "<string>"
  },
  "recommended_actions": [
    {
      "action": "<specific action>",
      "priority": "<HIGH | MEDIUM | LOW>",
      "owner": "<Founder | VC | Both>",
      "timeline": "<This week | 30 days | 60 days | 90 days>",
      "rationale": "<one sentence grounded in meeting notes>"
    }
  ],
  "open_loops": [
    {
      "topic": "<what is unresolved>",
      "raised_date": "<YYYY-MM-DD or null>",
      "status": "<open | following-up | stale>"
    }
  ],
  "bottlenecks": [
    {
      "description": "<bottleneck description>",
      "severity": "<high | medium | low>"
    }
  ]
}"""


def _build_user_message(
    company_name: str,
    playbook_content: str,
    meeting_summaries: list[dict],
) -> str:
    meetings_block = ""
    for m in meeting_summaries:
        meetings_block += f"\n### Meeting — {m.get('date', 'Unknown date')}\n"
        if m.get("summary"):
            summary = m["summary"]
            if isinstance(summary, list):
                for bullet in summary:
                    meetings_block += f"- {bullet}\n"
            else:
                meetings_block += f"{summary}\n"
        if m.get("commitments"):
            meetings_block += "\n**Commitments:**\n"
            for c in (m["commitments"] if isinstance(m["commitments"], list) else []):
                meetings_block += f"- [{c.get('person', '?')}] {c.get('action', '')} (due: {c.get('due_date', 'TBD')})\n"
        if m.get("risks"):
            meetings_block += "\n**Risks:**\n"
            for r in (m["risks"] if isinstance(m["risks"], list) else []):
                desc = r.get("description", r) if isinstance(r, dict) else r
                meetings_block += f"- {desc}\n"
        if m.get("financials"):
            meetings_block += "\n**Financials:**\n"
            for f_item in (m["financials"] if isinstance(m["financials"], list) else []):
                meetings_block += f"- {f_item.get('label', '')}: {f_item.get('value', '')}\n"
        if m.get("sentiment") is not None:
            meetings_block += f"\n**Sentiment score:** {m['sentiment']}"
            if m.get("sentiment_reason"):
                meetings_block += f" — {m['sentiment_reason']}"
            meetings_block += "\n"

    return f"""# GTM Playbook
{playbook_content}

# Meeting Notes — {company_name} (last 6 weeks)
{meetings_block}

# Required JSON schema
Return ONLY a JSON object that matches this schema exactly:
{RESPONSE_SCHEMA}
"""


async def generate_gtm_plan(
    company_name: str,
    playbook_content: str,
    meeting_summaries: list[dict],
) -> Dict[str, Any]:
    """
    Call Claude and return the parsed GTM plan dict.
    Raises anthropic.APIError on failure.
    """
    client = _get_client()
    user_message = _build_user_message(company_name, playbook_content, meeting_summaries)

    logger.info(
        f"Calling Claude ({settings.ANTHROPIC_MODEL}) for GTM plan — company={company_name}, "
        f"meetings={len(meeting_summaries)}"
    )

    message = await client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    raw_text = message.content[0].text.strip()

    # Strip accidental markdown fences
    if raw_text.startswith("```"):
        lines = raw_text.splitlines()
        raw_text = "\n".join(
            line for line in lines
            if not line.strip().startswith("```")
        ).strip()

    parsed = json.loads(raw_text)
    return parsed, raw_text
