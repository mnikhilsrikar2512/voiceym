"""Kokoro-82M (Apache-2.0) through ONNX Runtime — the default voice.

Runs the community ONNX export of hexgrad/Kokoro-82M on CPU with the same
pieces KittenTTS already ships (onnxruntime, misaki, espeak-ng), so it works
identically on macOS, Windows and Linux with no extra dependency. English uses
misaki's own G2P (what Kokoro was trained with); Hindi, Spanish, French,
Italian and Portuguese use espeak-ng phonemes, as upstream Kokoro does.

Weights come from ``onnx-community/Kokoro-82M-v1.0-ONNX`` on the Hugging Face
Hub: the 8-bit model (~92 MB), the tokenizer and one ~0.5 MB style file per
voice, fetched only for the voices actually used.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from typing import Optional

from services.tts_backend import TTSBackend, TTSInputError, _retry_once_with_fresh_hf_client

logger = logging.getLogger("omnivoice.tts")

REPO_ID = "onnx-community/Kokoro-82M-v1.0-ONNX"
GRAPH_FILE = "onnx/model_quantized.onnx"
TOKENIZER_FILE = "tokenizer.json"
SAMPLE_RATE = 24000
#: The model's positional limit, minus the two boundary tokens.
MAX_PHONEMES = 510

#: language code -> (default voice, espeak language or None for misaki English)
LANGUAGES: dict[str, tuple[str, Optional[str]]] = {
    "en": ("af_heart", None),
    "hi": ("hf_alpha", "hi"),
    "es": ("ef_dora", "es"),
    "fr": ("ff_siwis", "fr-fr"),
    "it": ("if_sara", "it"),
    "pt": ("pf_dora", "pt-br"),
}

#: Preset voices offered per language (hexgrad/Kokoro-82M VOICES.md).
PRESET_VOICES = [
    "af_heart", "af_bella", "af_nicole", "af_sarah", "am_michael", "am_fenrir",
    "bf_emma", "bf_isabella", "bm_george", "bm_fable",
    "hf_alpha", "hf_beta", "hm_omega", "hm_psi",
    "ef_dora", "em_alex", "ff_siwis", "if_sara", "im_nicola", "pf_dora", "pm_alex",
]

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_SENTENCE = re.compile(r"(?<=[.!?।॥])\s+")


def required_patterns(voices: list[str]) -> list[str]:
    return [GRAPH_FILE, TOKENIZER_FILE, *(f"voices/{v}.bin" for v in voices)]


class KokoroBackend(TTSBackend):
    id = "kokoro"
    display_name = "Kokoro (82M, Apache-2.0, English, Hindi + 4 more)"
    gpu_compat = ("cpu",)
    supports_cloning = False
    supports_voice_design = False
    language_display_names = {
        "en": "English", "hi": "Hindi", "es": "Spanish",
        "fr": "French", "it": "Italian", "pt": "Portuguese",
    }

    def __init__(self):
        self._session = None
        self._vocab: dict[str, int] = {}
        self._dir: Optional[str] = None
        self._voices: dict[str, object] = {}
        self._g2p: dict[str, object] = {}
        self._lock = threading.Lock()

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        try:
            import misaki  # noqa: F401
            import onnxruntime  # noqa: F401
        except ImportError as e:
            return False, f"Kokoro needs onnxruntime and misaki: {e}"
        return True, "ready"

    @property
    def sample_rate(self) -> int:
        return SAMPLE_RATE

    @property
    def supported_languages(self) -> list[str]:
        return list(LANGUAGES)

    # ── loading ──────────────────────────────────────────────────────────

    def _download(self, patterns: list[str]) -> str:
        from huggingface_hub import snapshot_download

        from services.hf_revisions import revision_for

        # Pinned to the reviewed commit, never the mutable main branch.
        return _retry_once_with_fresh_hf_client(
            lambda: snapshot_download(REPO_ID, revision=revision_for(REPO_ID), allow_patterns=patterns),
            what="Kokoro",
        )

    def _ensure_loaded(self):
        if self._session is not None:
            return
        import onnxruntime as ort

        defaults = sorted({voice for voice, _ in LANGUAGES.values()})
        self._dir = self._download(required_patterns(defaults))
        with open(f"{self._dir}/{TOKENIZER_FILE}", encoding="utf-8") as f:
            self._vocab = json.load(f)["model"]["vocab"]
        options = ort.SessionOptions()
        options.log_severity_level = 3
        logger.info("Loading Kokoro from %s", REPO_ID)
        self._session = ort.InferenceSession(
            f"{self._dir}/{GRAPH_FILE}", sess_options=options, providers=["CPUExecutionProvider"]
        )

    def unload(self) -> None:
        self._session = None
        self._voices.clear()
        self._g2p.clear()

    def _voice(self, name: str):
        import numpy as np

        if name not in self._voices:
            path = f"{self._dir}/voices/{name}.bin"
            try:
                data = np.fromfile(path, dtype=np.float32)
            except FileNotFoundError:
                self._dir = self._download([f"voices/{name}.bin"])
                data = np.fromfile(f"{self._dir}/voices/{name}.bin", dtype=np.float32)
            self._voices[name] = data.reshape(-1, 1, 256)
        return self._voices[name]

    def _phonemizer(self, language: str):
        if language not in self._g2p:
            from misaki import espeak

            espeak_lang = LANGUAGES[language][1]
            if espeak_lang is None:
                from misaki import en

                g2p = en.G2P(trf=False, british=False, fallback=espeak.EspeakFallback(british=False))
                self._g2p[language] = lambda text: g2p(text)[0]
            else:
                g2p = espeak.EspeakG2P(language=espeak_lang)
                self._g2p[language] = lambda text: g2p(text)[0]
        return self._g2p[language]

    # ── synthesis ────────────────────────────────────────────────────────

    def _resolve_language(self, text: str, language: object) -> str:
        code = self._normalize_language_code(language)
        if code is None:
            return "hi" if _DEVANAGARI.search(text) else "en"
        self._check_language(code)
        return code

    def _tokens(self, phonemes: str) -> list[int]:
        return [self._vocab[ch] for ch in phonemes if ch in self._vocab]

    def _chunks(self, text: str, phonemize) -> list[list[int]]:
        """Phoneme-token chunks within the model's limit, split at sentence
        and then word boundaries."""
        chunks: list[list[int]] = []
        for sentence in filter(None, (s.strip() for s in _SENTENCE.split(text))):
            tokens = self._tokens(phonemize(sentence))
            if len(tokens) <= MAX_PHONEMES:
                if tokens:
                    chunks.append(tokens)
                continue
            words, piece = sentence.split(), []
            for word in words:
                candidate = piece + [word]
                if piece and len(self._tokens(phonemize(" ".join(candidate)))) > MAX_PHONEMES:
                    chunks.append(self._tokens(phonemize(" ".join(piece)))[:MAX_PHONEMES])
                    piece = [word]
                else:
                    piece = candidate
            if piece:
                chunks.append(self._tokens(phonemize(" ".join(piece)))[:MAX_PHONEMES])
        return [c for c in chunks if c]

    def generate(self, text: str, **kw):
        import numpy as np
        import torch

        language = self._resolve_language(text, kw.get("language"))
        self._ensure_loaded()
        voice = kw.get("voice")
        if voice not in PRESET_VOICES:
            voice = LANGUAGES[language][0]
        speed = float(kw.get("speed", 1.0))
        with self._lock:
            phonemize = self._phonemizer(language)
            chunks = self._chunks(text, phonemize)
            if not chunks:
                raise TTSInputError(
                    "Kokoro: the input contains no speakable text — send at least one word."
                )
            styles = self._voice(voice)
            outs = []
            for tokens in chunks:
                feeds = {
                    "input_ids": np.array([[0, *tokens, 0]], dtype=np.int64),
                    "style": styles[min(len(tokens), len(styles) - 1)].astype(np.float32),
                    "speed": np.array([speed], dtype=np.float32),
                }
                outs.append(self._session.run(None, feeds)[0].reshape(-1))
        return torch.from_numpy(np.concatenate(outs).astype(np.float32)).unsqueeze(0)


__all__ = ["KokoroBackend", "REPO_ID", "PRESET_VOICES", "LANGUAGES"]
