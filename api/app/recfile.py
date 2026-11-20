"""OwnTracks Recorder の保存ファイル(/store/rec/<user>/<device>/YYYY-MM.rec)を読む。

1 行は「時刻 TAB 種別 TAB JSON」。JSON 部分だけを使う。
"""
import json
from collections.abc import Iterator
from pathlib import Path


def parse_rec_line(line: str) -> dict | None:
    i = line.find("{")
    if i < 0:
        return None
    try:
        data = json.loads(line[i:])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def iter_rec_dir(root: Path) -> Iterator[tuple[str, str, dict]]:
    """(user, device, payload) を時系列順(ファイル名順)に返す。"""
    for user_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for device_dir in sorted(p for p in user_dir.iterdir() if p.is_dir()):
            for rec in sorted(device_dir.glob("*.rec")):
                with rec.open(encoding="utf-8", errors="replace") as f:
                    for line in f:
                        payload = parse_rec_line(line)
                        if payload is not None:
                            yield user_dir.name, device_dir.name, payload
