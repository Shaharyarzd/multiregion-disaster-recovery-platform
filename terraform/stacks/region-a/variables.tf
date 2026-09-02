variable "name" {
  type    = string
  default = "portfolio-dr"
}
variable "region" {
  type    = string
  default = "us-east-1"
}
variable "table_name" { type = string }
variable "table_arn" { type = string }
variable "create_stage" {
  type        = bool
  default     = false
  description = "Enable only after the deploy role is scoped to this stack's exact API ID."
}
variable "stage_tags_enabled" {
  type    = bool
  default = false
}
variable "stage_traffic_enabled" {
  type    = bool
  default = false
}
variable "artifact_path" {
  type    = string
  default = "../../../dist/dr-app.zip"
}
