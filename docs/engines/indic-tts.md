# Voiceym — Indic-TTS Engine

Indic-TTS (AI4Bharat, MIT) speaks 12 Indian languages: Assamese, Bengali,
Bodo, Gujarati, Kannada, Malayalam, Manipuri, Marathi, Odia, Punjabi, Tamil
and Telugu, each with a female and a male voice. Everything downloads from
GitHub and PyPI, so it needs **no account**. It runs on
[coqui-tts](https://github.com/idiap/coqui-ai-TTS) (MPL-2.0), the maintained
Coqui TTS. Both licences allow commercial use, which makes it a commercial-use
alternative to [OmniVoice](omnivoice.md) (CC-BY-NC) for Indian languages.

## When to pick it

- Talk in Tamil, Telugu, Kannada, Malayalam, Bengali, Marathi, Gujarati,
  Odia, Punjabi and the other languages above, with no Hugging Face account.
  When it is installed, Talk speaks replies in those scripts with it, and
  keeps [Kokoro](kokoro.md) for English and Hindi.
- [Indic Parler-TTS](indic-parler.md) covers more languages (21, including
  Urdu) with more expressive voices, but needs a Hugging Face account. When
  both are installed, Talk uses Indic Parler-TTS.

It does not clone voices. It picks the language from the reply's script; in
Talk a named language wins (Assamese shares Bengali's script, and Marathi and
Bodo share Devanagari with Hindi).

## Setup

Install it with one click from **Model Catalogue** (TTS tab → Indic-TTS →
**Install**). That makes its own environment in
`DATA_DIR/engines/indic-tts/` with coqui-tts and CPU PyTorch (about 2 GB).

Each language's voice downloads the first time a reply needs it: about
1.5 GB from AI4Bharat's
[GitHub release](https://github.com/AI4Bharat/Indic-TTS/releases/tag/v1-checkpoints-release),
checked against a recorded SHA-256, of which about 0.5 GB is kept in
`DATA_DIR/engines/indic-tts/voices/<language>/`. While it downloads, Talk
answers in text and says so; the next reply after it finishes is spoken.

To make it the app's voice everywhere, select it via **Model Catalogue** (TTS
tab → **Use**) or `OMNIVOICE_TTS_BACKEND=indic-tts`.

## Hardware

- Runs on the CPU on every supported computer: about 1.5 seconds per 5
  seconds of speech once loaded, and about 15 seconds to load a language the
  first time in a session.
- Intel Macs aren't supported (no current PyTorch build).
- Hindi isn't included: Kokoro already speaks it, faster.
