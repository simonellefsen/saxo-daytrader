PYTHON := .venv/bin/python
PIP := .venv/bin/pip
PORT ?= 8501

.PHONY: help install run run-headless scheduler scheduler-once sync validate validate-phase1 validate-phase2 validate-phase3 validate-phase4 validate-phase4-live validate-phase5 validate-phase6 validate-phase7 validate-phase8 saxo-sim saxo-live saxo-sim-session saxo-live-session

help:
	@printf "%s\n" \
		"Targets:" \
		"  make install            Install Python dependencies into .venv" \
		"  make run                Run the Streamlit app" \
		"  make run-headless       Run the Streamlit app headless on PORT=$(PORT)" \
		"  make scheduler          Run the APScheduler worker continuously" \
		"  make scheduler-once     Run one scheduler cycle in mock-decision mode" \
		"  make sync               Import the CSV into ledger.db without starting Streamlit" \
		"  make validate           Run the latest phase validation (Phase 8)" \
		"  make validate-phase1    Run Phase 1 validation" \
		"  make validate-phase2    Run Phase 2 validation" \
		"  make validate-phase3    Run Phase 3 validation" \
		"  make validate-phase4    Run Phase 4 validation in mock mode" \
		"  make validate-phase4-live Run Phase 4 validation using the live xAI API" \
		"  make validate-phase5    Run Phase 5 validation" \
		"  make validate-phase6    Run Phase 6 Saxo live-adapter validation" \
		"  make validate-phase7    Run Phase 7 broker sync validation" \
		"  make validate-phase8    Run Phase 8 partial-fill reconciliation validation" \
		"  make saxo-sim           Run Saxo OAuth helper against SIM using PKCE" \
		"  make saxo-live          Run Saxo OAuth helper against LIVE using app secret" \
		"  make saxo-sim-session   Run Saxo OAuth helper against SIM and write the session cache" \
		"  make saxo-live-session  Run Saxo OAuth helper against LIVE and write the session cache"

install:
	$(PIP) install -r requirements.txt

run:
	$(PYTHON) main.py

run-headless:
	$(PYTHON) main.py --headless --no-browser --port $(PORT)

scheduler:
	$(PYTHON) scripts/run_scheduler.py

scheduler-once:
	$(PYTHON) scripts/run_scheduler.py --once --mock-decisions --force-decision

sync:
	$(PYTHON) main.py --sync-only

validate: validate-phase8

validate-phase1:
	$(PYTHON) scripts/validate_phase1.py

validate-phase2:
	$(PYTHON) scripts/validate_phase2.py

validate-phase3:
	$(PYTHON) scripts/validate_phase3.py

validate-phase4:
	$(PYTHON) scripts/validate_phase4.py

validate-phase4-live:
	$(PYTHON) scripts/validate_phase4.py --live

validate-phase5:
	$(PYTHON) scripts/validate_phase5.py

validate-phase6:
	$(PYTHON) scripts/validate_phase6.py

validate-phase7:
	$(PYTHON) scripts/validate_phase7.py

validate-phase8:
	$(PYTHON) scripts/validate_phase8.py

saxo-sim:
	$(PYTHON) scripts/saxo_oauth_helper.py --environment sim --auth-mode pkce

saxo-live:
	$(PYTHON) scripts/saxo_oauth_helper.py --environment live --auth-mode secret

saxo-sim-session:
	$(PYTHON) scripts/saxo_oauth_helper.py --environment sim --auth-mode pkce --write-env --write-session

saxo-live-session:
	$(PYTHON) scripts/saxo_oauth_helper.py --environment live --auth-mode secret --write-env --write-session
