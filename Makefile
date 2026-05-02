PYTHON := .venv/bin/python
PIP := .venv/bin/pip
PNPM := pnpm
API_PORT ?= 8000
WEB_PORT ?= 3000
KUBE_CONTEXT ?= docker-desktop

.PHONY: help install frontend-install install-web run run-web restart-web api frontend stop restart scheduler scheduler-once sync docker-build k8s-deploy k8s-seed-saxo-session k8s-status k8s-db-status k8s-stop validate validate-phase1 validate-phase2 validate-phase3 validate-phase4 validate-phase4-live validate-phase5 validate-phase6 validate-phase7 validate-phase8 validate-phase9 validate-phase10 validate-phase11 validate-phase12 validate-phase13 validate-phase14 validate-phase15 validate-phase16 validate-phase17 validate-phase18 validate-phase19 validate-phase20 validate-phase21 validate-phase22 validate-phase23 validate-phase24 validate-phase25 validate-phase26 validate-phase27 validate-phase28 validate-phase29 validate-phase30 validate-phase31 validate-phase32 validate-phase33 validate-phase34 validate-phase35 validate-phase36 validate-phase37 validate-phase38 validate-phase39 validate-phase40 validate-phase41 validate-phase42 validate-execution-regressions render-services saxo-sim saxo-live saxo-sim-session saxo-live-session

help:
	@printf "%s\n" \
		"Targets:" \
		"  make install            Install Python dependencies into .venv" \
		"  make frontend-install   Install Next.js frontend dependencies" \
		"  make install-web        Install Python + frontend dependencies" \
		"  make run                Run the FastAPI backend, Next.js frontend, and scheduler" \
		"  make run-web            Alias for make run" \
		"  make api                Run only the FastAPI backend on API_PORT=$(API_PORT)" \
		"  make frontend           Run only the Next.js frontend on WEB_PORT=$(WEB_PORT)" \
		"  make stop               Stop tracked API, frontend, scheduler, and legacy runtime processes" \
		"  make restart            Stop tracked processes, then run the FastAPI+Next.js stack" \
		"  make restart-web        Alias for make restart" \
		"  make scheduler          Run the APScheduler worker continuously" \
		"  make scheduler-once     Run one scheduler cycle in mock-decision mode" \
		"  make sync               Import the CSV into ledger.db without starting the web stack" \
		"  make docker-build       Build local Docker Desktop images" \
		"  make k8s-deploy         Deploy to Kubernetes context $(KUBE_CONTEXT), namespace saxo" \
		"  make k8s-seed-saxo-session Copy local .secrets/saxo_session.json into the k8s session PVC" \
		"  make k8s-status         Show Kubernetes resources in namespace saxo" \
		"  make k8s-db-status      Show CNPG, Docker MinIO, and backup resources" \
		"  make k8s-stop           Remove app resources from namespace saxo" \
		"  make validate           Run the latest phase validation plus execution regressions" \
		"  make validate-execution-regressions Run Saxo order execution regression checks" \
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
		"  make validate-phase41   Run Phase 41 FastAPI web-backend validation" \
		"  make validate-phase42   Run Phase 42 ladder guardrail validation" \
		"  make render-services    Render systemd and launchd service examples into deploy/rendered" \
		"  make saxo-sim           Run Saxo OAuth helper against SIM using PKCE" \
		"  make saxo-live          Run Saxo OAuth helper against LIVE using app secret" \
		"  make saxo-sim-session   Run Saxo OAuth helper against SIM and write the session cache" \
		"  make saxo-live-session  Run Saxo OAuth helper against LIVE and write the session cache"

install:
	$(PIP) install -r requirements.txt

frontend-install:
	cd frontend && $(PNPM) install

install-web: install frontend-install

run:
	$(PYTHON) main.py --with-scheduler --api-port $(API_PORT) --frontend-port $(WEB_PORT)

run-web:
	$(MAKE) run

api:
	PYTHONPATH=src $(PYTHON) -m uvicorn saxo_daytrader_xai.api.app:create_app --factory --host 127.0.0.1 --port $(API_PORT)

