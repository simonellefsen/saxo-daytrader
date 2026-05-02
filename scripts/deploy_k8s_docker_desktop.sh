#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTEXT="${KUBE_CONTEXT:-docker-desktop}"
NAMESPACE="saxo"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"
IMAGE_TAG="${IMAGE_TAG:-$(date +%Y%m%d%H%M%S)}"
API_IMAGE="${API_IMAGE:-daytrader-api:$IMAGE_TAG}"
FRONTEND_IMAGE="${FRONTEND_IMAGE:-daytrader-frontend:$IMAGE_TAG}"
MINIO_CONTAINER_NAME="${MINIO_CONTAINER_NAME:-daytrader-minio}"
SANITIZED_ENV_FILE=""

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf "Missing required command: %s\n" "$1" >&2
    exit 1
  fi
}

require_env() {
  if [ -z "${!1:-}" ]; then
    printf "Missing required environment value: %s\n" "$1" >&2
    exit 1
  fi
}

require_cmd docker
require_cmd kubectl
require_cmd helm
require_cmd python3

if [ ! -f "$ENV_FILE" ]; then
  printf "Missing env file: %s\n" "$ENV_FILE" >&2
  exit 1
fi

cleanup() {
  if [ -n "$SANITIZED_ENV_FILE" ] && [ -f "$SANITIZED_ENV_FILE" ]; then
    rm -f "$SANITIZED_ENV_FILE"
  fi
}
trap cleanup EXIT

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

SANITIZED_ENV_FILE="$(mktemp)"
python3 - "$ENV_FILE" "$SANITIZED_ENV_FILE" <<'PY'
import os
import re
import sys

source_path, target_path = sys.argv[1], sys.argv[2]
key_pattern = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
keys: list[str] = []
with open(source_path, encoding="utf-8") as handle:
    for line in handle:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = key_pattern.match(line)
        if match and match.group(1) not in keys:
            keys.append(match.group(1))

with open(target_path, "w", encoding="utf-8") as handle:
    for key in keys:
        if key in os.environ:
            value = os.environ[key].replace("\n", "\\n")
            handle.write(f"{key}={value}\n")
PY

require_env NGROK_API_KEY
require_env NGROK_AUTHTOKEN
require_env NGROK_DOMAIN
require_env NGROK_ALLOWED_EMAILS
NGROK_OAUTH_PROVIDER="${NGROK_OAUTH_PROVIDER:-google}"
MINIO_HOST_PATH="${MINIO_HOST_PATH:-$ROOT/minio-data}"
MINIO_ROOT_USER="${MINIO_ROOT_USER:-daytrader}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-daytrader-minio-password}"
MINIO_API_PORT="${MINIO_API_PORT:-9000}"
MINIO_CONSOLE_PORT="${MINIO_CONSOLE_PORT:-9001}"
MINIO_ENDPOINT_URL="${MINIO_ENDPOINT_URL:-http://host.docker.internal:$MINIO_API_PORT}"
POSTGRES_APP_USER="${POSTGRES_APP_USER:-daytrader}"
POSTGRES_APP_PASSWORD="${POSTGRES_APP_PASSWORD:-daytrader-postgres-password}"
export MINIO_HOST_PATH MINIO_ROOT_USER MINIO_ROOT_PASSWORD MINIO_ENDPOINT_URL POSTGRES_APP_USER POSTGRES_APP_PASSWORD API_IMAGE
DATABASE_URL="$(python3 - <<'PY'
import os
from urllib.parse import quote

user = quote(os.environ["POSTGRES_APP_USER"], safe="")
password = quote(os.environ["POSTGRES_APP_PASSWORD"], safe="")
print(f"postgresql://{user}:{password}@daytrader-postgres-rw:5432/daytrader")
PY
)"
export DATABASE_URL

