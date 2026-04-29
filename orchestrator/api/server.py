"""
REST API layer for the infrastructure provisioning system.
Exposes endpoints for submitting provisioning requests, checking workflow status,
and querying provisioned resources. Integrates with ITSM connectors for
automated ticket management.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import structlog
import yaml
from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

from orchestrator.core.engine import WorkflowEngine
from orchestrator.integrations.itsm import create_itsm_connector, ITSMConnector
from orchestrator.models import (
    Environment,
    ProvisionRequest,
    ProvisionResponse,
    WorkflowStatus,
    WorkflowStatusResponse,
)
from orchestrator.utils.observability import (
    configure_logging,
    init_metrics,
    ACTIVE_WORKFLOWS,
    PROVISION_REQUESTS,
    PROVISION_DURATION,
    track_duration,
    count_operation,
    TERRAFORM_OPERATIONS,
)

logger = structlog.get_logger(__name__)

# Shared state across the application lifecycle
engine = WorkflowEngine()
itsm_connector: Optional[ITSMConnector] = None


def load_config() -> dict:
    config_path = os.getenv("CONFIG_PATH", "config/settings.yaml")
    if os.path.exists(config_path):
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    return {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown hooks."""
    global itsm_connector
    config = load_config()

    configure_logging(
        log_level=config.get("log_level", "INFO"),
        json_output=config.get("json_logs", True),
    )
    init_metrics(version="1.0.0")

    itsm_config = config.get("itsm", {})
    itsm_connector = create_itsm_connector(itsm_config)

    logger.info("api.startup", itsm_enabled=itsm_connector is not None)
    yield

    if itsm_connector:
        await itsm_connector.close()
    logger.info("api.shutdown")


app = FastAPI(
    title="Infrastructure Provisioning API",
    description="Automated VM provisioning with Terraform, Ansible, and ITSM integration",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -- Provisioning Endpoints --

@app.post("/api/v1/provision", response_model=ProvisionResponse, status_code=202)
async def create_provision_request(
    request: ProvisionRequest,
    background_tasks: BackgroundTasks,
):
    """
    Submit a new infrastructure provisioning request. Returns immediately with a
    workflow ID for tracking. The actual provisioning runs asynchronously.
    """
    logger.info(
        "api.provision.request",
        project=request.project_name,
        env=request.environment.value,
        requestor=request.requestor,
    )

    PROVISION_REQUESTS.labels(
        environment=request.environment.value,
        status="accepted",
    ).inc()

    # Kick off the workflow in the background
    background_tasks.add_task(_run_workflow, request)

    # For production environments, create ITSM change request
    ticket_id = None
    if itsm_connector and request.environment == Environment.PROD:
        from orchestrator.models import WorkflowState
        temp_state = WorkflowState(request=request)
        try:
            ticket_id = await itsm_connector.create_change_request(temp_state)
        except Exception as exc:
            logger.warning("api.itsm.create_failed", error=str(exc))

    return ProvisionResponse(
        workflow_id=request.request_id,
        status=WorkflowStatus.PENDING,
        message=f"Provisioning request accepted. ITSM ticket: {ticket_id or 'N/A'}",
        tracking_url=f"/api/v1/workflows/{request.request_id}",
    )


@app.get("/api/v1/workflows/{workflow_id}", response_model=WorkflowStatusResponse)
async def get_workflow_status(workflow_id: str):
    """Retrieve the current status and step details for a provisioning workflow."""
    state = engine.get_workflow(workflow_id)
    if not state:
        raise HTTPException(status_code=404, detail="Workflow not found")

    duration = None
    if state.status == WorkflowStatus.COMPLETED:
        duration = (state.updated_at - state.created_at).total_seconds()

    return WorkflowStatusResponse(
        workflow_id=state.workflow_id,
        status=state.status,
        steps=state.steps,
        created_at=state.created_at,
        updated_at=state.updated_at,
        provisioned_vms=state.provisioned_vms,
        duration_seconds=duration,
    )


@app.get("/api/v1/workflows")
async def list_workflows(
    limit: int = Query(default=20, le=100),
    environment: Optional[Environment] = None,
):
    """List recent provisioning workflows with optional environment filter."""
    workflows = engine.list_workflows(limit=limit)
    if environment:
        workflows = [w for w in workflows if w.request.environment == environment]

    return [
        {
            "workflow_id": w.workflow_id,
            "project": w.request.project_name,
            "environment": w.request.environment.value,
            "status": w.status.value,
            "created_at": w.created_at.isoformat(),
            "vm_count": len(w.provisioned_vms),
        }
        for w in workflows
    ]


@app.post("/api/v1/workflows/{workflow_id}/retry")
async def retry_workflow(workflow_id: str, background_tasks: BackgroundTasks):
    """Retry a failed workflow from the last failed step."""
    state = engine.get_workflow(workflow_id)
    if not state:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if state.status != WorkflowStatus.FAILED:
        raise HTTPException(status_code=400, detail="Only failed workflows can be retried")

    background_tasks.add_task(_run_workflow, state.request)
    return {"message": "Retry initiated", "workflow_id": workflow_id}


@app.delete("/api/v1/provision/{project_name}")
async def destroy_infrastructure(
    project_name: str,
    environment: Environment = Query(...),
    background_tasks: BackgroundTasks = None,
):
    """Tear down provisioned infrastructure for a project/environment."""
    logger.info(
        "api.destroy.request",
        project=project_name,
        env=environment.value,
    )
    # In production, this would trigger terraform destroy
    return {
        "message": f"Destruction initiated for {project_name} ({environment.value})",
        "status": "pending",
    }


# -- Observability Endpoints --

@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus-compatible metrics endpoint."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "active_workflows": ACTIVE_WORKFLOWS._value._value,
    }


