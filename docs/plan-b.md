# 案B: 自作 API + PostgreSQL/PostGIS + 地図画面

案A(Recorder)の代わりに、自作の API が MQTT を購読して PostgreSQL/PostGIS に保存し、自作の地図画面で表示する構成。
スマホ(OwnTracks)の設定は案A と同じで、変える必要はない。要件は [requirements.md](requirements.md) を参照。

## 構成

```mermaid
flowchart LR
    phone["スマホ<br/>OwnTracks"]
    browser["ブラウザ"]
    subgraph compose["compose.b.yaml"]
        caddy["caddy<br/>TLS・443"]
        mqtt["mosquitto<br/>認証・ACL"]
        api["api<br/>FastAPI + 地図画面"]
        db[("db<br/>PostgreSQL 16 + PostGIS")]
        backup["backup<br/>毎日 pg_dump"]
    end
    phone -- "MQTT over WSS" --> caddy --> mqtt
    browser -- "HTTPS / WebSocket" --> caddy --> api
    mqtt -- "購読(api ユーザー)" --> api
    api <--> db
    backup --> db
```

| ディレクトリ・ファイル | 中身 |
| --- | --- |
| `api/` | Python 3.12 + FastAPI。MQTT 購読(aiomqtt)、REST API、WebSocket 配信、定期処理、管理コマンド |
| `web/` | React + Vite + MapLibre GL JS の地図画面。ビルド結果は API のイメージに入る |
| `compose.b.yaml` | caddy / mosquitto / db / api / backup |
| `caddy/Caddyfile.b` | `MQTT_DOMAIN` → Mosquitto、`APP_DOMAIN` → API |

## できること(要件 ID との対応)

| 機能 | 内容 |
| --- | --- |
| F-01 MQTT 受信 | `owntracks/<user>/<device>` の `_type: location` を保存。`clean_session=False` + QoS 1 なので、API 停止中の位置もブローカーが溜めて後から届く |
| F-02 HTTP 受信 | `POST /api/pub`(Web と同じ名前・パスワードの Basic 認証、端末名は `X-Limit-D`) |
| F-03 / F-04 | Mosquitto のパスワード + ACL(案A と同じ)。API 用の `api` ユーザーは読み取りのみ |
| F-05 保存 | 緯度経度(PostGIS の Point)・精度・高度・速度・電池・通信種別・生 JSON。`(端末, 時刻)` が同じ点は二重登録しない |
| F-06 現在地 | 端末ごとのマーカー。新着は WebSocket で即時反映 |
| F-07 履歴 | 端末と日付を選ぶと経路を線で表示(1 日ずつ前後に移動できる) |
| F-08 ログイン | Cookie セッション + Argon2。失敗が続く接続元は一時的に拒否 |
| F-09 削除 | その日・端末の全履歴・端末ごと。本人か管理者だけ |
| F-10 利用者管理 | 画面の「設定 → 利用者管理」で追加・権限・無効化・パスワード再設定・削除 |
| F-11 書き出し | GPX / GeoJSON / CSV(その日 / 全期間) |
| F-12 自動削除 | `RETENTION_DAYS` を過ぎた履歴を 6 時間ごとに削除 |
| F-13 共有 | 「設定 → 共有」で自分の位置を見せる相手を選ぶ。管理者は全員を見られる |
| F-14 ジオフェンス | 「設定 → 場所」で地図の中央に円(半径 20 m〜50 km)を登録すると、自分と共有してくれている人の端末の出入りをサーバーで判定して記録・画面に通知・Webhook 通知。OwnTracks 側の Regions(`owntracks/<user>/<device>/event`)の出入りも記録する |
| F-15 統計 | 直近 7 日の日別移動距離(精度 100 m より悪い点は除く) |
| 監視 | `ALERT_STALE_HOURS` 時間以上位置が届かない端末を Webhook(Slack / Discord 形式)で通知 |
| バックアップ | `backup` コンテナが毎日 `./backups/tracking-YYYY-MM-DD.dump` を作り、`BACKUP_KEEP` 世代残す |

