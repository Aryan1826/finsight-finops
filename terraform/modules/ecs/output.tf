output "cluster_id" {
  value = aws_ecs_cluster.main.id
}
output "cluster_name" {
  value = aws_ecs_cluster.main.name
}
output "service_name" {
  value = aws_ecs_service.main.name
}
output "task_definition_arn" {
  value = aws_ecs_task_definition.main.arn
}
output "ecs_security_group_id" {
  value = aws_security_group.ecs.id
}
output "log_group_name" {
  value = aws_cloudwatch_log_group.ecs.name
}