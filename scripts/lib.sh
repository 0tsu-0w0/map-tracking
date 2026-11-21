# scripts/*.sh で共通に使う関数。直接は実行しない(source して使う)。
# shellcheck shell=bash

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

MOSQ_IMAGE=eclipse-mosquitto:2.0.23

if [[ -t 1 ]]; then
  C_OK=$'\e[32m' C_NG=$'\e[31m' C_WARN=$'\e[33m' C_DIM=$'\e[2m' C_RESET=$'\e[0m'
else
  C_OK='' C_NG='' C_WARN='' C_DIM='' C_RESET=''
fi

ok() { echo "${C_OK}OK${C_RESET}   $*"; }
ng() { echo "${C_NG}NG${C_RESET}   $*"; }
warn() { echo "${C_WARN}注意${C_RESET} $*"; }
info() { echo "${C_DIM}--${C_RESET}   $*"; }
die() {
  echo "${C_NG}エラー:${C_RESET} $*" >&2
  exit 1
}

# .env の値を読む(無ければ空)。前後の引用符は外す
env_value() {
  [[ -f .env ]] || return 0
  local v
  v=$(grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- || true)
  v=${v%\"}
  v=${v#\"}
  v=${v%\'}
  v=${v#\'}
  printf '%s' "$v"
}

# .env の値を書き換える(無ければ末尾に足す)。値に / や & が入ってもよいよう awk で置き換える
env_set() {
  local key=$1 value=$2
  touch .env
  if grep -qE "^$key=" .env; then
    KEY=$key VALUE=$value awk 'BEGIN{FS=OFS="="} $1==ENVIRON["KEY"]{print ENVIRON["KEY"], ENVIRON["VALUE"]; next} {print}' .env >.env.tmp
    cat .env.tmp >.env
    rm -f .env.tmp
  else
    printf '%s=%s\n' "$key" "$value" >>.env
  fi
}

# 英数字だけのランダム文字列(既定 40 文字)
random_secret() {
  local n=${1:-40} s=''
  while ((${#s} < n)); do
    s+=$(head -c 64 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')
  done
  printf '%s' "${s:0:n}"
}

require_docker() {
  command -v docker >/dev/null 2>&1 || die "docker が見つかりません。Docker をインストールしてください。"
  docker compose version >/dev/null 2>&1 || die "docker compose(v2)が使えません。Docker Compose プラグインを入れてください。"
  docker info >/dev/null 2>&1 || die "Docker に接続できません。Docker が起動しているか、このユーザーが docker グループに入っているか確認してください。"
}

require_env() {
  [[ -f .env ]] || die ".env がありません。先に scripts/setup.sh を実行してください。"
}

# 案B(COMPOSE_FILE に compose.b.yaml を含む)かどうか
is_plan_b() {
  [[ "$(env_value COMPOSE_FILE)" == *compose.b.yaml* ]]
}

service_running() {
  docker compose ps --status running --services 2>/dev/null | grep -qx "$1"
}

# Mosquitto のパスワードファイルに「名前 改行 パスワード」の組を標準入力から登録する
mosq_passwd_set() {
  docker run --rm -i -v "$PWD/mosquitto/config:/mosquitto/config" "$MOSQ_IMAGE" sh -c '
    set -e
    f=/mosquitto/config/passwd
    while IFS= read -r user && IFS= read -r pass; do
      if [ -f "$f" ]; then mosquitto_passwd -b "$f" "$user" "$pass"; else mosquitto_passwd -c -b "$f" "$user" "$pass"; fi
    done
    chown root:root "$f"
    chmod 0700 "$f"
  '
}

mosq_passwd_delete() {
  docker run --rm -v "$PWD/mosquitto/config:/mosquitto/config" "$MOSQ_IMAGE" \
    mosquitto_passwd -D /mosquitto/config/passwd "$1"
}

mosq_passwd_has() {
  docker run --rm -v "$PWD/mosquitto/config:/mosquitto/config" "$MOSQ_IMAGE" \
    sh -c "[ -f /mosquitto/config/passwd ] && cut -d: -f1 /mosquitto/config/passwd | grep -qx '$1'"
}

mosq_reload() {
  if service_running mosquitto; then
    docker compose kill -s HUP mosquitto >/dev/null
  fi
}

# API コンテナの管理コマンド(python -m app.cli ...)
api_cli() {
  docker compose exec -T api python -m app.cli "$@"
}

wait_api_healthy() {
  local timeout=${1:-180} waited=0
  until docker compose exec -T api python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" >/dev/null 2>&1; do
    ((waited >= timeout)) && return 1
    sleep 3
    waited=$((waited + 3))
  done
}

valid_user_name() {
  [[ "$1" =~ ^[A-Za-z0-9_-]{1,64}$ ]]
}
