.PHONY: setup lint test run papertrade clean

VENV = .venv
PYTHON = $(VENV)/Scripts/python
PIP = $(VENV)/Scripts/pip

setup:
	python -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m mypy bot/

test:
	$(PYTHON) -m pytest tests/ -v

run:
	$(PYTHON) -m bot.cli.run

papertrade:
	$(PYTHON) -m bot.cli.papertrade --capital 1000

clean:
	rm -rf .venv
	rm -rf __pycache__
	rm -rf .pytest_cache
	rm -rf .ruff_cache
	rm -rf .mypy_cache
