#!/usr/bin/env bash
# Mosquitto のパスワードファイルを作り、サービス用アカウントを登録する。
#   案A: Recorder 用(RECORDER_MQTT_USER / RECORDER_MQTT_PASS)
#   案B: API 用(API_MQTT_USER / API_MQTT_PASS)
# .env にパスワードが入っているものだけ登録する。何度実行してもよい(既存の端末アカウントは残る)。
# 案B を新しく始めるときは scripts/setup.sh がこれを呼ぶ。
set -euo pipefail
source "$(dirname "$0")/lib.sh"

DEFAULT_PASS=change-me-long-random-string
require_env

accounts=()
for prefix in RECORDER API; do
  default_user=$(echo "$prefix" | tr '[:upper:]' '[:lower:]')
  user=$(env_value "${prefix}_MQTT_USER")
  pass=$(env_value "${prefix}_MQTT_PASS")
  user=${user:-$default_user}
  [[ -z $pass ]] && continue
  if [[ $pass == "$DEFAULT_PASS" ]]; then
    die ".env の ${prefix}_MQTT_PASS を変更してください(使わないなら空にする)。"
  fi
  if [[ $user != "$default_user" ]]; then
    warn "mosquitto/config/acl の 'user $default_user' も $user に書き換えてください。"
  fi
  accounts+=("$user" "$pass")
done

((${#accounts[@]})) || die ".env に RECORDER_MQTT_PASS(案A)か API_MQTT_PASS(案B)を設定してください。"

# パスワードは docker run の引数ではなく標準入力で渡す(ホストの ps やシェル履歴に出さない)
printf '%s\n' "${accounts[@]}" | mosq_passwd_set
for ((i = 0; i < ${#accounts[@]}; i += 2)); do
  echo "登録: ${accounts[i]}"
done

mosq_reload
echo "次に端末のアカウントを追加してください: 案B は scripts/member.sh add <名前>、案A は scripts/add-user.sh <名前>"
