#!/usr/bin/env bash
# 最新版に更新する。コードを取得 → バックアップ → イメージ更新 → 再起動 → 動作確認。
#
#   bash scripts/update.sh            git pull してから更新
#   bash scripts/update.sh --no-pull  手元のコードのままイメージだけ更新(Caddy・Mosquitto・DB の更新も含む)
#
# 案A・案B のどちらでも使える(.env の COMPOSE_FILE に従う)。
set -euo pipefail
source "$(dirname "$0")/lib.sh"

PULL=1
[[ ${1:-} == --no-pull ]] && PULL=0

require_env
require_docker

# 以前の init.sh が mosquitto/config/acl の所有者を変えていると git pull で上書きできないため、戻す
if [[ -e mosquitto/config/acl && ! -w mosquitto/config/acl ]]; then
  docker run --rm -v "$PWD/mosquitto/config:/c" "$MOSQ_IMAGE" chown "$(id -u):$(id -g)" /c/acl
  chmod 644 mosquitto/config/acl
  info "mosquitto/config/acl の所有者を戻しました"
fi

if ((PULL)); then
  if [[ -n $(git status --porcelain --untracked-files=no) ]]; then
    git status --short --untracked-files=no
    die "手元で変更したファイルがあります。退避(git stash)するか --no-pull で実行してください。"
  fi
  before=$(git rev-parse --short HEAD)
  git pull --ff-only
  after=$(git rev-parse --short HEAD)
  if [[ $before == "$after" ]]; then info "コードは最新でした ($after)"; else ok "コードを更新しました ($before → $after)"; fi
fi

if is_plan_b && service_running db; then
  bash scripts/backup.sh now
fi

echo "-- イメージを更新しています…"
docker compose pull --quiet --ignore-buildable
if [[ $(env_value COMPOSE_FILE) != *compose.prebuilt.yaml* ]] && is_plan_b; then
  docker compose build --quiet --pull
fi
docker compose up -d --wait --wait-timeout 300 --remove-orphans || {
  docker compose ps
  die "起動に失敗しました。docker compose logs で原因を確認してください。"
}
if is_plan_b; then
  wait_api_healthy 120 || die "API が応答しません。docker compose logs api を確認してください。"
fi
docker image prune -f >/dev/null
ok "更新しました"
bash scripts/doctor.sh || true
