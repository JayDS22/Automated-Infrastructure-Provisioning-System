"""
Test suite for the provisioning orchestrator.
Tests validation logic, workflow state transitions, and API endpoints.
Run with: pytest orchestrator/tests/ -v
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

# Test the data models directly
from orchestrator.models import (
    Environment,
    ProvisionRequest,
    TierSpec,
    VMSize,
    VM_SIZE_SPECS,
    WorkflowState,
    WorkflowStatus,
    WorkflowStep,
)


# -- Model Validation Tests --

class TestProvisionRequest:
    def test_valid_request(self):
        req = ProvisionRequest(
            project_name="test-project",
            environment=Environment.DEV,
            requestor="tester@example.com",
            web_tier=TierSpec(vm_count=1, vm_size=VMSize.SMALL, disk_gb=40),
        )
        assert req.project_name == "test-project"
        assert req.environment == Environment.DEV
        assert req.web_tier.vm_count == 1

    def test_invalid_project_name_uppercase(self):
        with pytest.raises(Exception):
            ProvisionRequest(
                project_name="BadName",
                environment=Environment.DEV,
                requestor="test@example.com",
            )

    def test_invalid_project_name_consecutive_hyphens(self):
        with pytest.raises(Exception):
            ProvisionRequest(
                project_name="bad--name",
                environment=Environment.DEV,
                requestor="test@example.com",
            )

    def test_tier_map_returns_only_specified_tiers(self):
        req = ProvisionRequest(
            project_name="test-proj",
            environment=Environment.DEV,
            requestor="test@example.com",
            web_tier=TierSpec(vm_count=2, vm_size=VMSize.MEDIUM, disk_gb=50),
            db_tier=TierSpec(vm_count=1, vm_size=VMSize.DB_OPTIMIZED, disk_gb=200),
        )
        tiers = req.tier_map()
        assert "web" in tiers
        assert "db" in tiers
        assert "app" not in tiers

    def test_vm_count_bounds(self):
        with pytest.raises(Exception):
            TierSpec(vm_count=0, vm_size=VMSize.SMALL, disk_gb=40)
        with pytest.raises(Exception):
            TierSpec(vm_count=51, vm_size=VMSize.SMALL, disk_gb=40)

    def test_additional_disks_validation(self):
        with pytest.raises(Exception):
            TierSpec(
                vm_count=1,
                vm_size=VMSize.SMALL,
                disk_gb=40,
                additional_disks=[{"no_size": True}],
            )


class TestWorkflowState:
    def _make_state(self):
        req = ProvisionRequest(
            project_name="test-proj",
            environment=Environment.DEV,
            requestor="test@example.com",
            web_tier=TierSpec(vm_count=1, vm_size=VMSize.SMALL, disk_gb=40),
        )
        return WorkflowState(
            request=req,
            steps=[
                WorkflowStep(name="validate"),
                WorkflowStep(name="terraform_plan"),
                WorkflowStep(name="terraform_apply"),
            ],
        )

    def test_initial_status(self):
        state = self._make_state()
        assert state.status == WorkflowStatus.PENDING
        assert len(state.steps) == 3

    def test_mark_step_complete(self):
        state = self._make_state()
        state.mark_step_complete("validate", {"validated": True})
        step = state.steps[0]
        assert step.status == WorkflowStatus.COMPLETED
        assert step.output["validated"] is True
        assert step.completed_at is not None

    def test_mark_step_failed(self):
        state = self._make_state()
        state.mark_step_failed("terraform_plan", "quota exceeded")
        step = state.steps[1]
        assert step.status == WorkflowStatus.FAILED
        assert step.error_message == "quota exceeded"
        assert state.status == WorkflowStatus.FAILED

    def test_current_step(self):
        state = self._make_state()
        state.steps[0].status = WorkflowStatus.COMPLETED
        state.steps[1].status = WorkflowStatus.PROVISIONING
        current = state.current_step()
        assert current.name == "terraform_plan"


class TestVMSizeSpecs:
    def test_all_sizes_have_specs(self):
        for size in VMSize:
            assert size in VM_SIZE_SPECS
            spec = VM_SIZE_SPECS[size]
            assert "cpu" in spec
            assert "memory_mb" in spec
            assert spec["cpu"] > 0
            assert spec["memory_mb"] > 0

    def test_db_optimized_is_largest_memory(self):
        db_mem = VM_SIZE_SPECS[VMSize.DB_OPTIMIZED]["memory_mb"]
        for size, spec in VM_SIZE_SPECS.items():
            if size != VMSize.DB_OPTIMIZED:
                assert spec["memory_mb"] <= db_mem


# -- Engine Validation Tests --

class TestWorkflowEngineValidation:
    """Test the validation step of the workflow engine in isolation."""

    @pytest.fixture
    def engine(self):
        from orchestrator.core.engine import WorkflowEngine
        return WorkflowEngine()

    @pytest.mark.asyncio
    async def test_validate_empty_tiers_fails(self, engine):
        req = ProvisionRequest(
            project_name="empty-proj",
            environment=Environment.DEV,
            requestor="test@example.com",
            # no tiers specified
        )
        state = WorkflowState(
            request=req,
            steps=[WorkflowStep(name="validate")],
        )
        with pytest.raises(ValueError, match="(?i)at least one tier"):
            await engine._step_validate(state)

    @pytest.mark.asyncio
    async def test_validate_dev_vm_limit(self, engine):
        req = ProvisionRequest(
            project_name="big-proj",
            environment=Environment.DEV,
            requestor="test@example.com",
            web_tier=TierSpec(vm_count=3, vm_size=VMSize.SMALL, disk_gb=40),
            app_tier=TierSpec(vm_count=3, vm_size=VMSize.SMALL, disk_gb=40),
        )
        state = WorkflowState(
            request=req,
            steps=[WorkflowStep(name="validate")],
        )
        with pytest.raises(ValueError, match="exceeds dev limit"):
            await engine._step_validate(state)

    @pytest.mark.asyncio
    async def test_validate_prod_passes(self, engine):
        req = ProvisionRequest(
            project_name="prod-proj",
            environment=Environment.PROD,
            requestor="test@example.com",
            web_tier=TierSpec(vm_count=3, vm_size=VMSize.MEDIUM, disk_gb=60),
            app_tier=TierSpec(vm_count=3, vm_size=VMSize.LARGE, disk_gb=100),
            db_tier=TierSpec(vm_count=2, vm_size=VMSize.DB_OPTIMIZED, disk_gb=200),
        )
        state = WorkflowState(
            request=req,
            steps=[WorkflowStep(name="validate")],
        )
        result = await engine._step_validate(state)
        assert result["validated"] is True
        assert result["total_vms"] == 8


# -- API Endpoint Tests --

class TestAPIEndpoints:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from orchestrator.api.server import app
        return TestClient(app)

    def test_health_check(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"

    def test_list_environments(self, client):
        resp = client.get("/api/v1/environments")
        assert resp.status_code == 200
        data = resp.json()
        assert "dev" in data
        assert "prod" in data
        assert data["prod"]["max_vms"] == 50

    def test_provision_request_accepted(self, client):
        payload = {
            "project_name": "test-api",
            "environment": "dev",
            "requestor": "test@example.com",
            "web_tier": {
                "vm_count": 1,
                "vm_size": "small",
                "disk_gb": 40,
            },
            "network": {
                "cidr_block": "10.20.0.0/16",
            },
        }
        resp = client.post("/api/v1/provision", json=payload)
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "pending"
        assert "workflow_id" in data

    def test_provision_invalid_project_name(self, client):
        payload = {
            "project_name": "BAD",
            "environment": "dev",
            "requestor": "test@example.com",
        }
        resp = client.post("/api/v1/provision", json=payload)
        assert resp.status_code == 422

    def test_workflow_not_found(self, client):
        resp = client.get("/api/v1/workflows/nonexistent-id")
        assert resp.status_code == 404

    def test_metrics_endpoint(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert b"provision_requests_total" in resp.content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
