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
variable "tags" { type = map(string) }
