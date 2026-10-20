import { startMicCapture } from '@shared/utils/aec/micCapture';
import { floatToInt16 } from '@shared/utils/aec/pcm';
import { backendWebSocketUrl } from '@/lib/api/websocket';

/** What the orb and the page show. `idle` means no conversation is running. */
export type AgentMode = 'idle' | 'connecting' | 'listening' | 'thinking' | 'speaking';

export type AgentLine = {
  id: number;
  role: 'user' | 'assistant';
  text: string;
  interrupted?: boolean;
  /** True while the user's words are still being heard (no final transcript yet). */
  pending?: boolean;
};

export type AgentIssue = 'microphone' | 'connection' | 'turn';

export type AgentSettings = {
  instructions: string;
  voice: string;
  language: string;
  bargeIn: boolean;
  greet: boolean;
};

export type AgentSnapshot = {
  mode: AgentMode;
  lines: AgentLine[];
  issue: AgentIssue | null;
  /** The microphone is open (false for a typing-only conversation). */
  micOn: boolean;
};

type Listener = (snapshot: AgentSnapshot) => void;

const INPUT_RATE = 16000;
const OUTPUT_RATE = 24000;

/**
 * One conversation with the backend voice agent (`WS /v1/agent/ws`).
 *
 * Microphone audio goes up as 16 kHz PCM16 frames; the agent's voice comes
 * back as 24 kHz PCM16 and is scheduled gap-free on its own AudioContext.
 * `level()` reports how loud the conversation is right now (0..1), which the
 * orb uses so its motion follows real speech instead of a loop.
 */
