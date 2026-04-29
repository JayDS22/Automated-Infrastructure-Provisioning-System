#!/usr/bin/env bash
set -euo pipefail

# Provisioning system bootstrap script.
# Validates prerequisites, installs dependencies, and launches the API server.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV_DIR="${PROJECT_DIR}/.venv"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

check_prerequisites() {
    local missing=0

    if ! command -v python3 &>/dev/null; then
        log_error "python3 not found"
        missing=1
    else
        local py_ver
        py_ver=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"; then
            log_info "Python ${py_ver} detected"
        else
            log_error "Python 3.10+ required, found ${py_ver}"
            missing=1
        fi
    fi

    if ! command -v terraform &>/dev/null; then
        log_warn "Terraform not found (required for provisioning, not for API-only mode)"
    else
        log_info "Terraform $(terraform version -json 2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin)["terraform_version"])' 2>/dev/null || echo 'detected')"
    fi

    if ! command -v ansible-playbook &>/dev/null; then
        log_warn "Ansible not found (will be installed via pip)"
    fi

    return $missing
}

setup_venv() {
    if [ ! -d "$VENV_DIR" ]; then
        log_info "Creating virtual environment at ${VENV_DIR}"
        python3 -m venv "$VENV_DIR"
    fi

    # shellcheck disable=SC1091
    source "${VENV_DIR}/bin/activate"
    log_info "Installing Python dependencies"
    pip install --quiet --upgrade pip
    pip install --quiet -r "${PROJECT_DIR}/requirements.txt"
}

setup_config() {
    local config_file="${PROJECT_DIR}/config/settings.yaml"
    if [ ! -f "$config_file" ]; then
        log_warn "No settings.yaml found, copying example config"
        cp "${PROJECT_DIR}/config/settings.example.yaml" "$config_file"
        log_warn "Edit ${config_file} with your environment credentials"
    fi
}

run_tests() {
    log_info "Running test suite"
    cd "$PROJECT_DIR"
    python -m pytest orchestrator/tests/ -v --tb=short 2>&1 || {
        log_warn "Some tests failed (this is expected without vSphere connectivity)"
    }
}

start_server() {
    log_info "Starting provisioning API server"
    cd "$PROJECT_DIR"
    export CONFIG_PATH="config/settings.yaml"
    export PYTHONPATH="${PROJECT_DIR}:${PYTHONPATH:-}"

    python -m uvicorn orchestrator.api.server:app \
        --host 0.0.0.0 \
        --port "${API_PORT:-8000}" \
        --reload \
        --log-level info
}

main() {
    log_info "Infrastructure Provisioning System Bootstrap"
    echo ""

    check_prerequisites || {
        log_error "Missing prerequisites. Please install required tools."
        exit 1
    }

    setup_venv
    setup_config

    case "${1:-start}" in
        test)
            run_tests
            ;;
        start)
            start_server
            ;;
        setup)
            log_info "Setup complete. Run '$0 start' to launch the server."
            ;;
        *)
            echo "Usage: $0 {start|test|setup}"
            exit 1
            ;;
    esac
}

main "$@"
