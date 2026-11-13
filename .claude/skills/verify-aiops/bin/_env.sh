# launch, doctor, drive, cleanup이 함께 쓰는 값. RUN_ID가 한 번의 검증 실행을 가리킨다.
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(cd "$SKILL_DIR/../../.." && pwd)"
PROJECT=aiops-verify
STATE_BASE="${TMPDIR:-/tmp}/aiops-verify"
EVIDENCE_BASE="$ROOT/results/verify"
export PATH="$PATH:$HOME/.docker/bin"
compose() { docker compose -p "$PROJECT" -f "$ROOT/docker-compose.yml" -f "$SKILL_DIR/verify.compose.yml" "$@"; }
need_run_id() { [ -n "${RUN_ID:-}" ] || { echo "RUN_ID가 필요하다. launch.sh가 출력한 값을 export RUN_ID=... 로 넣는다" >&2; exit 2; }; }
