variable "name" { type = string }
variable "github_repository" { type = string }
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
variable "tags" { type = map(string) }
