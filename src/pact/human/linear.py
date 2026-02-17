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

    async def get_issue(self, issue_id: str) -> dict:
        """Get a Linear issue by ID. Returns {id, title, description, state, comments}."""
        if not self.configured:
            return {}

        try:
            import httpx
        except ImportError:
            return {}

        query = """
        query GetIssue($id: String!) {
            issue(id: $id) {
                id
                title
                description
                state { name }
                comments {
                    nodes {
                        body
                        createdAt
                        user { name }
                    }
                }
            }
        }
        """

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self._base_url,
                    json={"query": query, "variables": {"id": issue_id}},
                    headers={
                        "Authorization": self._api_key,
                        "Content-Type": "application/json",
                    },
                )
                data = resp.json()
        except Exception:
            logger.debug("Failed to get Linear issue %s", issue_id)
            return {}

        issue = (data or {}).get("data") or {}
        issue = issue.get("issue")
        if not issue:
            return {}

        comments_raw = issue.get("comments", {}).get("nodes", [])
        comments = [
            {
                "body": c.get("body", ""),
                "createdAt": c.get("createdAt", ""),
                "userName": (c.get("user") or {}).get("name", ""),
            }
            for c in comments_raw
        ]

        return {
            "id": issue.get("id", ""),
            "title": issue.get("title", ""),
            "description": issue.get("description", ""),
            "state": (issue.get("state") or {}).get("name", ""),
            "comments": comments,
        }

    async def get_issue_comments(
        self, issue_id: str, since: str = "",
    ) -> list[dict]:
        """Get comments on a Linear issue, optionally filtered by timestamp."""
        if not self.configured:
            return []

        try:
            import httpx
        except ImportError:
            return []

        query = """
        query GetComments($id: String!) {
            issue(id: $id) {
                comments {
                    nodes {
                        body
                        createdAt
                        user { name }
                    }
                }
            }
        }
        """

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self._base_url,
                    json={"query": query, "variables": {"id": issue_id}},
                    headers={
                        "Authorization": self._api_key,
                        "Content-Type": "application/json",
                    },
                )
                data = resp.json()
        except Exception:
            logger.debug("Failed to get comments for %s", issue_id)
            return []

        nodes = (
            ((data or {}).get("data") or {})
            .get("issue") or {}
        ).get("comments", {}).get("nodes", [])

        comments = [
            {
                "body": c.get("body", ""),
                "createdAt": c.get("createdAt", ""),
                "userName": (c.get("user") or {}).get("name", ""),
            }
            for c in nodes
        ]

        if since:
            comments = [c for c in comments if c["createdAt"] > since]

        return comments

    async def add_comment(self, issue_id: str, body: str) -> bool:
        """Post a markdown comment on a Linear issue."""
        if not self.configured:
            return False

        try:
            import httpx
        except ImportError:
            return False

        mutation = """
        mutation AddComment($input: CommentCreateInput!) {
            commentCreate(input: $input) {
                success
            }
        }
        """

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self._base_url,
                    json={
                        "query": mutation,
                        "variables": {
                            "input": {
                                "issueId": issue_id,
                                "body": body,
                            },
                        },
                    },
                    headers={
                        "Authorization": self._api_key,
                        "Content-Type": "application/json",
                    },
                )
                data = resp.json()
        except Exception:
            logger.debug("Failed to add comment to %s", issue_id)
            return False

        return data.get("data", {}).get("commentCreate", {}).get("success", False)

    async def search_issues(
        self, query: str, team_id: str = "", limit: int = 5,
    ) -> list[dict]:
        """Search Linear issues. Returns [{id, identifier, title, description, state}]."""
        if not self.configured:
            return []

        try:
            import httpx
        except ImportError:
            return []

        gql = """
        query SearchIssues($query: String!, $first: Int) {
            issueSearch(query: $query, first: $first) {
                nodes {
                    id
                    identifier
                    title
                    description
                    state { name }
                }
            }
        }
        """

        search_query = query
        if team_id:
            search_query = f"team:{team_id} {query}"

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self._base_url,
                    json={
                        "query": gql,
                        "variables": {"query": search_query, "first": limit},
                    },
                    headers={
                        "Authorization": self._api_key,
                        "Content-Type": "application/json",
                    },
                )
                data = resp.json()
        except Exception:
            logger.debug("Failed to search Linear issues")
            return []

        nodes = data.get("data", {}).get("issueSearch", {}).get("nodes", [])
        return [
            {
                "id": n.get("id", ""),
                "identifier": n.get("identifier", ""),
                "title": n.get("title", ""),
                "description": n.get("description", ""),
                "state": (n.get("state") or {}).get("name", ""),
            }
            for n in nodes
        ]

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
