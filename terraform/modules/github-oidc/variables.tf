variable "name" { type = string }
variable "github_repository" { type = string }
variable "github_oidc_subject_prefix" {
  type        = string
  description = "Observed GitHub OIDC repository subject prefix, including immutable owner and repository IDs."

  validation {
    condition     = can(regex("^repo:[A-Za-z0-9_.-]+@[0-9]+/[A-Za-z0-9_.-]+@[0-9]+$", var.github_oidc_subject_prefix))
    error_message = "github_oidc_subject_prefix must contain immutable GitHub owner and repository IDs."
  }
}
variable "github_oidc_provider_arn" {
  type        = string
  description = "ARN of the existing account-level GitHub Actions OIDC provider. This module never creates or mutates it."

  validation {
    condition     = can(regex("^arn:aws:iam::[0-9]{12}:oidc-provider/token\\.actions\\.githubusercontent\\.com$", var.github_oidc_provider_arn))
    error_message = "github_oidc_provider_arn must be the exact token.actions.githubusercontent.com provider ARN."
  }
}
variable "deployment_environment" { type = string }
variable "recovery_environment" { type = string }
variable "evidence_environment" { type = string }
variable "resource_prefix" { type = string }
variable "primary_region" { type = string }
variable "secondary_region" { type = string }
variable "primary_api_id" {
  type        = string
  default     = ""
  description = "Exact Region A API ID captured during phase one; empty disables stage-create authority."

  validation {
    condition     = var.primary_api_id == "" || can(regex("^[a-z0-9]+$", var.primary_api_id))
    error_message = "primary_api_id must be empty or an exact lowercase alphanumeric API ID."
  }
}
variable "secondary_api_id" {
  type        = string
  default     = ""
  description = "Exact Region B API ID captured during phase one; empty disables stage-create authority."

  validation {
    condition     = var.secondary_api_id == "" || can(regex("^[a-z0-9]+$", var.secondary_api_id))
    error_message = "secondary_api_id must be empty or an exact lowercase alphanumeric API ID."
  }
}
variable "temporary_replica_update_item" {
  type        = bool
  default     = false
  description = "One-run Global Table bootstrap exception; must be false before baseline data."
}
variable "tags" { type = map(string) }
