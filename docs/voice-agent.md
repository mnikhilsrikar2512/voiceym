# Voice agent

Voiceym includes a voice agent you can talk to from a browser, a script, a bot
or any other program that can open a WebSocket. It listens, transcribes what
you say, asks your language model for a reply, and speaks it back in the voice
you choose. You can interrupt it by talking.

It uses the same pieces as the rest of the app: the speech-recognition engine
from **Settings → Models → ASR**, the language model from **Settings → Models →
LLM** (Ollama, LM Studio, llama.cpp, vLLM or a cloud provider), and any saved
voice or TTS engine.

## Try it in the browser

1. Start Voiceym (the desktop app, Docker, or `bun run dev`).
2. Open <http://localhost:3900/agent>.
3. Describe what the agent should do, pick a voice, and press **Start talking**.

The page asks for microphone access. Your browser's echo cancellation keeps
the agent from hearing itself, so speakers work, but headphones are better.
If the microphone is blocked you can still type messages.

## Connect from your own code

`WS /v1/agent/ws` speaks a small protocol: JSON for control and events, binary
frames for audio.

| Direction | Message |
|---|---|
| → | `{"type": "session.start", "instructions": "...", "voice": "<profile id>", "engine": "", "language": "en", "greet": false, "barge_in": true, "fast_default_voice": false}` (first message; every field optional). `fast_default_voice: true` with no `voice` or `engine` speaks with the light KittenTTS engine when it is installed, which keeps live conversation responsive on low-memory machines; the Talk screen sends it for its default voice. With it the reply speaks with Kokoro when Kokoro is downloaded, else KittenTTS; Hindi (Devanagari) sentences use Kokoro's Hindi voice, and sentences neither can say (another language or script such as Tamil or Telugu) use Indic Parler-TTS or Indic-TTS when installed, else VoxCPM2. `commercial_only: true` never uses OmniVoice (CC-BY-NC, non-commercial): a sentence no commercial-use voice can say, a cloned voice without a commercial cloning engine, or no installed voice at all is answered in text with a `voice_unavailable_language`, `voice_unavailable_clone` or `voice_unavailable_voice` error event; the Talk screen always sends it. `multilingual_asr: true` hears with the multilingual Whisper tier (language auto-detected) instead of the dictation model; the Talk screen sends it unless the language is English |
| → | binary: microphone audio, PCM16 little-endian mono, 16 kHz, any chunk size |
| → | `{"type": "input_text", "text": "..."}` a typed message |
| → | `{"type": "interrupt"}` stop the current reply |
| → | `{"type": "session.end"}` |
| ← | `{"type": "session.ready", "input_sample_rate": 16000, "output_sample_rate": 24000}` |
| ← | `{"type": "state", "state": "listening" \| "thinking" \| "speaking"}` |
| ← | `{"type": "input.speech_started"}`, `{"type": "input.speech_stopped"}` |
| ← | `{"type": "transcript", "role": "user" \| "assistant", "text": "...", "interrupted": true}` |
| ← | `{"type": "transcript.delta", "role": "assistant", "text": "<one sentence>"}` |
| ← | binary: the agent's voice, PCM16 little-endian mono, 24 kHz |
| ← | `{"type": "audio.clear", "reason": "barge_in" \| "client" \| "new_input"}` drop audio you have not played yet |
| ← | `{"type": "response.done"}` |
| ← | `{"type": "error", "code": "...", "message": "..."}` |

A text-only client (a chat bot, a test) can skip audio input entirely and send
`input_text`; it still receives the spoken reply as audio plus transcripts.

Minimal Python client (text in, transcript out):

```python
import asyncio, json
import websockets

async def main():
    async with websockets.connect("ws://localhost:3900/v1/agent/ws") as ws:
        await ws.send(json.dumps({"type": "session.start", "instructions": "Be brief."}))
        await ws.send(json.dumps({"type": "input_text", "text": "What can you do?"}))
        async for message in ws:
            if isinstance(message, bytes):
                continue  # 24 kHz PCM16 audio: play or save it
            event = json.loads(message)
            if event["type"] == "transcript" and event["role"] == "assistant":
                print(event["text"])
            if event["type"] == "response.done":
                break

asyncio.run(main())
```

## Access

Like live transcription, the socket accepts loopback clients, and remote
clients that present the backend's `OMNIVOICE_API_KEY` as a bearer token.
Browser pages must be served from the backend itself or from an origin you
allowed. Use HTTPS (for example Tailscale Serve) before exposing it beyond
your machine.

## How it relates to phone calls

Phone calls (see [calls](calls.md)) run their own agent with call-specific
guardrails, a disclosure line and outcomes. This agent is the general-purpose
version: no telephony, your own instructions, any transport.
