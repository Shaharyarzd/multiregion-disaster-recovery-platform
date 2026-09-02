variable "name" {
  type    = string
  default = "portfolio-dr"
}
variable "primary_region" {
  type    = string
  default = "us-east-1"
}
variable "secondary_region" {
  type    = string
  default = "us-west-2"
}
variable "primary_api_id" {
  type        = string
  default     = ""
  description = "Exact Region A API ID captured by the API-only deployment phase."
}
variable "secondary_api_id" {
  type        = string
  default     = ""
  description = "Exact Region B API ID captured by the API-only deployment phase."
}
variable "github_repository" {
  type        = string
  description = "GitHub owner/repository; no repository is created by Terraform."
}
variable "github_oidc_subject_prefix" {
  type        = string
  description = "Observed GitHub OIDC repository subject prefix with immutable owner and repository IDs."
}
variable "github_oidc_provider_arn" {
  type        = string
  description = "Existing account-level GitHub Actions OIDC provider ARN; bootstrap does not manage the provider."
}
variable "temporary_replica_update_item" {
  type        = bool
  default     = false
  description = "Temporary secondary-replica creation exception; remove before synthetic writes."
}
