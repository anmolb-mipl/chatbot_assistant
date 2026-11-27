import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, wsUrl } from "./api";
import type { ChatEvent, Conv, Msg } from "./types";
import { useSocket } from "./useSocket";
import { micErrorMessage, mmss, openCallMicrophone, VoiceCall } from "./voice";
import { useCallAudio } from "./useCallAudio";

type Detail = { conversation: Conv; messages: Msg[] };

function beep() {
  try {
    const ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
    const osc = ctx.createOscillator();
    osc.connect(ctx.destination);
    osc.frequency.value = 880;
    osc.start();
    setTimeout(() => {
      osc.stop();
      void ctx.close();
    }, 250);
  } catch {
    /* sound is optional */
  }
}

/** Two-tone phone ring */
function ring() {
  const tone = (freq: number, at: number) => {
    try {
      const ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
      const osc = ctx.createOscillator();
      osc.connect(ctx.destination);
      osc.frequency.value = freq;
      setTimeout(() => osc.start(), at);
      setTimeout(() => {
        osc.stop();
        void ctx.close();
      }, at + 300);
    } catch {
      /* sound is optional */
    }
  };
  tone(660, 0);
  tone(880, 400);
}

export default function AgentConsole() {
  const [token, setToken] = useState<string | null>(() => sessionStorage.getItem("agent_token"));
  const [name, setName] = useState(() => sessionStorage.getItem("agent_name") ?? "");
  const [tab, setTab] = useState<"active" | "past">("active");
  const [list, setList] = useState<Conv[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [note, setNote] = useState("");
  const [text, setText] = useState("");
  const [custTyping, setCustTyping] = useState(false);          // the customer is typing
  const custTypingTimer = useRef<number | undefined>(undefined);
  const lastTypingSent = useRef(0);
  const [activeList, setActiveList] = useState<Conv[]>([]);   // always kept fresh, so ringing works on any tab
  const [muted, setMuted] = useState(false);
  const [callState, setCallState] = useState("");
  const [seconds, setSeconds] = useState(0);
  const voiceRef = useRef<VoiceCall | null>(null);
  const acceptingRef = useRef(false);
  const callConvRef = useRef<string | null>(null);
  const { audioRef, needsPlayback, play, playStream, clearAudio } = useCallAudio();
  const endRef = useRef<HTMLDivElement>(null);

  const logout = useCallback(() => {
    sessionStorage.clear();
    setToken(null);
  }, []);

  const call = useCallback(
    async <T,>(path: string, method = "GET", body?: unknown): Promise<T> => {
      try {
        return await api<T>(path, { method, token, body });
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) logout();
        throw e;
      }
    },
    [token, logout]
  );

  const loadList = useCallback(() => {
    if (!token) return;
    call<Conv[]>("/api/agent/conversations?view=active")
      .then((a) => {
        setActiveList(a);
        if (tab === "active") setList(a);
      })
      .catch(() => {});
    if (tab === "past") call<Conv[]>("/api/agent/conversations?view=past").then(setList).catch(() => {});
  }, [token, tab, call]);

  const loadDetail = useCallback(
    (id: string) => {
      call<Detail>(`/api/agent/conversations/${id}`)
        .then((d) => {
          setDetail(d);
          setNote("");
        })
        .catch((e: Error) => {
          setDetail(null);
          setNote(e.message);
        });
    },
    [call]
  );

  useEffect(loadList, [loadList]);
  useEffect(() => {
    if (selected) loadDetail(selected);
  }, [selected, loadDetail]);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [detail?.messages.length]);

  const waitingCount = activeList.filter((c) => c.status === "waiting").length;
  const busy = activeList.some((c) => c.status === "live" && c.mine);   // already in a live chat or call
  const incomingCalls = activeList.filter((c) => c.status === "waiting" && c.channel === "call");
  useEffect(() => {
    document.title = `${waitingCount ? `(${waitingCount}) ` : ""}TradeWise Agent Console`;
  }, [waitingCount]);

  // keep ringing until someone answers (not while this agent is already busy)
  useEffect(() => {
    if (!incomingCalls.length || busy) return;
    ring();
    const t = window.setInterval(ring, 2500);
    return () => window.clearInterval(t);
  }, [incomingCalls.length, busy]);

  // ---- voice call (the agent is the callee; the customer's browser sends the offer) ----
  const stopCall = useCallback(() => {
    voiceRef.current?.close();
    voiceRef.current = null;
    callConvRef.current = null;
    clearAudio();
    setCallState("");
    setSeconds(0);
    setMuted(false);
  }, [clearAudio]);

  useEffect(() => {
    if (callState !== "connected") return;
    const t = window.setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => window.clearInterval(t);
  }, [callState]);

  const onSignal = async (kind: string, data: any) => {
    const vc = voiceRef.current;
    if (!vc) return;
    try {
      if (kind === "offer") await vc.handleOffer(data);
      else if (kind === "candidate") await vc.addCandidate(data);
    } catch {
      vc.fail();
    }
  };

  const prepareCallee = async (id: string, stream: MediaStream) => {
    const ice = await call<{ ice_servers: RTCIceServer[] }>("/api/agent/ice");
    const vc = new VoiceCall({
      iceServers: ice.ice_servers,
      send: (kind, data) => call(`/api/agent/conversations/${id}/signal`, "POST", { kind, data }),
      onRemoteStream: playStream,
      onState: setCallState,
    });
    vc.attach(stream);   // ready before the customer's offer arrives
    voiceRef.current = vc;
    callConvRef.current = id;
    setCallState("connecting");
    return vc;
  };

  const toggleMute = () => {
    const next = !muted;
    setMuted(next);
    voiceRef.current?.setMuted(next);
  };

  const socketReady = useSocket(
    token ? wsUrl(`/ws/agent?token=${encodeURIComponent(token)}`) : null,
    (ev: ChatEvent) => {
      if (ev.type === "queue.waiting" && !busy && ev.channel !== "call") beep();   // calls ring continuously instead
      if (ev.type === "call.signal" && ev.from === "customer" && ev.conversation_id === callConvRef.current) void onSignal(ev.kind, ev.data);
      if (ev.type === "queue.closed" && ev.conversation_id === callConvRef.current) stopCall();
      if (ev.type.startsWith("queue.")) loadList();
      if (ev.type === "typing" && ev.from === "customer" && ev.conversation_id === selected) {
        setCustTyping(true);
        window.clearTimeout(custTypingTimer.current);
        custTypingTimer.current = window.setTimeout(() => setCustTyping(false), 4000);
      }
      if (ev.type === "message.created" && ev.conversation_id === selected) {
        if (ev.message.sender === "customer") setCustTyping(false);   // their message arrived: stop "typing..."
        setDetail((d) => {
          if (!d || d.messages.some((m) => m.id === ev.message.id)) return d;
          return { ...d, messages: [...d.messages, ev.message] };
        });
      }
      if ((ev.type === "queue.taken" || ev.type === "queue.contact" || ev.type === "queue.closed") && ev.conversation_id === selected) loadDetail(ev.conversation_id);
    },
    () => {
      loadList();
      if (selected) loadDetail(selected);
    }
  );

  useEffect(() => {
    if (!token) stopCall();
  }, [token, stopCall]);

  const accept = async (id: string | null = selected) => {
    if (!id || acceptingRef.current || busy) return;
    const target = activeList.find((c) => c.id === id) ?? (detail?.conversation.id === id ? detail.conversation : undefined);
    const isCall = target?.channel === "call";
    if (isCall && !socketReady) {
      alert("The connection to the server is reconnecting. Try answering again in a moment.");
      return;
    }

    acceptingRef.current = true;
    let stream: MediaStream | null = null;
    let callee: VoiceCall | null = null;
    try {
      if (isCall) {
        try {
          stream = await openCallMicrophone();
        } catch (err) {
          alert(micErrorMessage(err));
          return;
        }
        // Accept notifies the customer immediately. The agent must be ready for their offer first.
        callee = await prepareCallee(id, stream);
      }
      await call(`/api/agent/conversations/${id}/accept`, "POST");
      callee?.waitForOffer();
      setSelected(id);
      loadDetail(id);
      loadList();
    } catch (e: any) {
      if (callee && voiceRef.current === callee) stopCall();
      else stream?.getTracks().forEach((t) => t.stop());
      alert(e.message || "Could not answer the call. Please try again.");
      if (e instanceof ApiError && e.status === 403) return;
      setSelected(null);
      setDetail(null);
      loadList();
    } finally {
      acceptingRef.current = false;
    }
  };

  useEffect(() => {
    setCustTyping(false);   // new chat selected: forget the old "typing..."
  }, [selected]);

  // tell the customer we are typing (at most once every 2.5 s)
  const notifyTyping = () => {
    if (!selected || !(detail?.conversation.mine && detail.conversation.status === "live")) return;
    const nowMs = Date.now();
    if (nowMs - lastTypingSent.current < 2500) return;
    lastTypingSent.current = nowMs;
    void call(`/api/agent/conversations/${selected}/typing`, "POST").catch(() => {});
  };

  const send = async () => {
    const t = text.trim();
    if (!selected || !t) return;
    setText("");
    try {
      await call(`/api/agent/conversations/${selected}/messages`, "POST", { text: t });
    } catch (e: any) {
      setNote(e.message);
    }
  };

  const end = async () => {
    if (!selected || !confirm(detail?.conversation.channel === "call" ? "End this call?" : "End this chat?")) return;
    await call(`/api/agent/conversations/${selected}/close`, "POST");
    stopCall();
    loadDetail(selected);
    loadList();
  };

  if (!token) return <Login onLogin={(t, n) => { sessionStorage.setItem("agent_token", t); sessionStorage.setItem("agent_name", n); setToken(t); setName(n); }} />;

  const conv = detail?.conversation;
  const mine = !!conv?.mine && conv.status === "live";
  const order = { waiting: 0, live: 1, closed: 2, bot: 3 } as const;
  const sorted = [...list].sort((a, b) => order[a.status] - order[b.status]);

  return (
    <div className="console">
      <aside>
        <h2>Customer chats</h2>
        <div className="who">
          Signed in as {name} · <button className="link inline" onClick={logout}>Sign out</button>
        </div>
        <div className="tabs">
          <button className={tab === "active" ? "on" : ""} onClick={() => setTab("active")}>Active</button>
          <button className={tab === "past" ? "on" : ""} onClick={() => setTab("past")}>Past chats</button>
        </div>
        {sorted.length === 0 && <div className="row muted">{tab === "past" ? "No past chats" : "No active chats"}</div>}
        {sorted.map((c) => (
          <div key={c.id} className={`row ${c.id === selected ? "sel" : ""}`} onClick={() => setSelected(c.id)}>
            <b>{c.channel === "call" ? "Call" : "Chat"} {c.id.slice(-4)}</b>
            <span className={`tag ${c.status}`}>
              {c.status === "waiting" ? (c.channel === "call" ? "Incoming call" : "New request") : c.status === "live" ? "Yours" : `Ended${c.agent_name ? ` · ${c.agent_name}` : ""}${c.rating ? ` · ★${c.rating}` : ""}`}
            </span>
            <small>
              {c.contact ? `Call back: ${c.contact}` : c.summary} · {new Date(c.updated_at).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}
            </small>
          </div>
        ))}
      </aside>

      <main>
        {incomingCalls.length > 0 && !busy && (
          <div className="ringing">
            <span>Incoming call{incomingCalls.length > 1 ? `s (${incomingCalls.length})` : ""}</span>
            {incomingCalls.map((c) => (
              <button key={c.id} onClick={() => accept(c.id)}>Answer {c.id.slice(-4)}</button>
            ))}
          </div>
        )}
        <div className="top">
          <span className="muted">
            {conv
              ? [
                  conv.summary,
                  conv.contact ? `Call back: ${conv.contact}` : "",
                  conv.status === "closed" ? `(chat ended${conv.closed_by ? ` by ${conv.closed_by}` : ""})` : "",
                  conv.rating ? `Rating: ${conv.rating}/5${conv.rating_comment ? ` - ${conv.rating_comment}` : ""}` : "",
                ].filter(Boolean).join(" · ")
              : note || "Select a chat"}
            {conv?.status === "waiting" && busy ? " · You are in a live chat. End it to accept new requests." : ""}
          </span>
          {conv?.status === "waiting" && (
            <button onClick={() => accept()} disabled={busy} title={busy ? "Finish your current chat or call first" : ""}>
              {conv.channel === "call" ? "Answer call" : "Accept chat"}
            </button>
          )}
          {mine && <button className="ghost" onClick={end}>{conv?.channel === "call" ? "End call" : "End chat"}</button>}
        </div>
        {mine && conv?.channel === "call" && (
          <div className="callbar">
            <span>{callState === "connected" ? `On a call · ${mmss(seconds)}` : callState === "failed" ? "Call connection failed. You can still chat." : "Connecting the call..."}</span>
            {needsPlayback && callState !== "failed" && (
              <button className="ghost small" onClick={() => void play()}>Enable call audio</button>
            )}
            <button className="ghost small" onClick={toggleMute} disabled={callState !== "connected"}>{muted ? "Unmute" : "Mute"}</button>
          </div>
        )}
        <div className="log">
          {detail?.messages.map((m) => (
            <div key={m.id} className={`m ${m.sender === "agent" ? "me" : m.sender === "customer" ? "cust" : m.sender}`}>
              {m.text}
            </div>
          ))}
          <div ref={endRef} />
        </div>
        {mine && (
          <div className="typing" aria-live="polite">
            {custTyping && (
              <>
                Customer is typing
                <span className="dots">
                  <i />
                  <i />
                  <i />
                </span>
              </>
            )}
          </div>
        )}
        {mine && (
          <form className="composer" onSubmit={(e) => { e.preventDefault(); void send(); }}>
            <input value={text} onChange={(e) => {
              setText(e.target.value);
              notifyTyping();
            }} placeholder="Type your reply" maxLength={1000} />
            <button className="send" disabled={!text.trim()}>Send</button>
          </form>
        )}
      </main>
      <audio ref={audioRef} autoPlay />
    </div>
  );
}

