#!/usr/bin/env bash
# 案B の初回セットアップ。.env を作り(秘密情報は自動生成)、Mosquitto のアカウントを登録して起動する。
#
#   bash scripts/setup.sh                      対話で聞く
#   bash scripts/setup.sh --mqtt-domain mqtt.example.com --app-domain map.example.com \
#        --email you@example.net --admin admin --yes
#   bash scripts/setup.sh --local --yes        お試し(自分の PC で https://map.localhost を開く)
#
# オプション:
#   --mqtt-domain / --app-domain / --email / --admin <名前>
#   --prebuilt   API のイメージをビルドせず GitHub のレジストリから取得する(メモリの少ない VPS 向け)
#   --local      ドメインなしで試す(mqtt.localhost / map.localhost、証明書は Caddy の自己署名)
#   --yes        確認せずに進める
# 管理者のパスワードは自動生成して最後に 1 回だけ表示する(環境変数 SETUP_ADMIN_PASS で指定も可)。
set -euo pipefail
source "$(dirname "$0")/lib.sh"

MQTT_DOMAIN='' APP_DOMAIN='' EMAIL='' ADMIN='' PREBUILT=0 LOCAL=0 YES=0
while (($#)); do
  case $1 in
    --mqtt-domain) MQTT_DOMAIN=$2; shift 2 ;;
    --app-domain) APP_DOMAIN=$2; shift 2 ;;
    --email) EMAIL=$2; shift 2 ;;
    --admin) ADMIN=$2; shift 2 ;;
    --prebuilt) PREBUILT=1; shift ;;
    --local) LOCAL=1; shift ;;
    --yes | -y) YES=1; shift ;;
    -h | --help) sed -n '2,15p' "$0"; exit 0 ;;
    *) die "知らないオプションです: $1" ;;
  esac
done

ask() { # ask 変数名 質問 [既定値]
  local __var=$1 prompt=$2 def=${3:-} ans
  if [[ -n ${!__var} ]]; then return; fi
  if ((YES)) && [[ -n $def ]]; then printf -v "$__var" '%s' "$def"; return; fi
  ((YES)) && die "$prompt を指定してください(--yes のときは省略できません)"
  read -r -p "$prompt${def:+ [$def]}: " ans
  printf -v "$__var" '%s' "${ans:-$def}"
}