| 暗号化 | OwnTracks の「Encryption Key」を使うと、位置は端末で暗号化され、ブローカーからは読めない。同じ鍵を「設定 → パスワード・暗号化」に登録すると API が復号して保存する |

ジオフェンスの判定の決まり:

- 判定するのは、その端末で一番新しい位置が届いたときだけ(後から届いた古い位置では判定しない)。精度が 500 m より悪い位置も使わない。
- 円の中に入ったら「着」、半径 + 30 m(精度が悪いときは最大 + 100 m)より外に出たら「発」。境界付近で出入りを繰り返さないための余裕。
- 場所を登録した直後の最初の位置では、今の状態を覚えるだけで記録しない。
- 出入りの記録は、場所の持ち主・端末の持ち主・管理者だけが見られる(OwnTracks 側の Regions は、端末を見られる人全員)。

セキュリティ上の確認結果と対策は [security.md](security.md) を参照。

## 利用者の考え方

- Web の利用者名 = MQTT のユーザー名 = トピックの `<user>`。
- MQTT のアカウント(スマホが接続に使う)は `scripts/add-user.sh` で Mosquitto に登録する。Web のパスワードとは別。
- まだ Web に登録していない人の位置が届くと、利用者が自動で作られる(Web パスワードなし = ログイン不可)。管理画面でパスワードを設定するとログインできる。
- 利用者を「無効」にすると Web にログインできず、届いた位置も保存しない。スマホからの送信自体を止めるには `scripts/add-user.sh -D <名前>` で MQTT アカウントも消す。

## セットアップ(新規)

> スクリプトでまとめて行う手順は [deploy.md](deploy.md)(`scripts/setup.sh`・`scripts/member.sh`)。以下は手作業で行う場合。

前提は案A と同じ(Docker・DNS 2 つ・80/443 を開ける)。[README](../README.md) の「準備」を参照。

```bash
cp .env.example .env
```

`.env` で次を設定する。

| 変数 | 値 |
| --- | --- |
| `COMPOSE_FILE` | `compose.b.yaml`(コメントを外す) |
| `MQTT_DOMAIN` / `APP_DOMAIN` / `ACME_EMAIL` | 案A と同じ |
| `API_MQTT_PASS` | 長いランダム文字列 |
| `DB_PASSWORD` | 長いランダム文字列 |
| `INITIAL_ADMIN_USER` / `INITIAL_ADMIN_PASS` | 最初の管理者(初回起動後は消してよい) |
| `RECORDER_MQTT_PASS` | 案A を使わないなら空にする |

ランダム文字列は `openssl rand -base64 32` などで作る。

```bash
# Mosquitto に API 用アカウントを登録
bash scripts/init.sh
# スマホごとの MQTT アカウント
bash scripts/add-user.sh taro
# ビルドして起動(初回はイメージのビルドに数分かかる)
docker compose up -d --build
docker compose logs -f api
```

`https://<APP_DOMAIN>/` を開き、`INITIAL_ADMIN_USER` でログインする。家族には「設定 → 利用者管理」で Web パスワードを設定して渡す(名前は MQTT のユーザー名と同じにする)。

管理者はコマンドでも作れる。

```bash
docker compose exec api python -m app.cli create-user admin --admin
docker compose exec api python -m app.cli set-password taro
docker compose exec api python -m app.cli list-users
```

## 案A からの移行

1. 案A を止める: `docker compose down`(ボリュームは残す。`-v` は付けない)
2. `.env` に案B の値を足し、`COMPOSE_FILE=compose.b.yaml` を有効にする
3. `bash scripts/init.sh`(既存の端末アカウントは残り、`api` が追加される)
4. `docker compose up -d --build`
5. Recorder の履歴を取り込む(重複は自動で飛ばすので、何度実行してもよい)

   ```bash
   docker compose exec api python -m app.cli import-recorder /recorder/rec
   ```

