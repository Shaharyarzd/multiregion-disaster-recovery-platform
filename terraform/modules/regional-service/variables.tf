variable "name" { type = string }
variable "region" { type = string }
variable "table_name" { type = string }
variable "table_arn" { type = string }
variable "artifact_path" { type = string }
variable "create_stage" {
  type        = bool
  default     = false
  description = "Second-phase gate; stage creation is disabled until exact API-ID IAM scope is installed."
}
variable "stage_tags_enabled" {
  type        = bool
  default     = false
  description = "Apply direct stage tags only after the inert stage exists."
}
variable "stage_traffic_enabled" {
  type        = bool
  default     = false
  description = "Enable auto-deployment only after exact stage tags have been verified."
}
variable "tags" { type = map(string) }
