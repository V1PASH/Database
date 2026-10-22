# Tasks for minidb.  Run "make" to see them all.

PYTHON := .venv/bin/python
PIP    := .venv/bin/pip

.DEFAULT_GOAL := help
.PHONY: help install test lint build release release-minor release-major \
        release-dry demo bench ui clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install:  ## Install the package and the development tools
	$(PIP) install -e ".[dev]"

test:  ## Run the test suite
	$(PYTHON) -m pytest tests/ -q

lint:  ## Check the code for problems
	$(PYTHON) -m pyflakes minidb app schemas bench tests django_example release.py

build:  ## Build the wheel and the source distribution
	$(PYTHON) -m build

release:  ## Publish a new release to PyPI
	$(PYTHON) release.py

release-minor:  ## Publish a minor release to PyPI
	$(PYTHON) release.py --minor

release-major:  ## Publish a major release to PyPI
	$(PYTHON) release.py --major

release-dry:  ## Rehearse a release without uploading anything
	$(PYTHON) release.py --dry-run

demo:  ## Run the example application
	$(PYTHON) -m app.demo

bench:  ## Run the benchmarks
	$(PYTHON) -m bench.benchmark

ui:  ## Browse a database in the browser
	$(PYTHON) -m minidb.webui app.db

clean:  ## Remove the build artefacts
	rm -rf dist build *.egg-info
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete
