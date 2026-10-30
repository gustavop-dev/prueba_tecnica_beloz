data "aws_caller_identity" "current" {}

locals {
  account_id   = data.aws_caller_identity.current.account_id
  state_bucket = "${var.project}-tfstate-${local.account_id}"
  oidc_host    = "token.actions.githubusercontent.com"
}

# --- Remote state: versioned, encrypted, private, TLS-only. Locking uses S3 lock files. -------

resource "aws_s3_bucket" "state" {
  bucket = local.state_bucket
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "state_tls_only" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = data.aws_iam_policy_document.state_tls_only.json
}

# --- GitHub Actions OIDC: short-lived credentials, no stored AWS keys. ------------------------

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://${local.oidc_host}"
  client_id_list = ["sts.amazonaws.com"]
}

# Each role can be assumed only by a job running in the matching GitHub Environment of this
# repository: `<env>-plan` (no approval, read-only) or `<env>` (approval for production).
data "aws_iam_policy_document" "assume_from_environment" {
  for_each = toset(flatten([for env in var.environments : [env, "${env}-plan"]]))

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values   = ["repo:${var.github_repository}:environment:${each.key}"]
    }
  }
}

# State access for one environment's key prefix (state objects and their lock files).
data "aws_iam_policy_document" "state_access" {
  for_each = var.environments

  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${each.key}/*"]
    }
  }

  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.state.arn}/${each.key}/*"]
  }
}

# --- Plan roles: read the account, write only the state lock. ----------------------------------

resource "aws_iam_role" "plan" {
  for_each = var.environments

  # Outside the `<project>-<env>-*` pattern the deploy role manages, so it cannot edit itself.
  name               = "github-${var.project}-${each.key}-plan"
  assume_role_policy = data.aws_iam_policy_document.assume_from_environment["${each.key}-plan"].json
}

resource "aws_iam_role_policy_attachment" "plan_read_only" {
  for_each = var.environments

  role       = aws_iam_role.plan[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

resource "aws_iam_role_policy" "plan_state" {
  for_each = var.environments

  name   = "terraform-state"
  role   = aws_iam_role.plan[each.key].id
  policy = data.aws_iam_policy_document.state_access[each.key].json
}

# --- Workload boundary: the most any role created by the pipeline can ever do. ----------------
# The API and migrate tasks only pull their image, write logs and read their own secrets. The
# deploy role can create or change stack roles only with this boundary attached, so it cannot
# mint a more powerful role and run a task with it.

data "aws_iam_policy_document" "workload_boundary" {
  for_each = var.environments

  statement {
    sid = "PullImages"
    actions = [
      "ecr:GetAuthorizationToken",
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["*"]
  }

  statement {
    sid     = "ReadOwnSecrets"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      "arn:aws:secretsmanager:${var.aws_region}:${local.account_id}:secret:${var.project}-${each.key}/*",
      # RDS-managed master credentials (used only by the migrate task).
      "arn:aws:secretsmanager:${var.aws_region}:${local.account_id}:secret:rds!*",
    ]
  }
}

resource "aws_iam_policy" "workload_boundary" {
  for_each = var.environments

  name   = "${var.project}-${each.key}-workload-boundary"
  policy = data.aws_iam_policy_document.workload_boundary[each.key].json
}

# --- Deploy roles: manage the stack's services, and only the stack's own, bounded IAM roles. --

resource "aws_iam_role" "deploy" {
  for_each = var.environments

  name               = "github-${var.project}-${each.key}-deploy"
  assume_role_policy = data.aws_iam_policy_document.assume_from_environment[each.key].json
}

resource "aws_iam_role_policy_attachment" "deploy_power_user" {
  for_each = var.environments

  role = aws_iam_role.deploy[each.key].name
  # Everything except IAM and Organizations; IAM is granted below, scoped to the stack.
  policy_arn = "arn:aws:iam::aws:policy/PowerUserAccess"
}

data "aws_iam_policy_document" "deploy_iam" {
  for_each = var.environments

  statement {
    sid = "GrantOnlyWithinTheBoundary"
    actions = [
      "iam:CreateRole",
      "iam:PutRolePolicy",
      "iam:AttachRolePolicy",
      "iam:PutRolePermissionsBoundary",
    ]
    resources = ["arn:aws:iam::${local.account_id}:role/${var.project}-${each.key}-*"]

    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [aws_iam_policy.workload_boundary[each.key].arn]
    }
  }

  statement {
    sid = "ManageStackRoles"
    actions = [
      "iam:DeleteRole", "iam:GetRole", "iam:UpdateRole", "iam:TagRole", "iam:UntagRole",
      "iam:UpdateAssumeRolePolicy", "iam:DeleteRolePolicy", "iam:GetRolePolicy",
      "iam:ListRolePolicies", "iam:DetachRolePolicy", "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
    ]
    resources = ["arn:aws:iam::${local.account_id}:role/${var.project}-${each.key}-*"]
  }

  statement {
    sid       = "PassStackRolesToEcsOnly"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${local.account_id}:role/${var.project}-${each.key}-*"]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  statement {
    sid       = "ServiceLinkedRoles"
    actions   = ["iam:CreateServiceLinkedRole"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "iam:AWSServiceName"
      values = [
        "ecs.amazonaws.com",
        "rds.amazonaws.com",
        "elasticloadbalancing.amazonaws.com",
      ]
    }
  }
}

resource "aws_iam_role_policy" "deploy_iam" {
  for_each = var.environments

  name   = "stack-iam"
  role   = aws_iam_role.deploy[each.key].id
  policy = data.aws_iam_policy_document.deploy_iam[each.key].json
}

resource "aws_iam_role_policy" "deploy_state" {
  for_each = var.environments

  name   = "terraform-state"
  role   = aws_iam_role.deploy[each.key].id
  policy = data.aws_iam_policy_document.state_access[each.key].json
}