案A の Recorder の保存データ(`recorder_store` ボリューム)は `/recorder` に読み取り専用でつながっている。
取り込みが終わって不要になったら `docker volume rm <フォルダ名>_recorder_store` で消せる。

## 運用

```bash
# 状態の確認
docker compose ps
docker compose exec api python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/healthz').read().decode())"

# 更新(コードを pull したあと)
docker compose up -d --build

# バックアップから戻す(例: 11/20 の分)
docker compose stop api
docker compose exec -T db pg_restore -U tracking -d tracking --clean --if-exists < backups/tracking-2026-11-20.dump
docker compose start api
```

`./backups` は同じサーバー上にあるので、定期的に別の場所(オブジェクトストレージなど)へコピーする。

背景地図は既定で国土地理院の標準地図を使う。MapTiler などのベクタータイルにしたい場合は `MAP_STYLE_URL` にスタイル JSON の URL を入れて `docker compose up -d` する。

## 開発

```bash
# API(Python 3.12 以上)
cd api
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest                        # DB を使わない単体テスト
DATABASE_URL=postgresql://test:test@localhost:5432/test pytest   # PostGIS があれば結合テストも

# 起動(MQTT なし、ログイン Cookie を http で使う)
MQTT_HOST= COOKIE_SECURE=false STATIC_DIR=../web/dist \
  INITIAL_ADMIN_USER=admin INITIAL_ADMIN_PASS=admin-password \
  DATABASE_URL=postgresql://test:test@localhost:5432/test uvicorn app.main:app --reload

# 地図画面(別のターミナル。/api は localhost:8000 に中継される)
cd web && npm ci && npm run dev
```

GitHub Actions(`.github/workflows/ci.yml`)が push のたびに、PostGIS を使った結合テスト・画面のビルド・compose / Caddyfile / Mosquitto 設定の検証・API イメージのビルドを行う。

### API 一覧

書き換え系(POST / PATCH / DELETE)は `X-Requested-With: fetch` ヘッダーが必要(CSRF 対策。`/api/pub` を除く)。

| メソッド | パス | 内容 |
| --- | --- | --- |
| POST | `/api/auth/login`, `/api/auth/logout` | ログイン・ログアウト |
| GET | `/api/auth/me` | ログイン中の利用者 |
| POST | `/api/auth/password` | 自分のパスワード変更 |
| GET | `/api/devices` | 見られる端末と最新位置 |
| GET | `/api/devices/{id}/locations?from=&to=` | 履歴(GeoJSON。既定は直近 24 時間、最大 93 日) |
| GET | `/api/devices/{id}/export?format=gpx\|geojson\|csv&from=&to=` | 書き出し(期間省略で全期間) |
| GET | `/api/devices/{id}/stats?days=7` | 日別の点数と移動距離 |
| GET | `/api/devices/{id}/events?from=&to=` | 出入りの記録 |
| GET / POST | `/api/places` | 自分の場所の一覧・登録 |
| PATCH / DELETE | `/api/places/{id}` | 場所の変更・削除 |
| PUT | `/api/auth/encryption-key` | OwnTracks の暗号化キーの登録・解除(`{"key": null}` で解除) |
| DELETE | `/api/devices/{id}/locations?from=&to=` | 履歴の削除(期間省略で全部) |
| DELETE | `/api/devices/{id}` | 端末ごと削除 |
| GET / POST | `/api/shares` | 共有設定の一覧・追加 |
| DELETE | `/api/shares/{viewer_id}`, `/api/shares/from/{owner_id}` | 共有の解除 |
| GET / POST | `/api/admin/users` | 利用者の一覧・追加(管理者) |
| PATCH / DELETE | `/api/admin/users/{id}` | 権限・無効化・パスワード・削除(管理者) |
| POST | `/api/pub` | OwnTracks の HTTP モード受信 |
| WS | `/api/ws` | 新着位置の配信 |
| GET | `/api/config` | 地図スタイルなどの設定 |