function Login({ onLogin }: { onLogin: (token: string, name: string) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);   // eye button: show or hide the password
  const [err, setErr] = useState("");
  return (
    <div className="login">
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setErr("");
          try {
            const r = await api<{ token: string; name: string }>("/api/agent/login", { method: "POST", body: { username, password } });
            onLogin(r.token, r.name);
          } catch (e2: any) {
            setErr(e2.message);
          }
        }}
      >
        <b>Agent login</b>
        <input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="Username (e.g. riya)" autoComplete="username" />
        <div className="pw">
          <input type={show ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Password" autoComplete="current-password" />
          <button type="button" className="eye" onClick={() => setShow((s) => !s)} aria-label={show ? "Hide password" : "Show password"} title={show ? "Hide password" : "Show password"}>
            {show ? (
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M17.94 17.94A10.9 10.9 0 0 1 12 20c-7 0-11-8-11-8a18.5 18.5 0 0 1 5.06-5.94" />
                <path d="M9.9 4.24A9.1 9.1 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" />
                <path d="M14.12 14.12a3 3 0 1 1-4.24-4.24" />
                <line x1="1" y1="1" x2="23" y2="23" />
              </svg>
            ) : (
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                <circle cx="12" cy="12" r="3" />
              </svg>
            )}
          </button>
        </div>
        <button className="send">Sign in</button>
        {err && <small className="err">{err}</small>}
      </form>
    </div>
  );
}