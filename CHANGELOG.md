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

### Changed

- Talk's default voice uses the light KittenTTS engine, so conversations stay responsive on 8 GB machines; cloned voices keep their engine (#1)
- Talk hears any language with multilingual Whisper unless set to English, and its default voice speaks Hindi with Kokoro and other scripts with OmniVoice GGUF when installed (#1)
- Kokoro picks a native voice for the requested language instead of reading everything in its American voice (#1)
- Talk only speaks with commercial-use voices and never falls back to OmniVoice; when no such voice can speak, it answers in text and says why (#1)
- OmniVoice engines are labelled non-commercial wherever they are offered (#1)
- Kokoro (Apache-2.0) is now the default voice on every platform; OmniVoice (CC-BY-NC) is opt-in and no longer preloaded unless chosen (#1)
- Chatterbox (MIT) adds commercial-use English voice cloning as a one-click install; Talk uses it for cloned voices (#1)

### Fixed

- Ending a Talk session no longer logs a server error when the client already disconnected (#1)
