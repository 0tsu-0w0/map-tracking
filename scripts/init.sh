#!/usr/bin/env bash
# 初回だけ実行: Mosquitto のパスワードファイルを作り、Recorder 用アカウントを登録する。
set -euo pipefail
cd "$(dirname "$0")/.."

MOSQ_IMAGE=eclipse-mosquitto:2.0.23

if [[ ! -f .env ]]; then
  echo ".env がありません。cp .env.example .env で作って値を入れてください。" >&2
  exit 1
fi

# .env から必要な値だけ読む
RECORDER_MQTT_USER=$(grep -E '^RECORDER_MQTT_USER=' .env | cut -d= -f2- || true)
RECORDER_MQTT_PASS=$(grep -E '^RECORDER_MQTT_PASS=' .env | cut -d= -f2- || true)
RECORDER_MQTT_USER=${RECORDER_MQTT_USER:-recorder}

if [[ -z "$RECORDER_MQTT_PASS" || "$RECORDER_MQTT_PASS" == "change-me-long-random-string" ]]; then
  echo ".env の RECORDER_MQTT_PASS を変更してください。" >&2
  exit 1
fi
if [[ "$RECORDER_MQTT_USER" != "recorder" ]]; then
  echo "注意: mosquitto/config/acl の 'user recorder' も $RECORDER_MQTT_USER に書き換えてください。" >&2
fi

if [[ -f mosquitto/config/passwd ]]; then
  echo "mosquitto/config/passwd は既にあります。利用者の追加は scripts/add-user.sh を使ってください。" >&2
  exit 1
fi

docker run --rm -v "$PWD/mosquitto/config:/mosquitto/config" "$MOSQ_IMAGE" sh -c "
  mosquitto_passwd -c -b /mosquitto/config/passwd '$RECORDER_MQTT_USER' '$RECORDER_MQTT_PASS' &&
  chown 1883:1883 /mosquitto/config/passwd /mosquitto/config/acl &&
  chmod 0700 /mosquitto/config/passwd /mosquitto/config/acl
"

echo "Recorder 用アカウント '$RECORDER_MQTT_USER' を登録しました。"
echo "次に scripts/add-user.sh <ユーザー名> で端末のアカウントを追加してください。"
