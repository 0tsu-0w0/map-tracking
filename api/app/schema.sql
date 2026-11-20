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
