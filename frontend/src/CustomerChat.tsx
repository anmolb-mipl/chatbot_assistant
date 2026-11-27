import { useCallback, useEffect, useRef, useState } from "react";
import { api, wsUrl } from "./api";
import type { ChatEvent, Conv, Msg } from "./types";
import { useSocket } from "./useSocket";
import { micErrorMessage, mmss, openCallMicrophone, VoiceCall } from "./voice";
import { useCallAudio } from "./useCallAudio";

type Faq = { id: string; question: string };

type OpenRes = { conversation: Conv; messages: Msg[] };
type SendRes = { status: Conv["status"]; messages: Msg[]; show_faqs?: boolean; show_agent_options?: boolean };

export default function CustomerChat() {
  const [token, setToken] = useState<string | null>(null);
  const [conv, setConv] = useState<Conv | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [contact, setContact] = useState("");
  const [stars, setStars] = useState(0);
  const [comment, setComment] = useState("");
  const [faqs, setFaqs] = useState<Faq[]>([]);
  const [choosing, setChoosing] = useState(false);           // "Chat or Call?" options
  const [muted, setMuted] = useState(false);
  const [callState, setCallState] = useState("");
  const [seconds, setSeconds] = useState(0);
  const micRef = useRef<MediaStream | null>(null);
  const voiceRef = useRef<VoiceCall | null>(null);
  const startingRef = useRef(false);
  const { audioRef, needsPlayback, play, playStream, clearAudio } = useCallAudio();
  const anchorRef = useRef<HTMLDivElement>(null);
  const [peerTyping, setPeerTyping] = useState(false);          // the agent is typing
  const peerTypingTimer = useRef<number | undefined>(undefined);
  const lastTypingSent = useRef(0);
  const endRef = useRef<HTMLDivElement>(null);

  // add messages we have not seen yet (WebSocket and HTTP can both deliver the same one)
  const merge = useCallback((incoming: Msg[]) => {
    setMsgs((prev) => {
      const seen = new Set(prev.map((m) => m.id));
      const fresh = incoming.filter((m) => !seen.has(m.id));
      return fresh.length ? [...prev, ...fresh] : prev;
    });
  }, []);

  const start = useCallback(async (tk: string) => {
    const r = await api<OpenRes>("/api/conversations/open", { method: "POST", token: tk });
    setConv(r.conversation);
    setMsgs(r.messages);
  }, []);

  useEffect(() => {
    (async () => {
      try {
        // In production your app passes the signed-in user's token (?token=...). In dev we ask for a demo token.
        const fromUrl = new URLSearchParams(location.search).get("token");
        let visitor = "";
        try {
          visitor = localStorage.getItem("tw_visitor") ?? "";
          if (!visitor) {
            visitor = crypto.randomUUID().replace(/-/g, "").slice(0, 24);
            localStorage.setItem("tw_visitor", visitor);
          }
        } catch {
          visitor = Math.random().toString(36).slice(2, 14);   // storage blocked: a fresh demo identity each visit
        }
        const tk = fromUrl ?? (await api<{ token: string }>(`/api/dev/customer-token?visitor=${visitor}`)).token;
        setToken(tk);
        await start(tk);
      } catch {
        setError("Could not start the chat. Please refresh the page.");
      }
    })();
  }, [start]);

  useEffect(() => {
    api<Faq[]>("/api/faqs").then(setFaqs).catch(() => {});
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [msgs]);

  const resync = useCallback(async () => {
    if (!token || !conv) return;
    try {
      const r = await api<OpenRes>(`/api/conversations/${conv.id}`, { token });
      setConv(r.conversation);
      merge(r.messages);
    } catch {
      /* will retry on next reconnect */
    }
  }, [token, conv, merge]);

  const socketReady = useSocket(
    token && conv ? wsUrl(`/ws/customer?token=${encodeURIComponent(token)}&conversation_id=${conv.id}`) : null,
    (ev: ChatEvent) => {
      if (ev.type === "message.created") {
        merge([ev.message]);
        if (ev.message.sender === "agent") setPeerTyping(false);   // their message arrived: stop "typing..."
      }
      if (ev.type === "typing" && ev.from === "agent") {
        setPeerTyping(true);
        window.clearTimeout(peerTypingTimer.current);
        peerTypingTimer.current = window.setTimeout(() => setPeerTyping(false), 4000);
      }
      if (ev.type === "queue.taken")
        setConv((c) => (c ? { ...c, status: "live", agent_name: ev.agent_name } : c));
      if (ev.type === "queue.closed") setConv((c) => (c ? { ...c, status: "closed" } : c));
      if (ev.type === "queue.timeout") setConv((c) => (c ? { ...c, fallback_sent: true } : c));
      if (ev.type === "call.signal" && ev.from === "agent" && ev.conversation_id === conv?.id) void onSignal(ev.kind, ev.data);
    },
    resync
  );

  // ---- voice call (the customer is the caller; the agent who answers is the callee) ----
  const convId = conv?.id;

  const onSignal = async (kind: string, data: any) => {
    const vc = voiceRef.current;
    if (!vc) return;
    try {
      if (kind === "answer") await vc.handleAnswer(data);
      else if (kind === "candidate") await vc.addCandidate(data);
    } catch {
      vc.fail();
    }
  };

  const stopCall = useCallback(() => {
    voiceRef.current?.close();
    voiceRef.current = null;
    micRef.current?.getTracks().forEach((t) => t.stop());
    micRef.current = null;
    clearAudio();
    setCallState("");
    setSeconds(0);
    setMuted(false);
  }, [clearAudio]);

  const beginCall = useCallback(async () => {
    if (!token || !convId || voiceRef.current || startingRef.current) return;
    startingRef.current = true;
    setCallState("connecting");
    try {
      const existing = micRef.current;
      const stream = existing?.getAudioTracks().some((track) => track.readyState === "live")
        ? existing
        : await openCallMicrophone();
      micRef.current = stream;
      const ice = await api<{ ice_servers: RTCIceServer[] }>(`/api/conversations/${convId}/ice`, { token });
      if (micRef.current !== stream) return;
      const vc = new VoiceCall({
        iceServers: ice.ice_servers,
        send: (kind, data) => api(`/api/conversations/${convId}/signal`, { method: "POST", token, body: { kind, data } }),
        onRemoteStream: playStream,
        onState: setCallState,
      });
      voiceRef.current = vc;
      await vc.startCaller(stream);
    } catch {
      stopCall();
      setCallState("failed");
    } finally {
      startingRef.current = false;
    }
  }, [token, convId, playStream, stopCall]);

  // start the call as soon as an agent answers; hang up when the chat is closed
  useEffect(() => {
    if (socketReady && conv?.status === "live" && conv.channel === "call") void beginCall();
    if (conv?.status === "closed") stopCall();
  }, [socketReady, conv?.status, conv?.channel, beginCall, stopCall]);

  useEffect(() => {
    if (callState !== "connected") return;
    const t = window.setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => window.clearInterval(t);
  }, [callState]);

  useEffect(() => {
    if (!choosing) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setChoosing(false);
    };
    const onDown = (e: MouseEvent) => {
      if (anchorRef.current && !anchorRef.current.contains(e.target as Node)) setChoosing(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, [choosing]);

  const toggleMute = () => {
    const next = !muted;
    setMuted(next);
    voiceRef.current?.setMuted(next);
  };

  // tell the agent we are typing (at most once every 2.5 s)
  const notifyTyping = () => {
    if (!token || !conv || conv.status !== "live") return;
    const nowMs = Date.now();
    if (nowMs - lastTypingSent.current < 2500) return;
    lastTypingSent.current = nowMs;
    void api(`/api/conversations/${conv.id}/typing`, { method: "POST", token }).catch(() => {});
  };

  const send = async (text: string) => {
    const clean = text.trim();
    if (!token || !conv || busy || !clean || conv.status === "waiting") return;
    setBusy(true);
    setError("");
    // Show the customer's message straight away; the server's copy replaces it when the reply arrives.
    const tempId = -Date.now();
    setMsgs((prev) => [...prev, { id: tempId, sender: "customer", text: clean, auto: false, created_at: new Date().toISOString() }]);
    try {
      const r = await api<SendRes>(`/api/conversations/${conv.id}/messages`, { method: "POST", token, body: { text: clean } });
      setMsgs((prev) => prev.filter((m) => m.id !== tempId));
      merge(r.messages);
      setConv((c) => (c ? { ...c, status: r.status } : c));
      if (r.show_agent_options) setChoosing(true);   // the customer asked for a person in words: open the Chat / Call card
    } catch (e: any) {
      setMsgs((prev) => prev.filter((m) => m.id !== tempId));   // sending failed: take it back...
      setInput((cur) => cur || clean);                          // ...and put the text back in the box
      setError(e.message || "Message failed");
    } finally {
      setBusy(false);
    }
  };

  // FAQ button: the answer comes from our own FAQ list (no LLM)
  const askFaq = async (faq: Faq) => {
    if (!token || !conv || busy) return;
    setBusy(true);
    setError("");
    const tempId = -Date.now();
    setMsgs((prev) => [...prev, { id: tempId, sender: "customer", text: faq.question, auto: false, created_at: new Date().toISOString() }]);
    try {
      const r = await api<SendRes>(`/api/conversations/${conv.id}/faq`, { method: "POST", token, body: { faq_id: faq.id } });
      setMsgs((prev) => prev.filter((m) => m.id !== tempId));
      merge(r.messages);
    } catch (e: any) {
      setMsgs((prev) => prev.filter((m) => m.id !== tempId));
      setError(e.message || "Could not load the answer");
    } finally {
      setBusy(false);
    }
  };

  const talkToPerson = async (channel: "chat" | "call") => {
    if (!token || !conv || busy) return;
    setBusy(true);
    setError("");
    setChoosing(false);
    try {
      if (channel === "call") {
        // Ask for the microphone now (it needs a click) so the call can start the moment an agent answers.
        try {
          micRef.current = await openCallMicrophone();
        } catch (err) {
          setError(micErrorMessage(err));
          return;
        }
      }
      const r = await api<SendRes>(`/api/conversations/${conv.id}/handoff`, { method: "POST", token, body: { channel } });
      merge(r.messages);
      setConv((c) => (c?.status === "bot" || c?.status === "live"
        ? { ...c, status: c.status === "live" ? "live" : r.status, channel } : c));
    } catch (e: any) {
      micRef.current?.getTracks().forEach((t) => t.stop());
      micRef.current = null;
      setError(e.message || "Could not reach the team");
    } finally {
      setBusy(false);
    }
  };

  const saveContact = async () => {
    if (!token || !conv || busy) return;
    setBusy(true);
    setError("");
    try {
      const r = await api<SendRes & { contact: string }>(`/api/conversations/${conv.id}/contact`, { method: "POST", token, body: { contact: contact.trim() } });
      merge(r.messages);
      setConv((c) => (c ? { ...c, contact: r.contact } : c));
    } catch (e: any) {
      setError(e.message || "Could not save your details");
    } finally {
      setBusy(false);
    }
  };

  const endChat = async () => {
    if (!token || !conv || busy || !confirm("End this chat?")) return;
    setBusy(true);
    try {
      const r = await api<SendRes>(`/api/conversations/${conv.id}/end`, { method: "POST", token });
      merge(r.messages);
      setConv((c) => (c ? { ...c, status: "closed" } : c));
    } catch (e: any) {
      setError(e.message || "Could not end the chat");
    } finally {
      setBusy(false);
    }
  };

  const submitRating = async () => {
    if (!token || !conv || !stars) return;
    setBusy(true);
    try {
      await api<{ ok: boolean }>(`/api/conversations/${conv.id}/rating`, { method: "POST", token, body: { score: stars, comment } });
      setConv((c) => (c ? { ...c, rating: stars } : c));
    } catch (e: any) {
      setError(e.message || "Could not save your rating");
    } finally {
      setBusy(false);
    }
  };

  const status = conv?.status;
  const customerSpoke = msgs.some((m) => m.sender === "customer");   // FAQ buttons only before the first message
  const isCall = conv?.channel === "call";
  const who = conv?.agent_name ?? "Agent";
  const badge =
    status === "waiting" ? (isCall ? "Calling..." : "Waiting for agent...")
    : status === "live" ? (isCall ? `Call with ${who}` : `${who} is live`)
    : status === "closed" ? (isCall ? "Call ended" : "Chat ended")
    : "Bot";

  return (
    <div className="app">
      <header>
        <div className="logo">TW</div>
        <div>
          <h1>TradeWise Support</h1>
          <small>Ask about your account, orders and funds</small>
        </div>
        <span className={`badge ${status === "live" ? "live" : ""}`}>{badge}</span>
      </header>

      <div className="log" aria-live="polite">
        {msgs.map((m) => (
          <div key={m.id} className={`m ${m.sender === "customer" ? "me" : m.sender}`}>
            {m.text}
          </div>
        ))}
        {status === "bot" && !customerSpoke && faqs.length > 0 && (
          <div className="chips">
            {faqs.map((f) => (
              <button key={f.id} className="chip" onClick={() => askFaq(f)} disabled={busy}>
                {f.question}
              </button>
            ))}
          </div>
        )}
        {busy && status === "bot" && <div className="sys">Typing...</div>}
        {status === "waiting" && !conv?.fallback_sent && (
          <div className="sys">{isCall ? "Ringing our agents... the first one free will pick up. Please keep this page open." : "All our agents have been notified. The first one free will join you."}</div>
        )}
        {status === "waiting" && conv?.fallback_sent && !conv.contact && (
          <form className="contact" onSubmit={(e) => { e.preventDefault(); void saveContact(); }}>
            <input value={contact} onChange={(e) => setContact(e.target.value)} placeholder="Phone number or email" maxLength={120} aria-label="Phone number or email" />
            <button className="send" disabled={busy || !contact.trim()}>Save</button>
          </form>
        )}
        {status === "waiting" && conv?.contact && <div className="sys">We will contact you at {conv.contact}. You can also keep waiting here.</div>}
        {error && <div className="sys err">{error}</div>}
        <div ref={endRef} />
      </div>

      {status === "live" && isCall && (
        <div className="callbar">
          <span>{callState === "connected" ? `On a call · ${mmss(seconds)}` : callState === "failed" ? "Call connection failed. You can still chat." : "Connecting the call..."}</span>
          {needsPlayback && callState !== "failed" && (
            <button className="ghost small" onClick={() => void play()}>Enable call audio</button>
          )}
          <button className="ghost small" onClick={toggleMute} disabled={callState !== "connected"}>{muted ? "Unmute" : "Mute"}</button>
        </div>
      )}
      <div className="actions">
        {status === "bot" && (
          <div className="anchor" ref={anchorRef}>
            {choosing && (
              <div className="pop" role="dialog" aria-label="Choose how to talk to an agent">
                <button className="row" onClick={() => talkToPerson("chat")} disabled={busy}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                  </svg>
                  Chat
                </button>
                <div className="sep" />
                <button className="row" onClick={() => talkToPerson("call")} disabled={busy}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8.1 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2z" />
                  </svg>
                  Call
                </button>
              </div>
            )}
            <button className="link" onClick={() => setChoosing((v) => !v)} disabled={busy}>
              Talk to agent
            </button>
          </div>
        )}
        {status && status !== "closed" && (
          <button className="link end" onClick={endChat} disabled={busy}>
            {isCall ? (status === "live" ? "End call" : "Cancel call") : "End chat"}
          </button>
        )}
      </div>
      {status === "live" && (
        <div className="typing" aria-live="polite">
          {peerTyping && (
            <>
              {who} is typing
              <span className="dots">
                <i />
                <i />
                <i />
              </span>
            </>
          )}
        </div>
      )}
      {status === "closed" ? (
        <div className="closed">
          {conv?.rating == null ? (
            <div className="rate">
              <div>{isCall ? "How was your call?" : "How was your chat?"}</div>
              <div className="stars">
                {[1, 2, 3, 4, 5].map((n) => (
                  <button key={n} className={n <= stars ? "star on" : "star"} onClick={() => setStars(n)} aria-label={`${n} star${n > 1 ? "s" : ""}`}>
                    ★
                  </button>
                ))}
              </div>
              <input value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Anything to add? (optional)" maxLength={500} />
              <button className="send" disabled={!stars || busy} onClick={submitRating}>
                Submit
              </button>
            </div>
          ) : (
            <div className="rate">Thanks for your feedback.</div>
          )}
          <div>
            {isCall ? "This call has ended." : "This chat has ended."}{" "}
            <button
              className="link inline"
              onClick={() => {
                setStars(0);
                setComment("");
                setContact("");
                if (token) void start(token);
              }}
            >
              Start a new chat
            </button>
          </div>
        </div>
      ) : (
        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            const t = input;
            setInput("");
            send(t);
          }}
        >
          <input
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              notifyTyping();
            }}
            disabled={status === "waiting"}
            placeholder={status === "waiting" ? (isCall ? "Waiting for an agent to answer..." : "Waiting for an agent to join...") : "Type your question"}
            maxLength={1000}
            aria-label="Your message"
          />
          <button className="send" disabled={busy || !input.trim() || status === "waiting"}>
            Send
          </button>
        </form>
      )}
      <audio ref={audioRef} autoPlay />
    </div>
  );
}