variable "project_name" {
  type = string
}
variable "region" {
  type = string
}
variable "vpc_id" {
  type = string
}
variable "private_subnet_ids" {
  type = list(string)
}
variable "ecr_repository_url" {
  type = string
}
variable "image_tag" {
  type = string
  default = "v1"
}
variable "execution_role_arn" {
  type = string
}
variable "task_role_arn" {
  type = string
}
variable "cpu" {
  type = number
  default = 512
}
variable "memory" {
  type = number
  default = 1024
}
variable "desired_count" {
  type = number
  default = 0
}
variable "alb_security_group_id" {
  type = string
}
variable "target_group_arn" {
  type = string
}