terraform {
  required_version = ">= 1.8.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.70" }
  }
}
provider "aws" { region = var.region }
module "service" {
  source        = "../../modules/regional-service"
  name          = var.name
  region        = var.region
  table_name    = var.table_name
  table_arn     = var.table_arn
  artifact_path = var.artifact_path
  create_stage  = var.create_stage
  tags          = local.tags
}
locals {
  tags = { Project = var.name, RegionRole = "active-b", DataClassification = "SYNTHETIC" }
}
output "api_endpoint" { value = module.service.api_endpoint }
output "api_id" { value = module.service.api_id }
