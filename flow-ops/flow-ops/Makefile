.PHONY: help dev generate validate check test lint mcp mcp-http up down logs clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

dev: ## dev dependencies
	pip install -r requirements-dev.txt

generate: ## build workflows/*.json from the Python definitions
	PYTHONPATH=src python -m flowops.cli generate

validate: ## lint the generated workflows
	PYTHONPATH=src python -m flowops.cli validate

check: ## fail if committed JSON drifted from the Python definitions
	PYTHONPATH=src python -m flowops.cli check

test: ## 87 tests
	PYTHONPATH=src pytest -q

lint:
	ruff check src tests

mcp: ## run the MCP server on stdio (desktop MCP clients)
	PYTHONPATH=src python -m flowops.mcp_server

mcp-http: ## run the MCP server on :8080 (n8n)
	PYTHONPATH=src python -m flowops.mcp_server --http --port 8080

up: ## n8n + postgres + mcp
	docker compose up -d --build
	@echo "n8n  → http://localhost:5678"
	@echo "mcp  → http://localhost:8080/healthz"
	@echo "import workflows from /workflows inside the n8n container"

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

clean:
	rm -rf .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
