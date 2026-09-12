"""Adversarial challenge test suite for Milestone M3: slack_bot.py and time_utils.py.

Evaluates:
1. Malformed message payloads, missing keys, None texts, non-string types, and timestamp parsing.
2. Summary query variants: case variations, leading/trailing whitespace, multi-word ranges, bot mentions.
3. Message subtype noise rejection: verifies no subtypes, bots, or empty messages are ingested into Sheets.
4. Thread grouping isolation: guarantees threaded messages do not pollute global semantic clustering.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest

from slack_bolt import App
import conversation_tracker
from slack_bot import SlackSocketListener
import time_utils


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.auth_test.return_value = {
        "ok": True,
        "team": "Adversarial Team",
        "team_id": "T_ADV_123",
        "user": "smart_summerizer",
        "user_id": "U_BOT_ADV",
        "bot_id": "B_BOT_ADV",
    }
    client.users_info.return_value = {
        "ok": True,
        "user": {
            "id": "U_USER_ADV",
            "name": "challenger",
            "real_name": "Empirical Challenger",
            "profile": {"display_name": "Challenger"},
        },
    }
    client.chat_getPermalink.return_value = {
        "ok": True,
        "permalink": "https://adversarial.slack.com/archives/C_ADV/p17260000009999",
    }
    client.chat_postMessage.return_value = {
        "ok": True,
        "ts": "1726000001.0001",
        "channel": "C_ADV",
    }
    client.reactions_add.return_value = {"ok": True}
    return client


@pytest.fixture
def listener(mock_client):
    inst = SlackSocketListener(
        bot_token="xoxb-adv-test-token-12345",
        app_token="xapp-adv-test-token-12345",
        channel_id=None,
        auto_react_emoji="eyes",
        token_verification_enabled=False,
    )
    inst.app._client = mock_client
    inst._bot_user_id = "U_BOT_ADV"
    return inst


# ==============================================================================
# Challenge Area 1: Malformed Payloads & Missing Keys
# ==============================================================================

@pytest.mark.parametrize("payload,desc", [
    ({}, "completely empty dictionary"),
    ({"type": "message"}, "type only without text or user"),
    ({"type": "message", "channel": "C123"}, "missing text and user"),
    ({"type": "message", "text": ""}, "empty string text"),
    ({"type": "message", "text": "   \n\t  "}, "whitespace-only text"),
    ({"type": "message", "text": "Task", "user": None}, "user is None"),
    ({"type": "message", "text": "Task", "channel": None}, "channel is None"),
    ({"type": "message", "text": "Task", "ts": None}, "ts is None"),
    ({"type": "message", "text": "Task", "ts": "not_a_float"}, "invalid float ts"),
    ({"type": "message", "text": "Task", "ts": "-100.5"}, "negative timestamp"),
    ({"type": "message", "text": "Task", "thread_ts": None}, "explicit thread_ts None"),
    ({"type": "message", "text": "Task", "thread_ts": ""}, "explicit thread_ts empty string"),
])
def test_malformed_payload_resilience(listener, payload, desc, monkeypatch):
    """Verify listener handles malformed payloads without crashing or uncaught exceptions."""
    mock_append = MagicMock()
    mock_extract = MagicMock(return_value={
        "category": "Testing",
        "notes": "Note",
        "action_items": [],
        "deadline": None,
    })
    monkeypatch.setattr("sheets_service.append_record", mock_append)
    monkeypatch.setattr("ai_extractor.extract", mock_extract)
    monkeypatch.setattr("conversation_tracker.get_or_create_conversation", MagicMock(return_value=("cid", "Topic")))

    # Must not raise an uncaught exception
    listener.handle_message(payload)


def test_payload_none_text_handling(listener, monkeypatch, caplog):
    """Verify that a message with text=None is handled without crashing, logging to Sheets, or emitting error logs."""
    mock_append = MagicMock()
    monkeypatch.setattr("sheets_service.append_record", mock_append)

    event = {
        "type": "message",
        "channel": "C123",
        "user": "U123",
        "text": None,
        "ts": "1726000000.0001",
    }
    with caplog.at_level("ERROR", logger="slack_bot"):
        listener.handle_message(event)
    mock_append.assert_not_called()
    assert "Error processing message event" not in caplog.text
    assert "AttributeError" not in caplog.text


# ==============================================================================
# Challenge Area 2: Summary Query Variants & Natural Language Ranges
# ==============================================================================

@pytest.mark.parametrize("query,expected_label", [
    ("!summarize", "today"),
    ("!SUMMARIZE", "today"),
    ("!Summarize", "today"),
    ("!sUmMaRiZe", "today"),
    ("   !summarize   ", "today"),
    ("!summarize   today", "today"),
    ("!summarize TODAY", "today"),
    ("!summarize yesterday", "yesterday"),
    ("!summarize YESTERDAY", "yesterday"),
    ("!summarize   Yesterday   ", "yesterday"),
    ("!summarize week", "the past week"),
    ("!summarize WEEK", "the past week"),
    ("!summarize this week", "the past week"),
    ("!summarize past week", "the past week"),
    ("!summarize month", "the past month"),
    ("!summarize MONTH", "the past month"),
    ("!summarize past month", "the past month"),
    ("!summarize last month", "the past month"),
    ("<@U_BOT_ADV> summarize", "today"),
    ("<@U_BOT_ADV> summarize week", "the past week"),
    ("<@U_BOT_ADV|smart_summerizer> summarize month", "the past month"),
    ("<@W99999> summarize yesterday", "yesterday"),
    ("<@B88888> summarize", "today"),
    ("@bot summarize", "today"),
    ("@bot summarize week", "the past week"),
    ("@smart_summerizer summarize month", "the past month"),
    ("   <@U_BOT_ADV>   summarize   today   ", "today"),
    ("@bot: summarize", "today"),
    ("<@U_BOT_ADV>: summarize", "today"),
    ("<@U_BOT_ADV>: summarize today", "today"),
    ("!summarize last 7 days", "the past week"),
    ("!summarize past 30 days", "the past month"),
])
def test_summary_query_variants_parsing_and_no_sheets_ingestion(listener, query, expected_label, monkeypatch):
    """Verify summary queries across case, whitespace, mentions, and ranges route to summary and never to Sheets."""
    mock_get_records = MagicMock(return_value=[])
    mock_build_summary = MagicMock(return_value=f"Summary for {expected_label}")
    mock_append = MagicMock()

    monkeypatch.setattr("sheets_service.get_records_in_range", mock_get_records)
    monkeypatch.setattr("ai_extractor.build_summary", mock_build_summary)
    monkeypatch.setattr("sheets_service.append_record", mock_append)

    event = {
        "type": "message",
        "channel": "C_ADV",
        "user": "U_USER_ADV",
        "text": query,
        "ts": "1726000000.0001",
    }
    listener.handle_message(event)

    # Must build summary and post to Slack
    mock_build_summary.assert_called_once_with([], expected_label)
    listener.app.client.chat_postMessage.assert_called_once()
    args, kwargs = listener.app.client.chat_postMessage.call_args
    assert f"Summary for {expected_label}" in kwargs["text"]

    # Must NOT ingest into Google Sheets
    mock_append.assert_not_called()


def test_summary_query_with_colon_mention(listener, monkeypatch):
    """Verify mention with trailing colon triggers summary post and is NOT appended to Sheets."""
    mock_get_records = MagicMock(return_value=[])
    mock_build_summary = MagicMock(return_value="Summary for today")
    mock_append = MagicMock()

    monkeypatch.setattr("sheets_service.get_records_in_range", mock_get_records)
    monkeypatch.setattr("ai_extractor.build_summary", mock_build_summary)
    monkeypatch.setattr("sheets_service.append_record", mock_append)

    assert time_utils.is_summary_request("<@U_BOT>: summarize") is True
    assert time_utils.is_summary_request("<@U_BOT>: summarize today") is True

    event = {
        "type": "message",
        "channel": "C_ADV",
        "user": "U_USER_ADV",
        "text": "<@U_BOT>: summarize today",
        "ts": "1726000000.0001",
    }
    listener.handle_message(event)

    mock_build_summary.assert_called_once_with([], "today")
    listener.app.client.chat_postMessage.assert_called_once()
    mock_append.assert_not_called()


def test_multi_word_range_last_7_days(monkeypatch):
    """Verify time_utils.parse_time_range behavior for 'last 7 days' resolves to 7-day range and past week label."""
    now = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start, end, label = time_utils.parse_time_range("last 7 days", now=now)

    assert label == "the past week"
    assert start == today_start - timedelta(days=7)
    assert end == now

    start_w, end_w, label_w = time_utils.parse_time_range("this week", now=now)
    assert label_w == "the past week"
    assert start_w == today_start - timedelta(days=7)


# ==============================================================================
# Challenge Area 3: Subtype Noise Rejection
# ==============================================================================

@pytest.mark.parametrize("subtype", [
    "bot_message",
    "message_changed",
    "message_deleted",
    "channel_join",
    "channel_leave",
    "channel_topic",
    "channel_purpose",
    "channel_name",
    "channel_archive",
    "channel_unarchive",
    "file_share",
    "file_comment",
    "pinned_item",
    "unpinned_item",
    "me_message",
    "thread_broadcast",
    "ekm_access_denied",
])
def test_all_subtypes_completely_ignored(listener, subtype, monkeypatch):
    """Verify that every Slack message subtype is dropped and never reaches Sheets or Calendar."""
    mock_append = MagicMock()
    mock_create_event = MagicMock()
    mock_extract = MagicMock()

    monkeypatch.setattr("sheets_service.append_record", mock_append)
    monkeypatch.setattr("calendar_service.create_event", mock_create_event)
    monkeypatch.setattr("ai_extractor.extract", mock_extract)

    event = {
        "type": "message",
        "subtype": subtype,
        "channel": "C_ADV",
        "user": "U_USER_ADV",
        "text": f"Crucial contract due by 2026-09-30 under subtype {subtype}",
        "ts": "1726000000.0001",
    }
    listener.handle_message(event)

    mock_extract.assert_not_called()
    mock_append.assert_not_called()
    mock_create_event.assert_not_called()
    listener.app.client.reactions_add.assert_not_called()


# ==============================================================================
# Challenge Area 4: Thread Grouping Isolation from Semantic Clustering
# ==============================================================================

def test_thread_messages_do_not_pollute_active_conversations(listener, monkeypatch):
    """Empirically verify that threaded messages do not pollute global semantic clustering.

    Scenario:
    1. Unthreaded message M1 (Lunch topic) arrives and establishes conversation in _active_conversations.
    2. Multiple threaded replies arrive under thread_ts="1726000888.0001" discussing an entirely different topic (Budgets).
    3. Verify _active_conversations is NOT mutated in count, embedding, or last_seen by the threaded messages.
    4. Unthreaded message M2 arrives (Lunch follow-up) and correctly joins M1's conversation without topic pollution.
    """
    conversation_tracker._active_conversations.clear()

    mock_append = MagicMock()
    monkeypatch.setattr("sheets_service.append_record", mock_append)
    monkeypatch.setattr("ai_extractor.extract", MagicMock(return_value={
        "category": "General", "notes": "", "action_items": [], "deadline": None
    }))

    # Deterministic embeddings: lunch -> [1.0, 0.0], budget -> [0.0, 1.0]
    def fake_embed(text: str) -> list[float]:
        if "lunch" in text.lower():
            return [1.0, 0.0]
        elif "budget" in text.lower():
            return [0.0, 1.0]
        return [0.5, 0.5]

    monkeypatch.setattr("conversation_tracker._embed", fake_embed)
    monkeypatch.setattr("conversation_tracker._call_gemini", MagicMock(return_value="Thread Topic"))

    t1 = datetime(2026, 9, 11, 10, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 9, 11, 10, 15, 0, tzinfo=timezone.utc)
    t3 = datetime(2026, 9, 11, 10, 30, 0, tzinfo=timezone.utc)

    # 1. First unthreaded message (lunch)
    listener.handle_message({
        "type": "message",
        "text": "Hey everyone, who wants lunch?",
        "channel": "C_ADV",
        "user": "U_USER_1",
        "ts": str(t1.timestamp()),
        "thread_ts": None,
    })
    rec1 = mock_append.call_args[0][0]
    cid1 = rec1["Conversation ID"]
    assert cid1 in conversation_tracker._active_conversations

    # Snapshot tracker state
    keys_before = set(conversation_tracker._active_conversations.keys())
    emb_before = list(conversation_tracker._active_conversations[cid1]["embedding"])
    last_seen_before = conversation_tracker._active_conversations[cid1]["last_seen"]

    # 2. Ingest 5 threaded messages with completely different content (Budget review)
    thread_ts = "1726000888.0001"
    for i in range(5):
        listener.handle_message({
            "type": "message",
            "text": f"Threaded budget remark {i}",
            "channel": "C_ADV",
            "user": f"U_USER_TH_{i}",
            "ts": str(t2.timestamp() + i),
            "thread_ts": thread_ts,
        })
        rec_th = mock_append.call_args[0][0]
        assert rec_th["Conversation ID"] == f"thread_{thread_ts}"

    # 3. Assert tracker state remained strictly untouched by threaded messages
    keys_after = set(conversation_tracker._active_conversations.keys())
    emb_after = list(conversation_tracker._active_conversations[cid1]["embedding"])
    last_seen_after = conversation_tracker._active_conversations[cid1]["last_seen"]

    assert keys_before == keys_after, "Threaded messages added entries to _active_conversations!"
    assert emb_before == emb_after, "Threaded messages mutated channel conversation embedding!"
    assert last_seen_before == last_seen_after, "Threaded messages updated last_seen timestamp!"

    # 4. Ingest second unthreaded message (lunch reply)
    listener.handle_message({
        "type": "message",
        "text": "I am down for lunch at 12!",
        "channel": "C_ADV",
        "user": "U_USER_2",
        "ts": str(t3.timestamp()),
        "thread_ts": None,
    })
    rec2 = mock_append.call_args[0][0]
    cid2 = rec2["Conversation ID"]

    # Must join the same conversation cid1
    assert cid2 == cid1, f"Unthreaded message should join conversation {cid1}, but got {cid2}"
