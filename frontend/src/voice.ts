/** One-to-one voice call in the browser (WebRTC). Audio goes directly between the customer and the agent;
 *  our server only passes the connection set-up messages ("signals"). */
export type SendSignal = (kind: "offer" | "answer" | "candidate", data: unknown) => void;

export class VoiceCall {
  private pc: RTCPeerConnection;
  private pending: RTCIceCandidateInit[] = [];
  private remoteSet = false;
  private local: MediaStream | null = null;
  private send: SendSignal;

  constructor(opts: {
    iceServers: RTCIceServer[];
    send: SendSignal;
    onRemoteStream: (s: MediaStream) => void;
    onState: (s: string) => void;
  }) {
    this.send = opts.send;
    this.pc = new RTCPeerConnection({ iceServers: opts.iceServers });
    this.pc.onicecandidate = (e) => {
      if (e.candidate) this.send("candidate", e.candidate.toJSON());
    };
    this.pc.ontrack = (e) => opts.onRemoteStream(e.streams[0]);
    this.pc.onconnectionstatechange = () => opts.onState(this.pc.connectionState);
  }

  /** Add our microphone to the call. */
  attach(stream: MediaStream): void {
    this.local = stream;
    stream.getTracks().forEach((t) => this.pc.addTrack(t, stream));
  }

  /** Caller (the customer) starts the call. */
  async startCaller(stream: MediaStream): Promise<void> {
    this.attach(stream);
    const offer = await this.pc.createOffer();
    await this.pc.setLocalDescription(offer);
    this.send("offer", { type: offer.type, sdp: offer.sdp });
  }

  /** Callee (the agent) answers the caller's offer. */
  async handleOffer(desc: RTCSessionDescriptionInit): Promise<void> {
    await this.pc.setRemoteDescription(desc);
    await this.flush();
    const answer = await this.pc.createAnswer();
    await this.pc.setLocalDescription(answer);
    this.send("answer", { type: answer.type, sdp: answer.sdp });
  }

  async handleAnswer(desc: RTCSessionDescriptionInit): Promise<void> {
    await this.pc.setRemoteDescription(desc);
    await this.flush();
  }

  /** Network candidates can arrive before the offer/answer: keep them until the other side's description is set. */
  async addCandidate(c: RTCIceCandidateInit): Promise<void> {
    if (this.remoteSet) await this.pc.addIceCandidate(c).catch(() => {});
    else this.pending.push(c);
  }

  private async flush(): Promise<void> {
    this.remoteSet = true;
    for (const c of this.pending) await this.pc.addIceCandidate(c).catch(() => {});
    this.pending = [];
  }

  setMuted(muted: boolean): void {
    this.local?.getAudioTracks().forEach((t) => (t.enabled = !muted));
  }

  close(): void {
    this.local?.getTracks().forEach((t) => t.stop());
    this.pc.close();
  }
}

export const mmss = (s: number): string => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;

/** Plain-English reason when the microphone cannot be opened. */
export function micErrorMessage(e: any): string {
  const name = e?.name ?? "";
  if (name === "NotAllowedError" || name === "SecurityError")
    return "The microphone is blocked for this site. Click the lock icon next to the address bar, set Microphone to Allow, then try again.";
  if (name === "NotFoundError" || name === "OverconstrainedError")
    return "No microphone was found. Plug one in (or enable it in Windows sound settings) and try again.";
  if (name === "NotReadableError" || name === "AbortError")
    return "Your microphone is being used by another app. Close that app and try again.";
  if (!navigator.mediaDevices)
    return "Calls need a secure page. Open the site as http://localhost:5173 (not an IP address), or use https.";
  return `Could not open the microphone (${name || "unknown error"}).`;
}