export class AgentClient {
  private socket: WebSocket | null = null;
  private stopMic: (() => Promise<void>) | null = null;
  private stream: MediaStream | null = null;
  private output: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private analyserData: Float32Array<ArrayBuffer> | null = null;
  private playing = new Set<AudioBufferSourceNode>();
  private nextStart = 0;
  private inputLevel = 0;
  private nextId = 1;
  private listeners = new Set<Listener>();
  private snapshot: AgentSnapshot = { mode: 'idle', lines: [], issue: null, micOn: false };

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    listener(this.snapshot);
    return () => this.listeners.delete(listener);
  }

  get state(): AgentSnapshot {
    return this.snapshot;
  }

  private publish(patch: Partial<AgentSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...patch };
    for (const listener of this.listeners) listener(this.snapshot);
  }

  /** Loudness of whoever is talking, 0..1. */
  level(): number {
    if (this.snapshot.mode === 'speaking' && this.analyser && this.analyserData) {
      this.analyser.getFloatTimeDomainData(this.analyserData);
      let sum = 0;
      for (const sample of this.analyserData) sum += sample * sample;
      return Math.min(1, Math.sqrt(sum / this.analyserData.length) * 4);
    }
    if (this.snapshot.mode === 'listening') return this.inputLevel;
    return 0;
  }

  get active(): boolean {
    return this.socket !== null;
  }

  /** Open the conversation. With `withMic` false it is a typed conversation that still speaks back. */
  async start(settings: AgentSettings, { withMic = true } = {}): Promise<void> {
    if (this.socket) return;
    this.publish({ mode: 'connecting', issue: null });
    try {
      this.output = new AudioContext({ sampleRate: OUTPUT_RATE });
      this.analyser = this.output.createAnalyser();
      this.analyser.fftSize = 1024;
      this.analyserData = new Float32Array(this.analyser.fftSize);
      this.analyser.connect(this.output.destination);
      const socket = new WebSocket(await backendWebSocketUrl('/v1/agent/ws'));
      socket.binaryType = 'arraybuffer';
      this.socket = socket;
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('timeout')), 10_000);
        socket.onopen = () => {
          clearTimeout(timer);
          resolve();
        };
        socket.onerror = () => {
          clearTimeout(timer);
          reject(new Error('connection failed'));
        };
      });
      socket.onmessage = (event) => this.receive(event.data);
      socket.onclose = () => {
        if (this.socket === socket) this.finish(this.snapshot.mode === 'idle' ? null : 'connection');
      };
      socket.send(
        JSON.stringify({
          type: 'session.start',
          instructions: settings.instructions,
          voice: settings.voice,
          language: settings.language,
          barge_in: settings.bargeIn,
          greet: settings.greet,
        }),
      );
    } catch {
      this.finish('connection');
      return;
    }
    if (withMic) await this.openMic();
  }

  private async openMic(): Promise<void> {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      this.stream = stream;
      const stop = await startMicCapture(
        stream,
        (frame) => {
          let sum = 0;
          for (const sample of frame) sum += sample * sample;
          const rms = Math.sqrt(sum / Math.max(1, frame.length));
          this.inputLevel = this.inputLevel * 0.6 + Math.min(1, rms * 6) * 0.4;
          const socket = this.socket;
          if (socket?.readyState === WebSocket.OPEN && socket.bufferedAmount < 1024 * 1024)
            socket.send(floatToInt16(frame).buffer);
        },
        { sampleRate: INPUT_RATE, channels: 1 },
      );
      this.stopMic = stop;
      this.publish({ micOn: true });
    } catch {
      this.publish({ issue: 'microphone', micOn: false });
    }
  }

  async toggleMic(): Promise<void> {
    if (!this.socket) return;
    if (this.stopMic) {
      await this.closeMic();
      this.publish({ micOn: false });
    } else {
      await this.openMic();
    }
  }

  private async closeMic(): Promise<void> {
    const stop = this.stopMic;
    this.stopMic = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    this.inputLevel = 0;
    await stop?.();
  }

  say(text: string): void {
    const value = text.trim();
    if (!value || this.socket?.readyState !== WebSocket.OPEN) return;
    this.socket.send(JSON.stringify({ type: 'input_text', text: value }));
  }

  interrupt(): void {
    if (this.socket?.readyState === WebSocket.OPEN)
      this.socket.send(JSON.stringify({ type: 'interrupt' }));
    this.clearPlayback();
  }

  async stop(): Promise<void> {
    const socket = this.socket;
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'session.end' }));
    this.finish(null);
  }

  /** Start a new conversation from a clean transcript. */
  reset(): void {
    this.publish({ lines: [], issue: null });
  }

  private finish(issue: AgentIssue | null): void {
    const socket = this.socket;
    this.socket = null;
    if (socket && socket.readyState <= WebSocket.OPEN) socket.close();
    void this.closeMic();
    this.clearPlayback();
    void this.output?.close().catch(() => {});
    this.output = null;
    this.analyser = null;
    this.analyserData = null;
    this.publish({
      mode: 'idle',
      micOn: false,
      issue: issue ?? this.snapshot.issue,
      lines: this.snapshot.lines.filter((line) => !line.pending),
    });
  }

  private clearPlayback(): void {
    for (const source of this.playing) {
      try {
        source.stop();
      } catch {
        // already ended
      }
    }
    this.playing.clear();
    this.nextStart = 0;
  }

  private play(bytes: ArrayBuffer): void {
    const context = this.output;
    if (!context || !this.analyser || bytes.byteLength < 2) return;
    const pcm = new Int16Array(bytes, 0, Math.floor(bytes.byteLength / 2));
    const buffer = context.createBuffer(1, pcm.length, OUTPUT_RATE);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i]! / 32768;
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.analyser);
    const at = Math.max(context.currentTime + 0.02, this.nextStart);
    source.start(at);
    this.nextStart = at + buffer.duration;
    this.playing.add(source);
    source.onended = () => this.playing.delete(source);
  }

  private receive(data: unknown): void {
    if (data instanceof ArrayBuffer) {
      this.play(data);
      return;
    }
    let event: Record<string, unknown>;
    try {
      event = JSON.parse(String(data));
    } catch {
      return;
    }
    switch (event.type) {
      case 'state': {
        const mode = event.state;
        // A new turn clears the last turn's problem; listening keeps it on screen.
        if (mode === 'listening') this.publish({ mode });
        else if (mode === 'thinking' || mode === 'speaking') this.publish({ mode, issue: null });
        break;
      }
      case 'session.ready':
        this.publish({ mode: 'listening' });
        break;
      case 'input.speech_started':
        if (!this.snapshot.lines.some((line) => line.pending))
          this.publish({
            lines: [...this.snapshot.lines, { id: this.nextId++, role: 'user', text: '', pending: true }],
          });
        break;
      case 'transcript': {
        const role = event.role === 'assistant' ? 'assistant' : 'user';
        const text = String(event.text ?? '');
        // The final transcript replaces the "still hearing you" placeholder
        // (user) or the line that was streaming in (assistant).
        const lines = this.snapshot.lines.filter((line) =>
          role === 'user' ? !line.pending : line.id !== this.streamingId,
        );
        const line: AgentLine = {
          id: this.nextId++,
          role,
          text,
          interrupted: event.interrupted === true,
        };
        this.publish({ lines: [...lines, line] });
        if (role === 'assistant') this.streamingId = null;
        break;
      }
      case 'transcript.delta': {
        const text = String(event.text ?? '');
        const lines = this.snapshot.lines.filter((line) => !line.pending);
        const current = lines.find((line) => line.id === this.streamingId);
        if (current) {
          this.publish({
            lines: lines.map((line) =>
              line === current ? { ...line, text: `${line.text} ${text}`.trim() } : line,
            ),
          });
        } else {
          this.streamingId = this.nextId++;
          this.publish({ lines: [...lines, { id: this.streamingId, role: 'assistant', text }] });
        }
        break;
      }
      case 'audio.clear':
        this.clearPlayback();
        break;
      case 'error':
        if (event.code === 'turn_failed') this.publish({ issue: 'turn' });
        break;
    }
  }

  private streamingId: number | null = null;
}
