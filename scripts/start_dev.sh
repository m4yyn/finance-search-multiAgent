#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
BACKEND_ENV="$BACKEND_DIR/.env"
FRONTEND_ENV="$FRONTEND_DIR/.env"

SKIP_INSTALL=0
NO_MIGRATE=0

usage() {
  cat <<'EOF'
Usage: ./scripts/start_dev.sh [--skip-install] [--no-migrate]

Environment variables accepted for non-interactive setup:
  OPENAI_API_KEY
  OPENAI_BASE_URL
  LLM_MODEL
  EMBEDDING_MODEL
  BOCHA_API_KEY
  DOCMIND_ACCESS_KEY_ID
  DOCMIND_ACCESS_KEY_SECRET
  VITE_API_BASE_URL

The script writes local backend/.env and frontend/.env files, then starts:
  Backend:  http://localhost:8000
  Frontend: http://localhost:5173
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-install)
      SKIP_INSTALL=1
      shift
      ;;
    --no-migrate)
      NO_MIGRATE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

ensure_env_files() {
  if [[ ! -f "$BACKEND_ENV" ]]; then
    cp "$BACKEND_DIR/.env.example" "$BACKEND_ENV"
    echo "Created $BACKEND_ENV"
  fi
  if [[ ! -f "$FRONTEND_ENV" ]]; then
    cp "$FRONTEND_DIR/.env.example" "$FRONTEND_ENV"
    echo "Created $FRONTEND_ENV"
  fi
}

read_env_value() {
  local file="$1"
  local key="$2"
  python3 - "$file" "$key" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
if not path.exists():
    print("")
    raise SystemExit
for line in path.read_text(encoding="utf-8").splitlines():
    if line.startswith(f"{key}="):
        print(line.split("=", 1)[1])
        break
else:
    print("")
PY
}

write_env_values() {
  local file="$1"
  shift
  python3 - "$file" "$@" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
updates = {}
for item in sys.argv[2:]:
    key, value = item.split("=", 1)
    updates[key] = value

lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
seen = set()
new_lines = []
for line in lines:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        new_lines.append(line)
        continue
    key = line.split("=", 1)[0]
    if key in updates:
        new_lines.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        new_lines.append(line)

for key, value in updates.items():
    if key not in seen:
        new_lines.append(f"{key}={value}")

path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
PY
}

prompt_public() {
  local env_name="$1"
  local label="$2"
  local default_value="$3"
  local value="${!env_name:-}"
  if [[ -n "$value" ]]; then
    printf '%s' "$value"
    return
  fi
  read -r -p "$label [$default_value]: " value
  printf '%s' "${value:-$default_value}"
}

prompt_secret() {
  local env_name="$1"
  local label="$2"
  local current_value="$3"
  local value="${!env_name:-}"
  if [[ -n "$value" ]]; then
    printf '%s' "$value"
    return
  fi
  if [[ -n "$current_value" ]]; then
    read -r -s -p "$label is already set. Press Enter to keep it, or paste a new value: " value
  else
    read -r -s -p "$label: " value
  fi
  echo >&2
  printf '%s' "${value:-$current_value}"
}

generate_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
  fi
}

