#!/usr/bin/env bash
# Mosquitto のパスワードファイルを作り、サービス用アカウントを登録する。
#   案A: Recorder 用(RECORDER_MQTT_USER / RECORDER_MQTT_PASS)
#   案B: API 用(API_MQTT_USER / API_MQTT_PASS)
# .env にパスワードが入っているものだけ登録する。何度実行してもよい(既存の端末アカウントは残る)。
set -euo pipefail
cd "$(dirname "$0")/.."

MOSQ_IMAGE=eclipse-mosquitto:2.0.23
DEFAULT_PASS=change-me-long-random-string

if [[ ! -f .env ]]; then
  echo ".env がありません。cp .env.example .env で作って値を入れてください。" >&2
  exit 1
fi

env_value() {
  grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- || true
}

accounts=()
for prefix in RECORDER API; do
  default_user=$(echo "$prefix" | tr '[:upper:]' '[:lower:]')
  user=$(env_value "${prefix}_MQTT_USER")
  pass=$(env_value "${prefix}_MQTT_PASS")
  user=${user:-$default_user}
  if [[ -z "$pass" ]]; then
    continue
  fi
  if [[ "$pass" == "$DEFAULT_PASS" ]]; then
    echo ".env の ${prefix}_MQTT_PASS を変更してください(使わないなら空にする)。" >&2
    exit 1
  fi
  if [[ "$user" != "$default_user" ]]; then
    echo "注意: mosquitto/config/acl の 'user $default_user' も $user に書き換えてください。" >&2
  fi
  accounts+=("$user" "$pass")
done

if [[ ${#accounts[@]} -eq 0 ]]; then
  echo ".env に RECORDER_MQTT_PASS(案A)か API_MQTT_PASS(案B)を設定してください。" >&2
  exit 1
fi

# パスワードは docker run の引数ではなく標準入力で渡す(ホストの ps やシェル履歴に出さない)
printf '%s\n' "${accounts[@]}" | docker run --rm -i -v "$PWD/mosquitto/config:/mosquitto/config" "$MOSQ_IMAGE" sh -c '
  set -e
  f=/mosquitto/config/passwd
  while IFS= read -r user && IFS= read -r pass; do
    if [ -f "$f" ]; then mosquitto_passwd -b "$f" "$user" "$pass"; else mosquitto_passwd -c -b "$f" "$user" "$pass"; fi
    echo "登録: $user"
  done
  chown 1883:1883 "$f" /mosquitto/config/acl
  chmod 0700 "$f" /mosquitto/config/acl
'

if docker compose ps --status running --services 2>/dev/null | grep -qx mosquitto; then
  docker compose kill -s HUP mosquitto >/dev/null
  echo "Mosquitto に再読み込みを指示しました。"
fi
echo "次に scripts/add-user.sh <ユーザー名> で端末のアカウントを追加してください。"