start_minio_container() {
  printf "Starting Docker MinIO backup target at %s...\n" "$MINIO_HOST_PATH"
  mkdir -p "$MINIO_HOST_PATH"

  if docker ps -a --format '{{.Names}}' | grep -qx "$MINIO_CONTAINER_NAME"; then
    docker rm -f "$MINIO_CONTAINER_NAME" >/dev/null
  fi

  docker run -d \
    --name "$MINIO_CONTAINER_NAME" \
    -p "$MINIO_API_PORT:9000" \
    -p "$MINIO_CONSOLE_PORT:9001" \
    -e "MINIO_ROOT_USER=$MINIO_ROOT_USER" \
    -e "MINIO_ROOT_PASSWORD=$MINIO_ROOT_PASSWORD" \
    -v "$MINIO_HOST_PATH:/data" \
    quay.io/minio/minio:latest \
    server /data --console-address :9001 >/dev/null

  docker run --rm --entrypoint /bin/sh quay.io/minio/mc:latest \
    -ec "
      until mc alias set local 'http://host.docker.internal:$MINIO_API_PORT' '$MINIO_ROOT_USER' '$MINIO_ROOT_PASSWORD'; do
        sleep 1
      done
      mc mb --ignore-existing local/daytrader-cnpg
    " >/dev/null
}

if [ "$(kubectl config current-context)" != "$CONTEXT" ]; then
  printf "Switch kubectl to context %s before deploying.\n" "$CONTEXT" >&2
  printf "Current context: %s\n" "$(kubectl config current-context)" >&2
  exit 1
fi

printf "Building local Docker images...\n"
docker build -f "$ROOT/Dockerfile.api" -t "$API_IMAGE" "$ROOT"
docker build -f "$ROOT/frontend/Dockerfile" -t "$FRONTEND_IMAGE" "$ROOT/frontend"

printf "Installing/upgrading CloudNativePG operator...\n"
helm repo add cnpg https://cloudnative-pg.github.io/charts >/dev/null 2>&1 || true
helm repo update cnpg >/dev/null
helm upgrade --install cnpg cnpg/cloudnative-pg \
  --namespace cnpg-system \
  --create-namespace
kubectl --context "$CONTEXT" -n cnpg-system wait \
  --for=condition=Available deployment \
  -l app.kubernetes.io/name=cloudnative-pg \
  --timeout=180s

printf "Installing/upgrading ngrok Kubernetes operator...\n"
helm repo add ngrok https://charts.ngrok.com >/dev/null 2>&1 || true
helm repo update ngrok >/dev/null
helm upgrade --install ngrok-operator ngrok/ngrok-operator \
  --namespace ngrok-operator \
  --create-namespace \
  --set "credentials.apiKey=$NGROK_API_KEY" \
  --set "credentials.authtoken=$NGROK_AUTHTOKEN"

start_minio_container

printf "Applying namespace, data PVC, and CloudNativePG resources...\n"
kubectl --context "$CONTEXT" apply -f "$ROOT/deploy/k8s/base/namespace.yaml"
kubectl --context "$CONTEXT" apply -f "$ROOT/deploy/k8s/base/pvc.yaml"
python3 - "$ROOT/deploy/k8s/postgres/postgres-stack.template.yaml" <<'PY' | kubectl --context "$CONTEXT" apply -f -
import os
import sys

template_path = sys.argv[1]
replacements = {
    "__MINIO_ROOT_USER__": os.environ["MINIO_ROOT_USER"],
    "__MINIO_ROOT_PASSWORD__": os.environ["MINIO_ROOT_PASSWORD"],
    "__MINIO_ENDPOINT_URL__": os.environ["MINIO_ENDPOINT_URL"],
    "__POSTGRES_APP_USER__": os.environ["POSTGRES_APP_USER"],
    "__POSTGRES_APP_PASSWORD__": os.environ["POSTGRES_APP_PASSWORD"],
    "__DATABASE_URL__": os.environ["DATABASE_URL"],
}
rendered = open(template_path, encoding="utf-8").read()
for key, value in replacements.items():
    rendered = rendered.replace(key, value)
