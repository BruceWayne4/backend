"""
Granola API service for fetching meeting notes.
"""
import httpx
from typing import List, Dict, Optional
from app.config import settings
import asyncio
import logging
from datetime import datetime
import re

logger = logging.getLogger(__name__)


# Company name aliases for matching Granola note titles
# Key = canonical company name (as in DB), Values = possible title fragments in Granola
COMPANY_ALIASES: dict[str, list[str]] = {
    "Jaagruk Bharat": ["jaagruk bharat", "jb"],
    "BubbleMe": ["bubbleme", "bubble me"],
    "Mindcase": ["mindcase", "mind case"],
    "Mithila Foods": ["mithila foods", "mithila"],
    "Handy Panda": ["handy panda", "handypanda"],
    "MultiBagg": ["multibagg", "multi bagg"],
    "CareDale": ["caredale", "care dale"],
    "KargoFit": ["kargofit", "kargo fit"],
    "Kinetic Age": ["kinetic age", "kineticage"],
    "Mora Maa": ["mora maa", "moromaa", "moro maa"],
    "Chop Finance": ["chop finance", "chop"],
    "Island Beauty": ["island beauty", "islandbeauty"],
}


class GranolaService:
    def __init__(self):
        self.api_key = settings.GRANOLA_API_KEY
        self.base_url = settings.GRANOLA_API_BASE_URL
        self.client = httpx.AsyncClient(
            headers={
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json'
            },
            timeout=30.0
        )
    
    async def list_all_notes(
        self,
        updated_after: Optional[str] = None,
        created_after: Optional[str] = None,
    ) -> List[Dict]:
        """
        Fetch notes from Granola API in a single paginated scan.

        The Granola API has no server-side title/company search filter —
        the only way to find notes for a specific company is to page through
        all notes and filter client-side by title.

        Uses page_size=30 (API maximum) to minimise HTTP requests.
        Rate limit: 5 req/s sustained, 25 req burst per 5s window.

        Args:
            updated_after:  ISO-8601 date string (e.g. "2026-01-15").
                            Only return notes updated on or after this date.
                            Use this for incremental syncs to avoid fetching
                            the full history on every run.
            created_after:  ISO-8601 date string. Only return notes created
                            on or after this date.

        Returns:
            List of note summary dicts (no transcripts) matching the filters.
        """
        all_notes: List[Dict] = []
        cursor: Optional[str] = None
        page = 1

        try:
            while True:
                params: Dict = {'page_size': 30}
                if cursor:
                    params['cursor'] = cursor
                if updated_after:
                    params['updated_after'] = updated_after
                if created_after:
                    params['created_after'] = created_after

                response = await self.client.get(
                    f'{self.base_url}/v1/notes',
                    params=params,
                )
                response.raise_for_status()
                data = response.json()

                notes = data.get('notes', [])
                all_notes.extend(notes)

                logger.debug(
                    f"list_all_notes page {page}: {len(notes)} notes "
                    f"(running total: {len(all_notes)})"
                )

                if not data.get('hasMore', False):
                    break

                cursor = data.get('cursor')
                if not cursor:
                    break

                page += 1
                # 200 ms between pages ≈ 5 req/s — within sustained rate limit
                await asyncio.sleep(0.2)

            logger.info(
                f"list_all_notes: fetched {len(all_notes)} notes "
                f"across {page} page(s)"
            )
            return all_notes

        except httpx.HTTPError as e:
            logger.error(f"HTTP error during list_all_notes: {e}")
            raise
        except Exception as e:
            logger.error(f"Error during list_all_notes: {e}")
            raise

    def filter_notes_for_company(
        self,
        all_notes: List[Dict],
        company_name: str,
    ) -> List[Dict]:
        """
        Filter a pre-fetched note list by company name (client-side).

        Args:
            all_notes:    Full list returned by list_all_notes().
            company_name: Canonical company name to match against note titles.

        Returns:
            Subset of notes whose title matches the company.
        """
        matching = [
            note for note in all_notes
            if self._matches_company(note.get('title', ''), company_name)
        ]
        logger.info(
            f"filter_notes_for_company({company_name!r}): "
            f"{len(matching)} match(es) from {len(all_notes)} total"
        )
        return matching

    async def list_notes_for_company(
        self,
        company_name: str,
        updated_after: Optional[str] = None,
    ) -> List[Dict]:
        """
        Convenience wrapper: fetch notes (with optional date filter) then
        filter by company name.

        For individual company syncs, pass the company's own
        MAX(granola_updated_at) as updated_after to fetch only new/changed
        notes since the last sync.

        NOTE: For bulk syncs (multiple companies), call list_all_notes() once
        and reuse the result with filter_notes_for_company() to avoid
        redundant full-scan API calls per company.

        Args:
            company_name:  Name of the company to search for.
            updated_after: ISO-8601 date string (YYYY-MM-DD). Only fetch notes
                           updated on or after this date.

        Returns:
            List of note dicts matching the company.
        """
        all_notes = await self.list_all_notes(updated_after=updated_after)
        return self.filter_notes_for_company(all_notes, company_name)
    
    async def get_note_details(self, note_id: str) -> Dict:
        """
        Get full note details including transcript.
        
        Args:
            note_id: Granola note ID
            
        Returns:
            Full note dictionary with transcript
        """
        try:
            url = f'{self.base_url}/v1/notes/{note_id}?include=transcript'
            response = await self.client.get(url)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"HTTP error fetching note {note_id}: {e}")
            raise
        except Exception as e:
            logger.error(f"Error fetching note {note_id}: {e}")
            raise
    
    def transform_to_raw_text(self, note: Dict) -> str:
        """
        Transform Granola note to text format for Gemini parsing.
        Mimics DOCX structure for compatibility with existing prompts.
        
        Args:
            note: Full Granola note dictionary
            
        Returns:
            Formatted text string ready for Gemini parsing
        """
        parts = []
        
        # Extract meeting date
        scheduled_time = note.get('calendar_event', {}).get('scheduled_start_time')
        if scheduled_time:
            try:
                dt = datetime.fromisoformat(scheduled_time.replace('Z', '+00:00'))
                parts.append(f"{dt.strftime('%d %b %y')}\n\n")
            except ValueError:
                logger.warning(f"Could not parse date: {scheduled_time}")
        
        # Add title
        parts.append(f"Meeting: {note.get('title', 'Untitled')}\n")
        
        # Add attendees
        attendees = note.get('attendees', [])
        if attendees:
            attendee_names = [a.get('name', 'Unknown') for a in attendees]
            parts.append(f"Attendees: {', '.join(attendee_names)}\n\n")
        
        # Add transcript as "General Discussion"
        parts.append("General Discussion\n")
        transcript = note.get('transcript', [])
        
        for utterance in transcript:
            speaker_source = utterance.get('speaker', {}).get('source', 'unknown')
            text = utterance.get('text', '')
            parts.append(f"[{speaker_source}] {text}\n")
        
        # Add Granola summary as "Key Action Items"
        if note.get('summary_markdown'):
            parts.append("\n\nKey Action Items\n")
            parts.append(note['summary_markdown'])
        
        return "".join(parts)
    
    def _matches_company(self, title: str, company_name: str) -> bool:
        """
        Check if Granola note title matches company name.
        Handles patterns like "Company <> ajvc", "Company > ajvc", etc.
        Also handles company aliases (e.g. "JB" for "Jaagruk Bharat").
        
        Args:
            title: Granola note title
            company_name: Company name to match
            
        Returns:
            True if title matches company name
        """
        title_lower = title.lower()
        
        # Build list of all name variants to check (canonical + aliases)
        canonical_lower = company_name.lower()
        variants = [canonical_lower]
        
        # Add aliases if defined for this company
        if company_name in COMPANY_ALIASES:
            variants.extend(COMPANY_ALIASES[company_name])
        
        # Deduplicate while preserving order
        variants = list(dict.fromkeys(v.lower() for v in variants))
        
        for variant in variants:
            # Direct substring match
            if variant in title_lower:
                return True
            
            # Pattern match: "Variant <> ajvc", "Variant > ajvc", etc.
            patterns = [
                rf'^{re.escape(variant)}\s*<>',
                rf'^{re.escape(variant)}\s*>',
                rf'^{re.escape(variant)}\s*-',
                rf'^{re.escape(variant)}\s*\|',
            ]
            for pattern in patterns:
                if re.search(pattern, title_lower):
                    return True
        
        return False
    
    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()


# Singleton instance
granola_service = GranolaService()
