output "vpc_id" {
  value = module.vpc.vpc_id
}
output "public_subnet_ids" {
  value = module.vpc.public_subnet_ids
}
output "private_subnet_ids" {
  value = module.vpc.private_subnet_ids
}
output "ecs_execution_role_arn" {
  value = module.iam.execution_role_arn
}
output "ecs_task_role_arn" {
  value = module.iam.task_role_arn
}
output "ecs_cluster_name" {
  value = module.ecs.cluster_name
}
output "ecs_service_name" {
  value = module.ecs.service_name
}
output "ecs_task_definition_arn" {
  value = module.ecs.task_definition_arn
}
output "ecs_security_group_id" {
  value = module.ecs.ecs_security_group_id
}
output "ecs_log_group_name" {
  value = module.ecs.log_group_name
}
output "finsight_alb_url" {
  value = "http://${module.alb.alb_dns_name}"
}
output "github_actions_role_arn" {
  value = module.github_oidc.github_actions_role_arn
}