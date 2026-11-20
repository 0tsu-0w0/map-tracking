export type Me = { id: number; name: string; role: "admin" | "member" };

export type LastLocation = {
  lat: number;
  lon: number;
  acc: number | null;
  batt: number | null;
  vel: number | null;
  recorded_at: string;
};

export type Device = {
  id: number;
  name: string;
  tid: string | null;
  user_id: number;
  user_name: string;
  can_edit: boolean;
  last: LastLocation | null;
};

export type LiveEvent = {
  type: "location";
  device_id: number;
  user_id: number;
  user_name: string;
  device_name: string;
  tid: string | null;
  lat: number;
  lon: number;
  acc: number | null;
  batt: number | null;
  vel: number | null;
  recorded_at: string;
};

export type Track = GeoJSON.FeatureCollection & { count: number; truncated: boolean };

export type AppConfig = { map_style_url: string | null; retention_days: number; tz: string };

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

/** API 呼び出し。書き換え系は X-Requested-With が必須(CSRF 対策)なので常に付ける。 */
export async function api<T>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { "X-Requested-With": "fetch" };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, {
    method: opts.method ?? "GET",
    headers,
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    credentials: "same-origin",
  });
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (typeof j.detail === "string") message = j.detail;
      else if (Array.isArray(j.detail)) message = "入力が正しくありません";
    } catch {
      /* 本文が JSON でなければステータスだけ出す */
    }
    throw new ApiError(res.status, message);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function deviceLabel(d: { user_name: string; name: string }) {
  return `${d.user_name} / ${d.name}`;
}

export function formatTime(iso: string) {
  return new Date(iso).toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function timeAgo(iso: string, now = Date.now()) {
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return "たった今";
  if (s < 3600) return `${Math.floor(s / 60)} 分前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 時間前`;
  return `${Math.floor(s / 86400)} 日前`;
}

/** 端末ごとに見分けやすい色を決める。 */
export function deviceColor(id: number) {
  const hues = [212, 4, 140, 32, 270, 180, 330, 90];
  return `hsl(${hues[id % hues.length]} 70% 45%)`;
}

/** input[type=date] の値(ローカル日付)から、その日 0 時〜翌日 0 時の ISO 文字列を作る。 */
export function dayRange(date: string): { from: string; to: string } {
  const [y, m, d] = date.split("-").map(Number);
  const start = new Date(y, m - 1, d);
  const end = new Date(y, m - 1, d + 1);
  return { from: start.toISOString(), to: end.toISOString() };
}

export function todayString() {
  const t = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())}`;
}
