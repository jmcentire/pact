"""Slack integration — notifications for key events.

Posts to a Slack channel when:
- Decomposition is complete
- Component implementation succeeds/fails
- Integration succeeds/fails
- Human intervention needed
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


class SlackNotifier:
    """Slack notifier with optional bot token for bidirectional communication."""

    def __init__(
        self,
        webhook_url: str = "",
        bot_token: str = "",
        channel: str = "",
    ) -> None:
        self._webhook_url = webhook_url or os.environ.get("CF_SLACK_WEBHOOK", "")
        self._bot_token = bot_token or os.environ.get("PACT_SLACK_BOT_TOKEN", "")
        self._channel = channel

    @property
    def configured(self) -> bool:
        return bool(self._webhook_url) or bool(self._bot_token)

    @property
    def read_configured(self) -> bool:
        """True only when bot token is set (enables reading threads)."""
        return bool(self._bot_token)

    async def notify(self, message: str, channel: str = "") -> bool:
        """Post a message to Slack via webhook. Returns success."""
        if not self.configured:
            logger.debug("Slack not configured — skipping notification")
            return False

        try:
            import httpx
        except ImportError:
            logger.warning("httpx not installed — skipping Slack notification")
            return False

        payload: dict = {"text": message}
        if channel:
            payload["channel"] = channel

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(self._webhook_url, json=payload)
                return resp.status_code == 200
        except Exception as e:
            logger.warning("Slack notification failed: %s", e)
            return False

    async def notify_decomposition_complete(
        self,
        project_name: str,
        component_count: int,
    ) -> bool:
        return await self.notify(
            f":building_construction: *{project_name}* decomposition complete: "
            f"{component_count} components identified"
        )

    async def notify_component_complete(
        self,
        project_name: str,
        component_id: str,
        test_results: str,
    ) -> bool:
        return await self.notify(
            f":white_check_mark: *{project_name}* component `{component_id}` "
            f"passed: {test_results}"
        )

    async def notify_component_failed(
        self,
        project_name: str,
        component_id: str,
        error: str,
    ) -> bool:
        return await self.notify(
            f":x: *{project_name}* component `{component_id}` failed: {error}"
        )

    async def notify_human_needed(
        self,
        project_name: str,
        reason: str,
    ) -> bool:
        return await self.notify(
            f":raising_hand: *{project_name}* needs human intervention: {reason}"
        )

    async def post_message(
        self,
        channel: str,
        text: str,
        thread_ts: str = "",
    ) -> dict:
        """Post a message via Slack Bot API. Returns {ok, ts, channel}."""
        if not self.read_configured:
            return {"ok": False, "ts": "", "channel": ""}

        try:
            import httpx
        except ImportError:
            return {"ok": False, "ts": "", "channel": ""}

        payload: dict = {"channel": channel, "text": text}
        if thread_ts:
            payload["thread_ts"] = thread_ts

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://slack.com/api/chat.postMessage",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self._bot_token}",
                        "Content-Type": "application/json",
                    },
                )
                data = resp.json()
        except Exception as e:
            logger.warning("Slack post_message failed: %s", e)
            return {"ok": False, "ts": "", "channel": ""}

        return {
            "ok": data.get("ok", False),
            "ts": data.get("ts", ""),
            "channel": data.get("channel", ""),
        }

    async def get_thread_replies(
        self,
        channel: str,
        thread_ts: str,
        since_ts: str = "",
    ) -> list[dict]:
        """Fetch replies in a Slack thread. Returns [{user, text, ts}]."""
        if not self.read_configured:
            return []

        try:
            import httpx
        except ImportError:
            return []

        params: dict = {
            "channel": channel,
            "ts": thread_ts,
        }

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://slack.com/api/conversations.replies",
                    params=params,
                    headers={
                        "Authorization": f"Bearer {self._bot_token}",
                    },
                )
                data = resp.json()
        except Exception as e:
            logger.warning("Slack get_thread_replies failed: %s", e)
            return []

        if not data.get("ok", False):
            return []

        messages = data.get("messages", [])
        # Skip the first message (parent) — only return replies
        replies = messages[1:] if len(messages) > 1 else []

        result = [
            {
                "user": m.get("user", ""),
                "text": m.get("text", ""),
                "ts": m.get("ts", ""),
            }
            for m in replies
        ]

        if since_ts:
            result = [r for r in result if r["ts"] > since_ts]

        return result
