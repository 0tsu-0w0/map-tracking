"""履歴を GPX / GeoJSON / CSV の文字列片にする(ストリーミング出力用)。"""
import csv
import io
import json
from datetime import datetime, timezone
from xml.sax.saxutils import escape, quoteattr

FORMATS = {
    "gpx": ("application/gpx+xml", "gpx"),
    "geojson": ("application/geo+json", "geojson"),
    "csv": ("text/csv; charset=utf-8", "csv"),
}
CSV_COLUMNS = ["recorded_at", "lat", "lon", "accuracy_m", "altitude_m", "velocity_kmh", "battery_pct", "conn"]


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def header(fmt: str, title: str) -> str:
    if fmt == "gpx":
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<gpx version="1.1" creator="map-tracking" xmlns="http://www.topografix.com/GPX/1/1">\n'
            f"<trk><name>{escape(title)}</name><trkseg>\n"
        )
    if fmt == "geojson":
        return '{"type":"FeatureCollection","features":[\n'
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerow(CSV_COLUMNS)
    return buf.getvalue()


def row(fmt: str, r: dict, first: bool) -> str:
    if fmt == "gpx":
        parts = [f"<trkpt lat={quoteattr(repr(r['lat']))} lon={quoteattr(repr(r['lon']))}>"]
        if r.get("altitude_m") is not None:
            parts.append(f"<ele>{r['altitude_m']}</ele>")
        parts.append(f"<time>{_iso(r['recorded_at'])}</time></trkpt>\n")
        return "".join(parts)
    if fmt == "geojson":
        feature = {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
            "properties": {k: (_iso(r[k]) if k == "recorded_at" else r.get(k)) for k in CSV_COLUMNS if k not in ("lat", "lon")},
        }
        return ("" if first else ",\n") + json.dumps(feature, ensure_ascii=False)
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerow(
        [_iso(r["recorded_at"]) if k == "recorded_at" else _cell(r.get(k)) for k in CSV_COLUMNS]
    )
    return buf.getvalue()


def _cell(v):
    """表計算ソフトで数式として実行されないよう、記号で始まる文字列の先頭に ' を付ける(CSV インジェクション対策)。"""
    if v is None:
        return ""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def footer(fmt: str) -> str:
    if fmt == "gpx":
        return "</trkseg></trk>\n</gpx>\n"
    if fmt == "geojson":
        return "\n]}\n"
    return ""


def render(fmt: str, title: str, rows: list[dict]) -> str:
    """テスト・小さな出力用に、まとめて 1 つの文字列にする。"""
    return header(fmt, title) + "".join(row(fmt, r, i == 0) for i, r in enumerate(rows)) + footer(fmt)
