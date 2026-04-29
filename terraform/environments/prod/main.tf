# Production environment - full HA, anti-affinity, larger allocations

module "network" {
  source = "../../modules/nsx_network"

  nsx_manager          = var.nsx_manager
  nsx_username         = var.nsx_username
  nsx_password         = var.nsx_password
  project_name         = "prod-${var.project_name}"
  tier0_gateway_path   = var.tier0_gateway_path
  allow_unverified_ssl = false

  network_segments = {
    web = { cidr = "10.50.1.1/24" }
    app = { cidr = "10.50.2.1/24" }
    db  = { cidr = "10.50.3.1/24" }
  }

  db_ports = ["5432"]
}

module "web_vms" {
  source = "../../modules/vsphere_vm"

  vsphere_user     = var.vsphere_user
  vsphere_password = var.vsphere_password
  vsphere_server   = var.vsphere_server

  datacenter_name = var.datacenter_name
  cluster_name    = var.cluster_name
  datastore_name  = var.datastore_name
  network_name    = "prod-${var.project_name}-web"
  template_name   = var.template_name

  vm_name_prefix          = "prod-web"
  vm_count                = 3
  cpu_count               = 4
  memory_mb               = 8192
  disk_size_gb            = 60
  firmware                = "efi"
  ha_restart_priority     = "high"
  anti_affinity_mandatory = true

  ip_cidr_block = "10.50.1.0/24"
  ip_offset     = 10
  gateway       = "10.50.1.1"
  dns_servers   = var.dns_servers
  domain        = "prod.infra.local"
  vm_tags       = ["prod", "web", "managed", "pci-scope"]

  depends_on = [module.network]
}

module "app_vms" {
  source = "../../modules/vsphere_vm"

  vsphere_user     = var.vsphere_user
  vsphere_password = var.vsphere_password
  vsphere_server   = var.vsphere_server

  datacenter_name = var.datacenter_name
  cluster_name    = var.cluster_name
  datastore_name  = var.datastore_name
  network_name    = "prod-${var.project_name}-app"
  template_name   = var.template_name

  vm_name_prefix          = "prod-app"
  vm_count                = 3
  cpu_count               = 8
  memory_mb               = 16384
  disk_size_gb            = 100
  firmware                = "efi"
  ha_restart_priority     = "high"
  anti_affinity_mandatory = true

  ip_cidr_block = "10.50.2.0/24"
  ip_offset     = 10
  gateway       = "10.50.2.1"
  dns_servers   = var.dns_servers
  domain        = "prod.infra.local"
  vm_tags       = ["prod", "app", "managed", "pci-scope"]

  depends_on = [module.network]
}

module "db_vms" {
  source = "../../modules/vsphere_vm"

  vsphere_user     = var.vsphere_user
  vsphere_password = var.vsphere_password
  vsphere_server   = var.vsphere_server

  datacenter_name = var.datacenter_name
  cluster_name    = var.cluster_name
  datastore_name  = var.datastore_name
  network_name    = "prod-${var.project_name}-db"
  template_name   = var.template_name

  vm_name_prefix          = "prod-db"
  vm_count                = 2
  cpu_count               = 16
  memory_mb               = 65536
  disk_size_gb            = 200
  firmware                = "efi"
  ha_restart_priority     = "high"
  anti_affinity_mandatory = true

  additional_disks = [
    { size_gb = 500, thin = false },
    { size_gb = 200, thin = true },
  ]

  ip_cidr_block = "10.50.3.0/24"
  ip_offset     = 10
  gateway       = "10.50.3.1"
  dns_servers   = var.dns_servers
  domain        = "prod.infra.local"
  vm_tags       = ["prod", "db", "managed", "pci-scope"]

  depends_on = [module.network]
}

output "web_ips" { value = module.web_vms.vm_ips }
output "app_ips" { value = module.app_vms.vm_ips }
output "db_ips"  { value = module.db_vms.vm_ips }
