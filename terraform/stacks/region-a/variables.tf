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
variable "artifact_path" {
  type    = string
  default = "../../../dist/dr-app.zip"
}
