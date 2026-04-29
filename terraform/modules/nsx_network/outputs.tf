output "tier1_gateway_path" {
  value = nsxt_policy_tier1_gateway.app_gw.path
}

output "segment_paths" {
  description = "Map of tier name to NSX segment path"
  value = {
    for k, v in nsxt_policy_segment.segments : k => v.path
  }
}

output "security_policy_id" {
  value = nsxt_policy_security_policy.app_policy.id
}

output "segment_cidrs" {
  description = "Map of tier name to CIDR block"
  value = {
    for k, v in var.network_segments : k => v.cidr
  }
}
