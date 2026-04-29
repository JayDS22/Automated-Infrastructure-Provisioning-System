variable "nsx_manager" {
  type = string
}

variable "nsx_username" {
  type      = string
  sensitive = true
}

variable "nsx_password" {
  type      = string
  sensitive = true
}

variable "allow_unverified_ssl" {
  type    = bool
  default = false
}

variable "project_name" {
  type = string
}

variable "transport_zone_name" {
  type    = string
  default = "nsx-overlay-transportzone"
}

variable "edge_cluster_name" {
  type    = string
  default = "edge-cluster-01"
}

variable "tier0_gateway_path" {
  type        = string
  description = "Path to the existing Tier-0 gateway for north-south routing"
}

variable "network_segments" {
  type = map(object({
    cidr        = string
    dhcp_ranges = optional(list(string), [])
  }))

  default = {
    web = {
      cidr = "10.10.1.1/24"
    }
    app = {
      cidr = "10.10.2.1/24"
    }
    db = {
      cidr = "10.10.3.1/24"
    }
  }
}

variable "db_ports" {
  type    = list(string)
  default = ["5432", "3306"]
}
