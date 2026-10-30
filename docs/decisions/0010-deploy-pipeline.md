# ADR 0010 — Manual, approved deploy pipeline: plan, migrate, then roll out

- Status: **accepted**
- Related: [infra/terraform/README.md](../../infra/terraform/README.md), `.github/workflows/deploy.yml`, ADR 0005, ADR 0008

## Context

The AWS stack existed only as validated Terraform. Deploying needs: infrastructure changes reviewed before they apply, migrations that run before new code serves traffic, credentials that cannot leak, and two environments. No AWS account is connected to this repository yet, so nothing may run automatically and turn CI red.

## Decision (operator choices in bold)

- **Manual trigger** (`workflow_dispatch`) with the environment as input — **staging and production** — gated by GitHub Environments; production requires a reviewer.
- **Plan before approval.** A `plan` job runs in the `<env>-plan` environment with a read-only role, prints the plan in the run summary and stores it as an artifact. The `deploy` job (environment `<env>`, approval for production) applies **exactly that plan file**.
- **Migrate, then roll out.** Terraform registers new task definition revisions but ignores the service's task definition. The pipeline pushes the image, runs the migrate task, fails if its exit code is not 0, then points the service at the new revision, waits until stable, verifies that the new revision is the one serving (a circuit-breaker rollback also ends "stable"), and smoke-tests `/health`.
- **OIDC, no stored keys.** A one-time bootstrap stack creates the state bucket, GitHub's OIDC provider and, per environment, a plan role (read-only) and a deploy role, each assumable only from its GitHub Environment of this repository.
- **Bounded deploy role.** The deploy role can create or change only the stack's own roles, and only with a permissions boundary that caps them to pulling images, writing logs and reading the stack's secrets; it cannot modify itself.
- **The same gates as CI** run first, through the reusable CI workflow.

## Alternatives considered

| Alternative | Why not |
| --- | --- |
| Deploy on every push to `main` or on tags | Fails on every merge until an AWS account exists; no human gate for production. |
| One `terraform apply` that also updates the service | New code can serve traffic before its migrations run. |
| Long-lived AWS access keys in repository secrets | Leakable and rotation-dependent; OIDC credentials last one job. |
| `AdministratorAccess` for the deploy role | One compromised workflow run would own the account. |

## Consequences

- Migrations must be **backward compatible** with the version still running (expand/contract), because the old tasks keep serving while they run.
- A failed migration leaves the running version untouched; a failed rollout is rolled back by the circuit breaker and fails the run.
- Before the first run an administrator applies `bootstrap/` and fills the GitHub Environment variables; until then the workflow is inert.
- The workflow and both Terraform roots are linted and validated in CI (`make infra-check`), but the pipeline itself has never run against AWS.
