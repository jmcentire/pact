.PHONY: all install dev test clean help

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

all: install  ## Default: create venv and install pact

$(VENV)/bin/activate:
	python3 -m venv $(VENV)

install: $(VENV)/bin/activate  ## Install pact in editable mode
	$(PIP) install -e ".[dev]"
	@echo ""
	@echo "  Pact installed. Activate with:"
	@echo "    source $(VENV)/bin/activate"
	@echo ""
	@echo "  Then try:"
	@echo "    pact init my-project"
	@echo "    pact --help"

dev: $(VENV)/bin/activate  ## Install with LLM backend support
	$(PIP) install -e ".[dev,llm]"
	@echo ""
	@echo "  Pact installed with LLM support."

test: $(VENV)/bin/activate  ## Run all tests
	$(VENV)/bin/python -m pytest tests/ -v

test-quick: $(VENV)/bin/activate  ## Run tests (stop on first failure)
	$(VENV)/bin/python -m pytest tests/ -x -q

clean:  ## Remove venv, caches, build artifacts
	rm -rf $(VENV) dist build *.egg-info .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'
