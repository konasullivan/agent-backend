"""
Lightweight RAG-style conversation clustering: figures out which
"conversation" (topic thread) a new message belongs to by comparing
its embedding against currently-active threads -- so "when do you
want to have lunch" + "let's go at 6pm" land in the same thread, but
Sunday's trip planning and Monday's meeting chat stay separate even
in the same channel.

State lives in memory only for this demo -- it resets if the bot
restarts. For production, persist _active_conversations somewhere
durable (a small database, or alongside the sheet) instead.
"""
import logging
import math
import uuid
from datetime import datetime, timedelta
from typing import Optional

from qdrant_client import QdrantClient, models

from config import QDRANT_HOST, QDRANT_PORT, QDRANT_COLLECTION
from ai_extractor import client, _call_gemini

logger = logging.getLogger(__name__)

EMBED_MODEL = "gemini-embedding-001"

# How long a conversation thread stays "open" for new messages to
# join it. Messages further apart than this are treated as different
# conversations even if the topic sounds similar.
CONVERSATION_TIMEOUT_HOURS = 4

# How close a new message's embedding needs to be to an active
# thread's embedding to count as "the same conversation".
# Lower = groups more loosely, higher = splits into more threads.
SIMILARITY_THRESHOLD = 0.72

# conversation_id -> {"topic": str, "embedding": [float], "last_seen": datetime}
_active_conversations: dict[str, dict] = {}

# Qdrant client instance cache and reconnection retry state
_client_instance: Optional[QdrantClient] = None
_last_connect_attempt: float = 0.0
_CONNECT_RETRY_INTERVAL: float = 5.0


def set_qdrant_client(client_obj: Optional[QdrantClient]) -> None:
    """Explicitly set or override the Qdrant client (useful for testing or fallback toggling)."""
    global _client_instance, _last_connect_attempt
    _client_instance = client_obj
    _last_connect_attempt = 0.0


def get_qdrant_client() -> Optional[QdrantClient]:
    """Get active Qdrant client connection or None if unavailable."""
    global _client_instance, _last_connect_attempt
    if _client_instance is not None:
        return _client_instance

    now_ts = datetime.now().timestamp()
    if now_ts - _last_connect_attempt < _CONNECT_RETRY_INTERVAL:
        return None

    _last_connect_attempt = now_ts
    try:
        c = QdrantClient(
            host=QDRANT_HOST,
            port=QDRANT_PORT,
            timeout=1.0,
            check_compatibility=False,
        )
        c.get_collections()
        _client_instance = c
        logger.info("Connected to Qdrant at %s:%s", QDRANT_HOST, QDRANT_PORT)
        return _client_instance
    except Exception as exc:
        logger.debug(
            "Qdrant unavailable at %s:%s (%s), using in-memory fallback",
            QDRANT_HOST,
            QDRANT_PORT,
            exc,
        )
        return None


def ensure_collection_exists(qdrant: QdrantClient, vector_size: int = 768) -> None:
    """Ensure that the target Qdrant collection exists with appropriate vector configuration."""
    try:
        if not qdrant.collection_exists(QDRANT_COLLECTION):
            qdrant.create_collection(
                collection_name=QDRANT_COLLECTION,
                vectors_config=models.VectorParams(
                    size=vector_size,
                    distance=models.Distance.COSINE,
                ),
            )
        else:
            coll_info = qdrant.get_collection(QDRANT_COLLECTION)
            current_size = getattr(coll_info.config.params.vectors, "size", None)
            if current_size and current_size != vector_size:
                logger.warning(
                    "Collection %s has vector size %s, recreating for %s",
                    QDRANT_COLLECTION,
                    current_size,
                    vector_size,
                )
                qdrant.delete_collection(QDRANT_COLLECTION)
                qdrant.create_collection(
                    collection_name=QDRANT_COLLECTION,
                    vectors_config=models.VectorParams(
                        size=vector_size,
                        distance=models.Distance.COSINE,
                    ),
                )
    except Exception as exc:
        logger.warning("ensure_collection_exists failed: %s", exc)
        raise


