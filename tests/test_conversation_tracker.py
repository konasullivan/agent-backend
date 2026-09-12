"""Unit tests for conversation_tracker.py: cosine similarity, timeout purging, and threshold clustering."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest

import conversation_tracker
from conversation_tracker import (
    CONVERSATION_TIMEOUT_HOURS,
    SIMILARITY_THRESHOLD,
    _active_conversations,
    _cosine_similarity,
    _generate_topic_label,
    _purge_expired,
    get_or_create_conversation,
)


@pytest.fixture(autouse=True)
def clear_active_conversations(monkeypatch):
    """Ensure in-memory conversation state is reset and Qdrant is disabled for in-memory tests."""
    _active_conversations.clear()
    monkeypatch.setattr("conversation_tracker.get_qdrant_client", lambda: None)
    yield
    _active_conversations.clear()


class TestCosineSimilarity:
    """Mathematical verification of _cosine_similarity function."""

    def test_identical_vectors(self):
        vec = [1.0, 2.0, 3.0]
        sim = _cosine_similarity(vec, vec)
        assert pytest.approx(sim, rel=1e-5) == 1.0

    def test_proportional_vectors(self):
        v1 = [1.0, 2.0, 3.0]
        v2 = [2.0, 4.0, 6.0]
        sim = _cosine_similarity(v1, v2)
        assert pytest.approx(sim, rel=1e-5) == 1.0

    def test_orthogonal_vectors(self):
        v1 = [1.0, 0.0]
        v2 = [0.0, 1.0]
        assert _cosine_similarity(v1, v2) == 0.0

    def test_opposite_vectors(self):
        v1 = [1.0, 2.0]
        v2 = [-1.0, -2.0]
        assert pytest.approx(_cosine_similarity(v1, v2), rel=1e-5) == -1.0

    def test_zero_vector_safety(self):
        zero = [0.0, 0.0, 0.0]
        valid = [1.0, 2.0, 3.0]
        assert _cosine_similarity(zero, valid) == 0.0
        assert _cosine_similarity(valid, zero) == 0.0
        assert _cosine_similarity(zero, zero) == 0.0

    def test_empty_vector_safety(self):
        assert _cosine_similarity([], []) == 0.0


class TestPurgeExpired:
    """Verification of conversation inactivity timeout purging."""

    def test_purge_removes_old_threads_and_keeps_recent(self):
        now = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)

        # Thread 1: 5 hours ago (expired)
        _active_conversations["old_1"] = {
            "topic": "Old Thread",
            "embedding": [1.0, 0.0],
            "last_seen": now - timedelta(hours=5),
        }

        # Thread 2: 2 hours ago (active)
        _active_conversations["recent_1"] = {
            "topic": "Recent Thread",
            "embedding": [0.0, 1.0],
            "last_seen": now - timedelta(hours=2),
        }

        _purge_expired(now)

        assert "old_1" not in _active_conversations
        assert "recent_1" in _active_conversations

    def test_purge_boundary_condition(self):
        now = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)

        # Exactly 4 hours ago (boundary: cutoff is now - 4h; condition is last_seen < cutoff)
        # 4 hours + 1 second is expired
        _active_conversations["just_expired"] = {
            "topic": "Just Expired",
            "embedding": [1.0, 0.0],
            "last_seen": now - timedelta(hours=CONVERSATION_TIMEOUT_HOURS, seconds=1),
        }

        # 4 hours - 1 second is active
        _active_conversations["just_active"] = {
            "topic": "Just Active",
            "embedding": [0.0, 1.0],
            "last_seen": now - timedelta(hours=CONVERSATION_TIMEOUT_HOURS, seconds=-1),
        }

        _purge_expired(now)

        assert "just_expired" not in _active_conversations
        assert "just_active" in _active_conversations


class TestTopicLabelGeneration:
    """Verification of topic label generation and clean stripping."""

    def test_topic_label_stripping(self):
        with patch("conversation_tracker._call_gemini", return_value='  "Quarterly Audit" \n'):
            label = _generate_topic_label("Audit kickoff message")
            assert label == "Quarterly Audit"

    def test_topic_label_fallback_when_empty(self):
        with patch("conversation_tracker._call_gemini", return_value=""):
            label = _generate_topic_label("Empty response test")
            assert label == "General"


class TestGetOrCreateConversation:
    """Verification of semantic grouping, threshold joining, and thread updating."""

    def test_create_initial_conversation(self):
        now = datetime(2026, 9, 11, 10, 0, 0, tzinfo=timezone.utc)
        mock_embedding = [0.5, 0.5, 0.0]

        with patch("conversation_tracker._embed", return_value=mock_embedding), \
             patch("conversation_tracker._call_gemini", return_value="Project Alpha"):
            cid, topic = get_or_create_conversation("Starting alpha", now)

            assert len(cid) == 8
            assert topic == "Project Alpha"
            assert cid in _active_conversations
            assert _active_conversations[cid]["topic"] == "Project Alpha"
            assert _active_conversations[cid]["last_seen"] == now
            assert _active_conversations[cid]["embedding"] == mock_embedding

    def test_join_conversation_when_similarity_above_threshold(self):
        now = datetime(2026, 9, 11, 10, 0, 0, tzinfo=timezone.utc)
        initial_embedding = [1.0, 0.0, 0.0]

        # Pre-seed an active conversation
        _active_conversations["convo_01"] = {
            "topic": "Budget Review",
            "embedding": list(initial_embedding),
            "last_seen": now,
        }

        # New message with very high similarity (cosine sim ~ 0.99 >= 0.72)
        next_time = now + timedelta(minutes=15)
        new_embedding = [0.99, 0.01, 0.0]

        with patch("conversation_tracker._embed", return_value=new_embedding):
            cid, topic = get_or_create_conversation("Checking line items", next_time)

            assert cid == "convo_01"
            assert topic == "Budget Review"
            assert _active_conversations[cid]["last_seen"] == next_time
            # Blended embedding: 0.7 * old + 0.3 * new
            expected_e0 = 0.7 * 1.0 + 0.3 * 0.99
            expected_e1 = 0.7 * 0.0 + 0.3 * 0.01
            assert pytest.approx(_active_conversations[cid]["embedding"][0], rel=1e-5) == expected_e0
            assert pytest.approx(_active_conversations[cid]["embedding"][1], rel=1e-5) == expected_e1

    def test_split_conversation_when_similarity_below_threshold(self):
        now = datetime(2026, 9, 11, 10, 0, 0, tzinfo=timezone.utc)
        _active_conversations["convo_01"] = {
            "topic": "Budget Review",
            "embedding": [1.0, 0.0, 0.0],
            "last_seen": now,
        }

        # Unrelated topic (orthogonal embedding -> similarity 0.0 < 0.72)
        next_time = now + timedelta(minutes=10)
        unrelated_embedding = [0.0, 1.0, 0.0]

        with patch("conversation_tracker._embed", return_value=unrelated_embedding), \
             patch("conversation_tracker._call_gemini", return_value="Lunch Plans"):
            cid, topic = get_or_create_conversation("Where should we eat?", next_time)

            assert cid != "convo_01"
            assert topic == "Lunch Plans"
            assert len(_active_conversations) == 2
            assert "convo_01" in _active_conversations
            assert cid in _active_conversations

    def test_split_conversation_on_inactivity_timeout_even_with_high_similarity(self):
        t1 = datetime(2026, 9, 11, 8, 0, 0, tzinfo=timezone.utc)
        _active_conversations["convo_01"] = {
            "topic": "Morning Standup",
            "embedding": [1.0, 0.0, 0.0],
            "last_seen": t1,
        }

        # Message arrives 5 hours later (> 4 hour timeout), but with identical embedding
        t2 = t1 + timedelta(hours=5)
        same_embedding = [1.0, 0.0, 0.0]

        with patch("conversation_tracker._embed", return_value=same_embedding), \
             patch("conversation_tracker._call_gemini", return_value="Afternoon Standup"):
            cid, topic = get_or_create_conversation("Another standup", t2)

            # Old convo was purged by _purge_expired(t2)
            assert cid != "convo_01"
            assert "convo_01" not in _active_conversations
            assert cid in _active_conversations
            assert topic == "Afternoon Standup"
