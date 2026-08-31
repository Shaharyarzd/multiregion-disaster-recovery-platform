variable "name" {
  type    = string
  default = "portfolio-dr"
}
variable "primary_region" {
  type    = string
  default = "us-east-1"
}
variable "github_repository" {
  type        = string
  description = "GitHub owner/repository; no repository is created by Terraform."
}
