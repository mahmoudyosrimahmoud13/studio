#!/usr/bin/env bash
# Studio: clean product photos (background, centering, light). Run ./run.sh help for everything it can do.
set -e
cd "$(dirname "$0")"
ROOT="$(pwd)"
PY="$ROOT/.venv/bin/python"
PORT="${STUDIO_PORT:-8300}"
PIDFILE="$ROOT/.studio.pid"
MODEL="$ROOT/models/isnet-general-use.onnx"
MODEL_URL="https://github.com/danielgatis/rembg/releases/download/v0.0.0/isnet-general-use.onnx"
MODEL_SHA="60920e99c45464f2ba57bee2ad08c919a52bbf852739e96947fbb4358c0d964a"

say()  { printf "\n\033[1;31m◆\033[0m \033[1m%s\033[0m\n" "$1"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
fail() { printf "\n\033[31m✗ %s\033[0m\n" "$1"; exit 1; }

show_help() { cat <<'EOF'

  Studio: clean, matching product photos

    ./run.sh                    set up what's missing, then open the page at http://localhost:8300 (Ctrl+C stops it)
    ./run.sh --background       same, but keeps running after you close the window (stop with ./run.sh stop)
    ./run.sh --phone            also reachable from phones on the same Wi-Fi (http://<this laptop's address>:8300)
    ./run.sh stop               stop it
    ./run.sh clean <in> <out>   clean a whole folder from the terminal, no page needed, for example:
                                  ./run.sh clean ~/Pictures/new-pieces ~/Pictures/new-pieces-clean
                                options: --background "#F4F2EF" | transparent, --shape portrait, --format jpg, --no-enhance
    ./run.sh help               this list

EOF
}

running() { (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; }

setup() {
  say "Getting Studio ready"
  command -v python3 >/dev/null || fail "Python 3 is missing. Install it: sudo apt install -y python3 python3-venv"
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || fail "Python 3.10 or newer is needed (you have $(python3 --version))."
  if [ ! -x "$PY" ]; then
    echo "  Creating Studio's private Python folder (.venv)…"
    python3 -m venv .venv || fail "Couldn't create .venv. Install the tool: sudo apt install -y python3-venv"
  fi
  if [ requirements.txt -nt .venv/.installed ] || ! "$PY" -c "import onnxruntime, PIL, fastapi" 2>/dev/null; then
    echo "  Installing the photo libraries (first time takes a minute)…"
    "$PY" -m pip install -q --upgrade pip
    "$PY" -m pip install -q -r requirements.txt
    touch .venv/.installed
  fi
  ok "Libraries ready"
  if [ ! -f "$MODEL" ] || [ "$(sha256sum "$MODEL" | cut -d' ' -f1)" != "$MODEL_SHA" ]; then
    echo "  Downloading the background model (about 170 MB, one time only)…"
    mkdir -p "$ROOT/models"
    curl -fL --progress-bar -o "$MODEL.part" "$MODEL_URL" || fail "The download failed. Check the internet connection and run ./run.sh again."
    [ "$(sha256sum "$MODEL.part" | cut -d' ' -f1)" = "$MODEL_SHA" ] || { rm -f "$MODEL.part"; fail "The downloaded model isn't the expected file, so it was deleted. Try again."; }
    mv "$MODEL.part" "$MODEL"
  fi
  ok "Background model ready"
}

CMD="start"; BG=0; HOST="127.0.0.1"
case "${1:-}" in
  stop|clean|help|-h|--help) CMD="$1"; shift ;;
esac
if [ "$CMD" = "start" ]; then
  for a in "$@"; do
    case "$a" in
      --background|--bg) BG=1 ;;
      --phone) HOST="0.0.0.0" ;;
      *) fail "Unknown option: $a (try ./run.sh help)" ;;
    esac
  done
fi

case "$CMD" in
  help|-h|--help) show_help ;;

  stop)
    if [ -f "$PIDFILE" ]; then kill "$(cat "$PIDFILE")" 2>/dev/null || true; rm -f "$PIDFILE"; fi
    for pid in $(ss -ltnpH "sport = :$PORT" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u); do kill "$pid" 2>/dev/null || true; done
    for _ in $(seq 1 10); do running || break; sleep 1; done
    running && fail "Something still uses port $PORT." || ok "Studio stopped" ;;

  clean)
    setup
    [ $# -ge 2 ] || fail "Say which folder to clean and where to save. Example: ./run.sh clean ~/Pictures/in ~/Pictures/out"
    "$PY" -m studio.cli "$@" ;;

  start)
    running && fail "Studio is already running at http://localhost:$PORT (stop it with ./run.sh stop)."
    setup
    say "Starting Studio"
    mkdir -p "$ROOT/jobs"
    if [ "$BG" = 1 ]; then
      setsid nohup "$PY" -m uvicorn studio.web:app --host "$HOST" --port "$PORT" --log-level warning >>"$ROOT/studio.log" 2>&1 </dev/null &
      echo $! >"$PIDFILE"
    else
      "$PY" -m uvicorn studio.web:app --host "$HOST" --port "$PORT" --log-level warning &
      echo $! >"$PIDFILE"
      trap 'echo; echo "Stopping Studio…"; kill $(cat "$PIDFILE") 2>/dev/null; rm -f "$PIDFILE"; exit 0' INT TERM
    fi
    for _ in $(seq 1 30); do running && break; sleep 1; done
    running || fail "Studio didn't start. See studio.log."
    echo
    echo "  ┌──────────────────────────────────────────────"
    echo "  │  Studio is running: http://localhost:$PORT"
    if [ "$HOST" = "0.0.0.0" ]; then
      ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
      echo "  │  From a phone on the same Wi-Fi: http://${ip:-<laptop address>}:$PORT"
    fi
    [ "$BG" = 1 ] && echo "  │  Stop it with: ./run.sh stop" || echo "  │  Press Ctrl+C to stop."
    echo "  └──────────────────────────────────────────────"
    [ "$BG" = 1 ] && exit 0
    wait ;;
esac
