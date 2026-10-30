# Secret containers only (SEC-SECRET-1..4). Values are written out of band
# (`aws secretsmanager put-secret-value`), so they never appear in code or in the Terraform state.
# ECS injects them into the containers as environment variables.

locals {
  app_secrets = {
    django_secret_key      = "Django SECRET_KEY"
    auth_tokens            = "AUTH_TOKENS: role:principal_id:token entries"
    webhook_signing_secret = "WEBHOOK_SIGNING_SECRET shared with the payment provider"
    db_app_password        = "Password of the least-privilege database role"
  }
}

resource "aws_secretsmanager_secret" "app" {
  for_each = local.app_secrets

  name                    = "${local.name}/${each.key}"
  description             = each.value
  recovery_window_in_days = 7
}
