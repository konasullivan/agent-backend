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
import math
import uuid
from datetime import datetime, timedelta

from ai_extractor import client, _call_gemini

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
    _purge_expired(message_time)
    embedding = _embed(message_text)

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
