variable "project_name" {
  type = string
}
variable "github_owner" {
  type = string
}
variable "github_repository" {
  type = string
}
variable "github_branch" {
  type = string
  default = "main"
}
variable "region" {
  type = string
}
variable "ecr_repository_arn" {
  type = string
}
variable "ecs_cluster_name" {
  type = string
}
variable "ecs_service_name" {
  type = string
}
variable "execution_role_arn" {
  type = string
}
variable "task_role_arn" {
  type = string
}