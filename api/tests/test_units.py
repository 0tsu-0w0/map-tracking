"""DB なしで動く単体テスト。"""
import json
from datetime import datetime, timezone

from app import export
from app.auth import FailureLimiter, hash_password, verify_password
from app.owntracks import parse_location, parse_topic
from app.recfile import iter_rec_dir, parse_rec_line

SAMPLE = {"_type": "location", "tid": "ab", "lat": 35.6812, "lon": 139.7671, "acc": 12, "alt": 40,
          "vel": 0, "batt": 85, "conn": "w", "tst": 1763600000}


def test_parse_topic():
    assert parse_topic("owntracks/taro/iphone") == ("taro", "iphone")
    assert parse_topic("owntracks/taro/iphone/event") is None
    assert parse_topic("owntracks/taro") is None
    assert parse_topic("other/taro/iphone") is None
    assert parse_topic("owntracks//iphone") is None


def test_parse_location_ok():
    loc = parse_location(SAMPLE)
    assert loc is not None
    assert (loc.lat, loc.lon, loc.accuracy_m, loc.battery_pct, loc.tid) == (35.6812, 139.7671, 12.0, 85, "ab")
    assert loc.recorded_at == datetime.fromtimestamp(1763600000, tz=timezone.utc)


def test_parse_location_rejects_bad_input():
    assert parse_location({**SAMPLE, "_type": "transition"}) is None
    assert parse_location({**SAMPLE, "lat": 91}) is None
    assert parse_location({**SAMPLE, "lat": "35"}) is None
    assert parse_location({**SAMPLE, "lat": True}) is None
    assert parse_location({k: v for k, v in SAMPLE.items() if k != "tst"}) is None
    assert parse_location([SAMPLE]) is None
    # 範囲外の任意項目は捨てるだけで、位置そのものは受ける
    loc = parse_location({**SAMPLE, "batt": 150, "acc": -1})
    assert loc is not None and loc.battery_pct is None and loc.accuracy_m is None


ROWS = [
    {"recorded_at": datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc), "lat": 35.0, "lon": 139.0,
     "accuracy_m": 5.0, "altitude_m": 10.0, "velocity_kmh": 3.0, "battery_pct": 80, "conn": "w"},
    {"recorded_at": datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc), "lat": 35.001, "lon": 139.001,
     "accuracy_m": None, "altitude_m": None, "velocity_kmh": None, "battery_pct": None, "conn": None},
]


def test_export_geojson_is_valid_json():
    data = json.loads(export.render("geojson", "t", ROWS))
    assert data["type"] == "FeatureCollection"
    assert [f["geometry"]["coordinates"] for f in data["features"]] == [[139.0, 35.0], [139.001, 35.001]]
    assert data["features"][0]["properties"]["recorded_at"] == "2026-01-01T00:00:00Z"


def test_export_gpx_and_csv():
    import xml.etree.ElementTree as ET

    gpx = export.render("gpx", "taro/<phone>", ROWS)
    root = ET.fromstring(gpx)
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    pts = root.findall(".//g:trkpt", ns)
    assert len(pts) == 2 and pts[0].get("lat") == "35.0"
    assert root.find(".//g:name", ns).text == "taro/<phone>"

    lines = export.render("csv", "t", ROWS).splitlines()
    assert lines[0].startswith("recorded_at,lat,lon")
    assert lines[2] == "2026-01-01T00:01:00Z,35.001,139.001,,,,,"


def test_empty_exports_are_valid():
    assert json.loads(export.render("geojson", "t", []))["features"] == []


def test_password_hash():
    h = hash_password("correct horse")
    assert verify_password(h, "correct horse")
    assert not verify_password(h, "wrong")
    assert not verify_password(None, "anything")
    assert not verify_password("not-a-hash", "x")


def test_failure_limiter():
    import pytest
    from fastapi import HTTPException

    lim = FailureLimiter(limit=2, window_s=60)
    lim.check("ip")
    lim.fail("ip")
    lim.fail("ip")
    with pytest.raises(HTTPException):
        lim.check("ip")
    lim.reset("ip")
    lim.check("ip")


def test_rec_parsing(tmp_path):
    assert parse_rec_line('2026-01-01T00:00:00Z\t*                 \t{"_type":"location","lat":1}') == {"_type": "location", "lat": 1}
    assert parse_rec_line("garbage") is None
    d = tmp_path / "taro" / "iphone"
    d.mkdir(parents=True)
    (d / "2026-01.rec").write_text(
        "2026-01-01T00:00:00Z\t*\t" + json.dumps(SAMPLE) + "\nbroken line\n", encoding="utf-8"
    )
    assert [(u, dev, p["tid"]) for u, dev, p in iter_rec_dir(tmp_path)] == [("taro", "iphone", "ab")]
