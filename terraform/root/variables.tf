variable "region" {
  type = string
}
variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}
variable "image_tag" {
  type    = string
  default = "v1"
}