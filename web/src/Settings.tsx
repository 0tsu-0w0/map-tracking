import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, formatTime, type Me } from "./api";

type Person = { id: number; name: string };
type Shares = { viewers: Person[]; shared_with_me: Person[] };
type AdminUser = {
  id: number;
  name: string;
  role: "admin" | "member";
  disabled: boolean;
  has_password: boolean;
  devices: number;
  created_at: string;
  last_seen_at: string | null;
};

function errText(e: unknown) {
  return e instanceof Error ? e.message : String(e);
}

export default function Settings({ me, onClose }: { me: Me; onClose: () => void }) {
  const [tab, setTab] = useState<"account" | "share" | "admin">("share");
  return (
    <div className="modal" onClick={onClose}>
      <div className="card dialog" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="設定">
        <div className="row">
          <div className="tabs">
            <button className={tab === "share" ? "active" : ""} onClick={() => setTab("share")}>
              共有
            </button>
            <button className={tab === "account" ? "active" : ""} onClick={() => setTab("account")}>
              パスワード
            </button>
            {me.role === "admin" && (
              <button className={tab === "admin" ? "active" : ""} onClick={() => setTab("admin")}>
                利用者管理
              </button>
            )}
          </div>
          <span className="grow" />
          <button className="icon" onClick={onClose} aria-label="閉じる">
            ✕
          </button>
        </div>
        {tab === "share" && <ShareTab />}
        {tab === "account" && <PasswordTab />}
        {tab === "admin" && me.role === "admin" && <AdminTab me={me} />}
      </div>
    </div>
  );
}

function ShareTab() {
  const [shares, setShares] = useState<Shares | null>(null);
  const [name, setName] = useState("");
  const [msg, setMsg] = useState("");
  const load = useCallback(() => api<Shares>("/api/shares").then(setShares).catch((e) => setMsg(errText(e))), []);
  useEffect(() => {
    load();
  }, [load]);

  async function add(e: FormEvent) {
    e.preventDefault();
    try {
      await api("/api/shares", { method: "POST", body: { viewer: name.trim() } });
      setName("");
      setMsg("");
      load();
    } catch (err) {
      setMsg(errText(err));
    }
  }

  async function del(path: string) {
    await api(path, { method: "DELETE" }).catch((e) => setMsg(errText(e)));
    load();
  }

  return (
    <div className="stack">
      <p className="small">自分の位置(現在地と履歴)を見られる人を選びます。管理者は設定にかかわらず全員の位置を見られます。</p>
      <h3>自分の位置を見せている人</h3>
      {shares?.viewers.length === 0 && <p className="muted small">まだいません</p>}
      <ul className="plain">
        {shares?.viewers.map((v) => (
          <li key={v.id} className="row">
            <span className="grow">{v.name}</span>
            <button onClick={() => del(`/api/shares/${v.id}`)}>やめる</button>
          </li>
        ))}
      </ul>
      <form className="row" onSubmit={add}>
        <input placeholder="相手の名前" value={name} onChange={(e) => setName(e.target.value)} required />
        <button className="primary">追加</button>
      </form>
      <h3>自分に見せてくれている人</h3>
      {shares?.shared_with_me.length === 0 && <p className="muted small">まだいません</p>}
      <ul className="plain">
        {shares?.shared_with_me.map((v) => (
          <li key={v.id} className="row">
            <span className="grow">{v.name}</span>
            <button onClick={() => del(`/api/shares/from/${v.id}`)}>一覧から外す</button>
          </li>
        ))}
      </ul>
      {msg && <p className="error">{msg}</p>}
    </div>
  );
}

