<div align="center">
  <img src="docs/logo.png" alt="Voiceym" width="88" />
  <h1>Voiceym</h1>
  <p><strong>A voice agent you can run anywhere: open-source voice cloning, voice design, video dubbing, dictation, transcription & audiobook creation in 646 languages.</strong></p>
  <p>
    <a href="https://github.com/mnikhilsrikar2512/voiceym/releases/latest">Download</a> ·
    <a href="#get-started">Get started</a> ·
    <a href="#documentation">Docs</a> ·
    <a href="README_CN.md">简体中文</a> ·
    <a href="README_JA.md">日本語</a>
  </p>
  <p>
    <a href="https://github.com/mnikhilsrikar2512/voiceym/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/mnikhilsrikar2512/voiceym/ci.yml?branch=main" alt="CI" /></a>
    <a href="https://github.com/mnikhilsrikar2512/voiceym/releases/latest"><img src="https://img.shields.io/github/v/release/mnikhilsrikar2512/voiceym" alt="Latest release" /></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="AGPL-3.0" /></a>
  </p>
</div>

> Voiceym is based on [VoiceStudio](https://github.com/debpalash/VoiceStudio) by Palash Debnath and contributors, used under the [AGPL-3.0](LICENSE). See [license details](LICENSE-NOTICE.md).

![A tour of the Electron app: voice cloning, voice design, dubbing, and model management](docs/media/electron/voiceym.gif)

## Your voice. Your workflow.

| Create | Produce | Connect |
| :--- | :--- | :--- |
| Clone a voice or design your own | Dub videos with timed speech | Local API & MCP for agents |
| Dictate with a floating widget | Stories, audiobooks & batch jobs | Reusable recipes & watch folders |

Start with **Voiceym** (default, powered by k2-fsa/OmniVoice), or choose another engine. [Features & engine catalog](docs/feature-catalog.md).

Local workflows run on your hardware. Remote services are optional; usage analytics requires consent.

<details>
<summary><strong>Explore the workspaces</strong> · Clone, dub, design & models</summary>

<table>
  <tr>
    <td><img src="docs/media/electron/voice-cloning.png" alt="Electron voice cloning workspace with the bundled demo voice" width="100%" /></td>
    <td><img src="docs/media/electron/dubbing.png" alt="Electron video dubbing workspace" width="100%" /></td>
  </tr>
  <tr><td align="center">Voice cloning</td><td align="center">Video dubbing</td></tr>
  <tr>
    <td><img src="docs/media/electron/voice-design.png" alt="Describe a voice in the Electron voice design workspace" width="100%" /></td>
    <td><img src="docs/media/electron/models.png" alt="Install and manage local speech models" width="100%" /></td>
  </tr>
  <tr><td align="center">Voice design</td><td align="center">Local models</td></tr>
</table>

<img width="2628" height="1950" alt="Voiceym desktop workspace" src="https://github.com/user-attachments/assets/b474497d-a453-49a3-a2dd-f023ec6b7659" />

</details>

## Get started

### One-command install (macOS / Linux)

```sh
# Latest Electron release
curl -fsSL https://raw.githubusercontent.com/mnikhilsrikar2512/voiceym/main/scripts/install.sh | sh

# A specific published Electron release (replace X.Y.Z)
curl -fsSL https://raw.githubusercontent.com/mnikhilsrikar2512/voiceym/main/scripts/install.sh | sh -s -- --version X.Y.Z

# Build current main and install the desktop app
curl -fsSL https://raw.githubusercontent.com/mnikhilsrikar2512/voiceym/main/scripts/install.sh | sh -s -- --main

# Uninstall the app, keeping your data
curl -fsSL https://raw.githubusercontent.com/mnikhilsrikar2512/voiceym/main/scripts/install.sh | sh -s -- --uninstall
```

On Windows, run `irm https://raw.githubusercontent.com/mnikhilsrikar2512/voiceym/main/scripts/install.sh | iex` in PowerShell.

If `voiceym.example/install` is unreachable or returns 404, run the same script
straight from the repository, or clone it and run `scripts/install.sh`:

```bash
curl -fsSL https://raw.githubusercontent.com/mnikhilsrikar2512/voiceym/main/scripts/install.sh | sh
```

Release downloads require curl and a SHA-256 tool. `--main` requires Git,
Node.js 22+, Bun, Rust/Cargo, and platform build tools; see
[installer prerequisites and behavior](docs/install/script.md).
The installer preserves your settings, projects, and models. Older versions
must contain Electron packages; it never falls back to archived Tauri builds.

Or download from [Releases](https://github.com/mnikhilsrikar2512/voiceym/releases/latest) (macOS `.dmg`, Windows `.exe`, Linux `.AppImage` or `.deb`) and follow your platform guide:

**[macOS](docs/install/macos.md) · [Windows](docs/install/windows.md) · [Linux](docs/install/linux.md) · [Docker](docs/install/docker.md)**

| Hardware | Support |
|---|---|
| NVIDIA GPU (Windows / Linux) | CUDA acceleration |
| Apple Silicon | Metal (MPS) acceleration |
| No dedicated GPU (Intel/AMD integrated graphics, older PCs) | Fully usable on the CPU, slower; setup installs the small CPU build of PyTorch (about 5 GB free disk) |
| Windows on ARM (Snapdragon X etc.) | **Experimental**, validation pending: native ARM64 app, x64 Python backend under emulation, CPU only |
| Intel Mac | App UI only; connect to a remote backend ([why](docs/install/macos.md)) |

Open **Voice cloning**, choose a voice or add a clean reference recording, enter your text, and generate. Install the required model when prompted. Hardware needs vary by engine; see [performance](docs/performance.md).

### Install with prompt

Paste into your coding agent (Claude Code, Codex, Cursor, …):

```text
Install the Voiceym Electron app on this device and verify it works, following
https://github.com/mnikhilsrikar2512/voiceym/blob/main/docs/install/agent.md
```

The [agent guide](docs/install/agent.md) covers hardware detection, reusing existing
data, asking before model downloads, and a test generation. Agents that support skills
can also run `npx skills add mnikhilsrikar2512/voiceym`.

<details>
<summary><strong>Run the Electron preview from source</strong></summary>

```bash
git clone https://github.com/mnikhilsrikar2512/voiceym.git
cd Voiceym
bun install
bun run setup:api  # prepare Python dependencies before starting Electron
bun run dev
```

See [Electron setup](electron/README.md) for prerequisites and backend configuration.

Use `bun run smoke-test` to build and launch an isolated packaged Electron app.
Add `-- --install` for the networked managed-runtime installation check.

</details>

> **Electron is the only desktop app and web UI.** Version 0.5.3 was the final Tauri release. Existing Tauri users must [install Electron separately](docs/electron-migration.md). The retired Tauri shell and legacy UI entry points have been removed.

## Documentation

| Need | Start here |
|---|---|
| Setup help | [Troubleshooting](docs/install/troubleshooting.md) · [Model downloads](docs/downloading-models.md) |
| Models & audio quality | [Engine guides](docs/engines/README.md) · [Benchmarks](docs/benchmarks.md) |
| Voice agent | [Talk to it in the browser or from code](docs/voice-agent.md) |
| Integrations | [Local API](docs/speech-platform.md) · [MCP](docs/mcp.md) · [Examples](examples/README.md) |
| Phone companion (preview) | [iOS and Android apps](docs/mobile.md) |
| Development | [Contributing](.github/CONTRIBUTING.md) · [Electron](electron/README.md) · [Changelog](CHANGELOG.md) |

Agent skills: `npx skills add mnikhilsrikar2512/voiceym` — choose **voiceym** for audio workflows or **voiceym-maintainer** for repository maintenance.

## License & responsible use

[AGPL-3.0](LICENSE) covers the application, including commercial use under its conditions. Model weights, tokenizers, voices and generated-output conditions are separate. Clone voices only with permission. Model Manager now shows versioned model-source notices, evidence links and unresolved review gaps from the [shared inventory](docs/licensing/model-inventory.md). Its optional [reviewed-file preview](docs/licensing/model-review-workflow.md) does not enforce ordinary downloads or certify commercial permission. See [license details](LICENSE-NOTICE.md).

