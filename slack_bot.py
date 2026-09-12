"""Slack Socket Mode bot for real-time channel message ingestion and summary generation."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
import signal
import sys
from typing import Any, Callable, Optional

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

import ai_extractor
import calendar_service
import config
import conversation_tracker
import sheets_service
import time_utils

# All logging strictly to sys.stderr
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


class SlackSocketListener:
    """Manages Slack Socket Mode connection, event filtering, and ingestion pipeline."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        app_token: Optional[str] = None,
        channel_id: Optional[str] = None,
        auto_react_emoji: str = "eyes",
        token_verification_enabled: bool = True,
    ) -> None:
        self.bot_token = bot_token or config.SLACK_BOT_TOKEN
        self.app_token = app_token or config.SLACK_APP_TOKEN
        self.channel_id = channel_id or config.SLACK_CHANNEL_ID
        self.auto_react_emoji = auto_react_emoji

        if not self.bot_token or not self.bot_token.startswith("xoxb-"):
            raise ValueError(
                "SLACK_BOT_TOKEN is required and must start with 'xoxb-'. Check .env configuration."
            )
        if not self.app_token or not self.app_token.startswith("xapp-"):
            raise ValueError(
                "SLACK_APP_TOKEN is required and must start with 'xapp-'. Check .env configuration."
            )

        self.app = App(
            token=self.bot_token,
            token_verification_enabled=token_verification_enabled,
        )
        self.handler: Optional[SocketModeHandler] = None
        self._user_cache: dict[str, str] = {}
        self._thread_topics: dict[str, str] = {}
        self._active_events: dict[str, str] = {}
        self._bot_user_id: Optional[str] = None

        self._register_handlers()

        if token_verification_enabled:
            try:
                self.test_connection()
            except Exception as exc:
                logger.debug("Initial connection test skipped or failed: %s", exc)

    def test_connection(self) -> dict[str, Any]:
        """Test Slack API authentication and cache bot identity."""
        auth_response = self.app.client.auth_test()
        self._bot_user_id = auth_response.get("user_id")
        return {
            "ok": auth_response.get("ok"),
            "team": auth_response.get("team"),
            "team_id": auth_response.get("team_id"),
            "user": auth_response.get("user"),
            "bot_id": auth_response.get("bot_id"),
            "user_id": self._bot_user_id,
        }

    def _resolve_user_name(self, user_id: str) -> str:
        """Resolve a Slack user ID to real or display name with in-memory caching."""
        if not user_id:
            return "Unknown"
        if user_id in self._user_cache:
            return self._user_cache[user_id]

        try:
            res = self.app.client.users_info(user=user_id)
            if res.get("ok"):
                user_info = res.get("user", {})
                name = (
                    user_info.get("real_name")
                    or user_info.get("profile", {}).get("display_name")
                    or user_info.get("name")
                    or user_id
                )
                self._user_cache[user_id] = name
                return name
        except Exception as exc:
            logger.debug("Failed to resolve user name for %s: %s", user_id, exc)

        self._user_cache[user_id] = user_id or "Unknown"
        return self._user_cache[user_id]

    def _get_permalink(self, channel_id: str, ts: str) -> str:
        """Generate Slack message permalink with graceful synthetic fallback."""
        if not channel_id or not ts:
            return ""
        try:
            res = self.app.client.chat_getPermalink(channel=channel_id, message_ts=ts)
            if res.get("ok"):
                return res.get("permalink", "")
        except Exception as exc:
            logger.debug("Failed to fetch permalink for %s/%s: %s", channel_id, ts, exc)

        clean_ts = ts.replace(".", "")
        return f"https://slack.com/archives/{channel_id}/p{clean_ts}"

    def _resolve_thread_topic(self, thread_ts: str, text: str) -> str:
        """Resolve or generate topic label for a Slack thread."""
        if thread_ts in self._thread_topics:
            return self._thread_topics[thread_ts]

        try:
            topic = conversation_tracker._generate_topic_label(text)
        except Exception as exc:
            logger.debug("Failed to generate topic label for thread %s: %s", thread_ts, exc)
            topic = f"Thread {thread_ts}"

        self._thread_topics[thread_ts] = topic
        return topic

    def _get_recent_channel_context(
        self,
        channel_id: str,
        current_ts: str,
        max_messages: int = 15,
        hours: int = 24,
    ) -> list[dict[str, str]]:
        """Retrieve preceding messages from the past 24 hours in the channel with resolved names."""
        if not channel_id:
            return []
        try:
            oldest_ts = ""
            if current_ts:
                try:
                    c_dt = float(current_ts)
                    oldest_ts = str(c_dt - (hours * 3600))
                except (ValueError, TypeError):
                    pass

            kwargs: dict[str, Any] = {
                "channel": channel_id,
                "limit": max_messages,
            }
            if current_ts:
                kwargs["latest"] = current_ts
                kwargs["inclusive"] = False
            if oldest_ts:
                kwargs["oldest"] = oldest_ts

            res = self.app.client.conversations_history(**kwargs)
            if not res.get("ok"):
                return []

            raw_msgs = res.get("messages", [])
            context: list[dict[str, str]] = []
            for m in reversed(raw_msgs):  # chronological order
                if m.get("subtype") or m.get("bot_id"):
                    continue
                u_id = m.get("user") or ""
                u_name = self._resolve_user_name(u_id)
                m_text = (m.get("text") or "").strip()
                if not m_text:
                    continue
                m_ts = m.get("ts") or ""
                try:
                    m_dt_str = datetime.fromtimestamp(float(m_ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    m_dt_str = ""
                context.append({
                    "author": u_name,
                    "text": m_text,
                    "date": m_dt_str,
                })
            return context
        except Exception as exc:
            logger.debug("Failed to retrieve recent channel context for %s: %s", channel_id, exc)
            return []

    def _register_handlers(self) -> None:
        """Register Slack event listeners on the Bolt App."""

        @self.app.event("message")
        def on_message_event(event: dict[str, Any], say: Callable) -> None:
            self.handle_message(event, say)

    def handle_message(self, event: dict[str, Any], say: Optional[Callable] = None) -> None:
        """Process incoming message events: filter noise, route summaries, or ingest records."""
        try:
            # 1. Event noise filtering
            if event.get("subtype"):
                return
            if event.get("bot_id"):
                return
            user_id = event.get("user") or ""
            if self._bot_user_id and user_id == self._bot_user_id:
                return

            raw_text = event.get("text") or ""
            text = raw_text.strip()
            if not text:
                return

            channel_id = event.get("channel") or ""
            if self.channel_id and channel_id != self.channel_id:
                return

            ts_str = event.get("ts") or ""
            thread_ts = event.get("thread_ts")

            try:
                dt = datetime.fromtimestamp(float(ts_str), tz=timezone.utc)
            except (ValueError, TypeError):
                dt = datetime.now(timezone.utc)

            # 2. Summary query routing
            if time_utils.is_summary_request(text):
                logger.info("Summary query detected: %s", text)
                clean_range_text = time_utils.extract_range_text(text)
                start, end, label = time_utils.parse_time_range(clean_range_text)

                try:
                    records = sheets_service.get_records_in_range(start, end)
                    summary_text = ai_extractor.build_summary(records, label)
                except Exception as exc:
                    logger.error("Error generating summary via MCP/Gemini: %s", exc, exc_info=True)
                    summary_text = "Unable to generate summary at this time due to an upstream service error."

                post_kwargs: dict[str, Any] = {
                    "channel": channel_id,
                    "text": summary_text,
                }
                if thread_ts:
                    post_kwargs["thread_ts"] = thread_ts

                self.app.client.chat_postMessage(**post_kwargs)

                # Acknowledge summary query with reaction
                if self.auto_react_emoji and ts_str and channel_id:
                    try:
                        self.app.client.reactions_add(
                            channel=channel_id,
                            name=self.auto_react_emoji,
                            timestamp=ts_str,
                        )
                    except Exception as exc:
                        logger.debug("Reaction add for summary query failed (ignored): %s", exc)

                return  # Return immediately so summary queries are never logged as records

            # 3. 7-Step User Message Ingestion Flow
            author_name = self._resolve_user_name(user_id)

            # Step 1: Extract structured metadata with 24h channel context
            context_messages = self._get_recent_channel_context(channel_id, ts_str)
            try:
                if context_messages:
                    data = ai_extractor.extract(text, author_name, context_messages=context_messages)
                else:
                    data = ai_extractor.extract(text, author_name)
            except Exception as exc:
                logger.error("ai_extractor.extract failed: %s", exc, exc_info=True)
                data = {
                    "category": "Miscellaneous",
                    "topic": "General",
                    "notes": text[:100],
                    "action_items": [],
                    "deadline": None,
                    "location": None,
                }

            # Step 2: Conversation session grouping
            if thread_ts:
                conversation_id = f"thread_{thread_ts}"
                conversation_topic = self._resolve_thread_topic(thread_ts, text)
            else:
                try:
                    conversation_id, conversation_topic = (
                        conversation_tracker.get_or_create_conversation(text, dt)
                    )
                    # If AI extractor produced a specific contextual topic, prioritize it
                    ai_topic = data.get("topic")
                    if ai_topic and ai_topic != "General":
                        conversation_topic = ai_topic
                except Exception as exc:
                    logger.error("conversation_tracker failed: %s", exc, exc_info=True)
                    conversation_id = f"convo_{ts_str}"
                    conversation_topic = data.get("topic") or "General"

            # Step 3: Subteam mapping
            subteam = config.get_subteam(author_name, user_id=user_id)

            # Step 4: Permalink generation
            link = self._get_permalink(channel_id, ts_str)

            # Step 5: Sheets logging via MCP
            action_items_val = data.get("action_items", [])
            if isinstance(action_items_val, list):
                action_items_str = "; ".join(str(item) for item in action_items_val)
            else:
                action_items_str = str(action_items_val or "")

            record = {
                "Message": text,
                "Category": data.get("category", "Miscellaneous"),
                "Conversation ID": conversation_id,
                "Conversation Topic": conversation_topic,
                "Staff Member": author_name,
                "Subteam": subteam,
                "Date": dt.isoformat(),
                "Notes": data.get("notes", ""),
                "Action Items": action_items_str,
                "Deadline": data.get("deadline") or "",
                "Link": link,
            }

            updated_range = sheets_service.append_record(dict(record))
            logger.info(
                "Logged record to Sheets for author=%s, category=%s",
                author_name,
                record["Category"],
            )

            # Step 6: Calendar event creation if deadline exists
            if record["Deadline"]:
                try:
                    # Prune older incomplete / superseded events before creating new one
                    try:
                        calendar_service.prune_superseded_events(
                            target_date_str=record["Deadline"],
                            new_location=data.get("location") or "",
                        )
                    except Exception as p_exc:
                        logger.debug("Event pruning skipped or failed: %s", p_exc)

                    # Also delete previously active event for this conversation if present
                    if conversation_id in self._active_events:
                        prev_ev_id = self._active_events.get(conversation_id)
                        if prev_ev_id:
                            try:
                                calendar_service.delete_event(prev_ev_id)
                                logger.info(
                                    "Deleted previous event %s for conversation %s",
                                    prev_ev_id,
                                    conversation_id,
                                )
                            except Exception as d_exc:
                                logger.debug("Failed to delete previous event %s: %s", prev_ev_id, d_exc)

                    cal_kwargs: dict[str, Any] = {
                        "summary": data.get("notes") or record["Category"],
                        "description": f"From Slack message by {author_name}: {text}",
                        "date_str": record["Deadline"],
                    }
                    if data.get("location"):
                        cal_kwargs["location"] = data["location"]

                    cal_link = calendar_service.create_event(**cal_kwargs)
                    if cal_link and isinstance(cal_link, str) and ("google.com" in cal_link or "calendar" in cal_link):
                        record["Link"] = cal_link
                        new_ev_id = calendar_service.extract_event_id_from_link(cal_link)
                        if new_ev_id:
                            self._active_events[conversation_id] = new_ev_id
                        logger.info("Created Calendar event (%s) for deadline: %s", cal_link, record["Deadline"])
                        if updated_range and isinstance(updated_range, str):
                            sheets_service.update_record_link(updated_range, cal_link)
                except Exception as exc:
                    logger.error("Failed to create Calendar event: %s", exc, exc_info=True)

            # Step 7: Acknowledgment reaction
            if self.auto_react_emoji and ts_str and channel_id:
                try:
                    self.app.client.reactions_add(
                        channel=channel_id,
                        name=self.auto_react_emoji,
                        timestamp=ts_str,
                    )
                except Exception as exc:
                    logger.debug("Reaction add failed (ignored): %s", exc)

        except Exception as exc:
            logger.error("Error processing message event: %s", exc, exc_info=True)

    def start(self) -> None:
        """Start the Socket Mode listener (blocking)."""
        if not self._bot_user_id:
            try:
                self.test_connection()
            except Exception as exc:
                logger.warning("auth_test in start() failed: %s", exc)

        self.handler = SocketModeHandler(self.app, self.app_token)

        def _handle_signal(sig: int, frame: Any) -> None:
            print(
                f"\n[*] Received signal {sig}. Shutting down Slack Socket Mode listener cleanly...",
                file=sys.stderr,
            )
            self.close()
            sys.exit(0)

        try:
            signal.signal(signal.SIGINT, _handle_signal)
            signal.signal(signal.SIGTERM, _handle_signal)
        except (ValueError, AttributeError):
            # When not running in the main interpreter thread
            pass

        print(
            f"[*] Slack Socket Mode Listener active (Bot User ID: {self._bot_user_id}). Listening for messages...",
            file=sys.stderr,
        )
        self.handler.start()

    def close(self) -> None:
        """Close the Socket Mode connection."""
        if self.handler:
            try:
                self.handler.close()
            except Exception as exc:
                logger.debug("Error closing SocketModeHandler: %s", exc)


def init_slack_app(
    token_verification_enabled: bool = True,
    bot_token: Optional[str] = None,
    app_token: Optional[str] = None,
) -> App:
    """Instantiate and configure the slack_bolt App instance with event handlers."""
    listener = SlackSocketListener(
        bot_token=bot_token,
        app_token=app_token,
        token_verification_enabled=token_verification_enabled,
    )
    return listener.app


def start_slack_bot() -> None:
    """Launch the live Slack bot listener."""
    listener = SlackSocketListener()
    try:
        listener.start()
    except KeyboardInterrupt:
        print("\n[*] Slack listener stopped by user.", file=sys.stderr)
    finally:
        listener.close()


if __name__ == "__main__":
    start_slack_bot()
