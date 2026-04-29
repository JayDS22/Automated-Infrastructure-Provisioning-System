"""
Workflow engine that coordinates Terraform and Ansible execution in a state-machine
pattern. Each provisioning request becomes a workflow with discrete steps that can
be retried, rolled back, or resumed independently.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from orchestrator.models import (
    ProvisionRequest,
    VM_SIZE_SPECS,
    WorkflowState,
    WorkflowStatus,
    WorkflowStep,
)

logger = structlog.get_logger(__name__)

TERRAFORM_DIR = Path(__file__).parent.parent.parent / "terraform"
ANSIBLE_DIR = Path(__file__).parent.parent.parent / "ansible"


class WorkflowEngine:
    """
    Orchestrates the full provisioning lifecycle:
      1. Validate request and check quotas
      2. Generate Terraform variables from request spec
      3. Run Terraform plan + apply
      4. Parse Terraform outputs (VM IPs, names)
      5. Generate dynamic Ansible inventory
      6. Run Ansible playbook for configuration
      7. Verify connectivity and compliance
    """

    def __init__(self, state_store: Optional[dict] = None):
        # In production this would be backed by Redis or a database.
        # Using an in-memory dict for the orchestrator layer.
        self._store: dict[str, WorkflowState] = state_store if state_store is not None else {}

    def get_workflow(self, workflow_id: str) -> Optional[WorkflowState]:
        return self._store.get(workflow_id)

    def list_workflows(self, limit: int = 50) -> list[WorkflowState]:
        workflows = sorted(
            self._store.values(),
            key=lambda w: w.created_at,
            reverse=True,
        )
        return workflows[:limit]

    async def execute(self, request: ProvisionRequest) -> WorkflowState:
        """Main entry point. Creates workflow state and runs all steps sequentially."""
        state = WorkflowState(
            request=request,
            status=WorkflowStatus.PENDING,
            steps=[
                WorkflowStep(name="validate"),
                WorkflowStep(name="terraform_plan"),
                WorkflowStep(name="terraform_apply"),
                WorkflowStep(name="ansible_configure"),
                WorkflowStep(name="verify"),
            ],
        )
        self._store[state.workflow_id] = state

        step_handlers = {
            "validate": self._step_validate,
            "terraform_plan": self._step_terraform_plan,
            "terraform_apply": self._step_terraform_apply,
            "ansible_configure": self._step_ansible_configure,
            "verify": self._step_verify,
        }

        for step in state.steps:
            step.status = WorkflowStatus.PROVISIONING
            step.started_at = datetime.now(timezone.utc)
            state.status = WorkflowStatus.PROVISIONING
            state.updated_at = datetime.now(timezone.utc)

            handler = step_handlers[step.name]
            try:
                logger.info("workflow.step.start", workflow=state.workflow_id, step=step.name)
                output = await handler(state)
                state.mark_step_complete(step.name, output or {})
                logger.info(
                    "workflow.step.complete",
                    workflow=state.workflow_id,
                    step=step.name,
                    duration=step.duration_seconds,
                )
            except Exception as exc:
                logger.error(
                    "workflow.step.failed",
                    workflow=state.workflow_id,
                    step=step.name,
                    error=str(exc),
                )
                state.mark_step_failed(step.name, str(exc))
                await self._handle_failure(state, step.name)
                break

        if state.status != WorkflowStatus.FAILED:
            state.status = WorkflowStatus.COMPLETED
            state.updated_at = datetime.now(timezone.utc)

        return state

    async def _step_validate(self, state: WorkflowState) -> dict:
        """Validate the request against environment policies and resource quotas."""
        request = state.request
        tiers = request.tier_map()

        if not tiers:
            raise ValueError("At least one tier (web, app, or db) must be specified")

        # Enforce environment-specific limits
        env_limits = {
            "dev": {"max_vms": 5, "max_cpu": 16, "max_memory_gb": 64},
            "staging": {"max_vms": 15, "max_cpu": 64, "max_memory_gb": 256},
            "prod": {"max_vms": 50, "max_cpu": 256, "max_memory_gb": 1024},
        }
        limits = env_limits[request.environment.value]

        total_vms = sum(t.vm_count for t in tiers.values())
        total_cpu = sum(
            t.vm_count * VM_SIZE_SPECS[t.vm_size]["cpu"] for t in tiers.values()
        )
        total_mem_gb = sum(
            t.vm_count * VM_SIZE_SPECS[t.vm_size]["memory_mb"] / 1024
            for t in tiers.values()
        )

        if total_vms > limits["max_vms"]:
            raise ValueError(
                f"Total VMs ({total_vms}) exceeds {request.environment.value} "
                f"limit of {limits['max_vms']}"
            )
        if total_cpu > limits["max_cpu"]:
            raise ValueError(f"Total vCPUs ({total_cpu}) exceeds limit of {limits['max_cpu']}")

        return {
            "total_vms": total_vms,
            "total_cpu": total_cpu,
            "total_memory_gb": round(total_mem_gb, 1),
            "validated": True,
        }

    async def _step_terraform_plan(self, state: WorkflowState) -> dict:
        """Generate tfvars from request and run terraform plan."""
        request = state.request
        env_dir = TERRAFORM_DIR / "environments" / request.environment.value
        tfvars = self._build_tfvars(request)

        # Write tfvars to a temp file
        tfvars_path = env_dir / "auto.tfvars.json"
        tfvars_path.write_text(json.dumps(tfvars, indent=2))

        result = await self._run_terraform(env_dir, ["init", "-input=false"])
        if result["returncode"] != 0:
            raise RuntimeError(f"Terraform init failed: {result['stderr']}")

        result = await self._run_terraform(
            env_dir,
            ["plan", "-input=false", "-out=tfplan", f"-var-file={tfvars_path}"],
        )
        if result["returncode"] != 0:
            raise RuntimeError(f"Terraform plan failed: {result['stderr']}")

        # Parse the plan summary for resource counts
        plan_summary = self._parse_plan_output(result["stdout"])
        return {"plan_summary": plan_summary, "tfvars_path": str(tfvars_path)}

    async def _step_terraform_apply(self, state: WorkflowState) -> dict:
        """Apply the terraform plan and capture outputs."""
        env_dir = TERRAFORM_DIR / "environments" / state.request.environment.value

        result = await self._run_terraform(
            env_dir, ["apply", "-input=false", "-auto-approve", "tfplan"]
        )
        if result["returncode"] != 0:
            raise RuntimeError(f"Terraform apply failed: {result['stderr']}")

        # Capture outputs
        output_result = await self._run_terraform(env_dir, ["output", "-json"])
        if output_result["returncode"] == 0:
            tf_outputs = json.loads(output_result["stdout"])
            state.terraform_output = tf_outputs

            # Extract VM details for downstream steps
            vms = self._extract_vm_details(tf_outputs)
            state.provisioned_vms = vms
            return {"vm_count": len(vms), "outputs": tf_outputs}

        return {"applied": True}

    async def _step_ansible_configure(self, state: WorkflowState) -> dict:
        """Run Ansible against the newly provisioned VMs."""
        if not state.provisioned_vms:
            logger.warning("workflow.ansible.skip", reason="no VMs to configure")
            return {"skipped": True}

        # Build dynamic inventory from terraform outputs
        inventory = self._build_ansible_inventory(state)
        inventory_path = ANSIBLE_DIR / "inventory" / f"{state.workflow_id}.json"
        inventory_path.write_text(json.dumps(inventory, indent=2))

        playbook = ANSIBLE_DIR / "playbooks" / "site.yml"
        cmd = [
            "ansible-playbook",
            "-i", str(inventory_path),
            str(playbook),
            "-e", f"env={state.request.environment.value}",
            "--timeout", "120",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(ANSIBLE_DIR),
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(f"Ansible failed (rc={proc.returncode}): {stderr.decode()}")

        state.ansible_results = self._parse_ansible_output(stdout.decode())
        return state.ansible_results

    async def _step_verify(self, state: WorkflowState) -> dict:
        """Post-deployment verification: connectivity, services, compliance."""
        results = {"checks": []}

        for vm in state.provisioned_vms:
            ip = vm.get("ip")
            if not ip:
                continue

            # TCP connectivity check on SSH
            ssh_ok = await self._check_port(ip, 22, timeout=10)
            results["checks"].append({
                "vm": vm["name"],
                "ip": ip,
                "ssh_reachable": ssh_ok,
            })

            # Node exporter health check
            exporter_ok = await self._check_port(ip, 9100, timeout=5)
            results["checks"][-1]["monitoring_active"] = exporter_ok

        passed = all(c["ssh_reachable"] for c in results["checks"])
        results["all_passed"] = passed

        if not passed:
            failed = [c["vm"] for c in results["checks"] if not c["ssh_reachable"]]
            raise RuntimeError(f"Verification failed for: {', '.join(failed)}")

        return results

    # -- Helper methods --

    def _build_tfvars(self, request: ProvisionRequest) -> dict[str, Any]:
        """Convert a ProvisionRequest into Terraform variable values."""
        tfvars: dict[str, Any] = {"project_name": request.project_name}

        for tier_name, tier_spec in request.tier_map().items():
            specs = VM_SIZE_SPECS[tier_spec.vm_size]
            prefix = f"{request.environment.value}-{tier_name}"
            # These get consumed by the respective module blocks in main.tf
            tfvars[f"{tier_name}_vm_count"] = tier_spec.vm_count
            tfvars[f"{tier_name}_cpu"] = specs["cpu"]
            tfvars[f"{tier_name}_memory_mb"] = specs["memory_mb"]
            tfvars[f"{tier_name}_disk_gb"] = tier_spec.disk_gb

        return tfvars

    def _build_ansible_inventory(self, state: WorkflowState) -> dict:
        """Build Ansible JSON inventory from provisioned VM list."""
        inventory: dict[str, Any] = {
            "_meta": {"hostvars": {}},
            "all": {"children": []},
        }

        tier_groups: dict[str, list[str]] = {}

        for vm in state.provisioned_vms:
            tier = vm.get("tier", "ungrouped")
            if tier not in tier_groups:
                tier_groups[tier] = []
            tier_groups[tier].append(vm["name"])

            inventory["_meta"]["hostvars"][vm["name"]] = {
                "ansible_host": vm["ip"],
                "ansible_user": "deploy",
                "ansible_ssh_private_key_file": "~/.ssh/infra_deploy",
            }

        for group, hosts in tier_groups.items():
            inventory["all"]["children"].append(group)
            inventory[group] = {"hosts": hosts}

        return inventory

    async def _run_terraform(self, work_dir: Path, args: list[str]) -> dict:
        """Execute a terraform command and return structured result."""
        cmd = ["terraform"] + args
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(work_dir),
        )
        stdout, stderr = await proc.communicate()
        return {
            "returncode": proc.returncode,
            "stdout": stdout.decode(),
            "stderr": stderr.decode(),
        }

    def _parse_plan_output(self, output: str) -> dict:
        """Extract resource counts from terraform plan output."""
        summary = {"to_add": 0, "to_change": 0, "to_destroy": 0}
        for line in output.splitlines():
            if "Plan:" in line:
                parts = line.split()
                for i, p in enumerate(parts):
                    if p == "to" and i + 1 < len(parts):
                        action = parts[i + 1].rstrip(".,")
                        count = int(parts[i - 1]) if parts[i - 1].isdigit() else 0
                        if action == "add":
                            summary["to_add"] = count
                        elif action == "change":
                            summary["to_change"] = count
                        elif action == "destroy":
                            summary["to_destroy"] = count
        return summary

    def _extract_vm_details(self, tf_outputs: dict) -> list[dict]:
        """Pull VM name/IP pairs from terraform output JSON."""
        vms = []
        for key, val in tf_outputs.items():
            if key.endswith("_ips") and isinstance(val, dict):
                tier = key.replace("_ips", "")
                ips = val.get("value", [])
                names_key = f"{tier}_names"
                names = tf_outputs.get(names_key, {}).get("value", [])
                for i, ip in enumerate(ips):
                    name = names[i] if i < len(names) else f"{tier}-{i}"
                    vms.append({"name": name, "ip": ip, "tier": tier})
        return vms

    def _parse_ansible_output(self, output: str) -> dict:
        """Extract play recap stats from ansible-playbook output."""
        recap = {}
        in_recap = False
        for line in output.splitlines():
            if "PLAY RECAP" in line:
                in_recap = True
                continue
            if in_recap and ":" in line:
                host, stats_str = line.split(":", 1)
                host = host.strip()
                stats = {}
                for pair in stats_str.strip().split():
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        stats[k] = int(v)
                recap[host] = stats
        return {"recap": recap}

    @staticmethod
    async def _check_port(host: str, port: int, timeout: int = 5) -> bool:
        """TCP connectivity probe."""
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=timeout,
            )
            writer.close()
            await writer.wait_closed()
            return True
        except (OSError, asyncio.TimeoutError):
            return False

    async def _handle_failure(self, state: WorkflowState, failed_step: str):
        """Attempt rollback on failure. Terraform destroy for provisioning failures."""
        if failed_step in ("terraform_apply", "ansible_configure"):
            logger.warning("workflow.rollback.start", workflow=state.workflow_id)
            state.status = WorkflowStatus.ROLLING_BACK
            try:
                env_dir = (
                    TERRAFORM_DIR / "environments" / state.request.environment.value
                )
                result = await self._run_terraform(
                    env_dir, ["destroy", "-input=false", "-auto-approve"]
                )
                if result["returncode"] == 0:
                    state.status = WorkflowStatus.ROLLED_BACK
                    logger.info("workflow.rollback.complete", workflow=state.workflow_id)
                else:
                    logger.error("workflow.rollback.failed", stderr=result["stderr"])
            except Exception as exc:
                logger.error("workflow.rollback.error", error=str(exc))
