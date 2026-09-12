"""Unit and integration tests for Qdrant vector tracker in conversation_tracker.py.

Covers:
1. Qdrant connection and collection initialization (creation, dimension mismatch recovery).
2. Point upsert and vector search with cosine similarity (score thresholding, payload, deterministic UUIDs).
3. In-memory fallback when Qdrant is unreachable or encounters runtime errors.
4. Conversation clustering threshold behavior (similarity threshold joining/splitting, inactivity timeout, multi-turn).
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import uuid
import pytest
from qdrant_client import QdrantClient, models

import conversation_tracker
from conversation_tracker import (
    CONVERSATION_TIMEOUT_HOURS,
    SIMILARITY_THRESHOLD,
    _active_conversations,
    _cid_to_qdrant_id,
    _purge_expired,
    _purge_expired_qdrant,
    _query_nearest_qdrant,
    _upsert_qdrant_conversation,
    ensure_collection_exists,
    get_or_create_conversation,
    get_qdrant_client,
    set_qdrant_client,
)
from config import QDRANT_COLLECTION


@pytest.fixture(autouse=True)
def reset_conversation_tracker_state():
    """Ensure in-memory state and Qdrant client instance are clean before and after each test."""
    _active_conversations.clear()
    set_qdrant_client(None)
    conversation_tracker._last_connect_attempt = 0.0
    yield
    _active_conversations.clear()
    set_qdrant_client(None)
    conversation_tracker._last_connect_attempt = 0.0


@pytest.fixture
def memory_qdrant() -> QdrantClient:
    """Provide an isolated, in-memory Qdrant client for fast, hermetic vector testing."""
    client = QdrantClient(":memory:")
    set_qdrant_client(client)
    return client


class TestQdrantConnectionAndInit:
    """Verification of Qdrant client connection management and collection initialization."""

    def test_get_qdrant_client_success(self):
        mock_client = MagicMock(spec=QdrantClient)
        mock_client.get_collections.return_value = MagicMock()

        with patch("conversation_tracker.QdrantClient", return_value=mock_client) as mock_cls:
            client = get_qdrant_client()
            assert client is mock_client
            mock_cls.assert_called_once()
            # Repeated calls should return cached instance without re-instantiating
            assert get_qdrant_client() is mock_client
            assert mock_cls.call_count == 1

    def test_get_qdrant_client_retry_interval_throttling(self):
        with patch("conversation_tracker.QdrantClient", side_effect=ConnectionError("Connection refused")) as mock_cls:
            client1 = get_qdrant_client()
            assert client1 is None
            assert mock_cls.call_count == 1

            # Second immediate attempt should be throttled by _CONNECT_RETRY_INTERVAL
            client2 = get_qdrant_client()
            assert client2 is None
            assert mock_cls.call_count == 1

            # Advancing time past retry interval allows a retry
            conversation_tracker._last_connect_attempt -= 6.0
            client3 = get_qdrant_client()
            assert client3 is None
            assert mock_cls.call_count == 2

    def test_set_qdrant_client_override(self):
        client = QdrantClient(":memory:")
        set_qdrant_client(client)
        assert get_qdrant_client() is client

        set_qdrant_client(None)
        assert conversation_tracker._client_instance is None
        assert conversation_tracker._last_connect_attempt == 0.0

    def test_ensure_collection_exists_creation(self, memory_qdrant: QdrantClient):
        assert not memory_qdrant.collection_exists(QDRANT_COLLECTION)
        ensure_collection_exists(memory_qdrant, vector_size=768)

        assert memory_qdrant.collection_exists(QDRANT_COLLECTION)
        coll = memory_qdrant.get_collection(QDRANT_COLLECTION)
        assert coll.config.params.vectors.size == 768
        assert coll.config.params.vectors.distance == models.Distance.COSINE

    def test_ensure_collection_exists_already_present_idempotent(self, memory_qdrant: QdrantClient):
        ensure_collection_exists(memory_qdrant, vector_size=384)
        # Calling again should not recreate or error
        ensure_collection_exists(memory_qdrant, vector_size=384)
        coll = memory_qdrant.get_collection(QDRANT_COLLECTION)
        assert coll.config.params.vectors.size == 384

    def test_ensure_collection_exists_dimension_mismatch_recreation(self, memory_qdrant: QdrantClient):
        ensure_collection_exists(memory_qdrant, vector_size=128)
        assert memory_qdrant.get_collection(QDRANT_COLLECTION).config.params.vectors.size == 128

        # When vector_size changes, collection is deleted and recreated
        ensure_collection_exists(memory_qdrant, vector_size=256)
        assert memory_qdrant.get_collection(QDRANT_COLLECTION).config.params.vectors.size == 256

    def test_ensure_collection_exists_exception_propagation(self):
        mock_client = MagicMock()
        mock_client.collection_exists.side_effect = RuntimeError("Collection check failed")
        with pytest.raises(RuntimeError, match="Collection check failed"):
            ensure_collection_exists(mock_client, vector_size=768)


class TestQdrantUpsertAndVectorSearch:
    """Verification of deterministic UUID mapping, point upsert, and cosine similarity query filtering."""

    def test_deterministic_cid_to_qdrant_id(self):
        cid1 = "a1b2c3d4"
        uid1 = _cid_to_qdrant_id(cid1)
        uid2 = _cid_to_qdrant_id(cid1)

        # Must be deterministic and valid UUID format
        assert uid1 == uid2
        parsed = uuid.UUID(uid1)
        assert parsed.version == 5

        # Different cid produces different UUID
        cid2 = "e5f60718"
        assert _cid_to_qdrant_id(cid2) != uid1

    def test_upsert_qdrant_conversation(self, memory_qdrant: QdrantClient):
        ensure_collection_exists(memory_qdrant, vector_size=3)
        now = datetime(2026, 9, 11, 14, 30, 0, tzinfo=timezone.utc)
        cid = "convo_10"
        vec = [0.6, 0.8, 0.0]

        _upsert_qdrant_conversation(memory_qdrant, cid, "Sprint Planning", vec, now)

        point_id = _cid_to_qdrant_id(cid)
        points = memory_qdrant.retrieve(
            collection_name=QDRANT_COLLECTION,
            ids=[point_id],
            with_payload=True,
            with_vectors=True,
        )
        assert len(points) == 1
        pt = points[0]
        assert pt.payload["conversation_id"] == cid
        assert pt.payload["topic"] == "Sprint Planning"
        assert pt.payload["last_seen"] == now.isoformat()
        assert pt.payload["last_seen_ts"] == now.timestamp()
        assert pytest.approx(pt.vector, rel=1e-4) == vec

    def test_query_nearest_qdrant_cosine_similarity_threshold(self, memory_qdrant: QdrantClient):
        ensure_collection_exists(memory_qdrant, vector_size=2)
        t1 = datetime(2026, 9, 11, 10, 0, 0, tzinfo=timezone.utc)

        # Seed conversation with vector [1.0, 0.0]
        _upsert_qdrant_conversation(memory_qdrant, "c1", "Design Sync", [1.0, 0.0], t1)

        # 1. Query with nearly identical vector (cosine sim ~ 1.0 >= 0.72)
        match_id, match_topic, match_vec = _query_nearest_qdrant(
            memory_qdrant, [0.99, 0.01], t1 + timedelta(minutes=5)
        )
        assert match_id == "c1"
        assert match_topic == "Design Sync"
        assert match_vec is not None

        # 2. Query with orthogonal vector (cosine sim 0.0 < 0.72)
        miss_id, miss_topic, miss_vec = _query_nearest_qdrant(
            memory_qdrant, [0.0, 1.0], t1 + timedelta(minutes=10)
        )
        assert miss_id is None
        assert miss_topic is None
        assert miss_vec is None

    def test_query_nearest_qdrant_timeout_filter(self, memory_qdrant: QdrantClient):
        ensure_collection_exists(memory_qdrant, vector_size=2)
        t1 = datetime(2026, 9, 11, 8, 0, 0, tzinfo=timezone.utc)
        _upsert_qdrant_conversation(memory_qdrant, "c1", "Architecture", [1.0, 0.0], t1)

        # Query within 4 hours (e.g. 2 hours later) -> matches
        t_within = t1 + timedelta(hours=2)
        cid, _, _ = _query_nearest_qdrant(memory_qdrant, [1.0, 0.0], t_within)
        assert cid == "c1"

        # Query past 4 hours (e.g. 4 hours + 1 second later) -> filtered out by cutoff
        t_expired = t1 + timedelta(hours=CONVERSATION_TIMEOUT_HOURS, seconds=1)
        cid_exp, _, _ = _query_nearest_qdrant(memory_qdrant, [1.0, 0.0], t_expired)
        assert cid_exp is None

    def test_purge_expired_qdrant(self, memory_qdrant: QdrantClient):
        ensure_collection_exists(memory_qdrant, vector_size=2)
        now = datetime(2026, 9, 11, 16, 0, 0, tzinfo=timezone.utc)

        # 1 old point (5 hours ago) and 1 recent point (1 hour ago)
        t_old = now - timedelta(hours=5)
        t_recent = now - timedelta(hours=1)

        _upsert_qdrant_conversation(memory_qdrant, "old_c", "Old Convo", [1.0, 0.0], t_old)
        _upsert_qdrant_conversation(memory_qdrant, "recent_c", "Recent Convo", [0.0, 1.0], t_recent)

        assert memory_qdrant.count(QDRANT_COLLECTION).count == 2

        _purge_expired_qdrant(memory_qdrant, now)

        assert memory_qdrant.count(QDRANT_COLLECTION).count == 1
        remaining = memory_qdrant.retrieve(
            collection_name=QDRANT_COLLECTION,
            ids=[_cid_to_qdrant_id("recent_c")],
        )
        assert len(remaining) == 1
        assert remaining[0].payload["conversation_id"] == "recent_c"


class TestQdrantInMemoryFallback:
    """Verification of seamless fallback to in-memory dictionary when Qdrant is unreachable or fails."""

    def test_fallback_when_get_qdrant_client_returns_none(self):
        set_qdrant_client(None)
        with patch("conversation_tracker.get_qdrant_client", return_value=None):
            now = datetime(2026, 9, 11, 10, 0, 0, tzinfo=timezone.utc)
            with patch("conversation_tracker._embed", return_value=[0.3, 0.7]), \
                 patch("conversation_tracker._call_gemini", return_value="Offline Thread"):
                cid, topic = get_or_create_conversation("First offline message", now)

                assert topic == "Offline Thread"
                assert cid in _active_conversations
                assert _active_conversations[cid]["topic"] == "Offline Thread"

    def test_fallback_when_qdrant_query_throws_runtime_exception(self):
        mock_qdrant = MagicMock(spec=QdrantClient)
        mock_qdrant.collection_exists.return_value = True
        mock_qdrant.query_points.side_effect = RuntimeError("503 Service Unavailable: High cluster load")
        set_qdrant_client(mock_qdrant)

        now = datetime(2026, 9, 11, 11, 0, 0, tzinfo=timezone.utc)
        with patch("conversation_tracker._embed", return_value=[0.4, 0.6]), \
             patch("conversation_tracker._call_gemini", return_value="Resilient Thread"):
            cid, topic = get_or_create_conversation("Resilient query message", now)

            assert topic == "Resilient Thread"
            assert cid in _active_conversations
            # Client instance is reset to None on failure to protect subsequent calls
            assert conversation_tracker._client_instance is None

    def test_fallback_when_qdrant_upsert_throws_exception(self):
        mock_qdrant = MagicMock(spec=QdrantClient)
        mock_qdrant.collection_exists.return_value = True
        # Query returns no existing match
        mock_qdrant.query_points.return_value = MagicMock(points=[])
        # Upsert fails
        mock_qdrant.upsert.side_effect = ConnectionResetError("Connection reset by peer")
        set_qdrant_client(mock_qdrant)

        now = datetime(2026, 9, 11, 11, 30, 0, tzinfo=timezone.utc)
        with patch("conversation_tracker._embed", return_value=[0.1, 0.9]), \
             patch("conversation_tracker._call_gemini", return_value="Upsert Fail Topic"):
            cid, topic = get_or_create_conversation("Upsert test message", now)

            assert topic == "Upsert Fail Topic"
            assert cid in _active_conversations
            assert conversation_tracker._client_instance is None

    def test_purge_expired_swallows_qdrant_purge_exception(self):
        mock_qdrant = MagicMock(spec=QdrantClient)
        mock_qdrant.collection_exists.return_value = True
        mock_qdrant.delete.side_effect = TimeoutError("Purge timed out")
        set_qdrant_client(mock_qdrant)

        now = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)
        # Should not raise exception
        _purge_expired(now)


class TestQdrantClusteringThreshold:
    """Verification of conversation clustering, similarity threshold boundary behavior, and vector nudging."""

    def test_high_similarity_joins_and_nudges_embedding(self, memory_qdrant: QdrantClient):
        now = datetime(2026, 9, 11, 9, 0, 0, tzinfo=timezone.utc)
        v1 = [1.0, 0.0]

        with patch("conversation_tracker._embed", return_value=v1), \
             patch("conversation_tracker._call_gemini", return_value="Security Audit"):
            cid1, topic1 = get_or_create_conversation("Starting security review", now)
            assert topic1 == "Security Audit"

        # Message 2 has cosine similarity ~ 0.999 >= 0.72 -> joins thread
        next_time = now + timedelta(minutes=15)
        v2 = [0.99, 0.05]
        with patch("conversation_tracker._embed", return_value=v2):
            cid2, topic2 = get_or_create_conversation("Continuing security check", next_time)
            assert cid2 == cid1
            assert topic2 == "Security Audit"

            # Check nudged embedding: 0.7 * v1 + 0.3 * v2
            expected_x = 0.7 * 1.0 + 0.3 * 0.99
            expected_y = 0.7 * 0.0 + 0.3 * 0.05
            assert pytest.approx(_active_conversations[cid1]["embedding"][0], rel=1e-4) == expected_x
            assert pytest.approx(_active_conversations[cid1]["embedding"][1], rel=1e-4) == expected_y

            # Verify in Qdrant store: vector direction matches nudged embedding
            pt = memory_qdrant.retrieve(
                collection_name=QDRANT_COLLECTION,
                ids=[_cid_to_qdrant_id(cid1)],
                with_vectors=True,
            )[0]
            # Qdrant normalizes vectors for Distance.COSINE; cosine similarity of stored vector must be ~1.0
            from conversation_tracker import _cosine_similarity
            assert _cosine_similarity(pt.vector, [expected_x, expected_y]) == pytest.approx(1.0, rel=1e-4)

    def test_low_similarity_splits_into_new_conversation(self, memory_qdrant: QdrantClient):
        now = datetime(2026, 9, 11, 9, 0, 0, tzinfo=timezone.utc)
        v1 = [1.0, 0.0]

        with patch("conversation_tracker._embed", return_value=v1), \
             patch("conversation_tracker._call_gemini", return_value="Database Migration"):
            cid1, topic1 = get_or_create_conversation("Running migration scripts", now)

        # Orthogonal embedding (sim = 0.0 < 0.72) -> splits into separate thread
        next_time = now + timedelta(minutes=10)
        v2 = [0.0, 1.0]
        with patch("conversation_tracker._embed", return_value=v2), \
             patch("conversation_tracker._call_gemini", return_value="Team Lunch"):
            cid2, topic2 = get_or_create_conversation("Who wants pizza?", next_time)
            assert cid2 != cid1
            assert topic2 == "Team Lunch"
            assert memory_qdrant.count(QDRANT_COLLECTION).count == 2

    def test_threshold_boundary_precision(self, memory_qdrant: QdrantClient):
        ensure_collection_exists(memory_qdrant, vector_size=2)
        now = datetime(2026, 9, 11, 9, 0, 0, tzinfo=timezone.utc)
        # Seed conversation with unit vector along X axis
        base_v = [1.0, 0.0]
        _upsert_qdrant_conversation(memory_qdrant, "base_cid", "Base Topic", base_v, now)

        # Vector with cosine similarity 0.73 (> 0.72 SIMILARITY_THRESHOLD):
        # [0.73, sqrt(1 - 0.73^2)] ~ [0.73, 0.683447] -> cosine sim with [1.0, 0.0] is 0.73
        v_above = [0.73, 0.683447]
        match_id, match_topic, _ = _query_nearest_qdrant(memory_qdrant, v_above, now + timedelta(minutes=5))
        assert match_id == "base_cid"
        assert match_topic == "Base Topic"

        # Vector with cosine similarity 0.70 (< 0.72 SIMILARITY_THRESHOLD):
        # [0.70, sqrt(1 - 0.70^2)] ~ [0.70, 0.71414] -> cosine sim with [1.0, 0.0] is 0.70
        v_below = [0.70, 0.71414]
        miss_id, miss_topic, _ = _query_nearest_qdrant(memory_qdrant, v_below, now + timedelta(minutes=10))
        assert miss_id is None
        assert miss_topic is None

    def test_timeout_splits_conversation_even_with_identical_embedding(self, memory_qdrant: QdrantClient):
        t1 = datetime(2026, 9, 11, 8, 0, 0, tzinfo=timezone.utc)
        emb = [1.0, 0.0]

        with patch("conversation_tracker._embed", return_value=emb), \
             patch("conversation_tracker._call_gemini", return_value="Morning Standup"):
            cid1, topic1 = get_or_create_conversation("Morning standup kickoff", t1)

        # Message arrives 5 hours later (> 4h timeout) with exact same embedding
        t2 = t1 + timedelta(hours=5)
        with patch("conversation_tracker._embed", return_value=emb), \
             patch("conversation_tracker._call_gemini", return_value="Evening Standup"):
            cid2, topic2 = get_or_create_conversation("Evening recap kickoff", t2)
            assert cid2 != cid1
            assert topic2 == "Evening recap kickoff" or topic2 == "Evening Standup"

    def test_multi_turn_two_topic_clustering(self, memory_qdrant: QdrantClient):
        """Simulate a realistic sequence with 2 interleaved conversation topics."""
        t0 = datetime(2026, 9, 11, 10, 0, 0, tzinfo=timezone.utc)
        emb_work = [1.0, 0.0, 0.0]
        emb_social = [0.0, 1.0, 0.0]

        # 1. First work message
        with patch("conversation_tracker._embed", return_value=emb_work), \
             patch("conversation_tracker._call_gemini", return_value="Release Checklist"):
            work_cid, _ = get_or_create_conversation("Did anyone push the release tags?", t0)

        # 2. First social message (dissimilar)
        with patch("conversation_tracker._embed", return_value=emb_social), \
             patch("conversation_tracker._call_gemini", return_value="Coffee Run"):
            social_cid, _ = get_or_create_conversation("Grabbing espresso, who wants one?", t0 + timedelta(minutes=5))

        assert work_cid != social_cid

        # 3. Second work message (similar to work)
        emb_work_reply = [0.95, 0.05, 0.0]
        with patch("conversation_tracker._embed", return_value=emb_work_reply):
            reply_work_cid, _ = get_or_create_conversation("Yes, tags pushed to v1.2.0", t0 + timedelta(minutes=10))
            assert reply_work_cid == work_cid

        # 4. Second social message (similar to social)
        emb_social_reply = [0.05, 0.95, 0.0]
        with patch("conversation_tracker._embed", return_value=emb_social_reply):
            reply_social_cid, _ = get_or_create_conversation("Large oat latte please!", t0 + timedelta(minutes=12))
            assert reply_social_cid == social_cid
