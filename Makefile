# Makefile for SahajMails – Build, Test, Publish

.PHONY: help install test build upload clean

# Default: show help
help:
	@echo "SahajMails Makefile"
	@echo ""
	@echo "  make install    → Install package in dev mode"
	@echo "  make test       → Run tests"
	@echo "  make build      → Build wheel + sdist"
	@echo "  make upload     → Upload to PyPI"
	@echo "  make clean      → Remove build artifacts"
	@echo ""

# Install package + dev deps
install:
	pip install -e .[dev]

# Run tests
test:
	pytest tests/

# Build package
build:
	python -m build

# Upload to PyPI
upload:
	twine upload dist/*

# Clean build files
clean:
	rm -rf dist/ build/ *.egg-info .pytest_cache