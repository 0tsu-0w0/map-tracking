import { useEffect, useState } from "react";
import { api, dayRange, deviceColor, deviceLabel, formatTime, timeAgo, todayString, type Device, type Track } from "./api";

type Stat = { day: string; points: number; meters: number };

type Props = {
  devices: Device[];
  selected: Device | null;
  onSelect: (id: number) => void;
  date: string;
  onDate: (d: string) => void;
  track: Track | null;
  loading: boolean;
  onChanged: () => void;
};

function km(m: number) {
  return m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${m} m`;
}

export default function DevicePanel({ devices, selected, onSelect, date, onDate, track, loading, onChanged }: Props) {
  const [stats, setStats] = useState<Stat[]>([]);
  const [, tick] = useState(0);

  // 「◯分前」の表示を 1 分ごとに更新する
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 60000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    setStats([]);
    if (!selected) return;
    api<Stat[]>(`/api/devices/${selected.id}/stats?days=7`).then(setStats).catch(() => setStats([]));
  }, [selected?.id, track]); // eslint-disable-line react-hooks/exhaustive-deps

  async function remove(range: { from?: string; to?: string } | null, message: string) {
    if (!selected || !confirm(message)) return;
    const q = range ? `?${new URLSearchParams(range as Record<string, string>)}` : "";
    try {
      const r = await api<{ deleted: number }>(`/api/devices/${selected.id}/locations${q}`, { method: "DELETE" });
      alert(`${r.deleted} 件削除しました`);
      onChanged();
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    }
  }

  async function removeDevice() {
    if (!selected || !confirm(`${deviceLabel(selected)} を履歴ごと削除します。元に戻せません。よろしいですか?`)) return;
    try {
      await api(`/api/devices/${selected.id}`, { method: "DELETE" });
      onChanged();
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    }
  }

  const range = dayRange(date);
  const exportUrl = (format: string, all: boolean) =>
    `/api/devices/${selected?.id}/export?${new URLSearchParams(all ? { format } : { format, ...range })}`;
  const dayStat = stats.find((s) => s.day === date);

  return (
    <aside className="panel">
      <section>
        <h2>端末</h2>
        {devices.length === 0 && (
          <p className="muted">まだ位置が届いていません。OwnTracks の設定を確認してください。</p>
        )}
        <ul className="devices">
          {devices.map((d) => (
            <li key={d.id}>
              <button className={d.id === selected?.id ? "device active" : "device"} onClick={() => onSelect(d.id)}>
                <span className="badge" style={{ background: deviceColor(d.id) }}>
                  {d.tid || d.name.slice(0, 2)}
                </span>
                <span className="grow">
                  <span className="name">{deviceLabel(d)}</span>
                  <span className="muted small">
                    {d.last ? timeAgo(d.last.recorded_at) : "位置なし"}
                    {d.last?.batt != null && ` ・ 電池 ${d.last.batt}%`}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>

      {selected && (
        <section>
          <h2>履歴: {deviceLabel(selected)}</h2>
          <div className="row">
            <button onClick={() => onDate(shift(date, -1))} aria-label="前の日">
              ◀
            </button>
            <input type="date" value={date} max={todayString()} onChange={(e) => e.target.value && onDate(e.target.value)} />
            <button onClick={() => onDate(shift(date, 1))} disabled={date >= todayString()} aria-label="次の日">
              ▶
            </button>
          </div>
          <p className="small">
            {loading
              ? "読み込み中…"
              : track
                ? `${track.count} 点${track.truncated ? "(多すぎるため途中まで)" : ""}${dayStat ? ` ・ 約 ${km(dayStat.meters)}` : ""}`
                : ""}
          </p>
          {selected.last && <p className="muted small">最終受信 {formatTime(selected.last.recorded_at)}</p>}

          {stats.length > 0 && (
            <details>
              <summary>直近 7 日の移動距離</summary>
              <table className="stats">
                <tbody>
                  {stats.map((s) => (
                    <tr key={s.day}>
                      <td>
                        <button className="link" onClick={() => onDate(s.day)}>
                          {s.day.slice(5).replace("-", "/")}
                        </button>
                      </td>
                      <td>{km(s.meters)}</td>
                      <td className="muted">{s.points} 点</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}

          <details>
            <summary>書き出し</summary>
            <p className="small">この日:</p>
            <div className="row">
              {["gpx", "geojson", "csv"].map((f) => (
                <a key={f} className="button" href={exportUrl(f, false)} download>
                  {f.toUpperCase()}
                </a>
              ))}
            </div>
            <p className="small">全期間:</p>
            <div className="row">
              {["gpx", "geojson", "csv"].map((f) => (
                <a key={f} className="button" href={exportUrl(f, true)} download>
                  {f.toUpperCase()}
                </a>
              ))}
            </div>
          </details>

          {selected.can_edit && (
            <details>
              <summary>削除</summary>
              <div className="stack">
                <button className="danger" onClick={() => remove(range, `${date} の履歴を削除します。よろしいですか?`)}>
                  この日の履歴を削除
                </button>
                <button
                  className="danger"
                  onClick={() => remove(null, `${deviceLabel(selected)} の全履歴を削除します。元に戻せません。よろしいですか?`)}
                >
                  この端末の全履歴を削除
                </button>
                <button className="danger" onClick={removeDevice}>
                  端末ごと削除
                </button>
              </div>
            </details>
          )}
        </section>
      )}
    </aside>
  );
}

function shift(date: string, days: number) {
  const [y, m, d] = date.split("-").map(Number);
  const t = new Date(y, m - 1, d + days);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())}`;
}
