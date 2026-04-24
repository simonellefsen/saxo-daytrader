PYTHON := .venv/bin/python
PIP := .venv/bin/pip
PORT ?= 8501

.PHONY: help install run run-headless run-ui-only stop restart scheduler scheduler-once sync validate validate-phase1 validate-phase2 validate-phase3 validate-phase4 validate-phase4-live validate-phase5 validate-phase6 validate-phase7 validate-phase8 validate-phase9 validate-phase10 validate-phase11 validate-phase12 validate-phase13 validate-phase14 validate-phase15 validate-phase16 validate-phase17 validate-phase18 validate-phase19 validate-phase20 validate-phase21 validate-phase22 validate-phase23 validate-phase24 validate-phase25 validate-phase26 validate-phase27 validate-phase28 validate-phase29 validate-phase30 validate-phase31 validate-phase32 validate-phase33 validate-phase34 validate-phase35 validate-phase36 validate-phase37 validate-phase38 validate-phase39 validate-phase40 render-services saxo-sim saxo-live saxo-sim-session saxo-live-session

help:
	@printf "%s\n" \
		"Targets:" \
		"  make install            Install Python dependencies into .venv" \
		"  make run                Run the dashboard and autonomous scheduler" \
		"  make run-ui-only        Run only the Streamlit dashboard" \
		"  make run-headless       Run dashboard+scheduler headless on PORT=$(PORT)" \
		"  make stop               Stop tracked dashboard and scheduler processes" \
		"  make restart            Stop tracked processes, then run dashboard+scheduler" \
		"  make scheduler          Run the APScheduler worker continuously" \
		"  make scheduler-once     Run one scheduler cycle in mock-decision mode" \
		"  make sync               Import the CSV into ledger.db without starting Streamlit" \
		"  make validate           Run the latest phase validation (Phase 40)" \
		"  make validate-phase1    Run Phase 1 validation" \
		"  make validate-phase2    Run Phase 2 validation" \
		"  make validate-phase3    Run Phase 3 validation" \
		"  make validate-phase4    Run Phase 4 validation in mock mode" \
		"  make validate-phase4-live Run Phase 4 validation using the live xAI API" \
		"  make validate-phase5    Run Phase 5 validation" \
		"  make validate-phase6    Run Phase 6 Saxo live-adapter validation" \
		"  make validate-phase7    Run Phase 7 broker sync validation" \
		"  make validate-phase8    Run Phase 8 partial-fill reconciliation validation" \
		"  make validate-phase9    Run Phase 9 broker amendment/cancellation validation" \
		"  make validate-phase10   Run Phase 10 daily summary notification validation" \
		"  make validate-phase11   Run Phase 11 deployment-template validation" \
		"  make validate-phase12   Run Phase 12 live order replace/cancel validation" \
		"  make validate-phase13   Run Phase 13 notification throttling/backoff validation" \
		"  make validate-phase14   Run Phase 14 weekly/monthly digest validation" \
		"  make validate-phase15   Run Phase 15 quarterly/YTD digest validation" \
		"  make validate-phase16   Run Phase 16 broker alert notification validation" \
		"  make validate-phase17   Run Phase 17 per-kind routing validation" \
		"  make validate-phase18   Run Phase 18 alert suppression validation" \
		"  make validate-phase19   Run Phase 19 named route profile validation" \
		"  make validate-phase20   Run Phase 20 grouped broker alert validation" \
		"  make validate-phase21   Run Phase 21 autonomous launcher validation" \
		"  make validate-phase22   Run Phase 22 scheduler heartbeat validation" \
		"  make validate-phase23   Run Phase 23 manual scheduler-cycle control validation" \
		"  make validate-phase24   Run Phase 24 route-profile formatting validation" \
		"  make validate-phase25   Run Phase 25 scheduler cycle-history validation" \
		"  make validate-phase26   Run Phase 26 stale-worker detection validation" \
		"  make validate-phase27   Run Phase 27 scheduler history retention validation" \
		"  make validate-phase28   Run Phase 28 invalid simulation trade repair validation" \
		"  make validate-phase29   Run Phase 29 whole-share quantity validation" \
		"  make validate-phase30   Run Phase 30 Saxo symbol resolution validation" \
		"  make validate-phase31   Run Phase 31 execution failure alert validation" \
		"  make validate-phase32   Run Phase 32 broker management failure validation" \
		"  make validate-phase33   Run Phase 33 initial cash and cash-balance validation" \
		"  make validate-phase34   Run Phase 34 price monitor and 06:00 reset validation" \
		"  make validate-phase35   Run Phase 35 execution notification validation" \
		"  make validate-phase36   Run Phase 36 portfolio history validation" \
		"  make validate-phase37   Run Phase 37 identifier enrichment validation" \
		"  make validate-phase38   Run Phase 38 goal tracking validation" \
		"  make validate-phase39   Run Phase 39 price-monitor trading-hours gating validation" \
		"  make validate-phase40   Run Phase 40 ladder-order strategy validation" \
		"  make render-services    Render systemd and launchd service examples into deploy/rendered" \
		"  make saxo-sim           Run Saxo OAuth helper against SIM using PKCE" \
		"  make saxo-live          Run Saxo OAuth helper against LIVE using app secret" \
		"  make saxo-sim-session   Run Saxo OAuth helper against SIM and write the session cache" \
		"  make saxo-live-session  Run Saxo OAuth helper against LIVE and write the session cache"

