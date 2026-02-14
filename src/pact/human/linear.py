"""Linear integration — issue creation and tracking.

Maps decomposition tree to parent/child Linear issues.
Updates issue status as components progress.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class LinearClient:
    """Minimal Linear API client for issue management."""

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key or os.environ.get("LINEAR_API_KEY", "")
        self._base_url = "https://api.linear.app/graphql"

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    async def create_issue(
        self,
        title: str,
        description: str = "",
        team_id: str = "",
        parent_id: str = "",
        labels: list[str] | None = None,
    ) -> dict:
        """Create a Linear issue. Returns {id, identifier, url}."""
        if not self.configured:
            logger.warning("Linear not configured — skipping issue creation")
            return {"id": "", "identifier": "", "url": ""}

        try:
            import httpx
        except ImportError:
            logger.warning("httpx not installed — skipping Linear integration")
            return {"id": "", "identifier": "", "url": ""}

        mutation = """
        mutation CreateIssue($input: IssueCreateInput!) {
            issueCreate(input: $input) {
                success
                issue {
                    id
                    identifier
                    url
                }
            }
        }
        """

        variables: dict = {
            "input": {
                "title": title,
                "description": description,
            }
        }
        if team_id:
            variables["input"]["teamId"] = team_id
        if parent_id:
            variables["input"]["parentId"] = parent_id

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self._base_url,
                json={"query": mutation, "variables": variables},
                headers={
                    "Authorization": self._api_key,
                    "Content-Type": "application/json",
                },
            )
            data = resp.json()

        result = data.get("data", {}).get("issueCreate", {}).get("issue", {})
        return {
            "id": result.get("id", ""),
            "identifier": result.get("identifier", ""),
            "url": result.get("url", ""),
        }

    async def update_issue_status(
        self,
        issue_id: str,
        status: str,
    ) -> bool:
        """Update a Linear issue's status. Returns success."""
        if not self.configured:
            return False

        try:
            import httpx
        except ImportError:
            return False

        mutation = """
        mutation UpdateIssue($id: String!, $input: IssueUpdateInput!) {
            issueUpdate(id: $id, input: $input) {
                success
            }
        }
        """

        # Note: status must be a state ID in Linear's API
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self._base_url,
                json={
                    "query": mutation,
                    "variables": {
                        "id": issue_id,
                        "input": {"stateId": status},
                    },
                },
                headers={
                    "Authorization": self._api_key,
                    "Content-Type": "application/json",
                },
            )
            data = resp.json()

        return data.get("data", {}).get("issueUpdate", {}).get("success", False)


async def sync_tree_to_linear(
    client: LinearClient,
    tree_path: Path,
    team_id: str,
) -> dict[str, str]:
    """Create Linear issues mirroring the decomposition tree.

    Returns:
        Dict of component_id -> linear_issue_id.
    """
    if not client.configured:
        return {}

    tree_data = json.loads(tree_path.read_text())
    nodes = tree_data.get("nodes", {})
    issue_map: dict[str, str] = {}

    # Create issues in topological order (roots first)
    root_id = tree_data.get("root_id", "")

    async def create_subtree(node_id: str, parent_issue_id: str = "") -> None:
        node = nodes.get(node_id, {})
        result = await client.create_issue(
            title=f"[CF] {node.get('name', node_id)}",
            description=node.get("description", ""),
            team_id=team_id,
            parent_id=parent_issue_id,
        )
        issue_map[node_id] = result["id"]

        for child_id in node.get("children", []):
            await create_subtree(child_id, result["id"])

    if root_id:
        await create_subtree(root_id)

    return issue_map
