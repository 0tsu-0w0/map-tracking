#!/usr/bin/env bash
# 案B の利用者(スマホ)を管理する。MQTT のアカウントと Web のログインをまとめて作り、
# OwnTracks にそのまま読み込める接続設定(QR コード・.otrc ファイル)を作る。
#
#   bash scripts/member.sh add <名前> [--device <端末名>] [--admin] [--encrypt] [--no-qr]
#        利用者を追加(既にいれば MQTT のパスワードを作り直す)。端末名の既定は phone
#        --encrypt: OwnTracks のペイロード暗号化を有効にする(鍵を生成してサーバーにも登録)
#   bash scripts/member.sh qr <名前> [--device <端末名>]   保存済みの接続設定の QR をもう一度表示
#   bash scripts/member.sh web-password <名前>             Web のパスワードを作り直す
#   bash scripts/member.sh disable <名前>                  MQTT のアカウントを消し、Web も無効にする(履歴は残る)
#   bash scripts/member.sh enable <名前>                   無効にした利用者を戻す(MQTT は add で作り直す)
#   bash scripts/member.sh list
#
# 作ったファイルは members/<名前>/ に置く(Git には入らない)。本人に渡したら消してよい。
set -euo pipefail
source "$(dirname "$0")/lib.sh"

usage() { sed -n '2,15p' "$0"; exit "${1:-0}"; }

require_env
require_docker
is_plan_b || die "member.sh は案B 用です(.env の COMPOSE_FILE に compose.b.yaml を設定)。案A は scripts/add-user.sh を使ってください。"
service_running api || die "API が起動していません。docker compose up -d を実行してください。"

cmd=${1:-}
[[ -n $cmd ]] || usage 1
shift
name=''
device=phone
admin=0 encrypt=0 show_qr=1
while (($#)); do
  case $1 in
    --device) device=$2; shift 2 ;;
    --admin) admin=1; shift ;;
    --encrypt) encrypt=1; shift ;;
    --no-qr) show_qr=0; shift ;;
    -h | --help) usage ;;
    -*) die "知らないオプションです: $1" ;;
    *) [[ -z $name ]] || die "名前は 1 つだけ指定してください"; name=$1; shift ;;
  esac
done

need_name() {
  [[ -n $name ]] || die "名前を指定してください"
  valid_user_name "$name" || die "名前は英数字・_・- だけにしてください(MQTT のトピックに使われるため)"
}

MQTT_DOMAIN=$(env_value MQTT_DOMAIN)
APP_DOMAIN=$(env_value APP_DOMAIN)
dir=members/$name

# OwnTracks の接続設定(iOS・Android 共通のキー)を JSON で作る
otrc_json() { # otrc_json <MQTT パスワード> <暗号化キー or 空> <tid>
  # 値は標準入力で渡す(パスワードをコマンドラインに載せない)
  printf '%s\n' "$MQTT_DOMAIN" "$name" "$device" "$1" "$2" "$3" | docker compose exec -T api python -c '
import json, sys
host, name, device, password, enckey, tid = sys.stdin.read().split("\n")[:6]
c = {
    "_type": "configuration",
    "mode": 0,
    "host": host,
    "port": 443,
    "ws": True,
    "tls": True,
    "auth": True,
    "username": name,
    "password": password,
    "deviceId": device,
    "clientId": name + "-" + device,
    "tid": tid,
    "cleanSession": False,
    "pubQos": 1,
    "keepalive": 60,
    "mqttProtocolLevel": 4,
    "monitoring": 1,
}
if enckey:
    c["encryptionKey"] = enckey
print(json.dumps(c, ensure_ascii=False, indent=2))
'
}

config_url() { # 標準入力の JSON から OwnTracks の読み込み用 URL を作る
  docker compose exec -T api python -c '
import base64, sys, urllib.parse
data = base64.b64encode(sys.stdin.buffer.read()).decode()
print("owntracks:///config?inline=" + urllib.parse.quote(data, safe=""))
'
}

show_bundle() {
  local f=$dir/$device.otrc
  [[ -f $f ]] || die "$f がありません。先に add してください。"
  echo
  echo "== $name / $device の接続設定 =="
  if ((show_qr)); then
    echo "スマホのカメラで読み取ると OwnTracks が開き、設定を読み込みます:"
    api_cli qr <"$dir/$device.url"
  fi
  cat <<EOF
QR が使えないときは、次のどちらかを本人に渡してください(パスワードが入っているので安全な方法で):
  - ファイル: $f(OwnTracks で開く)
  - URL:      $dir/$device.url の中身(スマホで開く)
EOF
}

