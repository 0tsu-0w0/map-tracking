#!/usr/bin/env bash
# まっさらな Ubuntu(22.04 / 24.04)の VM を、このリポジトリを動かせる状態にする。root で実行する。
#   - Docker と Docker Compose を入れる
#   - 80/tcp・443/tcp・443/udp を OS のファイアウォールで開ける(Oracle Cloud の Ubuntu は既定で閉じている)
#   - メモリが少なければスワップを作る
#   - OS のセキュリティ更新を自動で入れる
#   - リポジトリを利用者のホームに取得する
#
#   sudo bash scripts/bootstrap-ubuntu.sh [ユーザー名]     ユーザー名の既定は ubuntu(無ければ sudo したユーザー)
# 何度実行してもよい。Oracle Cloud では VM 作成時に deploy/oracle-cloud-init.yaml から自動で呼ばれる。
set -euo pipefail

REPO_URL=${REPO_URL:-https://github.com/0tsu-0w0/map-tracking.git}
[[ $(id -u) == 0 ]] || { echo "root で実行してください(sudo bash $0)" >&2; exit 1; }

target=${1:-}
if [[ -z $target ]]; then
  if id ubuntu >/dev/null 2>&1; then target=ubuntu; else target=${SUDO_USER:-root}; fi
fi
id "$target" >/dev/null 2>&1 || { echo "ユーザー $target がいません" >&2; exit 1; }
home=$(getent passwd "$target" | cut -d: -f6)

log() { echo "[bootstrap] $*"; }
export DEBIAN_FRONTEND=noninteractive

# --- パッケージ ---
log "パッケージを入れています"
apt-get update -q
apt-get install -y -q ca-certificates curl git openssl unattended-upgrades >/dev/null
if ! command -v docker >/dev/null 2>&1; then
  apt-get install -y -q docker.io >/dev/null
fi
if ! docker compose version >/dev/null 2>&1; then
  apt-get install -y -q docker-compose-v2 >/dev/null || apt-get install -y -q docker-compose-plugin >/dev/null
fi
systemctl enable --now docker >/dev/null
usermod -aG docker "$target"
log "Docker: $(docker --version) / $(docker compose version --short)"

# --- ファイアウォール ---
# Oracle Cloud の Ubuntu は iptables の INPUT の最後に REJECT があり、22 番以外を断る。その前に許可を足す
open_port() { # open_port <tcp|udp> <port>
  local proto=$1 port=$2
  if iptables -C INPUT -p "$proto" -m state --state NEW --dport "$port" -j ACCEPT 2>/dev/null; then return; fi
  local pos
  pos=$(iptables -L INPUT --line-numbers -n | awk '$2=="REJECT"{print $1; exit}')
  if [[ -n $pos ]]; then
    iptables -I INPUT "$pos" -p "$proto" -m state --state NEW --dport "$port" -j ACCEPT
  else
    iptables -A INPUT -p "$proto" -m state --state NEW --dport "$port" -j ACCEPT
  fi
}
if command -v iptables >/dev/null 2>&1; then
  open_port tcp 80
  open_port tcp 443
  open_port udp 443
  if command -v netfilter-persistent >/dev/null 2>&1; then
    netfilter-persistent save >/dev/null 2>&1 || true
  elif [[ -d /etc/iptables ]]; then
    iptables-save >/etc/iptables/rules.v4
  fi
  log "iptables で 80/tcp・443/tcp・443/udp を開けました"
fi
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp >/dev/null
  ufw allow 443/tcp >/dev/null
  ufw allow 443/udp >/dev/null
  log "ufw で 80/tcp・443/tcp・443/udp を開けました"
fi

# --- スワップ(メモリ 4 GB 未満のとき 2 GB) ---
mem_kb=$(awk '/MemTotal/{print $2}' /proc/meminfo)
if ((mem_kb < 4000000)) && ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
  log "スワップ 2 GB を作りました(メモリ $((mem_kb / 1024)) MB)"
fi

# --- OS のセキュリティ更新を自動で入れる ---
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
log "OS のセキュリティ更新を自動で入れる設定にしました"

# --- リポジトリ ---
dir=$home/map-tracking
if [[ ! -d $dir/.git ]]; then
  sudo -u "$target" git clone -q "$REPO_URL" "$dir"
  log "リポジトリを $dir に取得しました"
else
  log "リポジトリは $dir にあります"
fi

cat <<EOF
[bootstrap] 完了。次は $target でログインし直して(docker グループを反映するため)、
  cd ~/map-tracking
  bash scripts/setup.sh --sslip <この VM の公開 IP> --prebuilt
EOF
