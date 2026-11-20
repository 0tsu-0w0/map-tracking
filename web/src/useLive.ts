import { useEffect, useRef, useState } from "react";
import type { LiveEvent } from "./api";

/** /api/ws に接続し、新着位置を受け取る。切れたら待ち時間を伸ばしながら再接続する。 */
export function useLive(onEvent: (ev: LiveEvent) => void) {
  const [connected, setConnected] = useState(false);
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let delay = 1000;
    let stopped = false;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/api/ws`);
      ws.onopen = () => {
        delay = 1000;
        setConnected(true);
      };
      ws.onmessage = (e) => {
        try {
          const ev = JSON.parse(e.data);
          if (ev?.type === "location") handler.current(ev as LiveEvent);
        } catch {
          /* 想定外のメッセージは無視 */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (stopped) return;
        timer = setTimeout(connect, delay);
        delay = Math.min(delay * 2, 30000);
      };
    };
    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      ws?.close();
    };
  }, []);

  return connected;
}
