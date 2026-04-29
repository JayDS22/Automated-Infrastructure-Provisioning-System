terraform {
  required_providers {
    nsxt = {
      source  = "vmware/nsxt"
      version = "~> 3.4"
    }
  }
}

provider "nsxt" {
  host                 = var.nsx_manager
  username             = var.nsx_username
  password             = var.nsx_password
  allow_unverified_ssl = var.allow_unverified_ssl
}

# Transport zone lookup for overlay segments
data "nsxt_policy_transport_zone" "overlay" {
  display_name = var.transport_zone_name
}

data "nsxt_policy_edge_cluster" "edge" {
  display_name = var.edge_cluster_name
}

# Tier-1 gateway per application tier
resource "nsxt_policy_tier1_gateway" "app_gw" {
  display_name              = "${var.project_name}-t1-gw"
  edge_cluster_path         = data.nsxt_policy_edge_cluster.edge.path
  failover_mode             = "NON_PREEMPTIVE"
  default_rule_logging      = false
  enable_firewall           = true
  enable_standby_relocation = false
  tier0_path                = var.tier0_gateway_path

  route_advertisement_types = [
    "TIER1_CONNECTED",
    "TIER1_STATIC_ROUTES",
    "TIER1_NAT",
  ]

  tag {
    scope = "project"
    tag   = var.project_name
  }
}

# Overlay segments for each network tier (web, app, db)
resource "nsxt_policy_segment" "segments" {
  for_each = var.network_segments

  display_name        = "${var.project_name}-${each.key}"
  connectivity_path   = nsxt_policy_tier1_gateway.app_gw.path
  transport_zone_path = data.nsxt_policy_transport_zone.overlay.path

  subnet {
    cidr        = each.value.cidr
    dhcp_ranges = lookup(each.value, "dhcp_ranges", [])
  }

  tag {
    scope = "tier"
    tag   = each.key
  }

  tag {
    scope = "project"
    tag   = var.project_name
  }
}

# Distributed firewall policies for micro-segmentation
resource "nsxt_policy_security_policy" "app_policy" {
  display_name = "${var.project_name}-segmentation"
  category     = "Application"
  locked       = false
  stateful     = true

  tag {
    scope = "project"
    tag   = var.project_name
  }

  # Allow web tier to app tier on specific ports
  rule {
    display_name       = "web-to-app"
    source_groups      = [nsxt_policy_group.tier_groups["web"].path]
    destination_groups = [nsxt_policy_group.tier_groups["app"].path]
    services           = ["/infra/services/HTTPS"]
    action             = "ALLOW"
    logged             = true
  }

  # Allow app tier to db tier on database ports
  rule {
    display_name       = "app-to-db"
    source_groups      = [nsxt_policy_group.tier_groups["app"].path]
    destination_groups = [nsxt_policy_group.tier_groups["db"].path]
    services           = [nsxt_policy_service.db_service.path]
    action             = "ALLOW"
    logged             = true
  }

  # Default deny within project scope
  rule {
    display_name = "default-deny-intra-project"
    action       = "DROP"
    logged       = true
  }
}

# Security groups per tier, matched by segment tag
resource "nsxt_policy_group" "tier_groups" {
  for_each = var.network_segments

  display_name = "${var.project_name}-${each.key}-group"

  criteria {
    condition {
      key         = "Tag"
      member_type = "Segment"
      operator    = "EQUALS"
      value       = "${each.key}|tier"
    }
  }
}

# Custom service for database port
resource "nsxt_policy_service" "db_service" {
  display_name = "${var.project_name}-db-port"

  l4_port_set_entry {
    display_name      = "db-tcp"
    protocol          = "TCP"
    destination_ports = var.db_ports
  }
}
