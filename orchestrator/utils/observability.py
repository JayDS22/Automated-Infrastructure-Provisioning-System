"""
Structured logging and Prometheus metrics for the orchestrator.
Logging uses structlog for JSON output; metrics expose standard counters,
histograms, and gauges for provisioning workflow observability.
"""

from __future__ import annotations

import logging
import sys
import time
from contextlib import contextmanager
from functools import wraps
from typing import Callable

import structlog
from prometheus_client import Counter, Gauge, Histogram, Info


# -- Structured Logging Setup --

def configure_logging(log_level: str = "INFO", json_output: bool = True):
    """
    Set up structlog with processors for timestamping, log level, and
    optional JSON serialization. Call once at application startup.
    """
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if json_output:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    ))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, log_level.upper()))


# -- Prometheus Metrics --

PROVISION_REQUESTS = Counter(
    "provision_requests_total",
    "Total provisioning requests received",
    ["environment", "status"],
)

PROVISION_DURATION = Histogram(
    "provision_duration_seconds",
    "End-to-end provisioning duration",
    ["environment"],
    buckets=[60, 300, 600, 900, 1800, 3600, 7200],
)

ACTIVE_WORKFLOWS = Gauge(
    "active_workflows",
    "Number of currently running provisioning workflows",
)

TERRAFORM_OPERATIONS = Counter(
    "terraform_operations_total",
    "Terraform operations executed",
    ["operation", "result"],
)

ANSIBLE_RUNS = Counter(
    "ansible_runs_total",
    "Ansible playbook executions",
    ["playbook", "result"],
)

VM_COUNT = Gauge(
    "provisioned_vm_count",
    "Total VMs managed by the provisioning system",
    ["environment", "tier"],
)

ITSM_API_CALLS = Counter(
    "itsm_api_calls_total",
    "ITSM integration API calls",
    ["provider", "operation", "result"],
)

APP_INFO = Info(
    "orchestrator",
    "Orchestrator application metadata",
)


def init_metrics(version: str = "1.0.0"):
    """Set static application info labels."""
    APP_INFO.info({
        "version": version,
        "component": "infra-provisioner",
    })


@contextmanager
def track_duration(histogram: Histogram, labels: dict):
    """Context manager to measure and record operation duration."""
    start = time.monotonic()
    try:
        yield
    finally:
        elapsed = time.monotonic() - start
        histogram.labels(**labels).observe(elapsed)


def count_operation(counter: Counter, labels: dict, success: bool = True):
    """Increment an operation counter with success/failure label."""
    result = "success" if success else "failure"
    counter.labels(**labels, result=result).inc()
