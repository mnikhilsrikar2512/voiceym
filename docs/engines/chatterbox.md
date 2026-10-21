# Voiceym — Chatterbox Engine

Chatterbox (Resemble AI, MIT) clones an English voice from a short reference
clip. Its licence allows commercial use of the model and of the audio you
make, which makes it the commercial-use alternative to
[OmniVoice](omnivoice.md) (CC-BY-NC) for English cloning. Talk uses it for a
cloned voice when it is installed.

## When to pick it

- English voice cloning you may use commercially.
- Pair it with [Kokoro](kokoro.md), the default voice, for everything that
  doesn't need a clone.

English only: for other languages, a cloned voice still needs OmniVoice
(non-commercial). Chatterbox embeds Resemble AI's imperceptible Perth
watermark in its output.

## Setup

Upstream pins PyTorch 2.6 and Transformers 5.2, so Chatterbox runs in its own
environment. Install it with one click from **Model Catalogue** (TTS tab →
Chatterbox → **Install**): the installer clones a reviewed upstream commit
into `DATA_DIR/engines/chatterbox/` with its own venv (about 6 GB with
PyTorch). The weights (`ResembleAI/chatterbox`, about 3 GB) download on the
first synthesis; a long first render is that download, not a hang.

Then select it via **Model Catalogue** (TTS tab → **Use**) or
`OMNIVOICE_TTS_BACKEND=chatterbox`.

## Hardware

- NVIDIA on Linux uses PyPI's CUDA build of PyTorch 2.6.
- Apple Silicon uses MPS.
- Windows runs on the CPU (PyTorch 2.6 has no CUDA 12.8 build to pin), which
  works but is slow.
- Intel Macs aren't supported (no PyTorch 2.6 build).
- Expect about 2–3 GB of memory while it synthesizes.