valid_domain() { [[ $1 =~ ^([A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z0-9-]+$ ]]; }

require_docker
[[ -f .env ]] && die ".env が既にあります。作り直すときは .env を別名に退避してから実行してください。"

echo "== 案B のセットアップ =="
if ((LOCAL)); then
  MQTT_DOMAIN=${MQTT_DOMAIN:-mqtt.localhost}
  APP_DOMAIN=${APP_DOMAIN:-map.localhost}
  EMAIL=${EMAIL:-local@example.net}
fi
ask MQTT_DOMAIN "スマホが接続するドメイン(例: mqtt.example.com)"
ask APP_DOMAIN "地図画面のドメイン(例: map.example.com)"
ask EMAIL "Let's Encrypt に登録するメールアドレス"
ask ADMIN "最初の管理者の名前(英数字)" "admin"

valid_domain "$MQTT_DOMAIN" || die "ドメインの形が正しくありません: $MQTT_DOMAIN"
valid_domain "$APP_DOMAIN" || die "ドメインの形が正しくありません: $APP_DOMAIN"
[[ $MQTT_DOMAIN != "$APP_DOMAIN" ]] || die "MQTT と地図画面には別々のドメインが必要です(OwnTracks は WebSocket のパスを指定できないため)"
[[ $EMAIL == *@*.* ]] || die "メールアドレスの形が正しくありません: $EMAIL"
valid_user_name "$ADMIN" || die "管理者の名前は英数字・_・- だけにしてください"

# DNS が向いているかの確認(結果は参考。最終的には Caddy が証明書を取れるかで分かる)
if ((!LOCAL)); then
  for d in "$MQTT_DOMAIN" "$APP_DOMAIN"; do
    ips=$(getent ahostsv4 "$d" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ' || true)
    if [[ -z $ips ]]; then
      warn "$d の DNS がまだ引けません。A レコードを設定してから起動しないと証明書の取得に失敗します。"
    else
      info "$d → $ips"
    fi
  done
  info "このサーバーの IP: $(hostname -I 2>/dev/null || echo '不明')"
fi

COMPOSE_FILE_VALUE=compose.b.yaml
((PREBUILT)) && COMPOSE_FILE_VALUE=compose.b.yaml:compose.prebuilt.yaml
ADMIN_PASS=${SETUP_ADMIN_PASS:-$(random_secret 20)}

echo
echo "MQTT: $MQTT_DOMAIN / 地図: $APP_DOMAIN / 管理者: $ADMIN / イメージ: $( ((PREBUILT)) && echo 取得 || echo ビルド)"
if ((!YES)); then
  read -r -p "この内容で .env を作って起動します。よろしいですか? [y/N] " ans
  [[ $ans == [yY]* ]] || die "中止しました"
fi

umask 077
cat >.env <<EOF
# scripts/setup.sh が $(date '+%Y-%m-%d %H:%M') に作成。値の意味は .env.example を参照
COMPOSE_FILE=$COMPOSE_FILE_VALUE
MQTT_DOMAIN=$MQTT_DOMAIN
APP_DOMAIN=$APP_DOMAIN
ACME_EMAIL=$EMAIL
TZ=${TZ:-Asia/Tokyo}
$( ((LOCAL)) && echo "CADDY_GLOBAL_OPTIONS=local_certs" || echo "CADDY_GLOBAL_OPTIONS=" )

API_MQTT_USER=api
API_MQTT_PASS=$(random_secret)
DB_PASSWORD=$(random_secret)
INITIAL_ADMIN_USER=$ADMIN
INITIAL_ADMIN_PASS=$ADMIN_PASS
RETENTION_DAYS=365
MAP_STYLE_URL=
ALERT_WEBHOOK_URL=
ALERT_STALE_HOURS=0
NOTIFY_DEVICE_TRANSITIONS=true
BACKUP_KEEP=7
# バックアップのファイルの持ち主(このユーザー)
BACKUP_UID=$(id -u)
BACKUP_GID=$(id -g)

# 案A(Recorder)は使わない
RECORDER_MQTT_USER=recorder
RECORDER_MQTT_PASS=
EOF
umask 022
ok ".env を作成しました(パスワード類は自動生成)"
mkdir -p backups
chmod 700 backups

bash scripts/init.sh >/dev/null
ok "Mosquitto に API 用アカウントを登録しました"

if ((PREBUILT)); then
  echo "-- イメージを取得しています…"
  docker compose pull --quiet
else
  echo "-- イメージを取得・ビルドしています(初回は数分かかります)…"
  docker compose pull --quiet --ignore-buildable
  docker compose build --quiet
fi
docker compose up -d --wait --wait-timeout 300 || {
  docker compose ps
  die "起動に失敗しました。docker compose logs で原因を確認してください。"
}
wait_api_healthy 120 || die "API が応答しません。docker compose logs api を確認してください。"
ok "起動しました"

# 管理者ができたら、パスワードを .env から消して API を作り直す(コンテナの環境変数に残さない)
grep -q "^$ADMIN"$'\t'"admin" <<<"$(api_cli list-users)" || die "管理者 $ADMIN を作れませんでした"
env_set INITIAL_ADMIN_PASS ''
docker compose up -d --wait --wait-timeout 120 api >/dev/null
ok "管理者 $ADMIN を作成しました(初期パスワードは .env から消しました)"

cat <<EOF

========================================================
  地図画面:   https://$APP_DOMAIN/
  管理者:     $ADMIN
  パスワード: $ADMIN_PASS
  (このパスワードは今だけ表示します。ログイン後「設定 → パスワード」で変えてください)
========================================================

次の手順:
  1. 利用者(スマホ)を追加:   bash scripts/member.sh add <名前>
     → スマホで読み取る QR コードと、OwnTracks の設定ファイルを作ります
  2. 状態の確認:             bash scripts/doctor.sh
EOF
if ((LOCAL)); then
  cat <<EOF

お試しモード: 証明書は Caddy の自己署名です。ブラウザの警告を許可して https://$APP_DOMAIN/ を開いてください。
(スマホからはつながりません。本番では --local を付けずに実行し直してください)
EOF
fi