def _cid_to_qdrant_id(cid: str) -> str:
    """Convert a short hex conversation ID into a deterministic valid Qdrant UUID string."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"convo-{cid}"))


def _query_nearest_qdrant(
    qdrant: QdrantClient,
    embedding: list[float],
    message_time: datetime,
) -> tuple[Optional[str], Optional[str], Optional[list[float]]]:
    """Query nearest active conversation in Qdrant within SIMILARITY_THRESHOLD and CONVERSATION_TIMEOUT_HOURS.

    Returns (conversation_id, topic, old_embedding) or (None, None, None).
    """
    cutoff = message_time - timedelta(hours=CONVERSATION_TIMEOUT_HOURS)
    cutoff_ts = cutoff.timestamp()

    query_filter = models.Filter(
        must=[
            models.FieldCondition(
                key="last_seen_ts",
                range=models.Range(gte=cutoff_ts),
            )
        ]
    )

    results = qdrant.query_points(
        collection_name=QDRANT_COLLECTION,
        query=embedding,
        query_filter=query_filter,
        score_threshold=SIMILARITY_THRESHOLD,
        limit=1,
        with_payload=True,
        with_vectors=True,
    )

    if results.points:
        top_match = results.points[0]
        cid = top_match.payload.get("conversation_id")
        topic = top_match.payload.get("topic")
        vec = list(top_match.vector) if top_match.vector is not None else None
        return cid, topic, vec

    return None, None, None


def _upsert_qdrant_conversation(
    qdrant: QdrantClient,
    cid: str,
    topic: str,
    embedding: list[float],
    message_time: datetime,
) -> None:
    """Upsert or update a conversation vector and metadata in Qdrant."""
    point_id = _cid_to_qdrant_id(cid)
    payload = {
        "conversation_id": cid,
        "topic": topic,
        "last_seen": message_time.isoformat(),
        "last_seen_ts": message_time.timestamp(),
    }
    qdrant.upsert(
        collection_name=QDRANT_COLLECTION,
        points=[
            models.PointStruct(
                id=point_id,
                vector=embedding,
                payload=payload,
            )
        ],
    )


def _purge_expired_qdrant(qdrant: QdrantClient, now: datetime) -> None:
    """Purge conversations from Qdrant older than CONVERSATION_TIMEOUT_HOURS."""
    cutoff = now - timedelta(hours=CONVERSATION_TIMEOUT_HOURS)
    cutoff_ts = cutoff.timestamp()
    qdrant.delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="last_seen_ts",
                        range=models.Range(lt=cutoff_ts),
                    )
                ]
            )
        ),
    )


def _embed(text: str) -> list[float]:
    result = client.models.embed_content(model=EMBED_MODEL, contents=text)
    return result.embeddings[0].values


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def _purge_expired(now: datetime) -> None:
    cutoff = now - timedelta(hours=CONVERSATION_TIMEOUT_HOURS)
    expired = [cid for cid, c in _active_conversations.items() if c["last_seen"] < cutoff]
    for cid in expired:
        del _active_conversations[cid]

    qdrant = get_qdrant_client()
    if qdrant is not None:
        try:
            if qdrant.collection_exists(QDRANT_COLLECTION):
                _purge_expired_qdrant(qdrant, now)
        except Exception as exc:
            logger.debug("Qdrant purge failed: %s", exc)


def _generate_topic_label(message_text: str) -> str:
    system = (
        "Give a short 2-5 word topic label for a group-chat conversation "
        "that starts with this message. Respond with ONLY the label, "
        "no punctuation, no quotes, no explanation."
    )
    label = _call_gemini(system, message_text, max_output_tokens=20)
    return label.strip().strip('"').strip("'") or "General"


def get_or_create_conversation(message_text: str, message_time: datetime) -> tuple[str, str]:
    """Returns (conversation_id, topic_label). Joins an existing active
    thread if the message is a close enough semantic match, else opens
    a new one."""
    global _client_instance
    _purge_expired(message_time)
    embedding = _embed(message_text)

    # 1. Attempt Qdrant vector search if available
    qdrant = get_qdrant_client()
    if qdrant is not None:
        try:
            ensure_collection_exists(qdrant, vector_size=len(embedding))
            best_id, topic, old_vec = _query_nearest_qdrant(qdrant, embedding, message_time)
            if best_id and topic:
                # nudge the thread's embedding toward this new message
                new_vec = (
                    [e * 0.7 + n * 0.3 for e, n in zip(old_vec, embedding)]
                    if old_vec
                    else embedding
                )
                _upsert_qdrant_conversation(qdrant, best_id, topic, new_vec, message_time)
                _active_conversations[best_id] = {
                    "topic": topic,
                    "embedding": new_vec,
                    "last_seen": message_time,
                }
                return best_id, topic

            # In case test fixtures or previous messages populated in-memory store
            best_mem_id, best_mem_score = None, 0.0
            for cid, convo in _active_conversations.items():
                score = _cosine_similarity(embedding, convo["embedding"])
                if score > best_mem_score:
                    best_mem_id, best_mem_score = cid, score

            if best_mem_id and best_mem_score >= SIMILARITY_THRESHOLD:
                convo = _active_conversations[best_mem_id]
                convo["embedding"] = [e * 0.7 + n * 0.3 for e, n in zip(convo["embedding"], embedding)]
                convo["last_seen"] = message_time
                _upsert_qdrant_conversation(qdrant, best_mem_id, convo["topic"], convo["embedding"], message_time)
                return best_mem_id, convo["topic"]

            # Open a new conversation thread
            new_id = uuid.uuid4().hex[:8]
            new_topic = _generate_topic_label(message_text)
            _upsert_qdrant_conversation(qdrant, new_id, new_topic, embedding, message_time)
            _active_conversations[new_id] = {
                "topic": new_topic,
                "embedding": embedding,
                "last_seen": message_time,
            }
            return new_id, new_topic
        except Exception as exc:
            logger.warning(
                "Qdrant vector operation failed: %s; falling back to in-memory dictionary",
                exc,
            )
            _client_instance = None
            # Fall through to in-memory dictionary fallback below

    # 2. In-memory dictionary fallback
    best_id, best_score = None, 0.0
    for cid, convo in _active_conversations.items():
        score = _cosine_similarity(embedding, convo["embedding"])
        if score > best_score:
            best_id, best_score = cid, score

    if best_id and best_score >= SIMILARITY_THRESHOLD:
        convo = _active_conversations[best_id]
        # nudge the thread's embedding toward this new message
        convo["embedding"] = [e * 0.7 + n * 0.3 for e, n in zip(convo["embedding"], embedding)]
        convo["last_seen"] = message_time
        return best_id, convo["topic"]

    new_id = uuid.uuid4().hex[:8]
    topic = _generate_topic_label(message_text)
    _active_conversations[new_id] = {
        "topic": topic,
        "embedding": embedding,
        "last_seen": message_time,
    }
    return new_id, topic