sys.stdout.write(rendered)
PY
kubectl --context "$CONTEXT" -n "$NAMESPACE" delete scheduledbackup daytrader-postgres-hourly --ignore-not-found
kubectl --context "$CONTEXT" -n "$NAMESPACE" delete job daytrader-minio-bucket --ignore-not-found
kubectl --context "$CONTEXT" -n "$NAMESPACE" delete deployment daytrader-minio --ignore-not-found
kubectl --context "$CONTEXT" -n "$NAMESPACE" delete service daytrader-minio --ignore-not-found
kubectl --context "$CONTEXT" -n "$NAMESPACE" delete pvc daytrader-minio-data --ignore-not-found
kubectl --context "$CONTEXT" delete pv daytrader-minio-pv --ignore-not-found
kubectl --context "$CONTEXT" -n "$NAMESPACE" wait --for=condition=Ready cluster/daytrader-postgres --timeout=420s

printf "Applying app Kubernetes resources...\n"
kubectl --context "$CONTEXT" -n "$NAMESPACE" create secret generic daytrader-env \
  --from-env-file="$SANITIZED_ENV_FILE" \
  --dry-run=client \
  -o yaml | kubectl --context "$CONTEXT" apply -f -
kubectl --context "$CONTEXT" apply -k "$ROOT/deploy/k8s/base"
kubectl --context "$CONTEXT" -n "$NAMESPACE" set image deployment/daytrader-api "api=$API_IMAGE"
kubectl --context "$CONTEXT" -n "$NAMESPACE" set image deployment/daytrader-scheduler "scheduler=$API_IMAGE"
kubectl --context "$CONTEXT" -n "$NAMESPACE" set image deployment/daytrader-frontend "frontend=$FRONTEND_IMAGE"
kubectl --context "$CONTEXT" -n "$NAMESPACE" set image cronjob/daytrader-postgres-backup-schedule "backup=$API_IMAGE"
kubectl --context "$CONTEXT" -n "$NAMESPACE" set image cronjob/daytrader-postgres-backup-retention "retention=$API_IMAGE"

printf "Migrating SQLite ledger into CloudNativePG if needed...\n"
kubectl --context "$CONTEXT" -n "$NAMESPACE" delete job daytrader-sqlite-to-postgres-migration --ignore-not-found
python3 - "$ROOT/deploy/k8s/postgres/sqlite-migration-job.template.yaml" <<'PY' | kubectl --context "$CONTEXT" apply -f -
import os
import sys

template_path = sys.argv[1]
rendered = open(template_path, encoding="utf-8").read().replace("__API_IMAGE__", os.environ["API_IMAGE"])
sys.stdout.write(rendered)
PY
kubectl --context "$CONTEXT" -n "$NAMESPACE" wait --for=condition=complete job/daytrader-sqlite-to-postgres-migration --timeout=300s

kubectl --context "$CONTEXT" -n "$NAMESPACE" rollout restart deployment/daytrader-api
kubectl --context "$CONTEXT" -n "$NAMESPACE" rollout restart deployment/daytrader-scheduler
kubectl --context "$CONTEXT" -n "$NAMESPACE" rollout restart deployment/daytrader-frontend

printf "Applying ngrok OAuth endpoint for %s...\n" "$NGROK_DOMAIN"
kubectl --context "$CONTEXT" -n "$NAMESPACE" delete ingress daytrader-frontend --ignore-not-found
kubectl --context "$CONTEXT" -n "$NAMESPACE" delete agentendpoint \
  -l k8s.ngrok.com/controller-name=ngrok-operator-manager \
  --ignore-not-found
python3 - "$ROOT/deploy/k8s/ngrok/ingress.template.yaml" <<'PY' | kubectl --context "$CONTEXT" apply -f -
import json
import os
import sys