configure_env() {
  ensure_env_files

  local current_openai
  current_openai="$(read_env_value "$BACKEND_ENV" "OPENAI_API_KEY")"
  local openai_key
  openai_key="$(prompt_secret "OPENAI_API_KEY" "OPENAI_API_KEY" "$current_openai")"

  local openai_base_url
  openai_base_url="$(prompt_public "OPENAI_BASE_URL" "OPENAI_BASE_URL" "$(read_env_value "$BACKEND_ENV" "OPENAI_BASE_URL")")"
  local llm_model
  llm_model="$(prompt_public "LLM_MODEL" "LLM_MODEL" "$(read_env_value "$BACKEND_ENV" "LLM_MODEL")")"
  local embedding_model
  embedding_model="$(prompt_public "EMBEDDING_MODEL" "EMBEDDING_MODEL" "$(read_env_value "$BACKEND_ENV" "EMBEDDING_MODEL")")"

  local current_bocha
  current_bocha="$(read_env_value "$BACKEND_ENV" "BOCHA_API_KEY")"
  local bocha_key
  bocha_key="$(prompt_secret "BOCHA_API_KEY" "BOCHA_API_KEY (optional, for web search)" "$current_bocha")"

  local current_docmind_id current_docmind_secret docmind_id docmind_secret
  current_docmind_id="$(read_env_value "$BACKEND_ENV" "DOCMIND_ACCESS_KEY_ID")"
  current_docmind_secret="$(read_env_value "$BACKEND_ENV" "DOCMIND_ACCESS_KEY_SECRET")"
  docmind_id="$(prompt_secret "DOCMIND_ACCESS_KEY_ID" "DOCMIND_ACCESS_KEY_ID (optional)" "$current_docmind_id")"
  docmind_secret="$(prompt_secret "DOCMIND_ACCESS_KEY_SECRET" "DOCMIND_ACCESS_KEY_SECRET (optional)" "$current_docmind_secret")"

  local jwt_secret
  jwt_secret="$(read_env_value "$BACKEND_ENV" "JWT_SECRET_KEY")"
  if [[ -z "$jwt_secret" || "$jwt_secret" == "replace-with-openssl-rand-hex-32-output" || "$jwt_secret" == "change-me-in-local-env" ]]; then
    jwt_secret="$(generate_secret)"
  fi

  write_env_values "$BACKEND_ENV" \
    "OPENAI_API_KEY=$openai_key" \
    "OPENAI_BASE_URL=${openai_base_url:-https://api.openai.com/v1}" \
    "LLM_MODEL=${llm_model:-gpt-4o-mini}" \
    "EMBEDDING_MODEL=${embedding_model:-text-embedding-3-small}" \
    "BOCHA_API_KEY=$bocha_key" \
    "DOCMIND_ACCESS_KEY_ID=$docmind_id" \
    "DOCMIND_ACCESS_KEY_SECRET=$docmind_secret" \
    "JWT_SECRET_KEY=$jwt_secret"

  local vite_api_base
  vite_api_base="$(prompt_public "VITE_API_BASE_URL" "VITE_API_BASE_URL" "$(read_env_value "$FRONTEND_ENV" "VITE_API_BASE_URL")")"
  write_env_values "$FRONTEND_ENV" "VITE_API_BASE_URL=${vite_api_base:-http://localhost:8000/api/v1}"

  mkdir -p "$BACKEND_DIR/data/milvus" "$BACKEND_DIR/data/uploads"
}

install_backend() {
  if [[ ! -d "$BACKEND_DIR/.venv" ]]; then
    python3 -m venv "$BACKEND_DIR/.venv"
  fi
  "$BACKEND_DIR/.venv/bin/python" -m pip install --upgrade pip
  "$BACKEND_DIR/.venv/bin/python" -m pip install -r "$BACKEND_DIR/requirements.txt"
}

install_frontend() {
  (cd "$FRONTEND_DIR" && npm install)
}

run_migrations() {
  (cd "$BACKEND_DIR" && "$BACKEND_DIR/.venv/bin/alembic" upgrade head)
}

warn_if_service_unavailable() {
  if command -v pg_isready >/dev/null 2>&1; then
    if ! pg_isready -h localhost -p 5432 >/dev/null 2>&1; then
      echo "Warning: PostgreSQL does not appear ready on localhost:5432." >&2
    fi
  fi
  if command -v redis-cli >/dev/null 2>&1; then
    if ! redis-cli -h localhost -p 6379 ping >/dev/null 2>&1; then
      echo "Warning: Redis does not appear ready on localhost:6379." >&2
    fi
  fi
}

cleanup() {
  local code=$?
  if [[ -n "${BACKEND_PID:-}" ]] && kill -0 "$BACKEND_PID" >/dev/null 2>&1; then
    kill "$BACKEND_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "${FRONTEND_PID:-}" ]] && kill -0 "$FRONTEND_PID" >/dev/null 2>&1; then
    kill "$FRONTEND_PID" >/dev/null 2>&1 || true
  fi
  exit "$code"
}

main() {
  configure_env

  if [[ "$SKIP_INSTALL" -eq 0 ]]; then
    install_backend
    install_frontend
  fi

  warn_if_service_unavailable

  if [[ "$NO_MIGRATE" -eq 0 ]]; then
    run_migrations
  fi

  trap cleanup INT TERM EXIT

  echo "Starting backend on http://localhost:8000"
  (cd "$BACKEND_DIR" && "$BACKEND_DIR/.venv/bin/uvicorn" app.main:app --reload --host 0.0.0.0 --port 8000) &
  BACKEND_PID=$!

  echo "Starting frontend on http://localhost:5173"
  (cd "$FRONTEND_DIR" && npm run dev -- --host 0.0.0.0) &
  FRONTEND_PID=$!

  echo
  echo "Development servers are running."
  echo "Frontend: http://localhost:5173"
  echo "Backend:  http://localhost:8000"
  echo "Press Ctrl+C to stop both servers."

  while kill -0 "$BACKEND_PID" >/dev/null 2>&1 && kill -0 "$FRONTEND_PID" >/dev/null 2>&1; do
    sleep 2
  done
}

main