frontend:
	cd frontend && NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:$(API_PORT) WATCHPACK_POLLING=true CHOKIDAR_USEPOLLING=true $(PNPM) exec next dev --port $(WEB_PORT) --hostname 127.0.0.1

stop:
	$(PYTHON) scripts/stop_runtime.py

restart:
	$(PYTHON) scripts/stop_runtime.py
	$(PYTHON) main.py --with-scheduler --api-port $(API_PORT) --frontend-port $(WEB_PORT)

restart-web:
	$(MAKE) restart

scheduler:
	$(PYTHON) scripts/run_scheduler.py

scheduler-once:
	$(PYTHON) scripts/run_scheduler.py --once --mock-decisions --force-decision

sync:
	$(PYTHON) main.py --sync-only

docker-build:
	docker build -f Dockerfile.api -t daytrader-api:local .
	docker build -f frontend/Dockerfile -t daytrader-frontend:local frontend

k8s-deploy:
	KUBE_CONTEXT=$(KUBE_CONTEXT) bash scripts/deploy_k8s_docker_desktop.sh

k8s-seed-saxo-session:
	@test -f .secrets/saxo_session.json || (echo "Missing .secrets/saxo_session.json. Run make saxo-sim-session first." >&2; exit 1)
	@kubectl --context $(KUBE_CONTEXT) -n saxo wait --for=condition=Ready pod -l app=daytrader-api --timeout=120s >/dev/null
	@pod=$$(kubectl --context $(KUBE_CONTEXT) -n saxo get pod -l app=daytrader-api --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}'); \
		kubectl --context $(KUBE_CONTEXT) -n saxo exec "$$pod" -- mkdir -p /session; \
		kubectl --context $(KUBE_CONTEXT) -n saxo cp .secrets/saxo_session.json "$$pod:/session/saxo_session.json"; \
		kubectl --context $(KUBE_CONTEXT) -n saxo exec "$$pod" -- chmod 600 /session/saxo_session.json; \
		echo "Seeded .secrets/saxo_session.json into k8s session PVC via $$pod."

k8s-status:
	kubectl --context $(KUBE_CONTEXT) -n saxo get pods,svc,agentendpoint,ngroktrafficpolicy,pvc,cluster,scheduledbackup,backup

k8s-db-status:
	kubectl --context $(KUBE_CONTEXT) -n saxo get cluster,scheduledbackup,backup,pvc
	kubectl --context $(KUBE_CONTEXT) -n saxo get pods -l cnpg.io/cluster=daytrader-postgres
	docker ps --filter name=daytrader-minio --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

k8s-stop:
	-kubectl --context $(KUBE_CONTEXT) -n saxo delete ingress daytrader-frontend --ignore-not-found
	-kubectl --context $(KUBE_CONTEXT) -n saxo delete agentendpoint daytrader-frontend --ignore-not-found --wait=false
	-kubectl --context $(KUBE_CONTEXT) -n saxo patch domain --all --type merge -p '{"metadata":{"finalizers":[]}}'
	-kubectl --context $(KUBE_CONTEXT) -n saxo delete domain --all --ignore-not-found --wait=false
	-kubectl --context $(KUBE_CONTEXT) -n saxo delete ngroktrafficpolicy daytrader-oauth --ignore-not-found
	-kubectl --context $(KUBE_CONTEXT) -n saxo delete deployment daytrader-api daytrader-scheduler daytrader-frontend --ignore-not-found
	-kubectl --context $(KUBE_CONTEXT) -n saxo delete service daytrader-api daytrader-frontend --ignore-not-found

validate: validate-phase42 validate-execution-regressions

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

validate-phase41:
	PYTHONPATH=src $(PYTHON) scripts/validate_phase41.py

validate-phase42:
	PYTHONPATH=src $(PYTHON) scripts/validate_phase42.py

validate-execution-regressions:
	PYTHONPATH=src $(PYTHON) scripts/validate_execution_regressions.py

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