@app.get("/api/v1/environments")
async def list_environments():
    """Return environment specs and resource limits."""
    return {
        "dev": {"max_vms": 5, "max_cpu": 16, "max_memory_gb": 64},
        "staging": {"max_vms": 15, "max_cpu": 64, "max_memory_gb": 256},
        "prod": {"max_vms": 50, "max_cpu": 256, "max_memory_gb": 1024},
    }


# -- Background task runner --

async def _run_workflow(request: ProvisionRequest):
    """Execute the full provisioning workflow with metrics tracking."""
    ACTIVE_WORKFLOWS.inc()
    try:
        with track_duration(
            PROVISION_DURATION,
            {"environment": request.environment.value},
        ):
            state = await engine.execute(request)

        count_operation(
            TERRAFORM_OPERATIONS,
            {"operation": "provision"},
            success=(state.status == WorkflowStatus.COMPLETED),
        )

        # Update ITSM ticket with results
        if itsm_connector and request.itsm_ticket:
            try:
                status_str = state.status.value
                await itsm_connector.update_status(
                    request.itsm_ticket,
                    status_str,
                    notes=f"Workflow {state.workflow_id}: {status_str}",
                )
                if state.status == WorkflowStatus.COMPLETED:
                    await itsm_connector.attach_results(
                        request.itsm_ticket,
                        {"vms": state.provisioned_vms},
                    )
            except Exception as exc:
                logger.warning("api.itsm.update_failed", error=str(exc))

    except Exception as exc:
        logger.error("api.workflow.unhandled", error=str(exc))
    finally:
        ACTIVE_WORKFLOWS.dec()


# -- Entry point --

def main():
    import uvicorn

    config = load_config()
    uvicorn.run(
        "orchestrator.api.server:app",
        host=config.get("host", "0.0.0.0"),
        port=config.get("port", 8000),
        reload=config.get("debug", False),
        workers=config.get("workers", 4),
        log_level="info",
    )


if __name__ == "__main__":
    main()
