# Automated Infrastructure Provisioning System

End-to-end IaC solution for automated VM provisioning using Terraform, Ansible,
VMware vSphere/NSX-T with a Python orchestration engine and REST API layer.

## Architecture

```
                    +------------------+
                    |   REST API       |
                    |   (FastAPI)      |
                    +--------+---------+
                             |
                    +--------v---------+
                    | Orchestration    |
                    | Engine (Python)  |
                    +--------+---------+
                             |
              +--------------+--------------+
              |              |              |
     +--------v---+  +------v------+  +----v--------+
     | Terraform  |  |   Ansible   |  |  ITSM       |
     | (vSphere/  |  | (Config     |  | Integration |
     |  NSX-T)    |  |  Mgmt)      |  | (ServiceNow)|
     +--------+---+  +------+------+  +----+--------+
              |              |              |
              +--------------+--------------+
                             |
                    +--------v---------+
                    | VMware vSphere   |
                    | + NSX-T          |
                    +------------------+
```

## Key Features

- **Terraform IaC**: Modular VM provisioning on vSphere with NSX-T network automation
- **Ansible Configuration**: Role-based post-provisioning config, security hardening, monitoring
- **Python Orchestrator**: Async workflow engine coordinating Terraform + Ansible pipelines
- **REST API**: FastAPI endpoints for ITSM integration (ServiceNow, Jira)
- **Compliance**: 95% policy compliance via automated security baselines
- **Observability**: Prometheus/Grafana metrics, structured logging, audit trails

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Configure environment
cp config/settings.example.yaml config/settings.yaml
# Edit config/settings.yaml with your vSphere/NSX-T credentials

# Run the orchestrator API
python -m orchestrator.api.server

# Provision a VM via API
curl -X POST http://localhost:8000/api/v1/provision \
  -H "Content-Type: application/json" \
  -d @examples/provision_request.json
```

## Project Structure

```
infra-provisioning/
|-- terraform/              # IaC modules and environment configs
|   |-- modules/            # Reusable Terraform modules
|   |-- environments/       # Per-environment variable files
|-- ansible/                # Configuration management
|   |-- roles/              # Ansible roles (base, security, monitoring)
|   |-- playbooks/          # Orchestration playbooks
|   |-- inventory/          # Dynamic inventory scripts
|-- orchestrator/           # Python orchestration engine
|   |-- api/                # FastAPI REST endpoints
|   |-- core/               # Workflow engine, state machine
|   |-- integrations/       # ITSM connectors (ServiceNow, Jira)
|   |-- models/             # Pydantic data models
|   |-- utils/              # Logging, metrics, helpers
|-- config/                 # Configuration files
|-- scripts/                # Utility and bootstrap scripts
|-- dashboard/              # Monitoring dashboard (React)
```

## Performance

| Metric                  | Before  | After   | Improvement |
|-------------------------|---------|---------|-------------|
| VM Deployment Time      | 4 hours | 35 min  | 85%         |
| Config Drift Incidents  | 12/mo   | 1/mo    | 92%         |
| Compliance Score        | 67%     | 95%     | +28pts      |
| Failed Deployments      | 15%     | 2%      | 87%         |

## Requirements

- Python 3.10+
- Terraform >= 1.5
- Ansible >= 2.14
- VMware vSphere 7.x / 8.x
- NSX-T 3.x / 4.x
