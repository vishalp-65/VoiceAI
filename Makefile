# Convenience targets. On Windows, run these from Git Bash, or use the raw
# commands shown in the README if `make` is unavailable.

.PHONY: install seed run dev test lint fmt clean

install:        ## Install the package + dev tools in editable mode
	python -m pip install -U pip
	python -m pip install -e ".[dev]"

seed:           ## (Re)create and seed the local SQLite availability store
	python scripts/seed_db.py

run:            ## Start the FastAPI server (browser demo at http://localhost:7860)
	uvicorn voiceai.main:app --host 0.0.0.0 --port 7860

dev:            ## Same as run, with autoreload for development
	uvicorn voiceai.main:app --host 0.0.0.0 --port 7860 --reload

test:           ## Run the unit tests (no network / API keys required)
	pytest -q

lint:           ## Static checks
	ruff check .

fmt:            ## Auto-format
	ruff format .

clean:          ## Remove caches and the local DB
	rm -rf .pytest_cache .ruff_cache **/__pycache__ data/*.db
