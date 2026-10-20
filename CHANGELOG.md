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
