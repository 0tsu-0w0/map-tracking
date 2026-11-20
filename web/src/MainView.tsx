import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, dayRange, todayString, type AppConfig, type Device, type LiveEvent, type Me, type Track } from "./api";
import DevicePanel from "./DevicePanel";
import MapView, { type Focus } from "./MapView";
import Settings from "./Settings";
import { useLive } from "./useLive";

/** 今見ている履歴の末尾に、届いた位置を足す(今日の分を見ているときだけ)。 */
function appendPoint(track: Track, ev: LiveEvent): Track {
  const point: GeoJSON.Feature = {
    type: "Feature",
    geometry: { type: "Point", coordinates: [ev.lon, ev.lat] },
    properties: { kind: "point", recorded_at: ev.recorded_at, acc: ev.acc, vel: ev.vel, batt: ev.batt },
  };
  const points = track.features.filter((f) => f.properties?.kind === "point");
  const coords = [...points.map((f) => (f.geometry as GeoJSON.Point).coordinates), [ev.lon, ev.lat]];
  const line: GeoJSON.Feature[] =
    coords.length >= 2 ? [{ type: "Feature", geometry: { type: "LineString", coordinates: coords }, properties: { kind: "track" } }] : [];
  return { ...track, features: [...line, ...points, point], count: track.count + 1 };
}

export default function Main({ me, onLogout }: { me: Me; onLogout: () => void }) {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [devices, setDevices] = useState<Device[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [date, setDate] = useState(todayString());
  const [track, setTrack] = useState<Track | null>(null);
  const [trackFitKey, setTrackFitKey] = useState(0);
  const [loading, setLoading] = useState(false);
  const [focus, setFocus] = useState<Focus | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [panelOpen, setPanelOpen] = useState(true);
  const [error, setError] = useState("");
  const reqId = useRef(0);

  const loadDevices = useCallback(async () => {
    try {
      const list = await api<Device[]>("/api/devices");
      setDevices(list);
      setSelectedId((cur) => (cur != null && list.some((d) => d.id === cur) ? cur : null));
      setError("");
    } catch (e) {
      if (e instanceof Error && "status" in e && e.status === 401) onLogout();
      else setError(e instanceof Error ? e.message : String(e));
    }
  }, [onLogout]);

  useEffect(() => {
    api<AppConfig>("/api/config").then(setConfig).catch(() => setConfig({ map_style_url: null, retention_days: 0, tz: "" }));
    loadDevices();
  }, [loadDevices]);

  const loadTrack = useCallback(async () => {
    if (selectedId == null) {
      setTrack(null);
      return;
    }
    const id = ++reqId.current;
    setLoading(true);
    try {
      const t = await api<Track>(`/api/devices/${selectedId}/locations?${new URLSearchParams(dayRange(date))}`);
      if (id !== reqId.current) return; // 古い要求の結果は捨てる
      setTrack(t);
      setTrackFitKey((k) => k + 1);
    } catch (e) {
      if (id === reqId.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (id === reqId.current) setLoading(false);
    }
  }, [selectedId, date]);

  useEffect(() => {
    loadTrack();
  }, [loadTrack]);

  const live = useLive((ev) => {
    setDevices((list) => {
      if (!list.some((d) => d.id === ev.device_id)) {
        loadDevices(); // 新しい端末は一覧を取り直す
        return list;
      }
      return list.map((d) =>
        d.id === ev.device_id && (!d.last || d.last.recorded_at <= ev.recorded_at)
          ? { ...d, tid: ev.tid ?? d.tid, last: { lat: ev.lat, lon: ev.lon, acc: ev.acc, batt: ev.batt, vel: ev.vel, recorded_at: ev.recorded_at } }
          : d,
      );
    });
    const { from, to } = dayRange(date);
    if (ev.device_id === selectedId && ev.recorded_at >= from && ev.recorded_at < to) {
      setTrack((t) => (t ? appendPoint(t, ev) : t));
    }
  });

  const selected = useMemo(() => devices.find((d) => d.id === selectedId) ?? null, [devices, selectedId]);

  function select(id: number) {
    setSelectedId(id);
    const d = devices.find((x) => x.id === id);
    if (d?.last) setFocus({ lon: d.last.lon, lat: d.last.lat, key: Date.now() });
  }

  async function logout() {
    await api("/api/auth/logout", { method: "POST" }).catch(() => undefined);
    onLogout();
  }

  return (
    <div className="layout">
      <header>
        <button className="icon" onClick={() => setPanelOpen((o) => !o)} aria-label="一覧の表示切り替え">
          ☰
        </button>
        <h1>位置マップ</h1>
        <span className={live ? "live on" : "live"} title={live ? "リアルタイム更新中" : "再接続中"}>
          ●
        </span>
        <span className="grow" />
        <span className="muted small">{me.name}</span>
        <button onClick={() => setShowSettings(true)}>設定</button>
        <button onClick={logout}>ログアウト</button>
      </header>
      {error && (
        <div className="banner" onClick={() => setError("")}>
          {error}
        </div>
      )}
      <div className={panelOpen ? "body" : "body closed"}>
        {panelOpen && (
          <DevicePanel
            devices={devices}
            selected={selected}
            onSelect={select}
            date={date}
            onDate={setDate}
            track={track}
            loading={loading}
            onChanged={() => {
              loadDevices();
              loadTrack();
            }}
          />
        )}
        {config && (
          <MapView
            styleUrl={config.map_style_url}
            devices={devices}
            selectedId={selectedId}
            track={track}
            trackFitKey={trackFitKey}
            focus={focus}
            onSelect={select}
          />
        )}
      </div>
      {showSettings && (
        <Settings
          me={me}
          onClose={() => {
            setShowSettings(false);
            loadDevices();
          }}
        />
      )}
    </div>
  );
}
