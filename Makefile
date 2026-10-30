COMPOSE := docker compose
RUN := $(COMPOSE) run --rm
TERRAFORM := docker run --rm --user "$$(id -u):$$(id -g)" --env TF_DATA_DIR=/tmp/terraform \
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

infra-check: ## Terraform fmt and validate for infra/terraform (AWS code, never applied here)
	$(TERRAFORM) "terraform fmt -check -recursive -diff && terraform init -backend=false -input=false >/dev/null && terraform validate"

migrate: env ## Apply database migrations
	$(RUN) app python manage.py migrate

logs: ## Follow the API logs
	$(COMPOSE) logs --follow app
