# One-time bootstrap, applied by an account administrator from a workstation (local state, kept
# safe by that administrator). It creates what the deploy pipeline needs before it can run:
# the remote-state bucket, GitHub's OIDC identity provider and the per-environment roles.

terraform {
  required_version = ">= 1.17.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.71"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform-bootstrap"
    }
  }
}
