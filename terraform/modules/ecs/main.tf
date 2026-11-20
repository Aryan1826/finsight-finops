resource "aws_ecs_cluster" "main" {
  name = "${var.project_name}-cluster"
}
resource "aws_cloudwatch_log_group" "ecs" {
    name = "/ecs/${var.project_name}"
    retention_in_days = 30
}
resource "aws_security_group" "ecs" {
  name = "${var.project_name}-ecs-sg"
  vpc_id = var.vpc_id
  egress {
    from_port = 0
    to_port = 0
    protocol = "-1"
    cidr_blocks = [ "0.0.0.0/0" ]
  }
  ingress {
    from_port = 8501
    to_port = 8501
    protocol = "tcp"
    security_groups = [var.alb_security_group_id]
  }
  ingress {
    from_port = 443
    to_port = 443
    protocol = "tcp"
    security_groups = [var.alb_security_group_id]
  }
  ingress {
    from_port = 80
    to_port = 80
    protocol = "tcp"
    security_groups = [var.alb_security_group_id]
  }
}
resource "aws_ecs_task_definition" "main" {
  family = "${var.project_name}-task"
  requires_compatibilities = ["FARGATE"]
  network_mode = "awsvpc"
  cpu = var.cpu
  memory = var.memory
  execution_role_arn = var.execution_role_arn
  task_role_arn = var.task_role_arn
  container_definitions = jsonencode([{
    name = "${var.project_name}-container"
    image = "${var.ecr_repository_url}:${var.image_tag}"
    essential = true
    portMappings = [{
        containerPort = 8501
        hostPort = 8501
        protocol = "tcp"}
    ]
  
  logConfiguration = {
    logDriver = "awslogs"
    options = {
        "awslogs-group" = aws_cloudwatch_log_group.ecs.name
        "awslogs-region" = var.region
        "awslogs-stream-prefix" = "finsight"
    }
  }
  }
  ])
}
resource "aws_ecs_service" "main" {
  name = "${var.project_name}-service"
  cluster = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.main.arn
  desired_count = var.desired_count
  launch_type = "FARGATE"
  platform_version = "LATEST"
  network_configuration {
    subnets = var.private_subnet_ids
    security_groups = [aws_security_group.ecs.id]
    assign_public_ip = false
  }
  load_balancer {
    target_group_arn = var.target_group_arn
    container_name = "${var.project_name}-container"
    container_port = 8501
  }
}