case $cmd in
  add)
    need_name
    [[ $device =~ ^[A-Za-z0-9_-]{1,64}$ ]] || die "端末名は英数字・_・- だけにしてください"
    mqtt_pass=$(random_secret 32)
    printf '%s\n%s\n' "$name" "$mqtt_pass" | mosq_passwd_set
    mosq_reload
    ok "MQTT アカウント $name を登録しました"

    web_pass=''
    admin_flag=()
    ((admin)) && admin_flag=(--admin)
    if grep -q "^$name"$'\t' <<<"$(api_cli list-users)"; then
      # 既にいる利用者: Web のパスワードは変えない(--admin なら権限だけ付ける)
      api_cli create-user "$name" --no-password "${admin_flag[@]}" >/dev/null
      api_cli enable-user "$name" >/dev/null
      info "Web の利用者 $name は既にいます(パスワードはそのまま)"
    else
      web_pass=$(random_secret 16)
      printf '%s\n' "$web_pass" | api_cli create-user "$name" --password-stdin "${admin_flag[@]}" >/dev/null
      ok "Web の利用者 $name を作成しました"
    fi

    enc_key=''
    if ((encrypt)); then
      enc_key=$(random_secret 32)
      printf '%s\n' "$enc_key" | api_cli set-enc-key "$name" >/dev/null
      ok "暗号化キーを生成してサーバーに登録しました"
    fi

    tid=$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]' | tr -dc 'a-z0-9' | cut -c1-2)
    umask 077
    mkdir -p "$dir"
    otrc_json "$mqtt_pass" "$enc_key" "${tid:-xx}" >"$dir/$device.otrc"
    config_url <"$dir/$device.otrc" >"$dir/$device.url"
    if [[ -n $web_pass ]]; then
      printf 'https://%s/\n名前: %s\nパスワード: %s\n' "$APP_DOMAIN" "$name" "$web_pass" >"$dir/web-login.txt"
    fi
    umask 022
    show_bundle
    if [[ -n $web_pass ]]; then
      cat <<EOF

地図画面: https://$APP_DOMAIN/
  名前:       $name
  パスワード: $web_pass   ($dir/web-login.txt にも保存。ログイン後に本人が変えられます)
EOF
    fi
    if ((encrypt)); then
      warn "暗号化キーは接続設定に入っています。スマホで設定を読み込めば有効になります。"
    fi
    ;;

  qr)
    need_name
    show_bundle
    ;;

  web-password)
    need_name
    web_pass=$(random_secret 16)
    printf '%s\n' "$web_pass" | api_cli set-password "$name" --password-stdin >/dev/null
    umask 077
    mkdir -p "$dir"
    printf 'https://%s/\n名前: %s\nパスワード: %s\n' "$APP_DOMAIN" "$name" "$web_pass" >"$dir/web-login.txt"
    umask 022
    ok "$name の Web パスワードを作り直しました: $web_pass  (ほかの端末のログインは切れます)"
    ;;

  disable)
    need_name
    if mosq_passwd_has "$name"; then
      mosq_passwd_delete "$name"
      mosq_reload
      ok "MQTT アカウント $name を削除しました(スマホから送れなくなります)"
    fi
    api_cli disable-user "$name" >/dev/null && ok "Web の利用者 $name を無効にしました(履歴は残っています)"
    ;;

  enable)
    need_name
    api_cli enable-user "$name" >/dev/null && ok "$name を有効にしました。スマホから送れるようにするには: bash scripts/member.sh add $name"
    ;;

  list)
    printf '%-20s %-30s %s\n' 名前 Web MQTT
    mqtt_users=$(docker run --rm -v "$PWD/mosquitto/config:/mosquitto/config" "$MOSQ_IMAGE" \
      sh -c '[ -f /mosquitto/config/passwd ] && cut -d: -f1 /mosquitto/config/passwd' || true)
    api_cli list-users | while IFS=$'\t' read -r n flags; do
      m=$(grep -qx "$n" <<<"$mqtt_users" && echo あり || echo なし)
      printf '%-20s %-30s %s\n' "$n" "$flags" "$m"
    done
    ;;

  *) usage 1 ;;
esac
