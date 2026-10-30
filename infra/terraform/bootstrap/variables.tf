variable "project" {
  description = "Name prefix shared with the main stack (its resources are named <project>-<environment>-*)."
  type        = string
  default     = "acme-secured-card"
}

variable "aws_region" {
  description = "AWS region of the state bucket and the main stack."
  type        = string
  default     = "us-east-1"
}

variable "github_repository" {
  description = "owner/name of the repository allowed to assume the roles."
  type        = string
  default     = "gustavop-dev/prueba_tecnica_beloz"
}

variable "environments" {
  description = "Deployable environments; each gets a plan role and a deploy role."
  type        = set(string)
  default     = ["staging", "production"]
}
