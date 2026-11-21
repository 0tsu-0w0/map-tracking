#!/usr/bin/env bash
# 構成一式の通しテスト(CI の e2e ジョブ)。Docker が使える Linux で実行する。
#   sudo 権限で /etc/hosts に mqtt.localhost・map.localhost を足すので、使い捨ての環境で動かすこと。
set -euo pipefail
cd "$(dirname "$0")/.."

export SETUP_ADMIN_PASS=e2e-admin-password-$RANDOM

grep -q "map.localhost" /etc/hosts || echo "127.0.0.1 mqtt.localhost map.localhost" | sudo tee -a /etc/hosts >/dev/null

echo "::group::setup.sh"
bash scripts/setup.sh --local --yes --admin admin
echo "::endgroup::"

echo "::group::member.sh"
bash scripts/member.sh add taro --no-qr
bash scripts/member.sh add jiro --encrypt --no-qr
bash scripts/member.sh qr taro >/dev/null # QR の表示が動くか
bash scripts/member.sh list
echo "::endgroup::"

# Caddy のローカル CA(TLS の検証に使う)
for _ in $(seq 1 30); do
  docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt e2e/root.crt 2>/dev/null && break
  sleep 2
done
test -s e2e/root.crt

echo "::group::通しテスト"
python e2e/check.py
echo "::endgroup::"

echo "::group::バックアップと復元"
count() { docker compose exec -T db psql -U tracking -d tracking -tAc "SELECT count(*) FROM locations" | tr -d '[:space:]'; }
before=$(count)
bash scripts/backup.sh now
f=$(ls -1t backups/manual-*.dump | head -1)
docker compose exec -T db psql -U tracking -d tracking -qc "DELETE FROM locations"
[[ $(count) == 0 ]]
bash scripts/backup.sh restore "$f" --yes
after=$(count)
echo "復元前 $before 件 → 削除 → 復元後 $after 件"
[[ $before == "$after" && $after -gt 0 ]]
echo "::endgroup::"

echo "::group::doctor.sh"
bash scripts/doctor.sh
echo "::endgroup::"

echo "::group::member.sh disable"
bash scripts/member.sh disable taro
python - <<'EOF'
import asyncio, json, ssl
import aiomqtt
c = json.load(open("members/taro/phone.otrc"))
ctx = ssl.create_default_context(cafile="e2e/root.crt")
async def main():
    try:
        async with aiomqtt.Client("mqtt.localhost", 443, transport="websockets", websocket_path="/", tls_context=ctx,
                                  username=c["username"], password=c["password"], identifier="e2e-disabled", timeout=10):
            raise SystemExit("NG   無効にした利用者が MQTT に接続できてしまう")
    except aiomqtt.MqttError:
        print("OK   無効にした利用者は MQTT に接続できない")
asyncio.run(main())
EOF
echo "::endgroup::"

echo "::group::update.sh --no-pull"
bash scripts/update.sh --no-pull
echo "::endgroup::"

echo "通しテスト: すべて成功"
