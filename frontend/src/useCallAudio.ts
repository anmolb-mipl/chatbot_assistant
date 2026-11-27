import { useCallback, useRef, useState } from "react";

export function useCallAudio() {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [needsPlayback, setNeedsPlayback] = useState(false);

  const play = useCallback(async () => {
    const audio = audioRef.current;
    const stream = audio?.srcObject;
    if (!audio || !stream) return;
    try {
      await audio.play();
      if (audio.srcObject === stream) setNeedsPlayback(false);
    } catch {
      // Playback may require a fresh click even when WebRTC is connected.
      if (audio.srcObject === stream) setNeedsPlayback(true);
    }
  }, []);

  const playStream = useCallback((stream: MediaStream) => {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.srcObject !== stream) audio.srcObject = stream;
    void play();
  }, [play]);

  const clearAudio = useCallback(() => {
    if (audioRef.current) audioRef.current.srcObject = null;
    setNeedsPlayback(false);
  }, []);

  return { audioRef, needsPlayback, play, playStream, clearAudio };
}
