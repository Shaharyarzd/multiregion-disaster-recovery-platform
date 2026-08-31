variable "name" {
  type    = string
  default = "portfolio-dr"
}
variable "region" {
  type    = string
  default = "us-west-2"
}
variable "table_name" { type = string }
variable "table_arn" { type = string }
variable "artifact_path" {
  type    = string
  default = "../../../dist/dr-app.zip"
}
