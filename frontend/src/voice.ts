/** One-to-one voice call in the browser (WebRTC). Audio goes directly between the customer and the agent;
 *  our server only passes the connection set-up messages ("signals"). */
export type SendSignal = (kind: "offer" | "answer" | "candidate", data: unknown) => Promise<unknown>;

export class VoiceCall {
  private pc: RTCPeerConnection;
  private pending: RTCIceCandidateInit[] = [];
  private remoteSet = false;
  private local: MediaStream | null = null;
  private microphoneSender: RTCRtpSender | null = null;
  private muted = false;
  private send: SendSignal;
  private localCandidates: RTCIceCandidateInit[] = [];
  private descriptionSent = false;
  private closed = false;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private onState: (state: string) => void;

  constructor(opts: {
    iceServers: RTCIceServer[];
    send: SendSignal;
    onRemoteStream: (s: MediaStream) => void;
    onState: (s: string) => void;
  }) {
    this.send = opts.send;
    this.onState = opts.onState;
    this.pc = new RTCPeerConnection({ iceServers: opts.iceServers });
    this.pc.onicecandidate = ({ candidate }) => {
      if (this.closed || !candidate) return;
      const data = candidate.toJSON();
      if (this.descriptionSent) this.sendCandidate(data);
      else this.localCandidates.push(data);
    };
    this.pc.ontrack = (e) => opts.onRemoteStream(e.streams[0] ?? new MediaStream([e.track]));
    this.pc.onconnectionstatechange = () => {
      const state = this.pc.connectionState;
      if (state === "failed") {
        this.fail();
        return;
      }
      if (state === "connected") clearTimeout(this.timer);
      if (state === "disconnected") this.startDeadline();
      this.onState(state);
    };
  }

  private startDeadline(): void {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.fail(), 25000);
  }

  private sendCandidate(candidate: RTCIceCandidateInit): void {
    void this.send("candidate", candidate).catch(() => this.fail());
  }

  private async sendDescription(kind: "offer" | "answer", description: RTCSessionDescriptionInit): Promise<void> {
    await this.send(kind, description);
    if (this.closed) return;
    this.descriptionSent = true;
    for (const candidate of this.localCandidates) this.sendCandidate(candidate);
    this.localCandidates = [];
  }

  /** Add our microphone to the call. */
  attach(stream: MediaStream): void {
    this.local = stream;
    const track = stream.getAudioTracks()[0];
    if (!track) throw new Error("No audio track available.");
    this.microphoneSender = this.pc.addTrack(track, stream);
  }

  /** Caller (the customer) starts the call. */
  async startCaller(stream: MediaStream): Promise<void> {
    this.attach(stream);
    this.startDeadline();
    const offer = await this.pc.createOffer();
    await this.pc.setLocalDescription(offer);
    await this.sendDescription("offer", offer);
  }

  /** Callee (the agent) answers the caller's offer. */
  async handleOffer(desc: RTCSessionDescriptionInit): Promise<void> {
    this.startDeadline();
    await this.pc.setRemoteDescription(desc);
    await this.flush();
    const answer = await this.pc.createAnswer();
    await this.pc.setLocalDescription(answer);
    await this.sendDescription("answer", answer);
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

  get microphoneId(): string {
    return this.microphoneSender?.track?.getSettings().deviceId ?? "";
  }

  async changeMicrophone(deviceId: string): Promise<void> {
    const sender = this.microphoneSender;
    if (!sender || this.closed) return;
    const stream = await openCallMicrophone(deviceId);
    const track = stream.getAudioTracks()[0];
    try {
      if (!track || this.closed) throw new Error("The call has ended.");
      track.enabled = !this.muted;
      await sender.replaceTrack(track);
      if (this.closed) throw new Error("The call has ended.");
      track.enabled = !this.muted;
    } catch (err) {
      stream.getTracks().forEach(t => t.stop());
      throw err;
    }
    this.local?.getTracks().forEach(t => t.stop());
    this.local = stream;
  }

  async audioLevel(incoming: boolean): Promise<number | undefined> {
    const reports = await this.pc.getStats();
    let level: number | undefined;
    reports.forEach(report => {
      const type = incoming ? "inbound-rtp" : "media-source";
      if (report.type === type && report.kind === "audio" && typeof report.audioLevel === "number") {
        level = Math.max(level ?? 0, report.audioLevel);
      }
    });
    return level;
  }

  setMuted(muted: boolean): void {
    this.muted = muted;
    this.local?.getAudioTracks().forEach((t) => (t.enabled = !muted));
  }

  waitForOffer(): void {
    if (!this.closed && !this.remoteSet) this.startDeadline();
  }

  fail(): void {
    if (this.closed) return;
    this.close();
    this.onState("failed");
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    clearTimeout(this.timer);
    this.pc.onconnectionstatechange = null;
    this.pc.ontrack = null;
    this.pc.onicecandidate = null;
    this.local?.getTracks().forEach((t) => t.stop());
    this.pc.close();
  }
}

/** Prefer the browser's voice processing for two-way calls. */
export function openCallMicrophone(deviceId?: string): Promise<MediaStream> {
  return navigator.mediaDevices.getUserMedia({
    audio: { deviceId: deviceId ? { exact: deviceId } : undefined, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
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
