import { useEffect, useRef, useState } from "react";
import type { ChatEvent } from "./types";

/** WebSocket that reconnects by itself. onOpen runs on every (re)connect so callers can re-sync missed data. */
export function useSocket(url: string | null, onEvent: (e: ChatEvent) => void, onOpen?: () => void) {
  const [openUrl, setOpenUrl] = useState<string | null>(null);
  const eventRef = useRef(onEvent);
  const openRef = useRef(onOpen);
  eventRef.current = onEvent;
  openRef.current = onOpen;

  useEffect(() => {
    if (!url) return;
    let ws: WebSocket | null = null;
    let stopped = false;
    let retry: number | undefined;

    const connect = () => {
      ws = new WebSocket(url);
      ws.onopen = () => {
        if (stopped) return;
        setOpenUrl(url);
        openRef.current?.();
      };
      ws.onmessage = (ev) => {
        try {
          eventRef.current(JSON.parse(ev.data) as ChatEvent);
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = () => {
        if (stopped) return;
        setOpenUrl(null);
        retry = window.setTimeout(connect, 2000);
      };
    };
    connect();
    const ping = window.setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN) ws.send("ping");
    }, 25000);

    return () => {
      stopped = true;
      setOpenUrl(null);
      window.clearTimeout(retry);
      window.clearInterval(ping);
      ws?.close();
    };
  }, [url]);

  return url !== null && openUrl === url;
}
