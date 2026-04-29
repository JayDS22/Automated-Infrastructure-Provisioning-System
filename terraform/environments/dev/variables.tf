variable "vsphere_user" { type = string }
variable "vsphere_password" { type = string; sensitive = true }
variable "vsphere_server" { type = string }
variable "nsx_manager" { type = string }
variable "nsx_username" { type = string }
variable "nsx_password" { type = string; sensitive = true }
variable "tier0_gateway_path" { type = string }
variable "datacenter_name" { type = string }
variable "cluster_name" { type = string }
variable "datastore_name" { type = string }
variable "template_name" { type = string }
variable "project_name" { type = string }
variable "dns_servers" { type = list(string); default = ["10.0.0.2", "10.0.0.3"] }
