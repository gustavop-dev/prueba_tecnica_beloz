resource "aws_ecr_repository" "api" {
  name                 = local.name
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${local.name}"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_cluster" "main" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

data "aws_secretsmanager_secret" "db_master" {
  arn = aws_db_instance.main.master_user_secret[0].secret_arn
}

locals {
  image = "${aws_ecr_repository.api.repository_url}:${var.image_tag}"

  common_environment = [
    { name = "DJANGO_ALLOWED_HOSTS", value = var.allowed_hosts },
    { name = "POSTGRES_HOST", value = aws_db_instance.main.address },
    { name = "POSTGRES_PORT", value = tostring(aws_db_instance.main.port) },
    { name = "POSTGRES_DB", value = var.db_name },
    { name = "POSTGRES_SSLMODE", value = "require" },
    { name = "DB_LOCK_TIMEOUT_MS", value = tostring(var.db_lock_timeout_ms) },
    { name = "LOG_LEVEL", value = "INFO" },
  ]

  common_secrets = [
    { name = "DJANGO_SECRET_KEY", valueFrom = aws_secretsmanager_secret.app["django_secret_key"].arn },
    { name = "AUTH_TOKENS", valueFrom = aws_secretsmanager_secret.app["auth_tokens"].arn },
    { name = "WEBHOOK_SIGNING_SECRET", valueFrom = aws_secretsmanager_secret.app["webhook_signing_secret"].arn },
  ]

  log_configuration = {
    logDriver = "awslogs"
    options = {
      awslogs-group         = aws_cloudwatch_log_group.api.name
      awslogs-region        = var.aws_region
      awslogs-stream-prefix = "api"
    }
  }
}

# The API: gunicorn only. It connects as the least-privilege role and never runs migrations.
resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.app_cpu
  memory                   = var.app_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name         = "api"
      image        = local.image
      essential    = true
      command      = ["gunicorn", "config.wsgi", "--config", "gunicorn.conf.py"]
      portMappings = [{ containerPort = 8000, protocol = "tcp" }]
      environment = concat(local.common_environment, [
        { name = "POSTGRES_USER", value = var.db_app_user },
      ])
      secrets = concat(local.common_secrets, [
        { name = "POSTGRES_PASSWORD", valueFrom = aws_secretsmanager_secret.app["db_app_password"].arn },
      ])
      healthCheck = {
        command  = ["CMD", "python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://localhost:8000/health', timeout=3).status != 200)"]
        interval = 10
        timeout  = 5
        retries  = 3
      }
      logConfiguration = local.log_configuration
    }
  ])
}

# One-off migration task, run by the deploy pipeline before the service update
# (`aws ecs run-task`), so concurrent API tasks never race on migrations. It uses the RDS master
# credentials, which own the schema and the guard triggers.
resource "aws_ecs_task_definition" "migrate" {
  family                   = "${local.name}-migrate"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name        = "migrate"
      image       = local.image
      essential   = true
      command     = ["python", "manage.py", "migrate", "--noinput"]
      environment = local.common_environment
      secrets = concat(local.common_secrets, [
        { name = "POSTGRES_USER", valueFrom = "${data.aws_secretsmanager_secret.db_master.arn}:username::" },
        { name = "POSTGRES_PASSWORD", valueFrom = "${data.aws_secretsmanager_secret.db_master.arn}:password::" },
      ])
      logConfiguration = local.log_configuration
    }
  ])
}

resource "aws_ecs_service" "api" {
  name            = "${local.name}-api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.app_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  depends_on = [aws_lb_listener.https]
}

# --- Load balancer (TLS termination) ---------------------------------------------------------

resource "aws_lb" "api" {
  name                       = substr(local.name, 0, 32)
  load_balancer_type         = "application"
  subnets                    = aws_subnet.public[*].id
  security_groups            = [aws_security_group.alb.id]
  drop_invalid_header_fields = true
}

resource "aws_lb_target_group" "api" {
  name        = substr("${local.name}-api", 0, 32)
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.main.id

  health_check {
    path                = "/health"
    matcher             = "200"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.api.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

resource "aws_lb_listener" "http_redirect" {
  load_balancer_arn = aws_lb.api.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}
