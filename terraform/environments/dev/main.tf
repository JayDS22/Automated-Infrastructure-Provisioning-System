# Dev environment - smaller resource allocations, relaxed HA

module "network" {
  source = "../../modules/nsx_network"

  nsx_manager          = var.nsx_manager
  nsx_username         = var.nsx_username
  nsx_password         = var.nsx_password
  project_name         = "dev-${var.project_name}"
  tier0_gateway_path   = var.tier0_gateway_path
  allow_unverified_ssl = true

  network_segments = {
    web = { cidr = "10.20.1.1/24" }
    app = { cidr = "10.20.2.1/24" }
    db  = { cidr = "10.20.3.1/24" }
  }
}

module "web_vms" {
  source = "../../modules/vsphere_vm"

  vsphere_user         = var.vsphere_user
  vsphere_password     = var.vsphere_password
  vsphere_server       = var.vsphere_server
  allow_unverified_ssl = true

  datacenter_name = var.datacenter_name
  cluster_name    = var.cluster_name
  datastore_name  = var.datastore_name
  network_name    = "dev-${var.project_name}-web"
  template_name   = var.template_name

  vm_name_prefix = "dev-web"
  vm_count       = 1
  cpu_count      = 2
  memory_mb      = 4096
  disk_size_gb   = 40

  ip_cidr_block = "10.20.1.0/24"
  ip_offset     = 10
  gateway       = "10.20.1.1"
  dns_servers   = var.dns_servers
  domain        = "dev.infra.local"
  vm_tags       = ["dev", "web", "managed"]

  depends_on = [module.network]
}

module "app_vms" {
  source = "../../modules/vsphere_vm"

  vsphere_user         = var.vsphere_user
  vsphere_password     = var.vsphere_password
  vsphere_server       = var.vsphere_server
  allow_unverified_ssl = true

  datacenter_name = var.datacenter_name
  cluster_name    = var.cluster_name
  datastore_name  = var.datastore_name
  network_name    = "dev-${var.project_name}-app"
  template_name   = var.template_name

  vm_name_prefix = "dev-app"
  vm_count       = 1
  cpu_count      = 2
  memory_mb      = 8192
  disk_size_gb   = 60

  ip_cidr_block = "10.20.2.0/24"
  ip_offset     = 10
  gateway       = "10.20.2.1"
  dns_servers   = var.dns_servers
  domain        = "dev.infra.local"
  vm_tags       = ["dev", "app", "managed"]

  depends_on = [module.network]
}

module "db_vms" {
  source = "../../modules/vsphere_vm"

  vsphere_user         = var.vsphere_user
  vsphere_password     = var.vsphere_password
  vsphere_server       = var.vsphere_server
  allow_unverified_ssl = true

  datacenter_name = var.datacenter_name
  cluster_name    = var.cluster_name
  datastore_name  = var.datastore_name
  network_name    = "dev-${var.project_name}-db"
  template_name   = var.template_name

  vm_name_prefix = "dev-db"
  vm_count       = 1
  cpu_count      = 4
  memory_mb      = 16384
  disk_size_gb   = 100

  additional_disks = [
    { size_gb = 200, thin = true }
  ]

  ip_cidr_block = "10.20.3.0/24"
  ip_offset     = 10
  gateway       = "10.20.3.1"
  dns_servers   = var.dns_servers
  domain        = "dev.infra.local"
  vm_tags       = ["dev", "db", "managed"]

  depends_on = [module.network]
}
