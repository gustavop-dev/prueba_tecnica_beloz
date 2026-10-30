output "state_bucket" {
  description = "Set as the TF_STATE_BUCKET variable of every GitHub Environment."
  value       = aws_s3_bucket.state.bucket
}

output "plan_role_arns" {
  description = "Set as AWS_PLAN_ROLE_ARN in the `<env>-plan` GitHub Environments."
  value       = { for env, role in aws_iam_role.plan : env => role.arn }
}

output "workload_boundary_arns" {
  description = "Set as WORKLOAD_PERMISSIONS_BOUNDARY_ARN in both GitHub Environments of each stage."
  value       = { for env, policy in aws_iam_policy.workload_boundary : env => policy.arn }
}

output "deploy_role_arns" {
  description = "Set as AWS_DEPLOY_ROLE_ARN in the `<env>` GitHub Environments."
  value       = { for env, role in aws_iam_role.deploy : env => role.arn }
}
