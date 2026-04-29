"""
ITSM tool integrations for ServiceNow and Jira.
Handles ticket creation, status updates, and approval workflows.
All HTTP calls use async httpx with retry logic for transient failures.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Optional

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from orchestrator.models import WorkflowState, WorkflowStatus

logger = structlog.get_logger(__name__)


class ITSMConnector(ABC):
    """Base class for ITSM integrations."""

    @abstractmethod
    async def create_change_request(self, state: WorkflowState) -> str:
        """Create a change request and return its ID."""
        ...

    @abstractmethod
    async def update_status(self, ticket_id: str, status: str, notes: str = "") -> None:
        """Update the status of an existing ticket."""
        ...

    @abstractmethod
    async def check_approval(self, ticket_id: str) -> bool:
        """Check if a change request has been approved."""
        ...

    @abstractmethod
    async def attach_results(self, ticket_id: str, results: dict) -> None:
        """Attach provisioning results to the ticket."""
        ...


class ServiceNowConnector(ITSMConnector):
    """
    ServiceNow REST API integration via Table API.
    Manages change requests through the standard change management process.
    """

    def __init__(self, instance_url: str, username: str, password: str):
        self.base_url = f"https://{instance_url}/api/now"
        self.auth = (username, password)
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            auth=self.auth,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=30.0,
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
    async def create_change_request(self, state: WorkflowState) -> str:
        request = state.request
        payload = {
            "type": "standard",
            "short_description": (
                f"Infrastructure Provisioning: {request.project_name} "
                f"({request.environment.value})"
            ),
            "description": self._build_description(state),
            "category": "Infrastructure",
            "subcategory": "VM Provisioning",
            "assignment_group": "Cloud Infrastructure",
            "risk": "moderate" if request.environment.value == "prod" else "low",
            "impact": "2" if request.environment.value == "prod" else "3",
            "u_automation_id": state.workflow_id,
        }

        resp = await self._client.post("/table/change_request", json=payload)
        resp.raise_for_status()
        result = resp.json()["result"]
        ticket_id = result["number"]

        logger.info(
            "snow.change_request.created",
            ticket=ticket_id,
            workflow=state.workflow_id,
        )
        return ticket_id

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
    async def update_status(self, ticket_id: str, status: str, notes: str = "") -> None:
        # Map workflow status to SNOW change states
        state_map = {
            "provisioning": "implement",
            "completed": "review",
            "failed": "canceled",
        }
        snow_state = state_map.get(status, status)

        payload = {"state": snow_state, "work_notes": notes}
        resp = await self._client.patch(
            f"/table/change_request?sysparm_query=number={ticket_id}",
            json=payload,
        )
        resp.raise_for_status()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
    async def check_approval(self, ticket_id: str) -> bool:
        resp = await self._client.get(
            f"/table/change_request?sysparm_query=number={ticket_id}"
            "&sysparm_fields=approval"
        )
        resp.raise_for_status()
        results = resp.json().get("result", [])
        if results:
            return results[0].get("approval") == "approved"
        return False

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
    async def attach_results(self, ticket_id: str, results: dict) -> None:
        import json

        payload = {
            "table_name": "change_request",
            "table_sys_id": ticket_id,
            "file_name": "provisioning_results.json",
            "content_type": "application/json",
        }
        # SNOW attachment API expects multipart
        resp = await self._client.post(
            "/attachment/file",
            data=payload,
            content=json.dumps(results, indent=2).encode(),
        )
        resp.raise_for_status()

    def _build_description(self, state: WorkflowState) -> str:
        request = state.request
        tiers = request.tier_map()
        lines = [
            f"Project: {request.project_name}",
            f"Environment: {request.environment.value}",
            f"Requestor: {request.requestor}",
            f"Workflow ID: {state.workflow_id}",
            "",
            "Requested Resources:",
        ]
        for tier_name, spec in tiers.items():
            lines.append(
                f"  {tier_name}: {spec.vm_count}x {spec.vm_size.value} "
                f"({spec.disk_gb}GB disk)"
            )
        return "\n".join(lines)

    async def close(self):
        await self._client.aclose()


class JiraConnector(ITSMConnector):
    """
    Jira Cloud integration for teams using Jira Service Management.
    Creates issues in a designated project and tracks through workflow transitions.
    """

    def __init__(self, base_url: str, email: str, api_token: str, project_key: str):
        self.project_key = project_key
        self._client = httpx.AsyncClient(
            base_url=f"{base_url}/rest/api/3",
            auth=(email, api_token),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=30.0,
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
    async def create_change_request(self, state: WorkflowState) -> str:
        request = state.request
        payload = {
            "fields": {
                "project": {"key": self.project_key},
                "summary": (
                    f"[Auto] Provision {request.project_name} - "
                    f"{request.environment.value}"
                ),
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": self._build_description(state)}
                            ],
                        }
                    ],
                },
                "issuetype": {"name": "Change"},
                "labels": ["auto-provisioned", request.environment.value],
            }
        }

        resp = await self._client.post("/issue", json=payload)
        resp.raise_for_status()
        issue_key = resp.json()["key"]

        logger.info("jira.issue.created", issue=issue_key, workflow=state.workflow_id)
        return issue_key

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
    async def update_status(self, ticket_id: str, status: str, notes: str = "") -> None:
        # Add comment with status update
        if notes:
            comment_payload = {
                "body": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": notes}],
                        }
                    ],
                }
            }
            await self._client.post(f"/issue/{ticket_id}/comment", json=comment_payload)

    async def check_approval(self, ticket_id: str) -> bool:
        resp = await self._client.get(f"/issue/{ticket_id}?fields=status")
        resp.raise_for_status()
        status = resp.json()["fields"]["status"]["name"].lower()
        return status in ("approved", "in progress")

    async def attach_results(self, ticket_id: str, results: dict) -> None:
        import json
        import io

        # Jira attachments use multipart form data
        files = {
            "file": ("provisioning_results.json", json.dumps(results, indent=2), "application/json")
        }
        headers = {"X-Atlassian-Token": "no-check"}
        await self._client.post(
            f"/issue/{ticket_id}/attachments",
            files=files,
            headers=headers,
        )

    def _build_description(self, state: WorkflowState) -> str:
        request = state.request
        tiers = request.tier_map()
        parts = [
            f"Project: {request.project_name}",
            f"Environment: {request.environment.value}",
            f"Requestor: {request.requestor}",
        ]
        for tier_name, spec in tiers.items():
            parts.append(f"{tier_name}: {spec.vm_count}x {spec.vm_size.value}")
        return " | ".join(parts)

    async def close(self):
        await self._client.aclose()


def create_itsm_connector(config: dict) -> Optional[ITSMConnector]:
    """Factory function to instantiate the configured ITSM connector."""
    provider = config.get("provider", "none")

    if provider == "servicenow":
        return ServiceNowConnector(
            instance_url=config["instance_url"],
            username=config["username"],
            password=config["password"],
        )
    elif provider == "jira":
        return JiraConnector(
            base_url=config["base_url"],
            email=config["email"],
            api_token=config["api_token"],
            project_key=config["project_key"],
        )
    else:
        logger.info("itsm.disabled", reason="no provider configured")
        return None
