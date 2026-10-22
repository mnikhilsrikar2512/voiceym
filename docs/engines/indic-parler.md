# Voiceym — Indic Parler-TTS Engine

Indic Parler-TTS (AI4Bharat, Apache-2.0) speaks 21 Indian languages with
named speakers: Assamese, Bengali, Bodo, Dogri, English, Gujarati, Hindi,
Kannada, Konkani, Maithili, Malayalam, Manipuri, Marathi, Nepali, Odia,
Sanskrit, Santali, Sindhi, Tamil, Telugu and Urdu. Punjabi and Chhattisgarhi
also work, unofficially. Its licence allows commercial use, which makes it the
commercial-use alternative to [OmniVoice](omnivoice.md) (CC-BY-NC) for
Indian languages.

## When to pick it

- Talk in Tamil, Telugu, Kannada, Malayalam, Bengali, Marathi, Gujarati,
  Odia, Urdu and the other languages above. When it is installed, Talk speaks
  any reply in those scripts with it, and keeps [Kokoro](kokoro.md) for
  English and Hindi, which is faster.
- Reading text in those languages from the speech pages.

It does not clone voices. It picks the language from the reply's script: a
named language in Talk wins, and Devanagari text on Auto is read as Hindi.

## Setup

1. Sign in to Hugging Face, open
   [ai4bharat/indic-parler-tts](https://huggingface.co/ai4bharat/indic-parler-tts)
   and accept its terms (the download is gated).
2. Add your Hugging Face token in **Settings → Credentials** in Voiceym.
3. Install the engine with one click from **Model Catalogue** (TTS tab →
   Indic Parler-TTS → **Install**).

Upstream `parler-tts` pins Transformers 4.46.1, so the engine runs in its own
environment. The installer clones a reviewed upstream commit into
`DATA_DIR/engines/indic-parler/` with its own venv (about 4 GB with PyTorch),
then downloads the reviewed revision of the weights (about 3.8 GB). The small
tokenizer that reads the speaker description (`google/flan-t5-large`)
downloads on the first synthesis.

To make it the app's voice everywhere, select it via **Model Catalogue** (TTS
tab → **Use**) or `OMNIVOICE_TTS_BACKEND=indic-parler`.

## Hardware

- NVIDIA and Apple Silicon run it in half precision, about 2 GB of memory.
- A CPU runs it in full precision, about 4 GB, and slowly.
- Intel Macs aren't supported (no current PyTorch build).
- It is much slower than Kokoro: expect a few seconds per sentence on Apple
  Silicon, more on a CPU.
