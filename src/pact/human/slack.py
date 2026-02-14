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
    """Minimal Slack webhook notifier."""

    def __init__(self, webhook_url: str = "") -> None:
        self._webhook_url = webhook_url or os.environ.get("CF_SLACK_WEBHOOK", "")

    @property
    def configured(self) -> bool:
        return bool(self._webhook_url)

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
