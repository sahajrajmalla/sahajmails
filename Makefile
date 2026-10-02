.DEFAULT_GOAL := help
.PHONY: help install lint format typecheck test cov check build clean run

PY ?= python3

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install the package and dev tooling (needs pip >= 25.1)
	$(PY) -m pip install -e . --group dev
	pre-commit install

lint: ## Check formatting and lint rules
	ruff format --check .
	ruff check .

format: ## Auto-fix formatting and lint rules
	ruff format .
	ruff check --fix .

typecheck: ## Run mypy in strict mode
	mypy

test: ## Run the test suite
	pytest

cov: ## Run tests with a coverage gate
	pytest --cov --cov-report=term-missing --cov-fail-under=40

check: lint typecheck cov ## Everything CI runs

build: clean ## Build the sdist and wheel
	$(PY) -m build

run: ## Start the app locally
	$(PY) -m sahajmails run --reload

clean: ## Remove build and test artifacts
	rm -rf dist build *.egg-info .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
