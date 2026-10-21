# Voiceym — Kokoro Engine (default)

Kokoro-82M (hexgrad, Apache-2.0) is Voiceym's default voice. It runs the
community ONNX export on the CPU with ONNX Runtime, so it behaves the same on
macOS, Windows and Linux and needs nothing beyond what Voiceym already
installs. Its licence allows commercial use of the model and of the audio you
make with it.

## When to pick it

- Everyday narration, the Talk voice agent, and anything you may use
  commercially.
- English and Hindi, plus Spanish, French, Italian and Portuguese.
- Machines without a strong GPU: the 8-bit model is about 92 MB.

The trade-off against [OmniVoice](omnivoice.md): no voice cloning or voice
design and six languages instead of 600+, but a small, fast install and a
commercial-use licence. OmniVoice is CC-BY-NC (non-commercial) and is now an
opt-in engine.

## Setup

Nothing to install. First-run setup downloads the model from
`onnx-community/Kokoro-82M-v1.0-ONNX` (about 95 MB: the 8-bit model, the
tokenizer and one small style file per default voice). Select it any time via
**Model Catalogue** (TTS tab → **Use**) or `OMNIVOICE_TTS_BACKEND=kokoro`.

## Languages and voices

| Language | Default voice | Phonemes |
|---|---|---|
| English | `af_heart` | misaki (Kokoro's own G2P) |
| Hindi | `hf_alpha` | espeak-ng |
| Spanish | `ef_dora` | espeak-ng |
| French | `ff_siwis` | espeak-ng |
| Italian | `if_sara` | espeak-ng |
| Portuguese (Brazil) | `pf_dora` | espeak-ng |

With the language on **Auto**, Devanagari text is spoken in Hindi and
everything else in English. Other preset voices (`af_bella`, `am_michael`,
`bf_emma`, `bm_george`, `hf_beta`, `hm_omega`, `hm_psi`, …) can be passed as
the voice; each downloads its ~0.5 MB style file on first use. Any other
language is refused with a clear message rather than read with the wrong
accent.
