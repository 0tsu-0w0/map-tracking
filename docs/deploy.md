# 本番に載せる手順(案B)

サーバーの用意・DNS・ファイアウォールが済んでいる前提で、そこから使い始めるまでの手順。
前提の 3 つは [README](../README.md) の「準備」を参照。
サーバーを持っていない場合は、[Oracle Cloud の無料 VM で動かす手順](oracle-cloud.md) から始める(サーバー・ファイアウォールの用意まで含む。独自ドメインも不要)。

| やること | コマンド | かかる時間 |
| --- | --- | --- |
| 1. コードを置く | `git clone` | 1 分 |
| 2. 初期設定・起動 | `bash scripts/setup.sh` | 3〜10 分 |
| 3. 利用者を追加 | `bash scripts/member.sh add <名前>` | 1 人 1 分 |
| 4. スマホに設定を読み込む | QR コードを読む | 1 人 2 分 |
| 5. 確認 | `bash scripts/doctor.sh` | 1 分 |

## 1. コードを置く

```bash
git clone https://github.com/0tsu-0w0/map-tracking.git
cd map-tracking
```

以後のコマンドはすべてこのフォルダで実行する。`sudo` は要らない(実行するユーザーを `docker` グループに入れておく)。

## 2. 初期設定・起動

```bash
bash scripts/setup.sh
```

聞かれるのは 4 つだけ。パスワード類(DB・MQTT・管理者)はすべて自動で作る。

| 質問 | 例 |
| --- | --- |
| スマホが接続するドメイン | `mqtt.example.com` |
| 地図画面のドメイン | `map.example.com` |
| Let's Encrypt に登録するメールアドレス | 証明書の期限切れ通知が届く |
| 最初の管理者の名前 | `admin` |

`setup.sh` がすること:

1. ドメインの DNS がこのサーバーを向いているかを表示する(向いていなければ注意を出す)
2. `.env` を作る(権限 600。秘密情報はランダムな 40 文字)
3. Mosquitto に API 用のアカウントを登録する
4. イメージを取得・ビルドして起動し、すべてのコンテナが正常になるまで待つ
5. 最初の管理者を作り、その初期パスワードを `.env` から消す
6. 管理者のパスワードを **1 回だけ** 表示する(ログイン後に「設定 → パスワード」で変える)

メモリが 1 GB 程度の VPS では、画面のビルドでメモリが足りなくなることがある。そのときは `--prebuilt` を付けると、
GitHub で作ったイメージ(amd64 / arm64)を取得するだけで済む。

```bash
bash scripts/setup.sh --prebuilt
```

### ドメインなしで試す

自分の PC(Docker Desktop など)で動きだけ見たいときは:

```bash
bash scripts/setup.sh --local
```

`https://map.localhost/` を開く(自己署名の証明書なので、ブラウザの警告を許可する)。スマホからはつながらない。
試し終わったら `docker compose down -v && rm .env` で片付けてから、本番の手順をやり直す。

## 3. 利用者を追加

```bash
bash scripts/member.sh add taro
```

1 回で次をまとめて行う。

- Mosquitto に MQTT のアカウント(パスワードは自動生成)を登録
- 地図画面のログイン(パスワードは自動生成)を作成
- OwnTracks の接続設定を `members/taro/` に作成し、端末に QR コードを表示

| `members/taro/` のファイル | 中身 |
| --- | --- |
| `phone.otrc` | OwnTracks の接続設定(このファイルを OwnTracks で開いても読み込める) |
| `phone.url` | 読み込み用の URL(QR コードの中身) |
| `web-login.txt` | 地図画面のアドレス・名前・パスワード |

どれもパスワードが入っている。本人に渡したら消してよい(`rm -r members/taro`)。もう一度必要になったら `add` をやり直せば作り直せる。

| オプション | 意味 |
| --- | --- |
| `--device ipad` | 2 台目以降の端末(端末名を変える。MQTT のアカウントは同じ) |
| `--encrypt` | OwnTracks のペイロード暗号化を有効にする(鍵を作ってサーバーにも登録) |
| `--admin` | 管理者にする |
| `--no-qr` | QR コードを表示しない |

