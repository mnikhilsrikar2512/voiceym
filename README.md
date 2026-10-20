<div align="center">
  <img src="docs/logo.png" alt="Voiceym" width="88" />
  <h1>Voiceym</h1>
  <p><strong>A voice agent you can run anywhere, plus a private studio for voice cloning, voice design, dubbing, dictation and transcription.</strong></p>
  <p>
    <a href="#get-started">Get started</a> ·
    <a href="#talk-to-the-voice-agent">Voice agent</a> ·
    <a href="#documentation">Docs</a> ·
    <a href="https://github.com/mnikhilsrikar2512/voiceym/releases/latest">Download</a>
  </p>
  <p>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="AGPL-3.0" /></a>
  </p>
</div>

## What Voiceym does

Voiceym runs on your own computer. Your voice, your recordings and your text
stay on your hardware unless you choose a cloud service.

| Talk | Create | Connect |
| :--- | :--- | :--- |
| A voice agent that listens, thinks and answers out loud | Clone a voice or design a new one | WebSocket agent API for any app |
| Interrupt it mid-sentence, like a real conversation | Dub videos with timed speech | Local speech API and MCP server for AI tools |
| Dictate anywhere with a floating widget | Stories, audiobooks and batch jobs | Phone companion apps (preview) |

Start with **Voiceym** (default, powered by k2-fsa/OmniVoice), or choose another
engine. See the [features and engine catalog](docs/feature-catalog.md).

## Get started

### Run it from source

You need [Git](https://git-scm.com/), [Node.js 22+](https://nodejs.org/),
[Bun](https://bun.sh/), [Python 3.11](https://www.python.org/) and
[uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/mnikhilsrikar2512/voiceym.git
cd voiceym
bun install
bun run setup:api   # installs the Python backend and its dependencies
bun run dev         # starts the backend and the desktop app
```

The first run downloads a speech model when you generate for the first time.
See [Electron setup](electron/README.md) for backend options and
[troubleshooting](docs/install/troubleshooting.md) if something doesn't start.

### Install the desktop app

```sh
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/mnikhilsrikar2512/voiceym/main/scripts/install.sh | sh
```

On Windows, run this in PowerShell:

```powershell
irm https://raw.githubusercontent.com/mnikhilsrikar2512/voiceym/main/scripts/install.ps1 | iex
```

The installer keeps your settings, projects and models. Add `--main` to build
the latest source instead of a release, or `--uninstall` to remove the app and
keep your data. See [installer details](docs/install/script.md) and the
platform guides: **[macOS](docs/install/macos.md) · [Windows](docs/install/windows.md) · [Linux](docs/install/linux.md) · [Docker](docs/install/docker.md)**.

| Hardware | Support |
|---|---|
| NVIDIA GPU (Windows / Linux) | CUDA acceleration |
| Apple Silicon | Metal (MPS) acceleration |
| No dedicated GPU | Works on the CPU, more slowly (about 5 GB of free disk) |
| Windows on ARM | Experimental, CPU only |
| Intel Mac | App UI only; connect to a backend on another machine |

## Talk to the voice agent

With Voiceym running, open <http://localhost:3900/agent>, describe what the
agent should do, pick a voice and press **Start talking**. You can interrupt it
by speaking, or type instead of talking.

Your own apps, bots and scripts can talk to the same agent over a WebSocket at
`ws://localhost:3900/v1/agent/ws`. The [voice agent guide](docs/voice-agent.md)
has the protocol and a short Python example.

The agent uses the speech recognition and language model you pick in
**Settings → Models**: a local model through Ollama, LM Studio, llama.cpp or
vLLM, or a cloud provider.

## Documentation

| Need | Start here |
|---|---|
| Setup help | [Troubleshooting](docs/install/troubleshooting.md) · [Model downloads](docs/downloading-models.md) |
| Voice agent | [Browser and WebSocket guide](docs/voice-agent.md) |
| Models and audio quality | [Engine guides](docs/engines/README.md) · [Benchmarks](docs/benchmarks.md) |
| Integrations | [Local API](docs/speech-platform.md) · [MCP](docs/mcp.md) · [Examples](examples/README.md) |
| Phone companion (preview) | [iOS and Android apps](docs/mobile.md) |
| Development | [Contributing](.github/CONTRIBUTING.md) · [Electron app](electron/README.md) |

Coding agents can install the Voiceym skills with
`npx skills add mnikhilsrikar2512/voiceym`.

## License and responsible use

Voiceym is open source under the [AGPL-3.0](LICENSE); see the
[license notice](LICENSE-NOTICE.md). Model weights, voices and generated output
have their own terms, which the Model Manager shows for each model. Only clone
a voice with the speaker's permission.
