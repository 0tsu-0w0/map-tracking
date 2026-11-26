# Oracle Cloud の無料 VM で動かす

Oracle Cloud の「Always Free」の VM を使うと、サーバーを買わずに無料で案B を動かせる。
独自ドメインも、[sslip.io](https://sslip.io/)(IP アドレスを名前にした無料の DNS)を使えば不要。

| 項目 | 内容 |
| --- | --- |
| 費用 | 無料(Always Free の範囲内。登録時にクレジットカードでの本人確認がある) |
| VM | Ampere A1(ARM)2 OCPU / メモリ 12 GB / ディスク 50 GB。Ampere の無料枠は月 1,500 OCPU 時間・9,000 GB 時間(= 2 OCPU / 12 GB を常時)なので、これで無料枠をちょうど使い切る。ディスクの無料枠は合計 200 GB、外向きの通信は月 10 TB |
| 無料枠の仕組み | 金額の枠ではなく、上の量までなら毎月ずっと無料(Always Free)。登録時の US$300 のクレジット(30 日間)はこれとは別の試用特典 |
| ドメイン | `mqtt.<IP>.sslip.io` と `map.<IP>.sslip.io`(後から独自ドメインに変えられる) |
| かかる時間 | 30〜60 分(アカウント登録の審査を除く) |
| パソコン側で要るもの | ブラウザと、Windows に最初から入っている `ssh` |

作業の流れ:

1. Oracle Cloud のアカウントを作る
2. SSH の鍵を作る(自分のパソコンで)
3. VM を作る(cloud-init を貼って、初期設定を自動にする)
4. ネットワークで 80 / 443 を開ける
5. VM に入って `setup.sh` を実行する
6. 利用者を追加し、スマホに設定を読み込む

## 1. アカウントを作る

1. <https://signup.cloud.oracle.com/> を開き、国・名前・メールアドレスを入れて登録する
2. **ホーム・リージョン** を選ぶ。**後から変えられない**。日本なら「Japan East (Tokyo)」か「Japan Central (Osaka)」
   - 無料の Ampere VM は混みやすい。作れないときは後で別の時間に試す(手順 3 の「作れないとき」)
3. クレジットカードで本人確認をする(Always Free の範囲では請求されない)
4. 登録が終わるとコンソールに入れる(審査に数分〜数時間かかることがある)

## 2. SSH の鍵を作る

自分のパソコン(PowerShell)で実行する。すでに `~/.ssh/id_ed25519.pub` があれば不要。

```powershell
ssh-keygen -t ed25519
```

聞かれたら Enter を押していけばよい(パスフレーズは付けるのを勧める)。次で公開鍵を表示し、手順 3 で使う。

```powershell
Get-Content $HOME\.ssh\id_ed25519.pub
```

## 3. VM を作る

コンソール左上のメニュー → **コンピュート → インスタンス → インスタンスの作成**。

| 項目 | 設定 |
| --- | --- |
| 名前 | 何でもよい(例: `map-tracking`) |
| イメージ | 「イメージの変更」→ **Canonical Ubuntu 24.04**(aarch64 版が選ばれる) |
| シェイプ | 「シェイプの変更」→ **Ampere → VM.Standard.A1.Flex**、OCPU **2**、メモリ **12 GB** |
| ネットワーキング | 「新規仮想クラウド・ネットワークの作成」「新規パブリック・サブネットの作成」、**パブリック IPv4 アドレスの割当て: はい** |
| SSH キーの追加 | 「公開キーの貼付け」→ 手順 2 で表示した 1 行を貼る |
| ブート・ボリューム | そのまま(約 47 GB) |
| 拡張オプションの表示 → 管理 → cloud-init スクリプト | 「cloud-init スクリプトの貼付け」→ [deploy/oracle-cloud-init.yaml](../deploy/oracle-cloud-init.yaml) の中身を全部貼る |

**作成** を押す。数分で「実行中」になる。インスタンスの画面に出る **パブリック IP アドレス** を控える(以下 `203.0.113.10` とする)。

cloud-init が VM の中で自動で次を行う(起動後 5 分ほど):

- Docker と Docker Compose を入れる
- Ubuntu のファイアウォール(iptables)で 80/tcp・443/tcp・443/udp を開ける
- OS のセキュリティ更新を自動で入れる設定にする
- このリポジトリを `/home/ubuntu/map-tracking` に取得する

### 作れないとき(Out of capacity)

「シェイプ VM.Standard.A1.Flex の容量が不足しています」と出たら:

- 「可用性ドメイン」を変えて試す(AD-1 / AD-2 / AD-3 がある場合)
- OCPU 1 / メモリ 6 GB に減らして試す
- 時間をおいて試す(早朝・深夜は空きやすいと言われる)
- それでも駄目なら、シェイプを **VM.Standard.E2.1.Micro**(AMD、メモリ 1 GB、これも無料)にする。この場合も手順は同じで、手順 5 の `--prebuilt` が必須

## 4. ネットワークで 80 / 443 を開ける

Oracle Cloud には、VM の外側にもファイアウォール(セキュリティ・リスト)がある。

1. インスタンスの画面 →「プライマリ VNIC」のサブネットのリンク → **セキュリティ・リスト** → `Default Security List for ...`
2. **イングレス・ルールの追加** で次の 2 つを追加する

| ソース CIDR | IP プロトコル | 宛先ポート範囲 | 用途 |
| --- | --- | --- | --- |
| `0.0.0.0/0` | TCP | `80,443` | 証明書の取得・HTTPS・MQTT over WSS |
| `0.0.0.0/0` | UDP | `443` | HTTP/3(無くても動く) |

22/TCP(SSH)は最初から開いている。

## 5. VM に入って起動する

```powershell
ssh ubuntu@203.0.113.10
```

初回は「Are you sure you want to continue connecting」と聞かれるので `yes`。VM に入ったら、cloud-init の完了を待つ。

```bash
cloud-init status --wait
```

`status: done` になったら、一度ログアウトして入り直す(docker グループを反映するため)。

```bash
exit
```

```powershell
ssh ubuntu@203.0.113.10
```

起動する。`--sslip` には手順 3 で控えたパブリック IP を入れる。

```bash
cd ~/map-tracking
bash scripts/setup.sh --sslip 203.0.113.10 --prebuilt
```

メールアドレスと管理者の名前を聞かれる(メールアドレスは Let's Encrypt からの証明書期限の通知用)。
数分で起動し、最後に地図画面のアドレスと管理者のパスワードが表示される。

```
  地図画面:   https://map.203-0-113-10.sslip.io/
  管理者:     admin
  パスワード: xxxxxxxxxxxxxxxxxxxx
```

ブラウザで開いてログインできればよい。続けて状態を確認する。

```bash
bash scripts/doctor.sh
```

## 6. 利用者を追加する

```bash
bash scripts/member.sh add taro
```

端末に QR コードが出るので、本人のスマホのカメラで読み取り、OwnTracks で設定を読み込む。
詳しくは [deploy.md](deploy.md) の「3. 利用者を追加」「4. スマホに設定を読み込む」を参照。

## 気をつけること

### 使われていない VM の回収

Oracle は、Always Free の VM が 7 日間ほとんど使われていない(CPU・ネットワーク・メモリの使用率がどれも低い)と、停止・回収することがあると案内している。
この用途は負荷が小さいため、対象になりうる。

- 確実に避けるには、アカウントを **従量課金(Pay As You Go)にアップグレード** する。Always Free の範囲内なら請求はされず、回収の対象から外れる(Oracle の案内による。アップグレードするかはご自身で判断を)
- アップグレードしない場合は、少なくとも **バックアップを別の場所に置いておく**([deploy.md](deploy.md) の「バックアップを別の場所に置く」)。回収されても、新しい VM で復元すれば戻せる

### sslip.io について

- sslip.io は第三者の無料サービス。止まると、スマホも地図画面もつながらなくなる
- 長く使うなら独自ドメイン(年 1,000〜2,000 円程度)を取るのを勧める。乗り換えは:
  1. DNS で `mqtt.<独自ドメイン>` と `map.<独自ドメイン>` の A レコードを VM の IP に向ける
  2. `.env` の `MQTT_DOMAIN` と `APP_DOMAIN` を書き換えて `docker compose up -d`
  3. 各利用者に `bash scripts/member.sh add <名前>` をやり直し、スマホで新しい設定を読み込んでもらう(接続先が変わるため)
- VM を作り直してパブリック IP が変わった場合も、同じく 2・3 が必要

### セキュリティ

- SSH はパスワードでは入れない設定(鍵のみ)になっている。秘密鍵(`~/.ssh/id_ed25519`)はパソコンから外に出さない
- 月 1 回程度 `bash scripts/update.sh` を実行する(OS の更新は自動、アプリとイメージの更新はこのコマンド)