ほかの操作:

```bash
bash scripts/member.sh list               # 一覧(Web と MQTT のアカウントの有無)
bash scripts/member.sh qr taro            # QR コードをもう一度表示
bash scripts/member.sh web-password taro  # 地図画面のパスワードを作り直す
bash scripts/member.sh disable taro       # 送信も閲覧も止める(履歴は残る)
bash scripts/member.sh enable taro        # 戻す(送信を再開するには add もやり直す)
```

## 4. スマホに設定を読み込む

1. スマホに OwnTracks を入れる(App Store / Google Play で「OwnTracks」)
2. スマホのカメラで QR コードを読み取り、表示されたリンクを開く → OwnTracks が開いて設定を読み込む
   - QR が使えないときは `phone.otrc` を安全な方法(AirDrop など)で送り、OwnTracks で開く
3. OwnTracks の位置情報の利用を「常に許可」にする
4. OwnTracks の右上の状態表示が「Connected」になればよい
5. 地図画面(`https://<地図のドメイン>/`)に端末が出るか確かめる

送信の頻度は OwnTracks の Monitoring モードで変える。読み込んだ設定は Significant(省電力)。
Move にすると細かく記録できるが、電池を多く使う。

## 5. 確認

```bash
bash scripts/doctor.sh
```

設定・各コンテナ・API と MQTT の接続・DNS・証明書の残り日数・ディスク・最新のバックアップをまとめて確かめ、問題があれば NG を出す。

## 運用

| いつ | やること | コマンド |
| --- | --- | --- |
| 毎日(自動) | DB のバックアップを `backups/` に作る。7 世代残す | backup コンテナ |
| 毎日(自動) | 保存期間を過ぎた履歴を消す | API |
| 週 1 回 | `backups/` を別の場所にコピーする | 下の例 |
| 月 1 回 | 最新版に更新(更新前に自動でバックアップを取る) | `bash scripts/update.sh` |
| 随時 | 状態の確認 | `bash scripts/doctor.sh` |

### バックアップを別の場所に置く

`backups/` は同じサーバーにあるので、サーバーごと失うと一緒に消える。別のマシンから定期的に取りに行くのが簡単。

```bash
# 自宅の PC などから(crontab に書いておく)
rsync -a --delete user@<サーバー>:map-tracking/backups/ ~/map-tracking-backups/
```

クラウドのストレージに送る場合は rclone などを使い、暗号化してから置く(バックアップには全員の位置履歴が入っている)。

### 復元

```bash
bash scripts/backup.sh list
bash scripts/backup.sh restore backups/tracking-2026-11-20.dump
```

復元の直前の状態も自動で `backups/before-restore-*.dump` に保存する。

### 新しいサーバーへの引っ越し

1. 新しいサーバーで `git clone` し、旧サーバーの `.env`・`mosquitto/config/passwd`・最新のバックアップをコピーする
2. `.env` の `INITIAL_ADMIN_PASS` は空のままでよい
3. `docker compose up -d --wait` で起動 → `bash scripts/backup.sh restore <ファイル> --yes`
4. DNS の A レコードを新しいサーバーに向ける(スマホ側の設定は変えなくてよい)

## うまくいかないとき

| 症状 | 確認すること |
| --- | --- |
| `setup.sh` が証明書の取得で止まる・ブラウザで開けない | DNS が向いているか(`doctor.sh`)、80/443 が開いているか。`docker compose logs caddy` |
| OwnTracks が Connected にならない | `docker compose logs mosquitto` に `not authorised` → 設定の読み込み直し(`member.sh add` をやり直して QR を読む) |
| Connected なのに地図に出ない | `doctor.sh` で API の MQTT 接続を確認。暗号化を使っているなら鍵が一致しているか |
| 地図画面に入れない | `bash scripts/member.sh web-password <名前>` でパスワードを作り直す |
| 管理者のパスワードを忘れた | `docker compose exec api python -m app.cli set-password admin` |
| ディスクがいっぱい | `docker system prune`(使っていないイメージを消す)、`RETENTION_DAYS` を短くする |
