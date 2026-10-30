output "api_url" {
  description = "Load balancer DNS name; point the API domain (allowed_hosts) at it."
  value       = "https://${aws_lb.api.dns_name}"
}

output "ecr_repository_url" {
  description = "Push the `runtime` image here."
  value       = aws_ecr_repository.api.repository_url
}

output "database_address" {
  description = "Private RDS endpoint."
  value       = aws_db_instance.main.address
}

output "db_master_secret_arn" {
  description = "RDS-managed master credentials (used only by the migrate task and sql/app_role.sql)."
  value       = aws_db_instance.main.master_user_secret[0].secret_arn
}

output "app_secret_arns" {
  description = "Secret containers to fill with `aws secretsmanager put-secret-value`."
  value       = { for key, secret in aws_secretsmanager_secret.app : key => secret.arn }
}

output "migrate_task_definition" {
  description = "Run with `aws ecs run-task` before each deployment."
  value       = aws_ecs_task_definition.migrate.arn
}

output "cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "service_name" {
  description = "API service the pipeline rolls out after migrating."
  value       = aws_ecs_service.api.name
}

output "api_task_definition" {
  description = "Latest API task definition revision (the pipeline sets it on the service)."
  value       = aws_ecs_task_definition.api.arn
}

output "private_subnet_ids" {
  description = "Subnets for one-off tasks such as migrations."
  value       = aws_subnet.private[*].id
}

output "app_security_group_id" {
  description = "Security group for one-off tasks (reaches the database)."
  value       = aws_security_group.app.id
}
