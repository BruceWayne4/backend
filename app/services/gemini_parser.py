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
Extract the following fields from the meeting notes in JSON format:

{
  "summary": ["3-5 bullet points, each under 20 words"],
  "decisions": ["concrete decisions made, written in past tense"],
  "risks": [{"description": "risk text", "severity": "low|medium|high"}],
  "gap_assessment": ["what is broken or missing"],
  "alignment_points": ["what fund manager and founder explicitly agreed on"],
  "gantt_status": "on-track|delayed|off-track|not-discussed",
  "gantt_notes": "one sentence explanation of gantt status",
  "commitments": [
    {
      "person": "Founder 1|Founder 2|Aviral|Shubham|Anshu|Ankit",
      "action": "what was committed to",
      "due_date": "YYYY-MM-DD or null",
      "source": "founder-initiated|aviral-pushed"
    }
  ],
  "vc_recommendations": ["advice given BY the fund manager (Aviral) TO the founder"],
  "initiatives": [{"name": "initiative name", "category": "category", "status_hint": "status hint"}],
  "financials_mentioned": [{"label": "metric name", "value": "value with units"}],
  "sentiment": -2 to +2 integer (-2=very concerning, -1=concerning, 0=neutral, +1=positive, +2=very strong),
  "sentiment_reason": "one sentence explaining the sentiment score"
}

Rules:
- Be conservative. Only extract what is clearly stated. Do not infer.
- For commitments, extract from "Key Action Items" and "Feedback / Ideas" sections
- Tag source as "aviral-pushed" if the action came from Aviral's recommendations/feedback
- Tag source as "founder-initiated" if founders committed on their own in action items
- vc_recommendations: only advice FROM Aviral TO founders, not reverse
- Extract due dates when explicitly mentioned or inferable from context (e.g., "by next week" = 7 days from meeting date)
- gap_assessment: extract from "Gap Assessment" and "Challenges" sections specifically
- alignment_points: extract from "Alignment Points" section specifically
- For person names, use the exact names as they appear in the text
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
