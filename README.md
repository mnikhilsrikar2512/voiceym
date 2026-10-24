<div align="center">
  <img src="docs/logo.svg" alt="Voiceym logo: seven sound bars forming a V" width="112" height="112" />
  <h1>Voiceym</h1>
  <p><strong>A private, local-first voice agent and voice studio.</strong><br />
  Talk to an AI out loud, clone and design voices, dub videos and transcribe speech, all on your own hardware.</p>
  <p>
    <a href="https://github.com/mnikhilsrikar2512/voiceym/actions/workflows/ci.yml"><img src="https://github.com/mnikhilsrikar2512/voiceym/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI status" /></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="License: AGPL-3.0" /></a>
    <img src="https://img.shields.io/badge/platforms-macOS%20%7C%20Windows%20%7C%20Linux%20%7C%20iOS%20%7C%20Android-informational" alt="Platforms: macOS, Windows, Linux, iOS, Android" />
    <img src="https://img.shields.io/badge/python-3.11-3776AB" alt="Python 3.11" />
    <img src="https://img.shields.io/badge/node-22%2B-339933" alt="Node.js 22+" />
  </p>
  <p>
    <a href="#quick-start">Quick start</a> ·
    <a href="#commands-by-platform">Commands</a> ·
    <a href="#using-voiceym">User guide</a> ·
    <a href="docs/TEST-CASES.md">Test cases</a> ·
    <a href="#documentation">Docs</a> ·
    <a href="https://github.com/mnikhilsrikar2512/voiceym/releases/latest">Download</a>
  </p>
</div>

---

## Overview

Voiceym runs entirely on your computer. Your voice, recordings and text stay
on your hardware unless you choose a cloud service. The default engines are
open source and licensed for commercial use, so the same setup works for
personal projects and paid work.

| Talk | Create | Connect |
| :--- | :--- | :--- |
| A voice agent that listens, thinks and answers out loud | Clone a voice or design a new one from a description | WebSocket agent API for any app |
| Auto-detects your language and answers in the same mix of languages | Dub videos with timed, translated speech | Local speech API and MCP server for AI tools |
| Interrupt it mid-sentence, like a real conversation | Stories, audiobooks, batch jobs and workflows | iPhone, iPad and Android companion apps |

## Contents

