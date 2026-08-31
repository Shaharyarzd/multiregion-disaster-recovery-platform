variable "name" { type = string }
variable "github_repository" { type = string }
variable "deployment_environment" { type = string }
variable "recovery_environment" { type = string }
variable "evidence_environment" { type = string }
variable "resource_prefix" { type = string }
variable "tags" { type = map(string) }
