"""Unit tests for Google Calendar search functionality."""

import pytest

from mcp_handley_lab.google_calendar.tool import (
    CompactCalendarEvent,
    _build_compact_event,
    _client_side_filter,
    _normalize_text,
    _term_threshold,
)


class TestClientSideFilter:
    """Test client-side filtering functionality."""

    def test_basic_search(self):
        """Test basic search functionality."""
        events = [
            {"summary": "Team Meeting", "description": "Weekly sync"},
            {"summary": "Client Call", "description": "Important discussion"},
            {"summary": "Code Review", "description": "Weekly team sync"},
        ]

        # Test basic search (case-insensitive by default)
        filtered = _client_side_filter(events, search_text="team")
        assert len(filtered) == 2  # "Team Meeting" and "Code Review" (contains "team")
        assert any("Team Meeting" in event["summary"] for event in filtered)
        assert any("Code Review" in event["summary"] for event in filtered)

        # Test case-sensitive search
        filtered = _client_side_filter(events, search_text="Team", case_sensitive=True)
        assert len(filtered) == 1  # Only "Team Meeting"
        assert filtered[0]["summary"] == "Team Meeting"

    def test_and_or_logic(self):
        """Test AND vs OR search logic."""
        events = [
            {"summary": "Team Meeting", "description": "Weekly sync"},
            {"summary": "Client Call", "description": "Important discussion"},
            {"summary": "Code Review", "description": "Weekly team sync"},
        ]

        # Test AND logic (default) - both terms must be present
        filtered = _client_side_filter(events, search_text="weekly sync")
        assert len(filtered) == 2  # Both events have "weekly" and "sync"

        # Test OR logic - any term can be present
        filtered = _client_side_filter(
            events, search_text="weekly important", match_all_terms=False
        )
        assert len(filtered) == 3  # All events match either "weekly" or "important"

    def test_field_specific_search(self):
        """Test searching in specific fields."""
        events = [
            {
                "summary": "Meeting",
                "description": "Team discussion",
                "location": "Office",
            },
            {"summary": "Call", "description": "Client update", "location": "Remote"},
        ]

        # Search only in summary
        filtered = _client_side_filter(
            events, search_text="team", search_fields=["summary"]
        )
        assert len(filtered) == 0  # "team" is not in any summary

        # Search only in description
        filtered = _client_side_filter(
            events, search_text="team", search_fields=["description"]
        )
        assert len(filtered) == 1  # "team" is in description of first event
        assert filtered[0]["summary"] == "Meeting"

        # Search in multiple fields
        filtered = _client_side_filter(
            events, search_text="remote", search_fields=["summary", "location"]
        )
        assert len(filtered) == 1  # "remote" is in location of second event
        assert filtered[0]["summary"] == "Call"

    def test_attendee_search(self):
        """Test searching by attendees."""
        events = [
            {
                "summary": "Team Meeting",
                "attendees": [
                    {"email": "alice@company.com", "displayName": "Alice Smith"},
                    {"email": "bob@company.com", "displayName": "Bob Jones"},
                ],
            },
            {
                "summary": "Client Call",
                "attendees": [
                    {"email": "client@external.com", "displayName": "Client Rep"}
                ],
            },
        ]

        # Search in attendees by name
        filtered = _client_side_filter(
            events, search_text="alice", search_fields=["attendees"]
        )
        assert len(filtered) == 1
        assert filtered[0]["summary"] == "Team Meeting"

        # Search by email domain
        filtered = _client_side_filter(
            events, search_text="company.com", search_fields=["attendees"]
        )
        assert len(filtered) == 1  # Only first event has company.com attendees

        # Search by display name
        filtered = _client_side_filter(
            events, search_text="Smith", search_fields=["attendees"]
        )
        assert len(filtered) == 1
        assert filtered[0]["summary"] == "Team Meeting"

    def test_edge_cases(self):
        """Test edge cases and error conditions."""
        events = [{"summary": "Test Event"}, {"description": "Another event"}]

        # Empty search text
        filtered = _client_side_filter(events, search_text="")
        assert len(filtered) == 2  # Returns all events

        # None search text
        filtered = _client_side_filter(events, search_text=None)
        assert len(filtered) == 2  # Returns all events

        # Search in non-existent field
        filtered = _client_side_filter(
            events, search_text="test", search_fields=["nonexistent"]
        )
        assert len(filtered) == 0  # No matches

        # Empty events list
        filtered = _client_side_filter([], search_text="test")
        assert len(filtered) == 0

    def test_missing_fields(self):
        """Test handling of events with missing fields."""
        events_with_missing = [
            {"summary": "Complete Event", "description": "Has everything"},
            {"summary": "Partial Event"},  # Missing description
            {},  # Missing everything
        ]

        # Should handle missing fields gracefully
        filtered = _client_side_filter(events_with_missing, search_text="event")
        assert len(filtered) == 2  # Should find events with "event" in summary

        # Search in description field with missing descriptions
        filtered = _client_side_filter(
            events_with_missing,
            search_text="everything",
            search_fields=["description"],
        )
        assert len(filtered) == 1  # Only first event has description with "everything"

    def test_fuzzy_matching(self):
        """Test fuzzy matching: typos, accents, punctuation, morphology."""
        events = [
            {"summary": "Examiners meeting", "description": "Board review"},
            {"summary": "Follow-up call", "description": "Client check-in"},
            {"summary": "Café discussion", "description": "Informal chat"},
            {"summary": "Team Meeting", "description": "Weekly sync"},
        ]

        # "examiner" should match "Examiners meeting" via fuzzy matching
        filtered = _client_side_filter(events, search_text="examiner")
        assert len(filtered) >= 1
        assert any(e["summary"] == "Examiners meeting" for e in filtered)

        # "followup" should match "Follow-up call" via punctuation normalization
        filtered = _client_side_filter(events, search_text="followup")
        assert len(filtered) >= 1
        assert any(e["summary"] == "Follow-up call" for e in filtered)

        # "cafe" should match "Café discussion" via Unicode normalization
        filtered = _client_side_filter(events, search_text="cafe")
        assert len(filtered) >= 1
        assert any(e["summary"] == "Café discussion" for e in filtered)

        # "meting" should match "Team Meeting" via typo tolerance
        filtered = _client_side_filter(events, search_text="meting")
        assert len(filtered) >= 1
        assert any("Meeting" in e["summary"] for e in filtered)

    def test_word_order(self):
        """Test that word order doesn't matter for multi-term queries."""
        events = [
            {"summary": "Lunch with Bob", "description": "Casual catch-up"},
        ]

        filtered = _client_side_filter(events, search_text="bob lunch")
        assert len(filtered) == 1

    def test_empty_search_fields_defaults(self):
        """Test that search_fields=[] behaves like None (uses defaults)."""
        events = [
            {"summary": "Team Meeting", "description": "Weekly sync"},
        ]

        filtered_none = _client_side_filter(
            events, search_text="team", search_fields=None
        )
        filtered_empty = _client_side_filter(
            events, search_text="team", search_fields=[]
        )
        assert filtered_none == filtered_empty
        assert len(filtered_none) == 1

    def test_short_term_matching(self):
        """Test short term behavior with partial_ratio."""
        events = [
            {"summary": "Air travel plans", "description": "Flight booking"},
            {"summary": "AI Workshop", "description": "Machine learning"},
            {"summary": "Lunch", "description": "Team lunch"},
        ]

        # Short terms (1-2 chars) match broadly via substring with partial_ratio
        # "AI" matches both "AI Workshop" (exact) and "Air" (substring)
        filtered = _client_side_filter(events, search_text="AI")
        assert any(e["summary"] == "AI Workshop" for e in filtered)

        # But unrelated events should not match
        assert not any(e["summary"] == "Lunch" for e in filtered)