- [Overview](#overview)
- [Platform support](#platform-support)
- [Quick start](#quick-start)
- [Commands by platform](#commands-by-platform)
- [Using Voiceym](#using-voiceym)
- [Voices, models and licences](#voices-models-and-licences)
- [Architecture](#architecture)
- [For developers and AI tools](#for-developers-and-ai-tools)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Documentation](#documentation)
- [Contributing and security](#contributing-and-security)
- [License and responsible use](#license-and-responsible-use)

## Platform support

| Platform | Status | Acceleration |
|---|---|---|
| macOS 13.3+ on Apple Silicon | Supported | Metal (MPS) |
| macOS on Intel | App only; connect to a backend on another machine | None |
| Windows 10 / 11 (x64) | Supported | NVIDIA CUDA, or CPU |
| Windows on ARM | Experimental | CPU (x64 backend under emulation) |
| Linux (x64) | Supported | NVIDIA CUDA, or CPU |
| iOS 17+ (iPhone, iPad) | Companion app (preview) | Runs on the paired computer |
| Android | Companion app (preview) | Runs on the paired computer |

**Memory.** 8 GB runs the voice agent with the small models suggested below.
16 GB leaves room for larger language models and voice cloning. Plan for about
10 GB of free disk for the app, its Python environment and models.

## Quick start

### Install the app

Download the installer for your computer from the
[latest release](https://github.com/mnikhilsrikar2512/voiceym/releases/latest),
or let the install script pick, verify and install it:

```sh
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/mnikhilsrikar2512/voiceym/main/scripts/install.sh | sh
```

```powershell
# Windows (PowerShell, not as administrator)
irm https://raw.githubusercontent.com/mnikhilsrikar2512/voiceym/main/scripts/install.ps1 | iex
```

Add `--main` to build the latest source instead of a release, or `--uninstall`
to remove the app and keep your data. Platform guides:
**[macOS](docs/install/macos.md) · [Windows](docs/install/windows.md) ·
[Linux](docs/install/linux.md) · [Docker](docs/install/docker.md)** ·
[installer details](docs/install/script.md).

### Run from source

You need [Git](https://git-scm.com/), [Node.js 22+](https://nodejs.org/),
[Bun](https://bun.sh/), [Python 3.11](https://www.python.org/),
[uv](https://docs.astral.sh/uv/) and, for the voice agent,
[Ollama](https://ollama.com/). The [commands below](#commands-by-platform)
install them for each platform.

```bash
git clone https://github.com/mnikhilsrikar2512/voiceym.git
cd voiceym
bun install
bun run setup:api   # installs the Python backend and its dependencies
bun run dev         # starts the backend and the desktop app
```

### First run

The first launch downloads the default voice, **Kokoro** (about 95 MB). Other
models download only when you choose them in **Settings → Models**, which shows
each model's size and licence first.

### Pick a language model

The voice agent needs a language model to think with. The simplest option is a
small local model through Ollama:

```bash
ollama pull qwen3:1.7b     # about 1.4 GB; good English and Indian languages
ollama pull llama3.2:1b    # about 1.3 GB; the lightest English option
```

Every model you pull appears in **Talk → Agent setup → Language model**. LM
Studio, llama.cpp, vLLM and cloud providers can be added from
**Settings → Models**.

## Commands by platform

Copy the block for your platform. Every command runs from the `voiceym`
folder unless it says otherwise. To check that everything works, follow the
[test cases](docs/TEST-CASES.md).

### macOS (Apple Silicon)

Install the tools once with [Homebrew](https://brew.sh/):

```bash
xcode-select --install
brew install git node python@3.11 uv ollama oven-sh/bun/bun
```

Get Voiceym and run it:

```bash
git clone https://github.com/mnikhilsrikar2512/voiceym.git
cd voiceym
bun install
bun run setup:api           # Python backend and its dependencies
ollama serve &              # skip if the Ollama app is already running
ollama pull qwen3:1.7b      # language model for Talk (good on 8 GB Macs)
bun run dev                 # starts the backend and the desktop app
```

Day to day:

```bash
git pull && bun install && bun run setup:api   # update to the latest code
bun run dev                                    # run the app
bun run dev:web                                # run in the browser at http://localhost:3901
bun run test                                   # desktop app unit tests
uv run pytest tests/ -q                        # backend tests
bun run typecheck && bun run lint              # code checks
bun run dist                                   # build a .dmg and .zip in electron/release
```

### Windows 10 / 11

Open **PowerShell** (not as administrator) and install the tools once:

```powershell
winget install Git.Git OpenJS.NodeJS.LTS Python.Python.3.11 Ollama.Ollama
powershell -c "irm bun.sh/install.ps1 | iex"
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Also install [Visual Studio 2022 Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)
with the **Desktop development with C++** workload; some Python packages
compile native code during setup.

Close and reopen PowerShell so the new tools are found, then get Voiceym and
run it:

```powershell
git clone https://github.com/mnikhilsrikar2512/voiceym.git
cd voiceym
bun install
bun run setup:api           # Python backend; picks CUDA or CPU automatically
ollama pull qwen3:1.7b      # language model for Talk
bun run dev                 # starts the backend and the desktop app
```

Day to day:

```powershell
git pull; bun install; bun run setup:api   # update to the latest code
bun run dev                                # run the app
bun run dev:web                            # run in the browser at http://localhost:3901
bun run test                               # desktop app unit tests
uv run pytest tests/ -q                    # backend tests
bun run typecheck; bun run lint            # code checks
bun run dist                               # build a .exe installer in electron\release
```

To keep the large voice engines off `C:`, see
[Keep engine environments off C:](docs/install/windows.md#keep-engine-environments-off-c).

### iOS (iPhone and iPad companion)

The iPhone app is a remote control for Voiceym running on your computer: it
does no AI work itself. You need a Mac with full **Xcode**, an iOS 17+
simulator or device, and Voiceym running on a computer on the same network.

```bash
brew install xcodegen
mobile/ios/scripts/check-swift.sh test                     # shared Swift package tests
xcodegen generate --spec mobile/ios/Voiceym/project.yml   # create the Xcode project
open mobile/ios/Voiceym/Voiceym.xcodeproj                 # run the Voiceym scheme
```

Run the app's UI tests on a simulator (list the names you have with
`xcrun simctl list devices available`):

```bash
xcodebuild -project mobile/ios/Voiceym/Voiceym.xcodeproj \
  -scheme Voiceym -destination 'platform=iOS Simulator,name=iPhone 16' test
```

To pair: on the computer, open the sidebar, click **Local** at the bottom and
choose **Network**. On the phone, tap **Scan QR code**. The
[phone app guide](docs/mobile.md) and [iOS developer notes](mobile/ios/README.md)
have the details.

## Using Voiceym

Voiceym opens on **Home**, which asks what you would like to do and links each
task (talk, text to speech, cloning, voice design, dubbing, transcription,
audiobooks, stories) to its page with a one-line description. The sidebar is a slim
icon rail; rest the pointer on it to open the full menu, which lists every page
by name under **Create**, **Library** and **More** (hover an item to see what it
does), and move away to close it. Each page's own side panel collapses with its
panel button. Press **Cmd/Ctrl+K** anywhere to search for a page or action,
and **Cmd/Ctrl+1–9** to jump between workspaces.

### Talk to the voice agent

Open **Talk** from Home or the sidebar.

1. Open **Agent setup** and write the agent's **Instructions**, for example
   "You are a friendly English tutor. Keep answers short."
2. Pick a **Voice**, a **Language model** and a **Language**. Leave Language on
   **Auto** to have it follow whatever language you speak.
3. Press **Start talking** (or the space bar) and speak. The agent listens,
   answers out loud and shows the transcript.
4. Talk over it to interrupt; turn this off with **Let me interrupt it**. Turn
   on **Agent speaks first** if you want it to greet you.
5. Prefer typing? Use **Type a message** at the bottom of the conversation.

Changes to the setup apply to your next conversation. When a language can't be
spoken with a commercial-use voice, the agent still answers, in text, and tells
you why.

**Languages.** Talk understands speech in many languages. It speaks English, Hindi,
Spanish, French, Italian and Portuguese with Kokoro. To hear Tamil, Telugu,
Kannada, Malayalam, Bengali, Marathi and the other Indian languages, install
**Indic-TTS**, which needs no account (see [Voices and licences](#voices-models-and-licences)). For Hindi
and other Indian languages, `qwen3:1.7b` answers well on an 8 GB computer.

**Your own apps** can talk to the same agent over a WebSocket at
`ws://localhost:3900/v1/agent/ws`. The [voice agent guide](docs/voice-agent.md)
has the protocol and a short Python example.

### Turn text into speech

Open **Text to speech**, type or paste your text, pick a voice and press
generate. Every take stays in the history, where you can play, download or
star it.

### Clone a voice

1. On **Home**, choose **Clone a voice** and add a short, clean recording of the voice (a few
   seconds of one person speaking, with no music), or record one in the app.
2. Check the transcript of the recording, give the voice a name and save it.
3. Type a script and press generate to hear it in the cloned voice.

Saved voices live in **My voices** and appear in every voice picker,
including Talk. English cloning uses **Chatterbox** and other languages use
**VoxCPM2**; install them once from **Settings → Models**. Only clone a voice
with the speaker's permission.

### Design a new voice

Open **Design a voice** to create a voice that doesn't belong to anyone. Choose a
preset or describe it ("a calm older woman with a British accent"), adjust
traits such as age, pitch and style, and write a test line. Press **New seed**
for a different voice with the same traits, and **Save as voice profile** to
keep it.

### Browse ready-made voices

**Voice gallery** lists ready-made voices by category. Filter by age, gender, pitch,
accent or language, play a preview, and star the ones you like.

### Stories and audiobooks

* **Stories**: write or import a script, pick a default voice, and give
  individual lines their own voices to make a conversation or radio play.
* **Audiobook**: import a manuscript; chapter titles are detected
  automatically. Preview a chapter before rendering the whole book, add
  pronunciations for tricky names, and export MP3 or M4B with chapters and a
  cover.

Long renders keep going if you switch pages, and you can stop or resume them.

### Dub a video

1. Open **Dub a video** and drop in a video or audio file.
2. Voiceym transcribes it and translates it into the language you pick.
3. Edit the translated lines, the voice and the timing of each segment.
4. Generate the dubbed track and export it.

Use **Batch Dubbing** to dub many videos into several languages at once, with
a queue that shows each job's progress. Save dubbing work in **Projects** to
come back to it later.

### Transcribe and dictate

* **Transcribe**: upload an audio file, or press **Start dictation**, then
  stop to get the text. Past transcriptions stay in the history.
* **Dictate anywhere**: set a **Dictation shortcut** in **Settings → Models**,
  then press it in any app to speak and have the text typed for you.

### Automate with workflows

**Workflows** run the same steps over many inputs. Build a pipeline on the
canvas from steps such as **Speak**, **Transcribe**, **Translate**, **Convert**
and **Normalize**, then run it over up to 50 scripts or audio clips.

### More tools

* **Projects** collects your dubbing projects, stories, audiobooks, voices,
  transcriptions and exports in one searchable library.
* **Tools** has speech-rate fitting and audio file inspection.
* **Integrations** connects Voiceym to other apps and AI tools.

### Settings you will use most

| Setting | What it's for |
|---|---|
| **Settings → Models** | Install, remove and switch voice, speech recognition and language models, read each model's licence, and set the dictation shortcut |
| **Settings → General** | App language and general behaviour |
| **Settings → Appearance** | Light or dark theme and layout |
| **Settings → Storage** | Where models and your files are kept, and how much space they use |
| **Settings → Privacy** | Analytics and what leaves your computer |
| **Settings → Diagnostics** and **Logs** | Help when something doesn't work |

## Voices, models and licences

The default voice is **Kokoro**. Every engine Voiceym offers is open source
and allows commercial use, and each model's licence is shown in
**Settings → Models** before you download it. The older
**OmniVoice** (k2-fsa, 600+ languages) engine is non-commercial (CC-BY-NC),
so it is retired.

| Engine | Used for | Licence |
|---|---|---|
| **Kokoro** (default) | Fast speech in English, Hindi, Spanish, French, Italian and Portuguese on any computer | Apache-2.0 |
| **Chatterbox** | English voice cloning | MIT |
| **VoxCPM2** | Voice design from a description, and cloning in 30 languages | Apache-2.0 |
| **Indic-TTS** | Speech in 12 Indian languages, including Tamil and Telugu, with no account | MIT |
| **Indic Parler-TTS** | Speech in 21 Indian languages (needs a Hugging Face account) | Apache-2.0 |
| **KittenTTS** | Very light English speech for CPU-only computers | Apache-2.0 |
| OmniVoice (retired) | Kept working only for installs that already use it | CC-BY-NC, **non-commercial only** |

* **Indic-TTS** installs with one click from **Model Catalogue** (about 2 GB).
  Each language's voice downloads the first time Talk needs it (about 1.5 GB,
  once). See the [Indic-TTS guide](docs/engines/indic-tts.md).
* **Indic Parler-TTS** is gated: accept its terms on
  [Hugging Face](https://huggingface.co/ai4bharat/indic-parler-tts), add your
  token in **Settings → Credentials**, then install it from **Model Catalogue**
  (about 8 GB). See the [Indic Parler-TTS guide](docs/engines/indic-parler.md).
* **OmniVoice** is no longer offered for new picks, and Talk never uses it.
  Speaker diarisation uses pyannote (MIT).

Speech recognition uses Whisper-family models, with dedicated Telugu and Hindi
models for Indian languages. The full list is in the
[features and engine catalog](docs/feature-catalog.md).

## Architecture

| Part | Technology | Where |
|---|---|---|
| Desktop app | Electron, React, TypeScript | [`electron/`](electron/README.md) |
| Backend and AI engines | Python 3.11, FastAPI, PyTorch / ONNX, on `localhost:3900` | [`backend/`](backend/) |
| Voice engines | Isolated per-engine Python environments, installed on demand | [Engine guides](docs/engines/README.md) |
| Native helpers | Rust (dictation, system integration) | [`native/`](native/) |
| Phone companions | SwiftUI (iOS) and Kotlin (Android), paired to the desktop | [`mobile/`](docs/mobile.md) |

The repository layout is described in [docs/STRUCTURE.md](docs/STRUCTURE.md).

## For developers and AI tools

| Interface | Where |
|---|---|
| Voice agent over WebSocket | `ws://localhost:3900/v1/agent/ws` · [guide](docs/voice-agent.md) |
| Local speech API (speech, transcription, voices) | `http://localhost:3900` · [guide](docs/speech-platform.md) |
| MCP server for Claude Code, Cursor and other agents | `http://localhost:3900/mcp` · [guide](docs/mcp.md) |
| Code examples | [examples](examples/README.md) |

Coding agents can install the Voiceym skills with
`npx skills add mnikhilsrikar2512/voiceym`.

## Testing

Manual test cases for macOS, Windows and iOS, with the automated suites to run
first, are in **[docs/TEST-CASES.md](docs/TEST-CASES.md)**. The main automated
checks are:

| Suite | Command |
|---|---|
| Backend (Python) | `uv run pytest tests/ backend/tests/ -q` |
| Docs match the engines | `uv run python scripts/check-docs-drift.py` |
| Desktop app unit tests | `bun run test` |
| Type checks and lint | `bun run typecheck` · `bun run lint` |
| Full desktop build check | `bun run check:electron` |
| iOS Swift package | `mobile/ios/scripts/check-swift.sh test` |

Set `HF_HUB_OFFLINE=1` (PowerShell: `$env:HF_HUB_OFFLINE = "1"`) before the
backend tests so they never download models. Every pull request runs these, plus browser smoke tests and desktop UI tests on
macOS, Windows and Linux, in [CI](.github/workflows/ci.yml).

## Troubleshooting

| Problem | Try this |
|---|---|
| The app opens but nothing generates | Check **Settings → Models** for a model that is still downloading or failed |
| Talk says it can't reach the language model | Make sure Ollama is running (`ollama list`) and pick a model in **Agent setup** |
| Replies are slow or the computer struggles | Use `qwen3:1.7b` or `llama3.2:1b`, and close other heavy apps |
| Dictation doesn't type anything | Allow microphone and accessibility access for Voiceym in system settings |
| `bun run dev` says a port is in use | Run `bun scripts/clear-dev-ports.mjs`, then start again |

More fixes are in the [troubleshooting guide](docs/install/troubleshooting.md)
and the [model download guide](docs/downloading-models.md).

## Documentation

| Need | Start here |
|---|---|
| Installing | [macOS](docs/install/macos.md) · [Windows](docs/install/windows.md) · [Linux](docs/install/linux.md) · [Docker](docs/install/docker.md) |
| Setup help | [Troubleshooting](docs/install/troubleshooting.md) · [Model downloads](docs/downloading-models.md) |
| Checking that it works | [Test cases for macOS, Windows and iOS](docs/TEST-CASES.md) |
| Voice agent | [Browser and WebSocket guide](docs/voice-agent.md) |
| Models and audio quality | [Engine guides](docs/engines/README.md) · [Benchmarks](docs/benchmarks.md) |
| Integrations | [Local API](docs/speech-platform.md) · [MCP](docs/mcp.md) · [Examples](examples/README.md) |
| Phone companions | [iOS and Android apps](docs/mobile.md) · [iOS developer notes](mobile/ios/README.md) |
| Development | [Contributing](.github/CONTRIBUTING.md) · [Electron app](electron/README.md) · [Changelog](CHANGELOG.md) · [Roadmap](docs/ROADMAP.md) |

## Contributing and security

Contributions are welcome. Read the [contributing guide](.github/CONTRIBUTING.md)
for the development setup, coding conventions and how new engines are
accepted. Report vulnerabilities privately as described in the
[security policy](.github/SECURITY.md), not in public issues.

## License and responsible use

Voiceym is open source under the [AGPL-3.0](LICENSE); see the
[license notice](LICENSE-NOTICE.md). Model weights, voices and generated audio
have their own terms, which **Settings → Models** shows for each model.

Only clone a voice with the speaker's permission, and label synthetic speech
where your audience could mistake it for a real person.
