import { useState, type FormEvent } from "react";
import { api, type Me } from "./api";

export default function Login({ onLogin }: { onLogin: (me: Me) => void }) {
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      onLogin(await api<Me>("/api/auth/login", { method: "POST", body: { name, password } }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="center">
      <form className="card login" onSubmit={submit}>
        <h1>位置マップ</h1>
        <label>
          名前
          <input value={name} onChange={(e) => setName(e.target.value)} autoComplete="username" required autoFocus />
        </label>
        <label>
          パスワード
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        {error && <p className="error">{error}</p>}
        <button className="primary" disabled={busy}>
          {busy ? "確認中…" : "ログイン"}
        </button>
      </form>
    </div>
  );
}
