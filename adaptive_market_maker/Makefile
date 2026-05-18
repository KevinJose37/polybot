.PHONY: lint test format setup

setup:
	pip install -r requirements.txt

lint:
	ruff check .
	mypy .

test:
	pytest tests/

format:
	ruff format .
	ruff check --fix .
