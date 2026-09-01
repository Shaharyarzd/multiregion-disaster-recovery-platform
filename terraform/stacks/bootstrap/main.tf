terraform {
  required_version = ">= 1.8.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.70" }
  }
}

provider "aws" { region = var.primary_region }

module "github_oidc" {
  source                        = "../../modules/github-oidc"
  name                          = var.name
  github_repository             = var.github_repository
  github_oidc_subject_prefix    = var.github_oidc_subject_prefix
  github_oidc_provider_arn      = var.github_oidc_provider_arn
  deployment_environment        = "aws-deployment"
  recovery_environment          = "aws-recovery-approval"
  evidence_environment          = "aws-evidence-approval"
  resource_prefix               = var.name
  primary_region                = var.primary_region
  secondary_region              = var.secondary_region
  temporary_replica_update_item = var.temporary_replica_update_item
  tags                          = local.tags
}

locals {
  tags = {
    Project            = var.name
    DataClassification = "SYNTHETIC"
    ManagedBy          = "Terraform"
  }
}
