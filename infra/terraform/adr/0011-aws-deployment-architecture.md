# ADR 0011 — AWS deployment architecture

- Status: **accepted, not applied** (no AWS account is connected to this repository)
- Related: [infra/terraform/README.md](../README.md), [ADR 0005](../../../docs/adr/0005-postgresql-in-every-environment.md), [ADR 0008](../../../docs/adr/0008-database-guards-as-last-line-of-defense.md), [ADR 0010](../../../docs/adr/0010-deploy-pipeline.md), ARCH-2 in [docs/architecture.md](../../../docs/architecture.md)

## Context

The author set AWS as the target cloud, with the infrastructure delivered as Terraform code. The workload is a single Django service that:

- holds money: every request runs short transactions with row locks on PostgreSQL 18, and correctness depends on database features (triggers, `CHECK`s, `FOR UPDATE`, `lock_timeout`);
- needs migrations to run once, before new code serves traffic;
- receives secrets only from the environment (tokens, webhook secret, database credentials);
- must be reachable over HTTPS by customers and by the payment provider's webhook;
- is small enough that a team of one or two operates it.

## Decision

| Concern | Choice |
| --- | --- |
| Compute | **ECS on Fargate**: the API as a service (gunicorn, non-root container) in private subnets; migrations as a one-off task with the same image. |
| Entry point | **Application Load Balancer** in public subnets, TLS 1.3/1.2 with an ACM certificate, HTTP redirected to HTTPS, `/health` as the target health check. |
| Database | **RDS for PostgreSQL 18**: private, encrypted, `rds.force_ssl=1`, Multi-AZ in production, deletion protection, backups, master password managed by RDS. |
| Database roles | Migrations as the RDS master user; the API as a narrower role without DDL, `DELETE` or `TRUNCATE` (`sql/app_role.sql`). |
| Secrets | **Secrets Manager**, injected by ECS as environment variables; values set out of band, never in code or Terraform state. |
| Images and logs | **ECR** (immutable tags, scan on push) and **CloudWatch Logs** for the service's JSON logs. |
| Network | VPC with two public and two private subnets; security groups allow internet → ALB, ALB → API, API → database only. |
| Releases | The manual, approved pipeline of ADR 0010: plan, approval, migrate, roll out, smoke test. |

## Alternatives considered

| Alternative | Why not |
| --- | --- |
| EKS (Kubernetes) | Same container, much more to operate (control plane upgrades, add-ons, node management) for a single service. |
| AWS App Runner | Simpler, but less control over networking and no natural one-off task for migrations before a rollout. |
| Lambda + API Gateway | Long-lived database connections and row-locked transactions fit poorly with per-invocation execution and cold starts; Django would need an adapter. |
| EC2 Auto Scaling group | Full control, but OS patching and AMI management become the team's job. |
| Aurora PostgreSQL | Strong option at scale; RDS gives the exact PostgreSQL 18 behaviour the guards rely on at lower cost for this size. |
| Self-managed PostgreSQL on EC2 | Backups, failover, patching and TLS all become manual. |

## Consequences

- The append-only guards hold in RDS for every role: its master user is `rds_superuser`, not a PostgreSQL superuser, so the `TRUNCATE` exemption used by the test runner never applies there.
- Running cost includes a NAT gateway and, in production, a Multi-AZ database; staging uses a single-AZ, smaller instance (`environments/staging.tfvars.json`).
- Not covered yet: WAF, DNS records, autoscaling policies, alarms and dashboards, automated creation of the API database role, key rotation schedules.
- The architecture is validated (`terraform validate`, CI) but has never been applied; the first deployment follows the runbook in the Terraform README.
