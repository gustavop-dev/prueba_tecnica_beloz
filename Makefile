COMPOSE := docker compose
RUN := $(COMPOSE) run --rm

.PHONY: env up down test lint format migrate logs

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

migrate: env ## Apply database migrations
	$(RUN) app python manage.py migrate

logs: ## Follow the API logs
	$(COMPOSE) logs --follow app
