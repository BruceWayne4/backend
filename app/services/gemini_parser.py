"""
Gemini AI parser service for extracting structured data from meeting notes.
"""
from google import genai
from google.genai import types
from app.config import settings
from typing import Dict, Any
import json
import logging

logger = logging.getLogger(__name__)

# Initialize Gemini client
client = genai.Client(api_key=settings.GEMINI_API_KEY)

SYSTEM_PROMPT = """
You are an AI assistant that extracts structured data from VC meeting notes.
Extract the following fields from the meeting notes and return ONLY a valid JSON object with no additional text, markdown, or code fences.

The JSON object must have exactly these fields:

{
  "summary": [],
  "decisions": [],
  "risks": [],
  "gap_assessment": [],
  "alignment_points": [],
  "gantt_status": "",
  "gantt_notes": "",
  "gantt_task_mentions": [],
  "suggested_gantt_tasks": [],
  "commitments": [],
  "vc_recommendations": [],
  "initiatives": [],
  "financials_mentioned": [],
  "sentiment": 0,
  "sentiment_reason": ""
}

Field definitions:

- summary: Array of 3-5 strings. Each bullet point must be under 20 words.
- decisions: Array of strings. Concrete decisions made, written in past tense.
- risks: Array of objects with shape {"description": "<risk text>", "severity": "<low|medium|high>"}.
- gap_assessment: Array of strings. What is broken or missing.
- alignment_points: Array of strings. What the fund manager and founder explicitly agreed on.
- gantt_status: One of exactly: "on-track", "delayed", "off-track", "not-discussed".
- gantt_notes: String. One sentence explaining the gantt status.
- gantt_task_mentions: Array of objects with shape {"task_or_project": "<name>", "status_hint": "<delayed|at-risk|on-track|completed>", "note": "<one sentence>"}.
- suggested_gantt_tasks: Array of objects with shape {"task": "<name>", "project": "<name or null>", "division": "<Tech|Marketing|Ops|Product|HR|Finance or null>", "resource": "<person or null>", "suggested_start_date": "<YYYY-MM-DD or null>", "suggested_end_date": "<YYYY-MM-DD or null>", "note": "<one sentence>"}.
- commitments: Array of objects with shape {"person": "<name>", "action": "<what was committed>", "due_date": "<YYYY-MM-DD or null>", "source": "<founder-initiated|aviral-pushed>"}.
- vc_recommendations: Array of strings. Advice given BY the fund manager (Aviral) TO the founder only.
- initiatives: Array of objects with shape {"name": "<initiative name>", "category": "<category>", "status_hint": "<status>"}.
- financials_mentioned: Array of objects with shape {"label": "<metric name>", "value": "<value with units>"}.
- sentiment: Integer from -2 to 2. Use -2 for very concerning, -1 for concerning, 0 for neutral, 1 for positive, 2 for very strong.
- sentiment_reason: String. One sentence explaining the sentiment score.

Extraction rules:
- Be conservative. Only extract what is clearly stated. Do not infer.
- For commitments, extract from "Key Action Items" and "Feedback / Ideas" sections.
- Tag source as "aviral-pushed" if the action came from Aviral's recommendations/feedback.
- Tag source as "founder-initiated" if founders committed on their own in action items.
- vc_recommendations: only advice FROM Aviral TO founders, not the reverse.
- Extract due dates when explicitly mentioned or inferable from context (e.g., "by next week" = 7 days from meeting date).
- gap_assessment: extract from "Gap Assessment" and "Challenges" sections specifically.
- alignment_points: extract from "Alignment Points" section specifically.
- For person names, use the exact names as they appear in the text.
- gantt_task_mentions: extract ONLY tasks or projects explicitly named in the meeting notes as delayed, at-risk, on-track, or completed. Use the exact name as spoken. If no specific tasks are named, return [].
- gantt_task_mentions status_hint must be one of: delayed, at-risk, on-track, completed.
- suggested_gantt_tasks: extract ONLY tasks explicitly discussed as NEW work to be planned — not existing tasks being updated or reviewed. These are tasks the team committed to starting or planning that are not yet underway.
- Do NOT add a task to suggested_gantt_tasks if it is clearly already in progress, done, or being tracked.
- suggested_start_date defaults to the meeting date if not explicitly mentioned.
- Return [] for suggested_gantt_tasks if no genuinely new tasks were discussed.
- All string values in JSON must use double quotes. All objects in arrays must be properly closed with }.
"""


async def parse_meeting_with_gemini(raw_notes: str) -> Dict[str, Any]:
    """
    Parse meeting notes using Gemini API.
    
    Args:
        raw_notes: Raw text from meeting DOCX
        
    Returns:
        Dict with extracted structured data
    """
    result_text = ""
    try:
        prompt = f"{SYSTEM_PROMPT}\n\nMeeting Notes:\n{raw_notes}"
        
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,  # Low temperature for consistency
                response_mime_type="application/json",  # Force valid JSON output via constrained decoding
            )
        )
        
        # Parse JSON from response
        result_text = response.text
        
        # Remove markdown code blocks if present
        result_text = result_text.replace('```json', '').replace('```', '').strip()
        
        # Parse JSON
        parsed = json.loads(result_text)
        
        logger.info(f"Successfully parsed meeting with Gemini. Extracted {len(parsed.get('commitments', []))} commitments.")
        
        return parsed
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Gemini response as JSON: {e}")
        if result_text:
            logger.error(f"Response text: {result_text}")
        # Return minimal structure
        return {
            "summary": ["Failed to parse meeting notes"],
            "decisions": [],
            "risks": [],
            "gap_assessment": [],
            "alignment_points": [],
            "gantt_status": "not-discussed",
            "gantt_notes": "",
            "gantt_task_mentions": [],
            "suggested_gantt_tasks": [],
            "commitments": [],
            "vc_recommendations": [],
            "initiatives": [],
            "financials_mentioned": [],
            "sentiment": 0,
            "sentiment_reason": "Parsing failed"
        }
    except Exception as e:
        logger.error(f"Error calling Gemini API: {e}")
        raise
