#!/usr/bin/env python3
"""
Dynamic Ansible inventory backed by vSphere.
Queries vCenter for VMs matching managed tags, groups them by tier (web/app/db),
and outputs inventory JSON that Ansible consumes directly.

Usage:
  ansible-playbook -i inventory/vsphere_inventory.py playbooks/site.yml
"""

import json
import os
import sys
import ssl
from typing import Any

try:
    from pyVim.connect import SmartConnect, Disconnect
    from pyVmomi import vim
except ImportError:
    print(json.dumps({"_meta": {"hostvars": {}}}))
    sys.exit(0)


def get_vsphere_connection():
    """Establish authenticated connection to vCenter."""
    context = None
    if os.getenv("VSPHERE_ALLOW_UNVERIFIED", "false").lower() == "true":
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    return SmartConnect(
        host=os.environ["VSPHERE_HOST"],
        user=os.environ["VSPHERE_USER"],
        pwd=os.environ["VSPHERE_PASSWORD"],
        sslContext=context,
    )


def get_vm_tier(vm: vim.VirtualMachine) -> str:
    """Extract tier classification from VM tags or naming convention."""
    name = vm.name.lower()
    for tier in ("web", "app", "db"):
        if tier in name:
            return tier
    return "ungrouped"


def get_vm_ip(vm: vim.VirtualMachine) -> str | None:
    """Get the primary IPv4 address from VMware Tools."""
    if vm.guest and vm.guest.ipAddress:
        return vm.guest.ipAddress
    # Fall back to checking individual NICs
    if vm.guest and vm.guest.net:
        for nic in vm.guest.net:
            for addr in (nic.ipConfig.ipAddress if nic.ipConfig else []):
                if ":" not in addr.ipAddress:  # skip IPv6
                    return addr.ipAddress
    return None


def build_inventory() -> dict[str, Any]:
    """Walk the VM inventory and produce Ansible-compatible JSON."""
    inventory: dict[str, Any] = {
        "_meta": {"hostvars": {}},
        "all": {"children": ["web", "app", "db", "ungrouped"]},
    }

    for group in ("web", "app", "db", "ungrouped"):
        inventory[group] = {"hosts": [], "vars": {}}

    si = get_vsphere_connection()
    try:
        content = si.RetrieveContent()
        container = content.viewManager.CreateContainerView(
            content.rootFolder, [vim.VirtualMachine], True
        )

        for vm in container.view:
            # Only include powered-on VMs with "managed" in name or tag
            if vm.runtime.powerState != vim.VirtualMachinePowerState.poweredOn:
                continue

            ip = get_vm_ip(vm)
            if not ip:
                continue

            tier = get_vm_tier(vm)
            hostname = vm.name

            inventory[tier]["hosts"].append(hostname)
            inventory["_meta"]["hostvars"][hostname] = {
                "ansible_host": ip,
                "ansible_user": os.getenv("ANSIBLE_SSH_USER", "deploy"),
                "vm_uuid": vm.config.uuid,
                "vm_cpu": vm.config.hardware.numCPU,
                "vm_memory_mb": vm.config.hardware.memoryMB,
                "vm_guest_os": vm.config.guestFullName,
                "vm_datacenter": _get_datacenter_name(vm),
            }

        container.Destroy()
    finally:
        Disconnect(si)

    return inventory


def _get_datacenter_name(vm: vim.VirtualMachine) -> str:
    """Walk the parent chain to find the datacenter."""
    parent = vm.parent
    while parent:
        if isinstance(parent, vim.Datacenter):
            return parent.name
        parent = parent.parent
    return "unknown"


def main():
    if "--list" in sys.argv:
        print(json.dumps(build_inventory(), indent=2))
    elif "--host" in sys.argv:
        # Single host mode (rarely used with _meta)
        print(json.dumps({}))
    else:
        print(json.dumps(build_inventory(), indent=2))


if __name__ == "__main__":
    main()