function PasswordTab() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [msg, setMsg] = useState("");

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (next !== again) return setMsg("新しいパスワードが一致しません");
    try {
      await api("/api/auth/password", { method: "POST", body: { current, new: next } });
      setCurrent("");
      setNext("");
      setAgain("");
      setMsg("変更しました。ほかの端末のログインは解除されました。");
    } catch (err) {
      setMsg(errText(err));
    }
  }

  return (
    <form className="stack" onSubmit={submit}>
      <p className="small">
        Web 画面のパスワードです。OwnTracks の HTTP モードでもこのパスワードを使います(MQTT のパスワードは別で、管理者がサーバーで設定します)。
      </p>
      <label>
        今のパスワード
        <input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" required />
      </label>
      <label>
        新しいパスワード(8 文字以上)
        <input type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" minLength={8} required />
      </label>
      <label>
        もう一度
        <input type="password" value={again} onChange={(e) => setAgain(e.target.value)} autoComplete="new-password" minLength={8} required />
      </label>
      <button className="primary">変更</button>
      {msg && <p className="small">{msg}</p>}
    </form>
  );
}

function AdminTab({ me }: { me: Me }) {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<"member" | "admin">("member");
  const [msg, setMsg] = useState("");
  const load = useCallback(() => api<AdminUser[]>("/api/admin/users").then(setUsers).catch((e) => setMsg(errText(e))), []);
  useEffect(() => {
    load();
  }, [load]);

  async function run(fn: () => Promise<unknown>) {
    try {
      await fn();
      setMsg("");
    } catch (e) {
      setMsg(errText(e));
    }
    load();
  }

  async function create(e: FormEvent) {
    e.preventDefault();
    await run(() => api("/api/admin/users", { method: "POST", body: { name: name.trim(), password: password || null, role } }));
    setName("");
    setPassword("");
  }

  function resetPassword(u: AdminUser) {
    const p = prompt(`${u.name} の新しい Web パスワード(8 文字以上)`);
    if (p) run(() => api(`/api/admin/users/${u.id}`, { method: "PATCH", body: { password: p } }));
  }

  return (
    <div className="stack">
      <p className="small">
        名前は MQTT のユーザー名と同じにします。端末を追加するにはサーバーで <code>scripts/add-user.sh 名前</code> も実行してください。
        位置が先に届いた利用者は自動で作られ、Web パスワードを設定するとログインできます。
      </p>
      <div className="table-wrap">
        <table className="users">
          <thead>
            <tr>
              <th>名前</th>
              <th>権限</th>
              <th>端末</th>
              <th>最終受信</th>
              <th>状態</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className={u.disabled ? "muted" : ""}>
                <td>
                  {u.name}
                  {!u.has_password && <span className="tag">Web 未設定</span>}
                </td>
                <td>
                  <select
                    value={u.role}
                    disabled={u.id === me.id}
                    onChange={(e) => run(() => api(`/api/admin/users/${u.id}`, { method: "PATCH", body: { role: e.target.value } }))}
                  >
                    <option value="member">一般</option>
                    <option value="admin">管理者</option>
                  </select>
                </td>
                <td>{u.devices}</td>
                <td className="small">{u.last_seen_at ? formatTime(u.last_seen_at) : "—"}</td>
                <td>
                  <label className="inline">
                    <input
                      type="checkbox"
                      checked={u.disabled}
                      disabled={u.id === me.id}
                      onChange={(e) => run(() => api(`/api/admin/users/${u.id}`, { method: "PATCH", body: { disabled: e.target.checked } }))}
                    />
                    無効
                  </label>
                </td>
                <td className="actions">
                  <button onClick={() => resetPassword(u)}>パスワード</button>
                  {u.id !== me.id && (
                    <button
                      className="danger"
                      onClick={() =>
                        confirm(`${u.name} と、その端末・履歴をすべて削除します。元に戻せません。よろしいですか?`) &&
                        run(() => api(`/api/admin/users/${u.id}`, { method: "DELETE" }))
                      }
                    >
                      削除
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3>利用者を追加</h3>
      <form className="row wrap" onSubmit={create}>
        <input placeholder="名前(英数字)" value={name} onChange={(e) => setName(e.target.value)} pattern="[A-Za-z0-9_\-]+" required />
        <input
          type="password"
          placeholder="Web パスワード(任意)"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          minLength={8}
          autoComplete="new-password"
        />
        <select value={role} onChange={(e) => setRole(e.target.value as "member" | "admin")}>
          <option value="member">一般</option>
          <option value="admin">管理者</option>
        </select>
        <button className="primary">追加</button>
      </form>
      {msg && <p className="error">{msg}</p>}
    </div>
  );
}
