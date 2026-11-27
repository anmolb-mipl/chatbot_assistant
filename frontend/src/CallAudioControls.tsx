import { useEffect, useRef, useState } from "react";
import { micErrorMessage, type VoiceCall } from "./voice";

export function CallLevel({ call, incoming }: { call: VoiceCall; incoming: boolean }) {
  const [level, setLevel] = useState<number>();
  useEffect(() => {
    let active = true;
    let timer: number;
    const sample = async () => {
      const next = await call.audioLevel(incoming).catch(() => undefined);
      if (active) {
        setLevel(next);
        timer = window.setTimeout(sample, 250);
      }
    };
    void sample();
    return () => { active = false; window.clearTimeout(timer); };
  }, [call, incoming]);

  const label = incoming ? "Customer audio" : "Your microphone";
  return <label>{label} {level === undefined ? "· level unavailable" :
    <meter aria-label={label} min={0} max={1} value={Math.sqrt(level)} />}</label>;
}

export function MicrophoneControls({ call }: { call: VoiceCall }) {
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState(call.microphoneId);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const changing = useRef(false);

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      const list = await navigator.mediaDevices.enumerateDevices().catch(() => []);
      if (active) setDevices(list.filter(d => d.kind === "audioinput"));
    };
    void refresh();
    navigator.mediaDevices.addEventListener("devicechange", refresh);
    return () => {
      active = false;
      navigator.mediaDevices.removeEventListener("devicechange", refresh);
    };
  }, []);

  const change = async (id: string) => {
    if (changing.current) return;
    changing.current = true;
    setPending(true);
    setError("");
    try {
      await call.changeMicrophone(id);
      setDeviceId(call.microphoneId);
    } catch (e) {
      setError(micErrorMessage(e));
    } finally {
      changing.current = false;
      setPending(false);
    }
  };

  const selected = devices.some(d => d.deviceId === deviceId) ? deviceId : "";
  return <>
    <select aria-label="Call microphone" value={selected} disabled={pending}
      onChange={event => void change(event.target.value)}>
      <option value="">System microphone</option>
      {devices.filter(d => d.deviceId).map((d, i) =>
        <option key={d.deviceId} value={d.deviceId}>{d.label || `Microphone ${i + 1}`}</option>)}
    </select>
    <button className="ghost small" disabled={pending} onClick={() => void change(selected)}>Restart mic</button>
    {error && <span role="alert">{error}</span>}
  </>;
}
