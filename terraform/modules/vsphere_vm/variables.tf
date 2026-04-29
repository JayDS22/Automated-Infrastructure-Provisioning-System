# vSphere connection
variable "vsphere_user" {
  type      = string
  sensitive = true
}

variable "vsphere_password" {
  type      = string
  sensitive = true
}

variable "vsphere_server" {
  type = string
}

variable "allow_unverified_ssl" {
  type    = bool
  default = false
}

# Infrastructure references
variable "datacenter_name" {
  type = string
}

variable "cluster_name" {
  type = string
}

variable "datastore_name" {
  type = string
}

variable "network_name" {
  type = string
}

variable "template_name" {
  type        = string
  description = "VM template to clone from"
}

# VM specification
variable "vm_name_prefix" {
  type = string
}

variable "vm_count" {
  type    = number
  default = 1

  validation {
    condition     = var.vm_count >= 1 && var.vm_count <= 50
    error_message = "VM count must be between 1 and 50."
  }
}

variable "cpu_count" {
  type    = number
  default = 2

  validation {
    condition     = contains([1, 2, 4, 8, 16, 32], var.cpu_count)
    error_message = "CPU count must be one of: 1, 2, 4, 8, 16, 32."
  }
}

variable "memory_mb" {
  type    = number
  default = 4096

  validation {
    condition     = var.memory_mb >= 1024 && var.memory_mb <= 131072
    error_message = "Memory must be between 1024 MB and 128 GB."
  }
}

variable "disk_size_gb" {
  type    = number
  default = 50
}

variable "thin_provisioned" {
  type    = bool
  default = true
}

variable "firmware" {
  type    = string
  default = "bios"

  validation {
    condition     = contains(["bios", "efi"], var.firmware)
    error_message = "Firmware must be 'bios' or 'efi'."
  }
}

variable "additional_disks" {
  type = list(object({
    size_gb = number
    thin    = optional(bool, true)
  }))
  default = []
}

variable "vm_folder" {
  type    = string
  default = ""
}

# Network configuration
variable "ip_cidr_block" {
  type        = string
  description = "CIDR block for IP allocation, e.g. 10.0.1.0/24"
}

variable "ip_offset" {
  type        = number
  default     = 10
  description = "Starting host offset within the CIDR block"
}

variable "netmask_bits" {
  type    = number
  default = 24
}

variable "gateway" {
  type = string
}

variable "dns_servers" {
  type    = list(string)
  default = ["8.8.8.8", "8.8.4.4"]
}

variable "domain" {
  type    = string
  default = "infra.local"
}

# HA and placement
variable "ha_restart_priority" {
  type    = string
  default = "medium"
}

variable "anti_affinity_mandatory" {
  type    = bool
  default = false
}

# Tagging
variable "vm_tags" {
  type    = list(string)
  default = ["managed", "auto-provisioned"]
}
