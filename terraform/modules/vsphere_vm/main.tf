terraform {
  required_version = ">= 1.5.0"

  required_providers {
    vsphere = {
      source  = "hashicorp/vsphere"
      version = "~> 2.6"
    }
  }

  backend "s3" {
    bucket         = "infra-tf-state"
    key            = "vsphere/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "tf-lock"
  }
}

provider "vsphere" {
  user                 = var.vsphere_user
  password             = var.vsphere_password
  vsphere_server       = var.vsphere_server
  allow_unverified_ssl = var.allow_unverified_ssl
}

# Pull datacenter, cluster, and datastore references
data "vsphere_datacenter" "dc" {
  name = var.datacenter_name
}

data "vsphere_compute_cluster" "cluster" {
  name          = var.cluster_name
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_datastore" "ds" {
  name          = var.datastore_name
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_network" "net" {
  name          = var.network_name
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_virtual_machine" "template" {
  name          = var.template_name
  datacenter_id = data.vsphere_datacenter.dc.id
}

# VM resource with full lifecycle management
resource "vsphere_virtual_machine" "vm" {
  count = var.vm_count

  name             = "${var.vm_name_prefix}-${format("%03d", count.index + 1)}"
  resource_pool_id = data.vsphere_compute_cluster.cluster.resource_pool_id
  datastore_id     = data.vsphere_datastore.ds.id
  folder           = var.vm_folder

  num_cpus               = var.cpu_count
  memory                 = var.memory_mb
  guest_id               = data.vsphere_virtual_machine.template.guest_id
  scsi_type              = data.vsphere_virtual_machine.template.scsi_type
  firmware               = var.firmware
  efi_secure_boot_enabled = var.firmware == "efi" ? true : false

  # HA and DRS settings
  ha_vm_restart_priority    = var.ha_restart_priority
  ha_vm_monitoring          = "vmMonitoringOnly"

  network_interface {
    network_id   = data.vsphere_network.net.id
    adapter_type = data.vsphere_virtual_machine.template.network_interface_types[0]
  }

  disk {
    label            = "disk0"
    size             = var.disk_size_gb
    eagerly_scrub    = false
    thin_provisioned = var.thin_provisioned
  }

  # Additional data disks if specified
  dynamic "disk" {
    for_each = var.additional_disks
    content {
      label            = "disk${disk.key + 1}"
      size             = disk.value.size_gb
      unit_number      = disk.key + 1
      thin_provisioned = lookup(disk.value, "thin", true)
    }
  }

  clone {
    template_uuid = data.vsphere_virtual_machine.template.id

    customize {
      linux_options {
        host_name = "${var.vm_name_prefix}-${format("%03d", count.index + 1)}"
        domain    = var.domain
      }

      network_interface {
        ipv4_address = cidrhost(var.ip_cidr_block, var.ip_offset + count.index)
        ipv4_netmask = var.netmask_bits
      }

      ipv4_gateway    = var.gateway
      dns_server_list = var.dns_servers
      dns_suffix_list = [var.domain]
    }
  }

  # Tags for inventory tracking and policy enforcement
  tags = [
    for tag in var.vm_tags : vsphere_tag.tags[tag].id
  ]

  lifecycle {
    ignore_changes = [
      annotation,
      vapp,
    ]
  }
}

# Tag category and tags for VM classification
resource "vsphere_tag_category" "env" {
  name        = "environment"
  cardinality = "SINGLE"
  description = "Deployment environment classification"

  associable_types = [
    "VirtualMachine",
    "Datastore",
  ]
}

resource "vsphere_tag" "tags" {
  for_each = toset(var.vm_tags)

  name        = each.key
  category_id = vsphere_tag_category.env.id
  description = "Auto-managed tag: ${each.key}"
}

# Anti-affinity rule to spread VMs across hosts
resource "vsphere_compute_cluster_vm_anti_affinity_rule" "spread" {
  count = var.vm_count > 1 ? 1 : 0

  name                = "${var.vm_name_prefix}-anti-affinity"
  compute_cluster_id  = data.vsphere_compute_cluster.cluster.id
  virtual_machine_ids = vsphere_virtual_machine.vm[*].id
  mandatory           = var.anti_affinity_mandatory
}
