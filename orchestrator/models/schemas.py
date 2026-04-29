"""
Data models for provisioning requests, workflow state, and API responses.
All validation happens at the model boundary so internal code can trust the data.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class Environment(str, Enum):
    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"


class VMSize(str, Enum):
    SMALL = "small"      # 2 vCPU, 4GB RAM
    MEDIUM = "medium"    # 4 vCPU, 8GB RAM
    LARGE = "large"      # 8 vCPU, 16GB RAM
    XLARGE = "xlarge"    # 16 vCPU, 32GB RAM
    DB_OPTIMIZED = "db_optimized"  # 16 vCPU, 64GB RAM


class WorkflowStatus(str, Enum):
    PENDING = "pending"
    VALIDATING = "validating"
    PROVISIONING = "provisioning"
    CONFIGURING = "configuring"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"


class TierSpec(BaseModel):
    """Specification for a single infrastructure tier (web, app, db)."""
    vm_count: int = Field(ge=1, le=50)
    vm_size: VMSize
    disk_gb: int = Field(ge=20, le=2000)
    additional_disks: list[dict] = Field(default_factory=list)

    @field_validator("additional_disks")
    @classmethod
    def validate_disks(cls, v):
        for disk in v:
            if "size_gb" not in disk:
                raise ValueError("Each additional disk must specify size_gb")
            if disk["size_gb"] < 10 or disk["size_gb"] > 5000:
                raise ValueError("Disk size must be between 10 and 5000 GB")
        return v


class NetworkSpec(BaseModel):
    """Network layout for the provisioning request."""
    cidr_block: str = Field(
        default="10.0.0.0/16",
        pattern=r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2}$",
    )
    enable_nsx: bool = True
    micro_segmentation: bool = True
    allowed_ports: list[dict] = Field(default_factory=lambda: [
        {"port": 22, "proto": "tcp", "src": "10.0.0.0/8"},
        {"port": 443, "proto": "tcp"},
    ])


class ProvisionRequest(BaseModel):
    """Inbound provisioning request from ITSM or direct API call."""
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_name: str = Field(min_length=3, max_length=50, pattern=r"^[a-z][a-z0-9-]+$")
    environment: Environment
    requestor: str
    itsm_ticket: Optional[str] = None

    web_tier: Optional[TierSpec] = None
    app_tier: Optional[TierSpec] = None
    db_tier: Optional[TierSpec] = None
    network: NetworkSpec = Field(default_factory=NetworkSpec)

    custom_tags: dict[str, str] = Field(default_factory=dict)
    callback_url: Optional[str] = None

    @field_validator("project_name")
    @classmethod
    def no_consecutive_hyphens(cls, v):
        if "--" in v:
            raise ValueError("Project name cannot contain consecutive hyphens")
        return v

    def tier_map(self) -> dict[str, TierSpec]:
        """Return only the tiers that were actually requested."""
        tiers = {}
        if self.web_tier:
            tiers["web"] = self.web_tier
        if self.app_tier:
            tiers["app"] = self.app_tier
        if self.db_tier:
            tiers["db"] = self.db_tier
        return tiers


# Mapping from VMSize to actual resource allocations
VM_SIZE_SPECS = {
    VMSize.SMALL:        {"cpu": 2,  "memory_mb": 4096},
    VMSize.MEDIUM:       {"cpu": 4,  "memory_mb": 8192},
    VMSize.LARGE:        {"cpu": 8,  "memory_mb": 16384},
    VMSize.XLARGE:       {"cpu": 16, "memory_mb": 32768},
    VMSize.DB_OPTIMIZED: {"cpu": 16, "memory_mb": 65536},
}


class WorkflowStep(BaseModel):
    """Tracks a single step within a provisioning workflow."""
    name: str
    status: WorkflowStatus = WorkflowStatus.PENDING
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    output: dict = Field(default_factory=dict)

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None


class WorkflowState(BaseModel):
    """Full state of a provisioning workflow, persisted between steps."""
    workflow_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    request: ProvisionRequest
    status: WorkflowStatus = WorkflowStatus.PENDING
    steps: list[WorkflowStep] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Populated during execution
    terraform_output: dict = Field(default_factory=dict)
    ansible_results: dict = Field(default_factory=dict)
    provisioned_vms: list[dict] = Field(default_factory=list)

    def current_step(self) -> Optional[WorkflowStep]:
        for step in self.steps:
            if step.status in (WorkflowStatus.PENDING, WorkflowStatus.PROVISIONING):
                return step
        return None

    def mark_step_complete(self, step_name: str, output: dict = None):
        for step in self.steps:
            if step.name == step_name:
                step.status = WorkflowStatus.COMPLETED
                step.completed_at = datetime.now(timezone.utc)
                if output:
                    step.output = output
                break
        self.updated_at = datetime.now(timezone.utc)

    def mark_step_failed(self, step_name: str, error: str):
        for step in self.steps:
            if step.name == step_name:
                step.status = WorkflowStatus.FAILED
                step.completed_at = datetime.now(timezone.utc)
                step.error_message = error
                break
        self.status = WorkflowStatus.FAILED
        self.updated_at = datetime.now(timezone.utc)


class ProvisionResponse(BaseModel):
    """API response returned immediately after accepting a provision request."""
    workflow_id: str
    status: WorkflowStatus
    message: str
    tracking_url: str


class WorkflowStatusResponse(BaseModel):
    """Detailed status of a running or completed workflow."""
    workflow_id: str
    status: WorkflowStatus
    steps: list[WorkflowStep]
    created_at: datetime
    updated_at: datetime
    provisioned_vms: list[dict]
    duration_seconds: Optional[float] = None