class TestSearchParameterValidation:
    """Test search parameter validation and combinations."""

    def test_search_field_combinations(self):
        """Test different search field combinations."""
        event = {
            "summary": "Important Meeting",
            "description": "Quarterly review with team",
            "location": "Conference Room A",
            "attendees": [
                {"email": "manager@company.com", "displayName": "Jane Manager"}
            ],
        }

        events = [event]

        # Test default fields (summary, description, location)
        filtered = _client_side_filter(events, search_text="quarterly")
        assert len(filtered) == 1

        # Test summary only
        filtered = _client_side_filter(
            events, search_text="quarterly", search_fields=["summary"]
        )
        assert len(filtered) == 0  # "quarterly" is in description, not summary

        # Test all possible fields
        filtered = _client_side_filter(
            events,
            search_text="manager",
            search_fields=["summary", "description", "location", "attendees"],
        )
        assert len(filtered) == 1  # Found in attendees

    def test_complex_search_scenarios(self):
        """Test complex real-world search scenarios."""
        events = [
            {
                "summary": "Weekly Team Standup",
                "description": "Engineering team sync",
                "location": "Room 101",
                "attendees": [{"email": "alice@eng.com", "displayName": "Alice"}],
            },
            {
                "summary": "Client Review Meeting",
                "description": "Quarterly business review",
                "location": "Conference Room",
                "attendees": [
                    {"email": "client@external.com", "displayName": "Client"}
                ],
            },
            {
                "summary": "Engineering All-Hands",
                "description": "Monthly team meeting",
                "location": "Auditorium",
            },
        ]

        # Find all engineering-related events
        filtered = _client_side_filter(
            events, search_text="engineering", case_sensitive=False
        )
        assert len(filtered) == 2

        # Find team meetings (should match multiple events with OR logic)
        filtered = _client_side_filter(
            events, search_text="team meeting", match_all_terms=False
        )
        assert len(filtered) == 3  # All have either "team" or "meeting"

        # Find specific room
        filtered = _client_side_filter(
            events, search_text="room 101", search_fields=["location"]
        )
        assert len(filtered) == 1


