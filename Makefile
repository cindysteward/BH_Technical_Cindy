PYTHON ?= python3
PY := .venv/bin/python
PORT ?= 8000

.PHONY: install run test lint check demo reset docker

install:
	@$(PYTHON) -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
		|| (echo 'Python 3.11+ is needed. Try: make install PYTHON=python3.12'; \
		exit 1)
	$(PYTHON) -m venv .venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -r requirements-dev.txt

run:
	$(PY) -m uvicorn app.main:create_app --factory --reload --port $(PORT)

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .
	$(PY) -m mypy

check: lint test
	$(PY) scripts/check_invariants.py

demo:
	$(PY) scripts/demo.py $(if $(STEP),--step)

reset:
	rm -f data/pricing.db data/pricing.db-wal data/pricing.db-shm

docker:
	docker build -t pricing-exceptions .
	docker run --rm -p 8000:8000 pricing-exceptions
