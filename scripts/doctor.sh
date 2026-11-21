#!/usr/bin/env bash
# 状態をまとめて確認する。問題があれば NG を出し、終了コード 1 で終わる。
#   bash scripts/doctor.sh
set -uo pipefail
source "$(dirname "$0")/lib.sh"

fails=0
bad() { ng "$*"; fails=$((fails + 1)); }

require_env
require_docker

MQTT_DOMAIN=$(env_value MQTT_DOMAIN)
APP_DOMAIN=$(env_value APP_DOMAIN)
LOCAL=0
[[ $(env_value CADDY_GLOBAL_OPTIONS) == *local_certs* ]] && LOCAL=1

echo "== 設定 =="
if is_plan_b; then ok "構成: 案B ($(env_value COMPOSE_FILE))"; else ok "構成: 案A"; fi
for k in MQTT_DOMAIN APP_DOMAIN ACME_EMAIL; do
  [[ -n $(env_value $k) ]] && ok "$k = $(env_value $k)" || bad "$k が空です"
done
if is_plan_b; then
  for k in API_MQTT_PASS DB_PASSWORD; do
    [[ -n $(env_value $k) ]] && ok "$k は設定済み" || bad "$k が空です"
  done
  [[ -n $(env_value INITIAL_ADMIN_PASS) ]] && warn "INITIAL_ADMIN_PASS が .env に残っています。管理者を作ったら消してください。"
fi
perm=$(stat -c '%a' .env 2>/dev/null || echo '?')
[[ $perm == 600 || $perm == 400 ]] && ok ".env の権限 $perm" || warn ".env の権限が $perm です。chmod 600 .env を勧めます(パスワードが入っているため)"
docker run --rm -v "$PWD/mosquitto/config:/c:ro" "$MOSQ_IMAGE" test -s /c/passwd &&
  ok "Mosquitto のパスワードファイルあり" || bad "mosquitto/config/passwd がありません(scripts/init.sh か setup.sh を実行)"

echo "== コンテナ =="
services=$(docker compose config --services 2>/dev/null)
for s in $services; do
  state=$(docker compose ps --all --format '{{.Service}} {{.State}} {{.Health}}' 2>/dev/null | awk -v s="$s" '$1==s{print $2, $3}')
  case $state in
    "running healthy" | "running ") ok "$s: 動作中" ;;
    "running starting") warn "$s: 起動中" ;;
    "") bad "$s: コンテナがありません(docker compose up -d)" ;;
    *) bad "$s: $state(docker compose logs $s で確認)" ;;
  esac
done

if is_plan_b && service_running api; then
  health=$(docker compose exec -T api python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=4).read().decode())" 2>/dev/null)
  if [[ $health == *'"mqtt":"connected"'* ]]; then ok "API: DB・MQTT とも接続中"; else bad "API: MQTT に接続できていません ($health)"; fi
  last=$(sed -n 's/.*"last_message_at":"\([^"]*\)".*/\1/p' <<<"$health")
  [[ -n $last ]] && info "最後に MQTT のメッセージを受けた時刻(UTC): $last" || info "起動後、まだ MQTT のメッセージは届いていません"
fi

echo "== DNS・証明書 =="
if ((LOCAL)); then
  info "お試しモード(local_certs)のため DNS と証明書の確認は省きます"
else
  my_ips=" $(hostname -I 2>/dev/null) "
  for d in "$MQTT_DOMAIN" "$APP_DOMAIN"; do
    ips=$(getent ahostsv4 "$d" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ')
    if [[ -z $ips ]]; then
      bad "$d の DNS が引けません"
      continue
    fi
    match=0
    for ip in $ips; do [[ $my_ips == *" $ip "* ]] && match=1; done
    if ((match)); then ok "$d → $ips(このサーバー)"; else warn "$d → $ips(このサーバーの IP $my_ips と違います。NAT 越しなら問題ありません)"; fi
    if command -v openssl >/dev/null; then
      end=$(echo | timeout 10 openssl s_client -connect "$d:443" -servername "$d" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
      if [[ -n $end ]]; then
        days=$((($(date -d "$end" +%s) - $(date +%s)) / 86400))
        if ((days > 7)); then ok "$d の証明書: あと $days 日(自動更新)"; else bad "$d の証明書: あと $days 日。docker compose logs caddy を確認"; fi
      else
        bad "$d:443 に TLS で接続できません(ファイアウォール・Caddy のログを確認)"
      fi
    fi
  done
fi

echo "== ディスク・バックアップ =="
df -h . | awk 'NR==2{print "--   ディスク: 使用 "$3" / "$2"("$5")"}'
use=$(df --output=pcent . | tail -1 | tr -dc '0-9')
((use < 90)) || bad "ディスクの空きが少なくなっています(${use}% 使用)"
if is_plan_b; then
  latest=$(ls -1t backups/*.dump 2>/dev/null | head -1)
  if [[ -z $latest ]]; then
    warn "バックアップがまだありません(backup コンテナが 1 日 1 回作ります。今すぐ取るなら scripts/backup.sh now)"
  else
    age=$((($(date +%s) - $(stat -c %Y "$latest")) / 3600))
    if ((age <= 26)); then ok "最新のバックアップ: $latest($age 時間前)"; else bad "最新のバックアップが $age 時間前です: $latest"; fi
  fi
fi

echo
if ((fails)); then
  echo "${C_NG}問題が $fails 件あります${C_RESET}"
  exit 1
fi
echo "${C_OK}問題は見つかりませんでした${C_RESET}"
