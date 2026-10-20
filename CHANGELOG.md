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

### Changed

- Talk's default voice uses the light KittenTTS engine, so conversations stay responsive on 8 GB machines; cloned voices keep their engine (#1)

### Fixed

- Ending a Talk session no longer logs a server error when the client already disconnected (#1)
