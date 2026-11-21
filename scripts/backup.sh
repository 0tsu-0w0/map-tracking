#!/usr/bin/env bash
# 案B の DB のバックアップと復元。
#
#   bash scripts/backup.sh now                今すぐバックアップ(backups/manual-YYYYmmdd-HHMMSS.dump)
#   bash scripts/backup.sh list               バックアップの一覧
#   bash scripts/backup.sh restore <ファイル> [--yes]
#        そのバックアップの内容に戻す。今の DB は上書きされる(直前に自動でバックアップを取る)
#
# 毎日の自動バックアップは backup コンテナが backups/tracking-YYYY-MM-DD.dump に作る。
# どちらも同じサーバー上にあるので、別の場所(別のサーバーやクラウドのストレージ)にも定期的にコピーすること。
set -euo pipefail
source "$(dirname "$0")/lib.sh"

require_env
require_docker
is_plan_b || die "backup.sh は案B 用です。"
service_running db || die "db が起動していません。docker compose up -d を実行してください。"

dump_to() { # dump_to <ファイル>
  local f=$1
  umask 077
  mkdir -p backups
  docker compose exec -T db pg_dump -U tracking -d tracking -Fc >"$f.tmp"
  mv "$f.tmp" "$f"
  umask 022
}

cmd=${1:-}
case $cmd in
  now)
    f=backups/manual-$(date +%Y%m%d-%H%M%S).dump
    dump_to "$f"
    ok "バックアップしました: $f ($(du -h "$f" | cut -f1))"
    ;;

  list)
    ls -lh backups/*.dump 2>/dev/null || echo "バックアップはまだありません"
    ;;

  restore)
    f=${2:-}
    [[ -n $f && -f $f ]] || die "復元するファイルを指定してください(bash scripts/backup.sh list で確認)"
    if [[ ${3:-} != --yes ]]; then
      warn "今の DB の内容を $f の時点に戻します。それ以降に届いた位置は消えます。"
      read -r -p "よろしければ yes と入力してください: " ans
      [[ $ans == yes ]] || die "中止しました"
    fi
    before=backups/before-restore-$(date +%Y%m%d-%H%M%S).dump
    dump_to "$before"
    info "念のため今の状態を $before に保存しました"
    docker compose stop api >/dev/null
    # 失敗しても API は必ず起動し直す
    trap 'docker compose start api >/dev/null' EXIT
    docker compose exec -T db pg_restore -U tracking -d tracking --clean --if-exists --no-owner --single-transaction <"$f"
    trap - EXIT
    docker compose start api >/dev/null
    wait_api_healthy 120 || die "復元後に API が起動しません。docker compose logs api を確認してください。"
    ok "$f から復元しました"
    ;;

  *) sed -n '2,12p' "$0"; exit 1 ;;
esac
