# Changelog

All notable changes to Voiceym.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/).
`package.json` is the maintained app-version source of truth; Electron package
metadata and the backend fallback mirror it.

## [Unreleased]

**Highlights**

- A voice agent you can talk to from a browser or any app over a WebSocket

### Added

- Voice agent: open `/agent` in a browser, or connect to `/v1/agent/ws`, to talk with a local agent that listens, answers out loud and stops when you interrupt it ([guide](docs/voice-agent.md)) (#1)
- Talk: choose which local Ollama model answers; any model you `ollama pull` shows up in Setup (#1)
- Indic Parler-TTS (AI4Bharat, Apache-2.0) speaks 21 Indian languages, including Tamil, Telugu, Kannada, Malayalam and Bengali, as a one-click install; Talk speaks those languages with it when it is installed (#9)
- Indic-TTS (AI4Bharat, MIT) speaks 12 Indian languages, including Tamil, Telugu, Kannada, Malayalam and Bengali, with no account: a one-click install, and each language's voice downloads from GitHub the first time Talk needs it (#9)

### Changed

- Voiceym opens on a new Home page that asks what you'd like to do and explains each task in one line; Talk moves to its own page (#10)
- The sidebar lists every page by name under Create, Library and More, each with a short description, instead of collapsed groups (#10)
- The sidebar's simple view leaves speed/quality to the detailed views and Settings → Performance, so the menu fits (#10)
- Messages that pointed to removed engines now name VoxCPM2, Chatterbox, Indic Parler-TTS or Kokoro, and Voice Design offers a Change engine button (#10)
- Talk's default voice uses the light KittenTTS engine, so conversations stay responsive on 8 GB machines; cloned voices keep their engine (#1)
- Talk hears any language with multilingual Whisper unless set to English, and its default voice speaks Hindi with Kokoro and other scripts with VoxCPM2 when installed (#1)
- Kokoro picks a native voice for the requested language instead of reading everything in its American voice (#1)
- Talk only speaks with commercial-use voices and never falls back to OmniVoice; when no such voice can speak, it answers in text and says why (#1)
- OmniVoice engines are labelled non-commercial wherever they are offered (#1)
- Kokoro (Apache-2.0) is now the default voice on every platform; OmniVoice (CC-BY-NC) is opt-in and no longer preloaded unless chosen (#1)
- Chatterbox (MIT) adds commercial-use English voice cloning as a one-click install; Talk uses it for cloned voices (#1)

### Removed

- Voiceym now ships only voice engines that are free for commercial use: Kokoro (default), KittenTTS, VoxCPM2, Chatterbox, Indic Parler-TTS and Indic-TTS (#10)
- Removed CosyVoice, GPT-SoVITS, IndexTTS, MOSS-TTS, dots.tts, Confucius4, PocketTTS, Supertonic-3, Sherpa-ONNX TTS, MLX-Audio, audio.cpp TTS and OmniVoice GGUF, so installs are smaller and simpler (#10)
- OmniVoice (CC-BY-NC) is no longer offered for new picks; an install that already uses it keeps working (#10)
- Removed the Sortformer speaker-diarisation runtime (non-commercial model) and the audio.cpp runtime it needed; diarisation uses pyannote (MIT), and a saved Sortformer choice switches to it (#10)

### Fixed

- Talk on Auto detects each sentence's language, re-hears Telugu or Hindi with its tuned model, replies in the same mix of languages, and reads English parts with the English voice (#10)
- Talk answers sooner: models load when a conversation starts, reasoning models skip silent thinking, replies are capped, and changing language, voice or model restarts the conversation at once (#10)
- Talk hears Telugu and Hindi with Whisper fine-tuned for each language (SPRING Lab, IIT Madras) when installed, and never speaks emoji (#10)
- The Indic-TTS install no longer fails at verify: it pins a PyTorch that coqui-tts can import (#10)
- Talk no longer splits a pause or an "uh" into its own turn, and hears quieter microphones such as AirPods (#10)
- Ending a Talk session no longer logs a server error when the client already disconnected (#1)
- A phone call that ended normally is no longer recorded as failed when the provider had already closed the stream (#1)
