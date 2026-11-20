"""管理用コマンド。

  python -m app.cli create-user <名前> [--admin]   パスワードは対話入力
  python -m app.cli set-password <名前>
  python -m app.cli list-users
  python -m app.cli import-recorder /recorder/rec   OwnTracks Recorder の履歴を取り込む
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


def _ask_password() -> str:
    p1 = getpass.getpass("パスワード(8 文字以上): ")
    p2 = getpass.getpass("もう一度: ")
    if p1 != p2:
        sys.exit("パスワードが一致しません")
    if len(p1) < 8:
        sys.exit("8 文字以上にしてください")
    return p1


async def create_user(name: str, admin: bool) -> None:
    if not USER_NAME_RE.match(name):
        sys.exit("名前は英数字・_・- だけにしてください")
    pw = hash_password(_ask_password())
    await db.get_pool().execute(
        """
        INSERT INTO users (name, password_hash, role) VALUES ($1, $2, $3)
        ON CONFLICT (name) DO UPDATE SET password_hash = EXCLUDED.password_hash, role = EXCLUDED.role
        """,
        name,
        pw,
        "admin" if admin else "member",
    )
    print(f"{name} を登録しました({'管理者' if admin else '一般'})")


async def set_password(name: str) -> None:
    pw = hash_password(_ask_password())
    pool = db.get_pool()
    status = await pool.execute("UPDATE users SET password_hash = $2 WHERE name = $1", name, pw)
    if status.endswith(" 0"):
        sys.exit(f"{name} はいません")
    await pool.execute("DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE name = $1)", name)
    print("変更しました")


async def list_users() -> None:
    for r in await db.get_pool().fetch("SELECT name, role, disabled, password_hash IS NOT NULL AS pw FROM users ORDER BY name"):
        flags = [r["role"]] + (["無効"] if r["disabled"] else []) + ([] if r["pw"] else ["Web ログイン不可"])
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


async def main(argv: list[str]) -> None:
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-user")
    c.add_argument("name")
    c.add_argument("--admin", action="store_true")
    sub.add_parser("set-password").add_argument("name")
    sub.add_parser("list-users")
    sub.add_parser("import-recorder").add_argument("root", type=Path)
    args = p.parse_args(argv)

    await db.connect(settings.database_url)
    try:
        if args.cmd == "create-user":
            await create_user(args.name, args.admin)
        elif args.cmd == "set-password":
            await set_password(args.name)
        elif args.cmd == "list-users":
            await list_users()
        else:
            await import_recorder(args.root)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
