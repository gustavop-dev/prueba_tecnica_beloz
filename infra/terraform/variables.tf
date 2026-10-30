variable "project" {
  description = "Name prefix for every resource."
  type        = string
  default     = "acme-secured-card"
}

variable "environment" {
  description = "Deployment environment, e.g. staging or production."
  type        = string
  default     = "staging"
}

variable "aws_region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

variable "vpc_cidr" {
  description = "CIDR of the VPC; split into two public and two private subnets."
  type        = string
  default     = "10.40.0.0/16"
}

variable "certificate_arn" {
  description = "ACM certificate for the HTTPS listener (TLS terminates at the load balancer)."
  type        = string
}

variable "allowed_hosts" {
  description = "Comma-separated DJANGO_ALLOWED_HOSTS, e.g. the API domain."
  type        = string
}

variable "image_tag" {
  description = "Tag of the `runtime` image pushed to the ECR repository."
  type        = string
}

variable "app_cpu" {
  description = "Fargate CPU units per task."
  type        = number
  default     = 512
}

variable "app_memory" {
  description = "Fargate memory (MiB) per task."
  type        = number
  default     = 1024
}

variable "app_desired_count" {
  description = "Number of API tasks."
  type        = number
  default     = 2
}

variable "db_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.medium"
}

variable "db_allocated_storage" {
  description = "Initial RDS storage (GiB)."
  type        = number
  default     = 20
}

variable "db_multi_az" {
  description = "Standby replica in a second availability zone."
  type        = bool
  default     = true
}

variable "db_name" {
  description = "Application database name."
  type        = string
  default     = "acme"
}

variable "db_app_user" {
  description = "Least-privilege database role used by the API (created by sql/app_role.sql)."
  type        = string
  default     = "acme_app"
}

variable "db_lock_timeout_ms" {
  description = "DB_LOCK_TIMEOUT_MS for money operations (CON-9)."
  type        = number
  default     = 5000
}

variable "log_retention_days" {
  description = "CloudWatch retention for the JSON logs."
  type        = number
  default     = 30
}