install:
	$(PIP) install -r requirements.txt

run:
	$(PYTHON) main.py --with-scheduler

run-ui-only:
	$(PYTHON) main.py --no-scheduler

run-headless:
	$(PYTHON) main.py --with-scheduler --headless --no-browser --port $(PORT)

stop:
	$(PYTHON) scripts/stop_runtime.py

restart:
	$(PYTHON) scripts/stop_runtime.py
	$(PYTHON) main.py --with-scheduler

scheduler:
	$(PYTHON) scripts/run_scheduler.py

scheduler-once:
	$(PYTHON) scripts/run_scheduler.py --once --mock-decisions --force-decision

sync:
	$(PYTHON) main.py --sync-only

validate: validate-phase40

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

validate-phase9:
	$(PYTHON) scripts/validate_phase9.py

validate-phase10:
	$(PYTHON) scripts/validate_phase10.py

validate-phase11:
	$(PYTHON) scripts/validate_phase11.py

validate-phase12:
	$(PYTHON) scripts/validate_phase12.py

validate-phase13:
	$(PYTHON) scripts/validate_phase13.py

validate-phase14:
	$(PYTHON) scripts/validate_phase14.py

validate-phase15:
	$(PYTHON) scripts/validate_phase15.py

validate-phase16:
	$(PYTHON) scripts/validate_phase16.py

validate-phase17:
	$(PYTHON) scripts/validate_phase17.py

validate-phase18:
	$(PYTHON) scripts/validate_phase18.py

validate-phase19:
	$(PYTHON) scripts/validate_phase19.py

validate-phase20:
	$(PYTHON) scripts/validate_phase20.py

validate-phase21:
	$(PYTHON) scripts/validate_phase21.py

validate-phase22:
	$(PYTHON) scripts/validate_phase22.py

validate-phase23:
	$(PYTHON) scripts/validate_phase23.py

validate-phase24:
	$(PYTHON) scripts/validate_phase24.py

validate-phase25:
	$(PYTHON) scripts/validate_phase25.py

validate-phase26:
	$(PYTHON) scripts/validate_phase26.py

validate-phase27:
	$(PYTHON) scripts/validate_phase27.py

validate-phase28:
	$(PYTHON) scripts/validate_phase28.py

validate-phase29:
	$(PYTHON) scripts/validate_phase29.py

validate-phase30:
	$(PYTHON) scripts/validate_phase30.py

validate-phase31:
	$(PYTHON) scripts/validate_phase31.py

validate-phase32:
	$(PYTHON) scripts/validate_phase32.py

validate-phase33:
	$(PYTHON) scripts/validate_phase33.py

validate-phase34:
	$(PYTHON) scripts/validate_phase34.py

validate-phase35:
	$(PYTHON) scripts/validate_phase35.py

validate-phase36:
	$(PYTHON) scripts/validate_phase36.py

validate-phase37:
	$(PYTHON) scripts/validate_phase37.py

validate-phase38:
	$(PYTHON) scripts/validate_phase38.py

validate-phase39:
	$(PYTHON) scripts/validate_phase39.py

validate-phase40:
	PYTHONPATH=src $(PYTHON) scripts/validate_phase40.py

render-services:
	$(PYTHON) scripts/render_service_templates.py

saxo-sim:
	$(PYTHON) scripts/saxo_oauth_helper.py --environment sim --auth-mode pkce

saxo-live:
	$(PYTHON) scripts/saxo_oauth_helper.py --environment live --auth-mode secret

saxo-sim-session:
	$(PYTHON) scripts/saxo_oauth_helper.py --environment sim --auth-mode pkce --write-env --write-session

saxo-live-session:
	$(PYTHON) scripts/saxo_oauth_helper.py --environment live --auth-mode secret --write-env --write-session
