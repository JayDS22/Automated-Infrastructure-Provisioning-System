output "vm_ids" {
  description = "Managed object IDs of provisioned VMs"
  value       = vsphere_virtual_machine.vm[*].id
}

output "vm_names" {
  description = "Names assigned to provisioned VMs"
  value       = vsphere_virtual_machine.vm[*].name
}

output "vm_ips" {
  description = "IPv4 addresses assigned during customization"
  value       = vsphere_virtual_machine.vm[*].default_ip_address
}

output "vm_uuids" {
  description = "BIOS UUIDs for each VM (useful for inventory correlation)"
  value       = vsphere_virtual_machine.vm[*].uuid
}

output "vm_moids" {
  description = "Managed object references for API operations"
  value       = vsphere_virtual_machine.vm[*].moid
}
