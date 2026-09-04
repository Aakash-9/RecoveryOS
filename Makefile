# RecoveryOS. Nothing here needs Docker, a cloud account or an API key.
PY := .venv/Scripts/python.exe
ifeq ($(OS),)
PY := .venv/bin/python
endif

.PHONY: help setup data eval sweep test demo api ui all clean

help:
	@echo "  make setup   install python deps into .venv"
	@echo "  make data    generate the synthetic world (120 cases, seed 42)"
	@echo "  make test    run the test suite"
	@echo "  make eval    run the four-policy comparison"
	@echo "  make sweep   run the comparison across seven seeds"
	@echo "  make demo    run every hero scenario"
	@echo "  make api     serve the backend on :8001"
	@echo "  make ui      serve the dashboard on :3000"

setup:
	$(PY) -m pip install -r requirements.txt

data:
	$(PY) scripts/generate_synthetic_data.py --cases 120 --seed 42

test:
	$(PY) -m pytest backend/tests -q

eval:
	$(PY) scripts/run_evaluation.py --cases 150 --seed 42

sweep:
	$(PY) scripts/run_evaluation.py --cases 150 --sweep 42,43,44,45,46,47,48

demo:
	$(PY) scripts/demo.py --all

api:
	$(PY) -m uvicorn recoveryos.api.app:app --app-dir backend --port 8001 --reload

ui:
	cd frontend && npm run dev

all: setup data test eval

clean:
	rm -rf data/*.db data/evaluation .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
