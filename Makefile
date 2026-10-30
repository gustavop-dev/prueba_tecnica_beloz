COMPOSE := docker compose
RUN := $(COMPOSE) run --rm
# The plugin cache lives in the (git-ignored) workspace: both roots and later runs reuse one
# provider download instead of fetching the AWS provider every time.
TERRAFORM := docker run --rm --user "$$(id -u):$$(id -g)" --env TF_DATA_DIR=/tmp/terraform \
	--env TF_PLUGIN_CACHE_DIR=/workspace/.terraform.d/plugin-cache \
	--volume ./infra/terraform:/workspace --workdir /workspace --entrypoint sh hashicorp/terraform:1.17.1 -c

.PHONY: help env up down test lint format walkthrough reconcile infra-check migrate logs

help: ## List the available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

env: ## Create .env from .env.example when it does not exist
	@test -f .env || cp .env.example .env

up: env ## Build and start PostgreSQL and the API; waits until both are healthy
	$(COMPOSE) up --detach --build --wait

down: ## Stop the stack (keeps the database volume)
	$(COMPOSE) down

test: env ## Run the full test suite against PostgreSQL. Extra pytest args: make test ARGS="-k ledger"
	$(COMPOSE) build app
	$(RUN) app pytest $(ARGS)

lint: env ## Ruff (lint + format check) and import-linter module boundaries
	$(COMPOSE) build app
	$(RUN) --no-deps app sh -c "ruff check . && ruff format --check . && lint-imports"

format: env ## Apply ruff formatting and safe fixes to the files on the host
	$(COMPOSE) build app
	$(RUN) --no-deps --volume ./backend:/app --user "$$(id -u):$$(id -g)" app \
		sh -c "ruff format --no-cache . && ruff check --fix --no-cache ."

walkthrough: ## End-to-end API walkthrough against the running stack (run `make up` first)
	bash scripts/api-walkthrough.sh

reconcile: ## Verify the balance cache and deposits against the ledger
	$(RUN) app python manage.py reconcile

infra-check: ## Terraform fmt + validate (stack and bootstrap) and actionlint on the workflows
	$(TERRAFORM) "mkdir -p \$$TF_PLUGIN_CACHE_DIR && terraform fmt -check -recursive -diff \
		&& terraform init -backend=false -input=false >/dev/null && terraform validate \
		&& cd bootstrap && export TF_DATA_DIR=/tmp/terraform-bootstrap \
		&& terraform init -backend=false -input=false >/dev/null && terraform validate"
	docker run --rm --volume .:/repo --workdir /repo rhysd/actionlint:1.7.12 -color

migrate: env ## Apply database migrations
	$(RUN) app python manage.py migrate

logs: ## Follow the API logs
	$(COMPOSE) logs --follow app