class TestNormalizeText:
    """Test text normalization."""

    def test_casefold(self):
        """Test case folding (default)."""
        assert _normalize_text("HELLO World") == "hello world"

    def test_case_sensitive(self):
        """Test that case_sensitive=True preserves case."""
        assert _normalize_text("HELLO World", case_sensitive=True) == "HELLO World"

    def test_unicode_normalization(self):
        """Test NFKD normalization strips accents."""
        assert _normalize_text("café") == "cafe"
        assert _normalize_text("naïve") == "naive"
        assert _normalize_text("résumé") == "resume"

    def test_punctuation_normalization(self):
        """Test punctuation is normalized to spaces."""
        assert _normalize_text("follow-up") == "follow up"
        assert _normalize_text("it's") == "it s"
        assert _normalize_text("path/to/file") == "path to file"

    def test_whitespace_collapse(self):
        """Test multiple spaces are collapsed."""
        assert _normalize_text("  hello   world  ") == "hello world"

    def test_empty(self):
        """Test empty input."""
        assert _normalize_text("") == ""


class TestTermThreshold:
    """Test dynamic threshold for fuzzy matching."""

    def test_short_terms_strict(self):
        assert _term_threshold(1) == 95
        assert _term_threshold(2) == 95

    def test_medium_terms(self):
        assert _term_threshold(3) == 90
        assert _term_threshold(4) == 90

    def test_longer_terms(self):
        assert _term_threshold(5) == 85
        assert _term_threshold(7) == 85

    def test_long_terms(self):
        assert _term_threshold(8) == 80
        assert _term_threshold(12) == 80

    def test_very_long_terms(self):
        assert _term_threshold(13) == 75
        assert _term_threshold(20) == 75


class TestCompactMode:
    """Test compact event model and builder."""

    def test_build_compact_event(self):
        """Test _build_compact_event produces CompactCalendarEvent."""
        event_data = {
            "id": "abc123",
            "summary": "Test Event",
            "description": "Should not appear in compact",
            "location": "Somewhere",
            "start": {"dateTime": "2026-03-03T10:00:00+00:00", "timeZone": "UTC"},
            "end": {"dateTime": "2026-03-03T11:00:00+00:00", "timeZone": "UTC"},
            "attendees": [{"email": "test@example.com"}],
            "calendar_name": "Primary",
        }

        result = _build_compact_event(event_data)
        assert isinstance(result, CompactCalendarEvent)
        assert result.id == "abc123"
        assert result.summary == "Test Event"
        assert result.calendar_name == "Primary"
        assert result.start.dateTime == "2026-03-03T10:00:00+00:00"
        assert result.end.dateTime == "2026-03-03T11:00:00+00:00"

        # Compact model should NOT have description, location, attendees
        assert not hasattr(result, "description")
        assert not hasattr(result, "location")
        assert (
            not hasattr(result, "attendees") or "attendees" not in result.model_fields
        )

    def test_compact_datetime_normalization(self):
        """Test that compact builder applies the same datetime normalization."""
        event_data = {
            "id": "abc123",
            "summary": "BST Event",
            "start": {"dateTime": "2026-06-15T09:00:00Z", "timeZone": "Europe/London"},
            "end": {"dateTime": "2026-06-15T10:00:00Z", "timeZone": "Europe/London"},
        }

        result = _build_compact_event(event_data)
        # UTC 09:00 in BST (UTC+1) should become 10:00+01:00
        assert "10:00:00+01:00" in result.start.dateTime
        assert result.start.timeZone == "Europe/London"

    def test_compact_model_fields(self):
        """Test that CompactCalendarEvent has exactly the expected fields."""
        fields = set(CompactCalendarEvent.model_fields.keys())
        assert fields == {"id", "summary", "start", "end", "calendar_name"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
