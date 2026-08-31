terraform {
  required_version = ">= 1.8.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.70" }
  }
}

provider "aws" {
  alias  = "primary"
  region = var.primary_region
}

provider "aws" {
  alias  = "secondary"
  region = var.secondary_region
}

module "data" {
  source = "../../modules/global-data"
  providers = {
    aws.primary   = aws.primary
    aws.secondary = aws.secondary
  }
  name             = var.name
  primary_region   = var.primary_region
  secondary_region = var.secondary_region
  tags             = local.tags
}

locals {
  tags = {
    Project            = var.name
    DataClassification = "SYNTHETIC"
    ManagedBy          = "Terraform"
  }
}

