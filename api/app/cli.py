"""管理用コマンド(docker compose exec api python -m app.cli ...)。

  create-user <名前> [--admin] [--password-stdin | --no-password]
  set-password <名前> [--password-stdin]
  disable-user <名前> / enable-user <名前>
  set-enc-key <名前>        OwnTracks の暗号化キーを標準入力から登録(空なら解除)
  list-users
  import-recorder /recorder/rec   OwnTracks Recorder の履歴を取り込む
  qr                        標準入力の文字列を端末に QR コードで表示する
"""
import argparse
import asyncio
import getpass
import sys
from pathlib import Path

from . import db
from .auth import hash_password
from .config import settings
from .ingest import save_location
from .owntracks import USER_NAME_RE, parse_location, valid_name
from .recfile import iter_rec_dir


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        p1 = sys.stdin.readline().rstrip("\n")
    else:
        p1 = getpass.getpass("パスワード(8 文字以上): ")
        if p1 != getpass.getpass("もう一度: "):
            sys.exit("パスワードが一致しません")
    if len(p1) < 8:
        sys.exit("8 文字以上にしてください")
    return p1


async def create_user(name: str, admin: bool, password: str | None) -> None:
    """利用者を作る。既にいれば、パスワード(指定時)と管理者権限(--admin 時)だけ更新する。"""
    if not USER_NAME_RE.match(name):
        sys.exit("名前は英数字・_・- だけにしてください")
    pw = hash_password(password) if password else None
    await db.get_pool().execute(
        """
        INSERT INTO users (name, password_hash, role) VALUES ($1, $2, $3)
        ON CONFLICT (name) DO UPDATE SET
            password_hash = COALESCE(EXCLUDED.password_hash, users.password_hash),
            role = CASE WHEN $4 THEN 'admin' ELSE users.role END,
            disabled = false
        """,
        name,
        pw,
        "admin" if admin else "member",
        admin,
    )
    print(f"{name} を登録しました")


async def set_password(name: str, password: str) -> None:
    pool = db.get_pool()
    status = await pool.execute("UPDATE users SET password_hash = $2 WHERE name = $1", name, hash_password(password))
    if status.endswith(" 0"):
        sys.exit(f"{name} はいません")
    await pool.execute("DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE name = $1)", name)
    print("変更しました")


async def set_disabled(name: str, disabled: bool) -> None:
    pool = db.get_pool()
    status = await pool.execute("UPDATE users SET disabled = $2 WHERE name = $1", name, disabled)
    if status.endswith(" 0"):
        sys.exit(f"{name} はいません")
    if disabled:
        await pool.execute("DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE name = $1)", name)
    print("無効にしました" if disabled else "有効にしました")


async def set_enc_key(name: str) -> None:
    key = sys.stdin.readline().rstrip("\n") or None
    status = await db.get_pool().execute("UPDATE users SET enc_key = $2 WHERE name = $1", name, key)
    if status.endswith(" 0"):
        sys.exit(f"{name} はいません")
    print("暗号化キーを登録しました" if key else "暗号化キーを解除しました")


async def list_users() -> None:
    for r in await db.get_pool().fetch(
        "SELECT name, role, disabled, password_hash IS NOT NULL AS pw, enc_key IS NOT NULL AS enc FROM users ORDER BY name"
    ):
        flags = [r["role"]] + (["無効"] if r["disabled"] else []) + ([] if r["pw"] else ["Web ログイン不可"])
        flags += ["暗号化"] if r["enc"] else []
        print(f"{r['name']}\t{', '.join(flags)}")


async def import_recorder(root: Path) -> None:
    if not root.is_dir():
        sys.exit(f"{root} がありません")
    added = skipped = 0
    async with db.get_pool().acquire() as conn:
        for user, device, payload in iter_rec_dir(root):
            loc = parse_location(payload)
            if loc is None or not (valid_name(user) and valid_name(device)):
                continue
            if await save_location(conn, user, device, loc):
                added += 1
            else:
                skipped += 1
            if (added + skipped) % 5000 == 0:
                print(f"  {added + skipped} 件処理…", flush=True)
    print(f"取り込み完了: 追加 {added} 件、重複などで飛ばした {skipped} 件")


def print_qr(text: str) -> None:
    import segno

    segno.make(text, error="m").terminal(compact=True, border=2)


async def main(argv: list[str]) -> None:
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-user")
    c.add_argument("name")
    c.add_argument("--admin", action="store_true")
    g = c.add_mutually_exclusive_group()
    g.add_argument("--password-stdin", action="store_true")
    g.add_argument("--no-password", action="store_true")
    s = sub.add_parser("set-password")
    s.add_argument("name")
    s.add_argument("--password-stdin", action="store_true")
    sub.add_parser("disable-user").add_argument("name")
    sub.add_parser("enable-user").add_argument("name")
    sub.add_parser("set-enc-key").add_argument("name")
    sub.add_parser("list-users")
    sub.add_parser("import-recorder").add_argument("root", type=Path)
    sub.add_parser("qr")
    args = p.parse_args(argv)

    if args.cmd == "qr":  # DB は使わない
        print_qr(sys.stdin.read().strip())
        return

    password = None
    if args.cmd == "create-user" and not args.no_password:
        password = _read_password(args.password_stdin)
    elif args.cmd == "set-password":
        password = _read_password(args.password_stdin)

    await db.connect(settings.database_url)
    try:
        if args.cmd == "create-user":
            await create_user(args.name, args.admin, password)
        elif args.cmd == "set-password":
            await set_password(args.name, password)
        elif args.cmd in ("disable-user", "enable-user"):
            await set_disabled(args.name, args.cmd == "disable-user")
        elif args.cmd == "set-enc-key":
            await set_enc_key(args.name)
        elif args.cmd == "list-users":
            await list_users()
        else:
            await import_recorder(args.root)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
