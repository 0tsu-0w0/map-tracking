import os

# app を import する前に、テスト用の設定にしておく
os.environ.setdefault("MQTT_HOST", "")
os.environ["COOKIE_SECURE"] = "false"
os.environ["INITIAL_ADMIN_USER"] = "admin"
os.environ["INITIAL_ADMIN_PASS"] = "admin-password"
os.environ["STATIC_DIR"] = "/nonexistent"
os.environ.setdefault("TZ", "Asia/Tokyo")
