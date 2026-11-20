#!/usr/bin/env bash
# 地図画面ログイン用のパスワードハッシュを作る(.env の WEB_PASS_HASH に貼る)
set -euo pipefail
docker run --rm -it caddy:2.11.9 caddy hash-password
