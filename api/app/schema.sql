-- 起動のたびに実行する(何度実行しても同じ結果になるよう IF NOT EXISTS で書く)
CREATE EXTENSION IF NOT EXISTS postgis;

-- 利用者。name は MQTT のユーザー名(トピック owntracks/<name>/<device>)と同じにする
CREATE TABLE IF NOT EXISTS users (
    id            serial PRIMARY KEY,
    name          text NOT NULL UNIQUE,
    password_hash text,                 -- NULL なら Web にログインできない(MQTT 受信で自動作成された直後など)
    role          text NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member')),
    disabled      boolean NOT NULL DEFAULT false,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS devices (
    id           serial PRIMARY KEY,
    user_id      integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         text NOT NULL,         -- OwnTracks の Device ID
    tid          text,                  -- OwnTracks の Tracker ID(2 文字)
    last_seen_at timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, name)
);

CREATE TABLE IF NOT EXISTS locations (
    id           bigserial PRIMARY KEY,
    device_id    integer NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    recorded_at  timestamptz NOT NULL,  -- 端末で測った時刻(tst)
    received_at  timestamptz NOT NULL DEFAULT now(),
    geom         geometry(Point, 4326) NOT NULL,
    accuracy_m   real,
    altitude_m   real,
    velocity_kmh real,
    battery_pct  smallint,
    conn         text,
    raw          jsonb NOT NULL,
    -- QoS 1 の再送で同じ点が二重に入らないようにする(期間検索の索引も兼ねる)
    UNIQUE (device_id, recorded_at)
);
CREATE INDEX IF NOT EXISTS locations_geom_idx ON locations USING gist (geom);
CREATE INDEX IF NOT EXISTS locations_recorded_at_idx ON locations (recorded_at);

-- owner の位置を viewer が見られる
CREATE TABLE IF NOT EXISTS shares (
    owner_user_id  integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    viewer_user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_user_id, viewer_user_id),
    CHECK (owner_user_id <> viewer_user_id)
);

-- Web ログインのセッション。トークンそのものではなく SHA-256 を保存する
CREATE TABLE IF NOT EXISTS sessions (
    token_hash text PRIMARY KEY,
    user_id    integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions (user_id);

-- OwnTracks のペイロード暗号化の鍵(利用者ごと。端末の Encryption Key と同じ文字列)
ALTER TABLE users ADD COLUMN IF NOT EXISTS enc_key text;

-- ジオフェンス(F-14): 利用者が登録した場所。円で表す
CREATE TABLE IF NOT EXISTS places (
    id         serial PRIMARY KEY,
    user_id    integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       text NOT NULL,
    geom       geometry(Point, 4326) NOT NULL,
    radius_m   real NOT NULL CHECK (radius_m BETWEEN 20 AND 50000),
    notify     boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS places_user_idx ON places (user_id);

-- 端末が各場所の中にいるか(出入りを判定するための直前の状態)
CREATE TABLE IF NOT EXISTS place_presence (
    place_id   integer NOT NULL REFERENCES places(id) ON DELETE CASCADE,
    device_id  integer NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    inside     boolean NOT NULL,
    changed_at timestamptz NOT NULL,
    PRIMARY KEY (place_id, device_id)
);

-- 出入りの記録。source = server(この API が判定)/ device(OwnTracks の Regions)
CREATE TABLE IF NOT EXISTS place_events (
    id          bigserial PRIMARY KEY,
    place_id    integer REFERENCES places(id) ON DELETE SET NULL,
    place_owner integer REFERENCES users(id) ON DELETE SET NULL,
    device_id   integer NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    place_name  text NOT NULL,
    event       text NOT NULL CHECK (event IN ('enter', 'leave')),
    source      text NOT NULL CHECK (source IN ('server', 'device')),
    occurred_at timestamptz NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (device_id, source, place_name, event, occurred_at)
);
CREATE INDEX IF NOT EXISTS place_events_device_idx ON place_events (device_id, occurred_at);
