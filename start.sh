#!/usr/bin/env bash
# start.sh — starts backend (port 8000) and frontend (port 8080) together
# Usage: ./start.sh [--no-reload]
# Stop:  Ctrl+C

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BACKEND_PORT=8000
FRONTEND_PORT=8080
FRONTEND_DIR="frontend"
RELOAD_FLAG="--reload"

if [[ "${1:-}" == "--no-reload" ]]; then
  RELOAD_FLAG=""
fi

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

log()  { echo -e "${BOLD}[start]${RESET} $*"; }
ok()   { echo -e "${GREEN}  ✓${RESET} $*"; }
warn() { echo -e "${YELLOW}  ⚠${RESET} $*"; }
err()  { echo -e "${RED}  ✗${RESET} $*"; }

# ── Load .env if present ─────────────────────────────────────────────────────
if [[ -f ".env" ]]; then
  log "Loading .env"
  set -o allexport
  # shellcheck disable=SC1091
  source .env
  set +o allexport
fi

# ── Check API keys ────────────────────────────────────────────────────────────
if [[ -z "${OPENAI_API_KEY:-}" || "${OPENAI_API_KEY}" == "YOUR_OPENAI_API_KEY" ]]; then
  warn "OPENAI_API_KEY not set — LLM calls will fail (agent won't respond)"
  warn "Set it in .env or: export OPENAI_API_KEY=sk-..."
fi

if [[ -z "${GEMINI_API_KEY:-}" || "${GEMINI_API_KEY}" == "YOUR_GEMINI_API_KEY" ]]; then
  warn "GEMINI_API_KEY not set — Gemini ASR/TTS will fail"
fi

# ── Find Python ───────────────────────────────────────────────────────────────
PYTHON=""
for candidate in ".venv/bin/python3" ".venv/bin/python" python3 python; do
  if command -v "$candidate" &>/dev/null 2>&1 || [[ -f "$candidate" ]]; then
    PYTHON="$candidate"
    break
  fi
done

if [[ -z "$PYTHON" ]]; then
  err "Python not found. Create a venv first:"
  echo "    python3 -m venv .venv && source .venv/bin/activate"
  echo "    pip install -r backend/requirements.txt"
  exit 1
fi

# Prefer venv python if it has uvicorn
if [[ -f ".venv/bin/uvicorn" ]]; then
  UVICORN=".venv/bin/uvicorn"
elif command -v uvicorn &>/dev/null; then
  UVICORN="uvicorn"
else
  err "uvicorn not found. Install dependencies:"
  echo "    source .venv/bin/activate && pip install -r backend/requirements.txt"
  exit 1
fi

ok "Python:  $("$PYTHON" --version)"
ok "uvicorn: $UVICORN"

# ── Check ports are free ──────────────────────────────────────────────────────
check_port() {
  local port=$1 name=$2
  if lsof -ti tcp:"$port" &>/dev/null; then
    warn "Port $port already in use ($name) — killing existing process"
    lsof -ti tcp:"$port" | xargs kill -9 2>/dev/null || true
    sleep 0.5
  fi
}

check_port "$BACKEND_PORT"  "backend"
check_port "$FRONTEND_PORT" "frontend"

# ── Process tracking ──────────────────────────────────────────────────────────
BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
  echo ""
  log "Shutting down..."
  [[ -n "$BACKEND_PID"  ]] && kill "$BACKEND_PID"  2>/dev/null && ok "Backend stopped"
  [[ -n "$FRONTEND_PID" ]] && kill "$FRONTEND_PID" 2>/dev/null && ok "Frontend stopped"
  exit 0
}
trap cleanup INT TERM

# ── Start backend ─────────────────────────────────────────────────────────────
log "Starting backend on http://localhost:${BACKEND_PORT}"

PYTHONPATH="$SCRIPT_DIR" \
  "$UVICORN" backend.main:app \
  $RELOAD_FLAG \
  --port "$BACKEND_PORT" \
  --host 0.0.0.0 \
  --log-level info \
  2>&1 | sed "s/^/${CYAN}[backend]${RESET} /" &

BACKEND_PID=$!

# Wait for backend to be ready (up to 10s)
echo -n "  Waiting for backend"
for i in $(seq 1 20); do
  if curl -sf "http://localhost:${BACKEND_PORT}/docs" &>/dev/null; then
    echo ""
    ok "Backend ready"
    break
  fi
  echo -n "."
  sleep 0.5
done
echo ""

# ── Start frontend (Vite dev server) ──────────────────────────────────────────
log "Starting frontend on http://localhost:${FRONTEND_PORT}"

cd "$FRONTEND_DIR"
npm run dev 2>&1 | sed "s/^/${YELLOW}[frontend]${RESET} /" &
FRONTEND_PID=$!
cd "$SCRIPT_DIR"
sleep 0.5

# ── Print URLs ────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}══════════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}  Voice Agent Console is running!${RESET}"
echo -e "${BOLD}══════════════════════════════════════════${RESET}"
echo ""
echo -e "  ${BOLD}Console:${RESET}  http://localhost:${FRONTEND_PORT}"
echo -e "  ${BOLD}API docs:${RESET} http://localhost:${BACKEND_PORT}/docs"
echo -e "  ${BOLD}WS:${RESET}       ws://localhost:${BACKEND_PORT}/ws/{session_id}"
echo ""
echo -e "  Press ${BOLD}Ctrl+C${RESET} to stop both servers"
echo ""

# ── Wait ──────────────────────────────────────────────────────────────────────
wait
