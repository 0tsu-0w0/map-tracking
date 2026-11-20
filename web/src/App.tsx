import { useEffect, useState } from "react";
import { api, type Me } from "./api";
import Login from "./Login";
import Main from "./MainView";

export default function App() {
  const [me, setMe] = useState<Me | null | undefined>(undefined);

  useEffect(() => {
    api<Me>("/api/auth/me")
      .then(setMe)
      .catch(() => setMe(null));
  }, []);

  if (me === undefined) return <div className="center muted">読み込み中…</div>;
  if (me === null) return <Login onLogin={setMe} />;
  return <Main me={me} onLogout={() => setMe(null)} />;
}
