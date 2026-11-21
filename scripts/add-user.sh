#!/usr/bin/env bash
# 端末(OwnTracks)用の MQTT アカウントを追加・パスワード変更する。
#   使い方: scripts/add-user.sh <ユーザー名>      → パスワードを対話入力
#   削除:   scripts/add-user.sh -D <ユーザー名>
set -euo pipefail
cd "$(dirname "$0")/.."

MOSQ_IMAGE=eclipse-mosquitto:2.0.23

if [[ ! -f mosquitto/config/passwd ]]; then
  echo "先に scripts/init.sh を実行してください。" >&2
  exit 1
fi

if [[ "${1:-}" == "-D" ]]; then
  user=${2:?ユーザー名を指定してください}
  docker run --rm -v "$PWD/mosquitto/config:/mosquitto/config" "$MOSQ_IMAGE" \
    mosquitto_passwd -D /mosquitto/config/passwd "$user"
else
  user=${1:?ユーザー名を指定してください}
  if [[ ! "$user" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "ユーザー名は英数字・_・- だけにしてください(トピック名に使われるため)。" >&2
    exit 1
  fi
  # -it: パスワードをコンテナ内で対話入力(履歴に残さない)
  docker run --rm -it -v "$PWD/mosquitto/config:/mosquitto/config" "$MOSQ_IMAGE" \
    mosquitto_passwd /mosquitto/config/passwd "$user"
fi

docker run --rm -v "$PWD/mosquitto/config:/mosquitto/config" "$MOSQ_IMAGE" sh -c \
  "chown root:root /mosquitto/config/passwd && chmod 0700 /mosquitto/config/passwd"

# 起動中なら設定を再読み込み(再起動不要)
if docker compose ps --status running --services 2>/dev/null | grep -qx mosquitto; then
  docker compose kill -s HUP mosquitto >/dev/null
  echo "Mosquitto に再読み込みを指示しました。"
fi
echo "完了: $user"