template_path = sys.argv[1]
allowed_emails = [
    email.strip()
    for email in os.environ["NGROK_ALLOWED_EMAILS"].split(",")
    if email.strip()
]
if not allowed_emails:
    raise SystemExit("NGROK_ALLOWED_EMAILS must include at least one email address.")

deny_expr = f"!(actions.ngrok.oauth.identity.email in {json.dumps(allowed_emails)})"
with open(template_path, encoding="utf-8") as handle:
    rendered = (
        handle.read()
        .replace("__NGROK_DOMAIN__", os.environ["NGROK_DOMAIN"])
        .replace("__NGROK_OAUTH_PROVIDER__", os.environ.get("NGROK_OAUTH_PROVIDER", "google"))
        .replace("__NGROK_DENY_EXPR__", deny_expr)
    )
sys.stdout.write(rendered)
PY

printf "Waiting for deployments...\n"
kubectl --context "$CONTEXT" -n "$NAMESPACE" rollout status deployment/daytrader-api --timeout=180s
kubectl --context "$CONTEXT" -n "$NAMESPACE" rollout status deployment/daytrader-scheduler --timeout=180s
kubectl --context "$CONTEXT" -n "$NAMESPACE" rollout status deployment/daytrader-frontend --timeout=180s

if [ -f "$ROOT/.secrets/saxo_session.json" ]; then
  printf "Checking Saxo session cache in the session PVC...\n"
  kubectl --context "$CONTEXT" -n "$NAMESPACE" wait --for=condition=Ready pod -l app=daytrader-api --timeout=120s >/dev/null
  api_pod="$(kubectl --context "$CONTEXT" -n "$NAMESPACE" get pod \
    -l app=daytrader-api \
    --field-selector=status.phase=Running \
    -o jsonpath='{.items[0].metadata.name}')"
  if kubectl --context "$CONTEXT" -n "$NAMESPACE" exec "$api_pod" -- mkdir -p /session; then
    local_session_rank="$(python3 - "$ROOT/.secrets/saxo_session.json" <<'PY'
import json
import sys
from datetime import datetime

path = sys.argv[1]
try:
    payload = json.load(open(path, encoding="utf-8"))
except Exception:
    print(0)
    raise SystemExit

values = []
for key in ("refresh_token_expires_at", "access_token_expires_at", "last_refreshed_at"):
    value = payload.get(key)
    if not value:
        continue
    try:
        values.append(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except ValueError:
        pass
print(int(max(values)) if values else 0)
PY
)"
    remote_session_rank="$(kubectl --context "$CONTEXT" -n "$NAMESPACE" exec "$api_pod" -- python -c 'import json, pathlib; from datetime import datetime; p=pathlib.Path("/session/saxo_session.json"); vals=[]; payload=json.loads(p.read_text()) if p.exists() else {}; [vals.append(datetime.fromisoformat(str(payload[k]).replace("Z","+00:00")).timestamp()) for k in ("refresh_token_expires_at","access_token_expires_at","last_refreshed_at") if payload.get(k)]; print(int(max(vals)) if vals else 0)' 2>/dev/null || printf "0")"
    if [ "${local_session_rank:-0}" -gt "${remote_session_rank:-0}" ]; then
      printf "Seeding newer local Saxo session cache into the session PVC...\n"
      kubectl --context "$CONTEXT" -n "$NAMESPACE" cp "$ROOT/.secrets/saxo_session.json" "$api_pod:/session/saxo_session.json" \
        || printf "Warning: could not copy Saxo session into PVC; re-run make saxo-sim-session if live trading needs a fresh token.\n" >&2
    else
      printf "Keeping existing Saxo session cache in PVC; local cache is not newer.\n"
    fi
  else
    printf "Warning: could not prepare Saxo session cache in PVC; deployment is otherwise complete.\n" >&2
  fi
fi

printf "Deployment complete.\n"
printf "Frontend: https://%s\n" "$NGROK_DOMAIN"
