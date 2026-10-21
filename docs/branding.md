# Voiceym brand

Voiceym's mark is seven rounded sound bars that rise and fall like a voice and
together form the letter V, so the same shape reads as a waveform and as the V
of Voiceym. The product voice is clear, calm, and direct.

## Assets

| Surface | Source |
|---|---|
| Primary vector mark | `docs/logo.svg` |
| README mark | `docs/logo.png` and `docs/logo-256.png` |
| Browser icon | `electron/public/favicon.svg` |
| In-app mark | `frontend/src/components/brand/VoiceymMark.jsx` |
| Desktop/platform icons | `electron/build/icons/` |
| Electron sidebar and browser icon | `electron/public/favicon.svg` via `electron/src/renderer/src/lib/brand.ts` |
| Sidebar/launchpad artwork | `electron/public/signal-field.webp` |

Regenerate every desktop icon from the canonical vector after changing the
mark:

Export the canonical vector to the required PNG, ICO, and ICNS sizes under
`electron/build/icons/`, then run the Electron packaging contract.

Do not redraw the bars per screen. Use `VoiceymMark` in React and the
canonical SVG elsewhere so the silhouette stays recognizable at 16–512 px.

## Palette

| Role | Color |
|---|---|
| Voice teal | `#5EEAD4` |
| Voice indigo | `#818CF8` |
| Voice violet | `#E879F9` |
| Indigo tile | `#262257` → `#0B0A1E`, with a soft indigo glow behind the bars |

The bars use one gradient from teal to violet. The mark may render in one color
inside app chrome. Keep all seven bars and their V shape; do not put text
inside the icon or add another enclosing ring.

## Name and copy

- Product name: **Voiceym**—one word, capital V and S.
- Voice: concise, professional, warm, and direct.
- Promise: local-first creation without a subscription or usage meter.
- Avoid absolute privacy claims: network-backed engines, downloads, analytics,
  and cloud integrations are explicit opt-ins, not nonexistent.
- Attribute the bundled default model as **k2-fsa/OmniVoice** where model lineage
  matters. OmniVoice is an upstream model/runtime name, not the product name.

## Compatibility names

The rebrand must not break existing installations. Keep these identifiers until
a separately tested migration exists:

- `omnivoice` Python imports and package name
- `omnivoice-studio` binary/package, Docker Hub path, and legacy GHCR alias
- `OMNIVOICE_*` environment variables and `X-OmniVoice-*` API headers
- existing OmniVoice data/cache directories and uninstall aliases
- upstream repositories, model IDs, classes, and engine IDs

Visible copy can explain those compatibility names, but must not silently rename
them on disk or over the wire.

Electron uses the shared multi-resolution ICO for Windows window/taskbar and tray
icons, the shared PNG for Linux and macOS runtime icons, and the ICNS for the macOS
bundle. The tray icon restores the window; closing the app retains its existing
quit behavior. Installed executable icons are applied when building the installer.
