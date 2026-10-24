"""
TTS adapter interface — Phase 3.1 (ROADMAP.md).

A uniform protocol for every TTS engine. Today we ship:

    • OmniVoiceBackend — wraps the current k2-fsa/OmniVoice model. Zero
      behaviour change for existing callers.
    • VoxCPM2Backend   — thin stub that raises with a clear install hint
      until `pip install "voxcpm>=2.0.3"` is present and enabled.

Callers should use `get_active_tts_backend()` to pick the configured engine
instead of importing a specific class. The selection is controlled by the
`OMNIVOICE_TTS_BACKEND` env var (default: `"omnivoice"`).

The protocol deliberately stays narrow: `generate(...)` returns a 1-channel
tensor sampled at `sample_rate`. Streaming is left for a later pass — the
dub generator consumes whole segments today.
"""
from __future__ import annotations

import functools
import logging
import os
import re
import threading
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from contextlib import contextmanager
from typing import Literal, Optional

import torch

logger = logging.getLogger("omnivoice.tts")


# ── HF token leak mitigation (Plan 02-04, T-02-12) ─────────────────────────
#
# Token shape is ``hf_`` + 30+ alphanumeric chars per Hugging Face's own
# format. Any error / status string surfaced through the engines API gets
# scrubbed via :func:`_mask_hf_tokens` before serialization so that a
# backend whose ``is_available()`` interpolates ``HF_TOKEN`` into its
# failure message can't accidentally leak it to the frontend. Phase 1's
# ``HFTokenRedactor`` covers logging only — FastAPI response bodies do
# NOT run through the logging filter chain.
_HF_TOKEN_MASK_RE = re.compile(r"hf_[A-Za-z0-9]{30,}")
_HF_TOKEN_MASK = "hf_***REDACTED***"


def _mask_hf_tokens(value):
    """Return ``value`` with any HF-shaped token substring redacted.

    Non-string values pass through unchanged. Used inside
    :func:`list_backends` for the ``reason`` and ``last_error`` fields.
    """
    if not isinstance(value, str):
        return value
    return _HF_TOKEN_MASK_RE.sub(_HF_TOKEN_MASK, value)


def _available_hint(msg) -> Optional[str]:
    """Advisory text carried by an *available* engine's ``is_available()``
    message, or None when the message is a plain readiness echo.

    Convention (established by VoxCPM2's version-floor hint): an engine
    that is available but wants the user to know something returns
    ``(True, "ready — <advice>")``. This extracts ``<advice>`` so
    :func:`list_backends` can surface it — previously the whole message
    was dropped for available rows (``reason`` is None when ok), so
    upgrade hints never reached the UI. Plain "ready" / "ready (…)"
    messages yield None. Output is token-masked like ``reason``.
    """
    if not isinstance(msg, str):
        return None
    head, sep, advice = msg.partition(" — ")
    advice = advice.strip()
    if not sep or not advice or not head.strip().lower().startswith("ready"):
        return None
    return _mask_hf_tokens(advice)


# ── HF Hub closed-client recovery (#880) ────────────────────────────────────
#
# huggingface_hub ≥1.x shares ONE global httpx client across every download.
# If anything closes it mid-lifecycle, every later hub call — e.g. an engine's
# first-use model download inside the generate path — dies with httpx's
# "Cannot send a request, as the client has been closed". The client is
# recoverable: ``close_session()`` drops it and the next hub call builds a
# fresh one, so the correct handling is a single targeted retry, not a
# user-facing failure.


def _is_closed_client_error(e) -> bool:
    """True iff ``e`` (or anything in its __cause__/__context__ chain) is
    httpx's closed-client lifecycle error. Cycle-safe."""
    seen, stack = set(), [e]
    while stack:
        exc = stack.pop()
        if exc is None or id(exc) in seen:
            continue
        seen.add(id(exc))
        low = str(exc).lower()
        if "client has been closed" in low or "cannot send a request" in low:
            return True
        stack.append(exc.__cause__)
        stack.append(exc.__context__)
    return False


def _retry_once_with_fresh_hf_client(loader, what: str):
    """Run ``loader()`` — a model constructor that may download from the HF
    Hub on first use — retrying transient download failures.

    Two failure shapes are retried, with deliberately different budgets:

    * the httpx **closed-client** lifecycle error (#880) — retried exactly
      ONCE, after resetting the hub's shared session. It's a client-state bug,
      not a network condition: if a fresh session hits it again, repeating
      won't help, and #880 chose to surface it rather than loop.
    * any **transient download** failure ``core.failure
      .is_hf_connectivity_error`` recognises — refused/reset connections, DNS,
      timeouts, and (#1224) a truncated body ("peer closed connection without
      sending complete message body"). A multi-GB model that dies at 90% is
      the single most retry-worthy failure in the path, so this gets the full
      bounded budget. The HF cache is resumable (correctly-sized blobs are
      skipped by hash), so each retry continues rather than restarting.

    Anything unrecognised propagates untouched, where the generation error
    classifier labels it.
    """
    from core.failure import is_hf_connectivity_error

    attempts = max(1, _int_env("OMNIVOICE_MODEL_LOAD_RETRIES", 3))
    backoff = max(0.0, _float_env("OMNIVOICE_MODEL_LOAD_BACKOFF_S", 2.0))
    client_reset_used = False
    attempt = 0
    while True:
        try:
            return loader()
        except Exception as e:
            if _is_closed_client_error(e):
                if client_reset_used:
                    raise  # #880: single-shot — a second one is not transient
                client_reset_used = True
                logger.warning(
                    "%s: HF Hub httpx client was closed mid-download (%s); "
                    "retrying once with a fresh client.", what, e,
                )
                try:
                    from huggingface_hub.utils import close_session
                    close_session()
                except Exception:  # pragma: no cover — hub too old / renamed
                    logger.warning(
                        "%s: couldn't reset the HF Hub client; retrying anyway.",
                        what,
                    )
                # Deliberately does NOT consume a download attempt: the two
                # budgets are independent, and letting the session reset eat
                # one left a resumable multi-GB download a retry short of its
                # configured budget (#1224 review).
                continue  # immediate — nothing to back off from
            attempt += 1
            if not is_hf_connectivity_error(str(e)) or attempt >= attempts:
                raise
            logger.warning(
                "%s: model download failed (%s); retrying (attempt %d/%d). "
                "Already-downloaded files are reused.",
                what, e, attempt, attempts,
            )
            if backoff:
                import time as _time
                _time.sleep(backoff * attempt)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    # inf/nan parse fine and then poison the caller: `sleep(inf)` raises
    # OverflowError, turning a retryable download failure into an unrelated
    # crash that hides the original error (#1224 review).
    if value != value or value in (float("inf"), float("-inf")):
        return default
    return value


# ── Protocol ────────────────────────────────────────────────────────────────


class TTSInputError(ValueError):
    """The caller input cannot be synthesized by the selected engine
    (empty text, nothing speakable after cleanup, or missing reference audio). Subclasses ValueError so the
    native /generate route's existing ValueError→400 mapping applies;
    /v1/audio/speech maps it to 400 explicitly (#1173 class — these used to
    surface as opaque 500s like "need at least one array to concatenate")."""


def _lease_render(method):
    """Keep engines resident for every render caller, including dub and batch."""
    @functools.wraps(method)
    def render(instance, *args, **kwargs):
        with engine_in_use(instance):
            return method(instance, *args, **kwargs)
    return render


class TTSBackend(ABC):
    """Every TTS engine exposes the same surface, regardless of vendor."""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Enforce lifetime at the engine boundary so every caller is covered.
        for name in ("generate", "generate_batch", "_ensure_loaded"):
            method = cls.__dict__.get(name)
            if method is not None:
                setattr(cls, name, _lease_render(method))

    #: Unique id for config + UI (e.g. "omnivoice", "voxcpm2").
    id: str = "base"

    #: Human-readable name for the UI.
    display_name: str = "Base TTS"

    #: Output sample rate. May differ per engine (OmniVoice = 24k, VoxCPM2 = 48k).
    @property
    @abstractmethod
    def sample_rate(self) -> int: ...

    #: Languages the engine supports (ISO codes or "multi").
    @property
    @abstractmethod
    def supported_languages(self) -> list[str]: ...

    #: Whether this engine can actually run in the current environment.
    #: Callers use this to fail fast with a clear message instead of loading
    #: a backend that will blow up on first call.
    @classmethod
    @abstractmethod
    def is_available(cls) -> tuple[bool, str]:
        """Return (ok, message). message explains why not, if not."""

    #: Whether this engine supports voice design from a text description
    #: (e.g. "young female, warm tone, British accent") without reference audio.
    #: ``False`` engines need a reference clip for the timbre, so Voice Design
    #: is refused up front (UI and ``/generate``) instead of failing inside the
    #: engine. ``None`` = not declared: design stays allowed and the engine
    #: decides. Read through :func:`voice_design_support`.
    supports_voice_design: Optional[bool] = None

    #: Whether this engine understands the graded-emotion generate kwargs
    #: (``emo_vector`` / ``emo_text`` + ``use_emo_text`` / ``emo_alpha``).
    #: Surfaced via ``list_backends()`` so UI surfaces (the Audiobook expressive
    #: panel, #1208) can show emotion controls ONLY for engines that apply them
    #: — no dead controls. Default False; IndexTTS2 overrides to True. Engines
    #: that don't set it still ignore the kwargs (every generate() takes **kw),
    #: so this is a discoverability hint, not an enforcement gate.
    supports_emotion: bool = False

    #: How ``instruct`` is read (#2389): ``"tags"`` for OmniVoice's closed
    #: voice-design vocabulary, which rejects anything else, or ``"freeform"``
    #: for engines that take a written description as-is (Qwen3-TTS
    #: VoiceDesign, VoxCPM2, audio.cpp) or ignore it. Surfaced via
    #: ``list_backends()`` so clients reduce prose to tags only where the
    #: engine requires it.
    instruct_vocabulary: Literal["tags", "freeform"] = "freeform"

    def ensure_ready(self) -> None:
        """Load model weights now (blocking), so callers can separate the
        LOAD budget from the GENERATE budget (#1033/#1037 class).

        Every adapter lazily loads inside ``generate()`` via a private
        ``_ensure_loaded()`` — which meant a cold first call spent its whole
        ``OMNIVOICE_GENERATE_TIMEOUT_S`` window (default 300s) downloading /
        loading weights and got killed with a misleading "too heavy for the
        available compute" error (measured in the wild on a fresh install:
        multi-GB checkpoint download, 0% GPU util, #1014). Routes call this
        first under the model-load budget (``OMNIVOICE_MODEL_LOAD_TIMEOUT``,
        default 1200s), then start the generate clock on an already-warm
        engine. Default implementation dispatches to the adapter's own
        ``_ensure_loaded`` when present; engines without lazy state no-op.
        Must be called on the GPU pool (it's blocking), same as generate.
        """
        loader = getattr(self, "_ensure_loaded", None)
        if callable(loader):
            loader()

    # ── Language enforcement (#2104) ──────────────────────────────────────
    #
    # An engine that declares a finite language set used to receive any
    # caller-supplied language and synthesize anyway — a 30-second Polish
    # sample on an English-only engine came back as 55 s of English phonemes
    # over Polish text, with no error. The picker offers every language the
    # app supports, so the failure mode was "user picks Polish on KittentTTS,
    # gets a wrong-accent wav, has no way to know the engine can't do it".
    #
    # ``supported_languages`` is the engine's own contract. The base class
    # enforces it in one place, so engines only have to declare their set
    # honestly — no per-adapter check, no risk of drift. Engines that
    # genuinely cover every language declare ``["multi"]`` and the helper
    # is a no-op for them; engines with a strict set get the same model
    # mlx-audio / PocketTTS already use ("doesn't support language X;
    # supported: ..."), enforced centrally.

    #: Class-level allowlist of display names that name the language to the
    #: user. Keeps the user-facing error free of a brittle per-engine table
    #: and matches what the Settings picker surfaces. Set on each subclass
    #: (e.g. ``{"en": "English"}``); an empty dict means the engine falls back
    #: to the raw ISO / display token as the language picker renders it.
    language_display_names: dict[str, str] = {}

    @classmethod
    def configured_identity(cls) -> Optional[str]:
        """What a fresh instance would load now, for engines whose model is a
        construction-time preference. ``None`` means construction is not
        preference-dependent and a cached instance never goes stale."""
        return None

    def built_identity(self) -> Optional[str]:
        """The preference-dependent identity this instance was constructed with."""
        return None

    def _normalize_language_code(self, language: object) -> Optional[str]:
        """Resolve picker names and region tags without treating unknown names as Auto."""
        if language is None:
            return None
        if not isinstance(language, str):
            raise ValueError("Language must be a string or None")
        value = language.strip().lower()
        if not value or value == "auto":
            return None
        from omnivoice.utils.lang_map import LANG_NAME_TO_ID

        # Reuse the same complete, bundled mapping as the language picker.
        aliases = {"mandarin": "zh", "arabic": "ar", "tagalog": "tl"}
        if value in aliases:
            return aliases[value]
        if value in LANG_NAME_TO_ID:
            return LANG_NAME_TO_ID[value]
        head = value.replace("_", "-").split("-", 1)[0]
        if head.isascii() and head.isalpha() and len(head) in (2, 3):
            return head
        # A supplied but unrecognized language remains explicit, so a finite
        # engine rejects it instead of silently using its default language.
        return value

    def _check_language(self, language: object) -> Optional[str]:
        """Reject caller-supplied languages outside this engine's declared
        ``supported_languages`` set (#2104).

        Skipped when:
          - ``language`` is None, empty, or "auto" — the user has no
            preference and the engine picks its own default.
          - ``supported_languages`` is ``["multi"]`` — the engine
            documents itself as open-ended and routes any extra check
            through its own per-engine logic (mlx-audio's per-model table,
            PocketTTS' own strict set, OmniVoice's 600-language zero-shot).

        Raises ``ValueError`` with the engine display name, the requested
        language, and the supported set so the rewrite in
        ``_language_rejection_or`` (api/routers/generation.py:1055) turns
        it into the standard "Engine X can't speak 'Y' …" message.

        3-letter ISO 639 codes (``cmn``, ``zho``, …) are intentionally
        NOT mapped to their 2-letter equivalent here: the convention
        varies per engine (``cmn`` is Mandarin to one, undefined to
        another) and a wrong fold silently mis-routes. Engines that
        advertise a 3-letter set must override ``_check_language`` (or
        accept the 3-letter token as-is and validate themselves); the
        base class only enforces exact 2-letter matches against the
        declared set, which covers the common case.
        """
        supported = self.supported_languages
        # open-ended: the engine handles its own checks.
        if not supported or supported == ["multi"]:
            return
        code = self._normalize_language_code(language)
        if code is None:
            return  # no preference → caller leaves it to the engine
        # Region tags resolve to their base language; three-letter codes
        # remain exact rather than guessing from their first two letters.
        if code in supported:
            return code
        # Build the user-facing list. ``multi`` is not in here because
        # ``supported`` already short-circuited above; entries are rendered
        # in declared order so the message matches ``list_backends()``.
        # Look up ``display_name`` on the instance, not the class — the
        # base class declares it as a class attribute (str), but subclasses
        # override with a ``@property`` that returns the instance value.
        # ``getattr(type(self), "display_name", ...)`` returns the property
        # descriptor object itself, not the string.
        engine_name = (
            getattr(self, "display_name", None)
            or getattr(type(self), "id", type(self).__name__)
        )
        if not isinstance(engine_name, str):
            # Property with no instance state — fall back to the class attr.
            engine_name = getattr(type(self), "id", type(self).__name__)
        quoted = ", ".join(f"'{c}'" for c in supported)
        raise ValueError(
            f"The {engine_name} engine doesn't support language={language!r}. "
            f"Supported: {quoted}. Pick one of those, leave language as 'Auto', "
            f"or switch engine in Model Catalogue."
        )

    def _supported_languages_display(self) -> list[str]:
        """``supported_languages`` rendered for the UI: ISO codes annotated
        with their display name when the engine declared one, else bare.

        Used by ``list_backends()`` and any picker that wants to show
        humans-readable labels instead of raw ISO tokens.
        """
        out: list[str] = []
        for code in self.supported_languages:
            label = self.language_display_names.get(code)
            out.append(f"{code} ({label})" if label else code)
        return out

    #: Whether this engine already emits mastered, studio-grade audio and should
    #: therefore skip the shared apply_mastering() chain (highpass + Compressor,
    #: tuned for OmniVoice's 24 kHz output). Studio engines like VoxCPM2 (native
    #: 48 kHz) set this True so their clean output isn't pumped. Loudness
    #: normalisation is applied regardless — it's a benign peak scale.
    applies_own_mastering: bool = False

    #: Whether this engine can clone an arbitrary voice from reference audio
    #: (`ref_audio=`), as opposed to only offering a fixed set of preset
    #: voices. Default True — most engines clone. Dub/batch gate on this
    #: (issue #312 class) before committing to a job that needs it, instead
    #: of silently falling back to OmniVoice or mis-cloning per segment.
    supports_cloning: bool = True

    #: Longest stretch of a reference clip this engine actually conditions on,
    #: in seconds, and how it chooses that stretch from a longer clip:
    #: ``"best_window"`` (picks the passage with the most speech), ``"head"``
    #: (keeps the start), ``"full"`` (uses everything). ``None`` = not verified
    #: in-repo; the UI then makes no engine-specific claim (#2281). Surfaced via
    #: ``list_backends()`` so Voice Clone can say what a long clip turns into.
    max_ref_seconds: Optional[float] = None
    ref_strategy: Optional[str] = None

    #: Curated model keys that DO accept a reference clip, for an adapter
    #: whose ``supports_cloning`` is model-dependent (a property rather than
    #: a plain bool). Empty when cloning is a fixed fact about the engine.
    #: Lets the cloning gate name the one-setting fix — pick this model —
    #: instead of telling the user their engine can't clone at all (#2201).
    cloning_model_keys: tuple[str, ...] = ()

    @classmethod
    def cloning_model_labels(cls) -> tuple[str, ...]:
        """:attr:`cloning_model_keys` as the model picker labels them."""
        return cls.cloning_model_keys

    #: GPU/accelerator targets the engine can run on. Surfaced via the
    #: Engine Compatibility Matrix (Plan 02-04 / ENGINE-06) so users can
    #: tell at a glance which engines will use their hardware. Defaults to
    #: CPU-only — subclasses override with the union of devices their
    #: implementation supports (cuda / mps / rocm / cpu). This is metadata,
    #: not enforced — actual device selection lives in the engine's loader.
    gpu_compat: tuple[str, ...] = ("cpu",)

    #: Approximate VRAM (GB) the engine needs to render comfortably on a
    #: dedicated GPU. Metadata, like ``gpu_compat`` — never enforced, because a
    #: hard refuse would block hosts that would actually cope (drivers page to
    #: system RAM, and a short input can fit where a long one won't).
    #:
    #: What it IS for: telling the user BEFORE they wait (#1226/#1222). Two
    #: users on 4 GB cards (GTX 1650 Ti, Quadro P2000) ran the `omnivoice`
    #: engine, waited out the full compute budget, and were told the job "was
    #: too heavy for the available compute" — after the fact, with no hint
    #: that their card was under-provisioned for the engine they'd picked.
    #: Routing showed a clean green "accelerated" the whole time, because
    #: family membership was the only thing anything checked.
    #:
    #: 0 means "no meaningful floor" (CPU-class engines) and never warns.
    min_vram_gb: float = 0.0

    @classmethod
    def runtime_compute_profile(cls, caps) -> dict:
        """Resolved compute metadata for this engine on the current host.

        Most engines have one implementation whose static declarations are
        sufficient. Native adapters may override this single hook when the
        installed executable determines both the available runtimes and the
        device actually selected.
        """
        from services.engine_routing import resolve_routing

        gpu_compat = tuple(getattr(cls, "gpu_compat", ("cpu",)))
        min_vram_gb = float(getattr(cls, "min_vram_gb", 0.0) or 0.0)
        return {
            "gpu_compat": gpu_compat,
            "min_vram_gb": min_vram_gb,
            **resolve_routing(gpu_compat, caps, min_vram_gb),
            "runtime_backend": None,
            "runtime_device_index": None,
            "runtime_device_name": None,
            "runtime_hardware_family": None,
            "runtime_vram_gb": None,
            "runtime_device_verified": None,
        }

    #: True when generation allocates in ANOTHER process — a dedicated-venv
    #: sidecar (SubprocessBackend) or a spawned binary (omnivoice-gguf).
    #: Parent-process accelerator counters cannot see those allocations, so
    #: profilers/diagnostics must not attribute the parent's VRAM numbers to
    #: the engine. Duck-typed (attribute, not issubclass) for the same
    #: module-purge reason as `_is_subprocess_isolated`.
    runs_out_of_process: bool = False

    def model_identity(self) -> Optional[str]:
        """Which concrete model this backend would run, for adapter engines
        that host several very different models behind one backend id
        (mlx-audio, sherpa-onnx, cosyvoice). None means the engine id
        already names the model. Profilers and diagnostics use this to
        label results — without it, Kokoro-under-mlx and Dia-under-mlx
        rows are indistinguishable."""
        return None

    @abstractmethod
    def generate(
        self,
        text: str,
        *,
        ref_audio: Optional[str] = None,
        ref_text: Optional[str] = None,
        instruct: Optional[str] = None,
        language: Optional[str] = None,
        duration: Optional[float] = None,
        description: Optional[str] = None,
        num_step: int = 16,
        guidance_scale: float = 2.0,
        speed: float = 1.0,
        **extras,
    ) -> torch.Tensor:
        """Synthesize `text`. Returns a tensor of shape (1, n_samples).

        When `description` is provided and `ref_audio` is None, engines that
        support voice design will create a synthetic voice matching the
        description (e.g. "young female, warm, slight British accent").
        Engines that don't support this will ignore the parameter.
        """

    @_lease_render
    def generate_batch(
        self,
        texts: list[str],
        *,
        ref_audio=None,
        ref_text=None,
        instruct=None,
        language=None,
        duration=None,
        speed=1.0,
        **extras,
    ) -> list[torch.Tensor]:
        """Synthesize several utterances, preserving the single-item contract.

        Engines with a native batch forward pass override this method.  The
        default keeps every existing adapter correct while giving callers one
        stable seam and per-item keyword handling.
        """
        if not texts:
            return []

        def _item(value, index):
            return value[index] if isinstance(value, list) else value

        # Validate the whole request before producing any partial output.
        for index in range(len(texts)):
            self._check_language(_item(language, index))

        return [
            self.generate(
                text,
                ref_audio=_item(ref_audio, index),
                ref_text=_item(ref_text, index),
                instruct=_item(instruct, index),
                language=_item(language, index),
                duration=_item(duration, index),
                speed=_item(speed, index),
                **extras,
            )
            for index, text in enumerate(texts)
        ]

    # ── Lifecycle (Phase 2 will enforce per-engine overrides) ──────────────
    #
    # Today every backend lazily loads its weights on first `generate()` and
    # keeps them in VRAM for the lifetime of the process. Switching engines
    # in Settings therefore leaks the old engine's allocations until the
    # next process restart — measurable on multi-engine sessions on 8 GB
    # MPS Macs.
    #
    # `unload()` is the contract that lets the registry release an engine
    # before instantiating the next one. It is a default no-op on the ABC
    # so this commit does not break any of the 9 existing subclasses; Phase
    # 2 (engine isolation) overrides it per-engine and adds a CI gate that
    # fails when a subclass doesn't implement it.
    #
    # Contract for overriders:
    #   • Idempotent: calling unload() twice must not raise.
    #   • Synchronous: returns after VRAM is freed (or after best-effort
    #     `torch.cuda.empty_cache()` / `torch.mps.empty_cache()`).
    #   • Safe to call before the first generate(): a backend that never
    #     loaded has nothing to release.
    # Attribute(s) that hold this backend's heavy model, cleared by the default
    # unload(). Every in-process engine loads its weights lazily into one of
    # these in `_ensure_loaded()`; the next generate() re-runs that loader. An
    # engine that holds its model elsewhere (or nowhere — e.g. an external HTTP
    # server) overrides `unload()` or leaves these unset. OmniVoice overrides
    # entirely (it drives the shared model_manager singleton).
    _MODEL_ATTRS: tuple[str, ...] = ("_model", "_tts")

    def execution_evidence_loaded(self) -> bool:
        """Whether this instance has live model state worth reporting."""
        if self.runs_out_of_process:
            proc = getattr(self, "_proc", None)
            return proc is not None and proc.poll() is None
        return any(getattr(self, attr, None) is not None for attr in self._MODEL_ATTRS)

    def unload(self) -> None:
        """Release the heavy model this backend holds, and free device caches.

        Called by the registry on engine switch, by the single-active-engine
        eviction (services.engine_memory), and on app shutdown. Clears each of
        ``_MODEL_ATTRS`` that is set on this instance, then empties the device
        cache — so switching engines actually hands the memory back instead of
        leaving the old model resident until GC (the 16 GB-Mac OOM class). The
        next generate() lazily reloads. Idempotent and safe before first load:
        a backend that never loaded has every attr already None/absent.
        """
        freed = False
        for attr in self._MODEL_ATTRS:
            if getattr(self, attr, None) is not None:
                setattr(self, attr, None)
                freed = True
        if freed:
            try:
                from services.model_manager import free_vram

                free_vram()
            except Exception:  # noqa: BLE001 — unload must never raise (idempotent contract)
                pass
        return None


# ── OmniVoice adapter (the current default) ─────────────────────────────────


# ── Voice-clone prompt cache (#427) ──────────────────────────────────────────
# Every cloned generation re-encodes the reference audio from scratch — a fixed
# per-request latency that compounds on batch/long-form workloads reusing one
# voice. The OmniVoice model exposes create_voice_clone_prompt(ref) →
# VoiceClonePrompt + generate(voice_clone_prompt=) to do that encoding ONCE.
# We cache the prompt (bounded LRU, keyed by ref path + mtime + ref_text) so
# repeated generations with the same voice skip the encode. Bounded because a
# VoiceClonePrompt holds tensors (VRAM). Thread-safe (generation runs in a GPU
# thread pool). Best-effort: any miss/error falls back to the inline ref path,
# so output is never affected — this is a pure latency optimization.
_PROMPT_CACHE_MAX = 8
_prompt_cache: "OrderedDict[tuple, object]" = OrderedDict()
_prompt_cache_lock = threading.Lock()

# Disk layer under the in-memory LRU (upstream k2-fsa VoiceClonePrompt.save/
# load format). The in-memory cache dies with the process, so the first
# generation of every session re-encodes each voice (~0.4 s + an ASR pass when
# ref_text is missing). Encoded prompts are tiny (a (8, T) int token tensor +
# transcript), so we persist them and reload across restarts. Keyed by the
# same tuple as the memory cache — the ref file's mtime is inside the key, so
# an edited reference never matches a stale file; stale files age out via the
# mtime prune. Best-effort like the memory cache: any failure means "no disk
# hit / no disk write", never a failed generation. OMNIVOICE_PROMPT_DISK_CACHE=0
# disables the layer entirely.
_PROMPT_DISK_CACHE_MAX = 32


def _prompt_disk_dir():
    """Return the prompt-cache directory (created on first use), or None when
    the layer is disabled or the directory can't be created."""
    if os.environ.get("OMNIVOICE_PROMPT_DISK_CACHE", "1") == "0":
        return None
    try:
        from core.config import DATA_DIR

        path = os.path.join(str(DATA_DIR), "prompt_cache")
        os.makedirs(path, exist_ok=True)
        return path
    except Exception as e:  # noqa: BLE001 — cache layer must never break synthesis
        logger.debug("prompt disk cache unavailable: %s", e)
        return None


def _prompt_disk_path(cache_dir: str, key: tuple) -> str:
    import hashlib

    digest = hashlib.sha256(repr(key).encode("utf-8")).hexdigest()[:32]
    return os.path.join(cache_dir, f"{digest}.pt")


def _prompt_disk_load(key: tuple):
    """Load a persisted prompt for ``key``, or None. Never raises."""
    cache_dir = _prompt_disk_dir()
    if cache_dir is None:
        return None
    path = _prompt_disk_path(cache_dir, key)
    if not os.path.exists(path):
        return None
    try:
        from omnivoice.models.omnivoice import VoiceClonePrompt

        prompt = VoiceClonePrompt.load(path)
        # Freshen so the LRU prune (by mtime) keeps actively used voices.
        os.utime(path, None)
        return prompt
    except Exception as e:  # noqa: BLE001
        logger.warning("failed to load cached voice prompt %s: %s", path, e)
        try:
            os.remove(path)  # corrupt/incompatible file — don't retry it forever
        except OSError:
            pass
        return None


def _prompt_cache_evict(key: tuple) -> None:
    """Discard one prompt from both cache layers. Never raises.

    Transcript-free prompts use a different identity from fully conditioned
    prompts. Once ASR resolves the transcript, the former must not remain as a
    viable stale fallback for the same reference clip.
    """
    with _prompt_cache_lock:
        _prompt_cache.pop(key, None)
    cache_dir = _prompt_disk_dir()
    if cache_dir is None:
        return
    try:
        os.remove(_prompt_disk_path(cache_dir, key))
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.debug("could not evict stale voice prompt: %s", exc)


def _prompt_disk_save(key: tuple, prompt) -> None:
    """Persist ``prompt`` under ``key`` and prune old entries. Never raises."""
    cache_dir = _prompt_disk_dir()
    if cache_dir is None:
        return
    path = _prompt_disk_path(cache_dir, key)
    try:
        # Unique per write: two GPU-pool threads missing the same key must not
        # interleave writes into one tmp file (os.replace stays atomic).
        import uuid

        tmp = f"{path}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"
        prompt.save(tmp)
        os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001
        logger.warning("failed to persist voice prompt to %s: %s", path, e)
        return
    try:
        entries = [
            os.path.join(cache_dir, f)
            for f in os.listdir(cache_dir)
            if f.endswith(".pt")
        ]
        entries.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for old in entries[_PROMPT_DISK_CACHE_MAX:]:
            os.remove(old)
    except OSError as e:
        logger.debug("prompt disk cache prune skipped: %s", e)


def _clone_prompt_key(ref_audio: str, ref_text, preprocess_prompt: bool = True, *, passage=None):
    try:
        mtime = os.path.getmtime(ref_audio)
    except OSError:
        mtime = 0.0
    # The selected passage changes conditioning even if two windows have the
    # same transcript. Keep short-reference keys unchanged.
    key = (os.path.abspath(ref_audio), mtime, ref_text or "", bool(preprocess_prompt))
    return key if passage is None else (*key, passage)


def reference_duration_s(path) -> Optional[float]:
    """Duration of a reference clip on disk in seconds, or ``None`` if unknown.

    Same decoders the OmniVoice loader uses (libsndfile, then pydub/ffmpeg),
    so every format a reference can be saved in resolves. Memoized per file
    version: callers probe once per generate call, and the ffmpeg fallback
    decodes the whole clip.
    """
    if not isinstance(path, str) or not path:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    return _reference_duration_cached(os.path.abspath(path), st.st_mtime_ns, st.st_size)


@functools.lru_cache(maxsize=64)
def _reference_duration_cached(path: str, _mtime_ns: int, _size: int) -> Optional[float]:
    try:
        import soundfile as sf

        return float(sf.info(path).duration)
    except Exception:  # noqa: BLE001 — fall through to ffmpeg
        pass
    try:
        from core.url_safety import is_manifest_file
        from pydub import AudioSegment

        # pydub's ffmpeg call cannot take input options, so a playlist named
        # like audio is refused here rather than given to ffmpeg to follow.
        if is_manifest_file(path):
            return None
        return float(AudioSegment.from_file(path).duration_seconds)
    except Exception:  # noqa: BLE001 — unknown length: callers keep old behavior
        return None


def omnivoice_ref_text(ref_audio, ref_text):
    """The transcript OmniVoice can actually align with ``ref_audio`` (#2281).

    OmniVoice rejects a transcript paired with a clip longer than
    ``CLONE_REF_TEXT_MAX_SECONDS``. Transcripts reaching the engine layer are
    overwhelmingly machine-made: the profile save and Voice Clone both
    transcribe the whole clip automatically, and a stored profile keeps that
    transcript forever. Passing one on made every saved voice longer than 20 s
    permanently unusable on the default engine. Dropping it lets the installed
    recognizer rank 15 s windows; the model's own Whisper snapshot is only the
    fallback when that recognizer finds no words. A transcript typed on a ``/generate`` request is
    rejected there with ``[clone_ref_too_long]`` before reaching this point.
    """
    if not ref_text or not ref_text.strip():
        # "" and whitespace are no transcript: the model checks
        # ``ref_text is not None``, so pass None and let it pick the passage.
        return None
    if not isinstance(ref_audio, str):
        return ref_text
    from omnivoice.utils.audio import CLONE_REF_TEXT_MAX_SECONDS

    duration = reference_duration_s(ref_audio)
    if duration is None or duration <= CLONE_REF_TEXT_MAX_SECONDS:
        return ref_text
    logger.info(
        "reference is %.1fs (>%.0fs): ignoring its whole-clip transcript so "
        "the best 15s passage can be selected",
        duration, CLONE_REF_TEXT_MAX_SECONDS,
    )
    return None


# Which 15 s window an installed recognizer picked for a long reference.
# Keyed by the file version and the recognizer identity: a different model
# must not reuse a passage it did not choose (#2281).
_PASSAGE_CHOICE_MAX = 64
# index < 0 means the clip was ranked and no installed recognizer produced words.
_NO_PASSAGE = -1
_passage_choices: "OrderedDict[tuple, tuple[int, str]]" = OrderedDict()
# Env pins mirrored from the ASR backends. A model change must change this key
# even when the backend id stays the same (#2281).
_ASR_MODEL_PINS = {
    "whisperx": ("ASR_MODEL_WHISPERX", "large-v3"),
    "mlx-whisper": ("ASR_MODEL", "mlx-community/whisper-large-v3-mlx"),
    "parakeet-mlx": ("ASR_MODEL_PARAKEET_MLX", "mlx-community/parakeet-tdt-0.6b-v3"),
    "nemo-parakeet": ("ASR_MODEL_NEMO", "nvidia/parakeet-tdt-0.6b-v3"),
    "moonshine": ("ASR_MODEL_MOONSHINE", "moonshine/base"),
}


def _recognizer_label(ab, backend: str) -> str:
    """``backend:model`` for one configured recognizer, or "" when unnamed."""
    if backend == "faster-whisper":
        return "faster-whisper:" + ab.faster_whisper_model_id()
    if backend == "faster-whisper-isolated":
        pinned = os.environ.get("ASR_MODEL_FW") or ab.faster_whisper_model_id()
        return "faster-whisper-isolated:" + pinned
    if backend == "sherpa-onnx-asr":
        return "sherpa-onnx-asr:" + ab.sherpa_engine_model_id()
    if backend == "openai-compat-asr":
        return "openai-compat-asr:" + ab.resolve_openai_compat_asr_model()
    if backend == "funasr":
        return "funasr:{model}:{vad}:{spk}".format(
            model=os.environ.get("ASR_MODEL_FUNASR", "iic/SenseVoiceSmall"),
            vad=os.environ.get("ASR_FUNASR_VAD", "fsmn-vad"),
            spk=os.environ.get("ASR_FUNASR_SPK", "cam++"),
        )
    pin = _ASR_MODEL_PINS.get(backend)
    if pin is None:
        return ""
    return backend + ":" + os.environ.get(pin[0], pin[1])


def _capture_recognizer_label(ab) -> str:
    """The dictation recognizer ``transcribe_reference`` may try second."""
    sid = ab.dictation_model_id()
    if sid:
        ok, _reason = ab.SherpaDictationBackend.is_available()
        if ok:
            from services import sherpa_dictation as sd

            spec = sd.get_spec(sid)
            if spec is not None and not sd.is_demoted(spec.id):
                return "sherpa-onnx-asr:" + spec.id
    if ab._capture_prefers_parakeet():
        model = os.environ.get(
            "ASR_MODEL_PARAKEET_MLX", "mlx-community/parakeet-tdt-0.6b-v3",
        )
        return "parakeet-mlx:" + model
    ok, _reason = ab.MLXWhisperBackend.is_available()
    if ok:
        return "mlx-whisper:" + ab._MLX_MODEL_TURBO
    ok, _reason = ab.FasterWhisperBackend.is_available()
    if ok:
        return "faster-whisper:" + ab.faster_whisper_model_id()
    model = os.environ.get(
        "OMNIVOICE_PYTORCH_ASR_MODEL", "openai/whisper-large-v3-turbo",
    )
    return "pytorch-whisper:" + model


def _fallback_recognizer_labels(ab, selected: list) -> Optional[list]:
    """Installed fallbacks ``transcribe_reference`` tries after the selection.

    ``None`` means the set could not be named. Callers then skip the passage
    cache instead of reusing a window some other recognizer chose.
    """
    selected_fw = set()
    selected_sherpa = set()
    for part in selected:
        kind, _, rest = part.partition(":")
        if kind == "faster-whisper" and rest:
            selected_fw.add(ab._fw_repo(rest) or rest)
        elif kind == "sherpa-onnx-asr" and rest:
            selected_sherpa.add(rest)
    labels = []
    try:
        from api.routers.setup.models import (
            KNOWN_MODELS, _model_supported, _snapshot_dirs, snapshot_is_complete,
        )

        available, _reason = ab.FasterWhisperBackend.is_available()
        if available:
            compatible = sorted(
                (
                    model for model in KNOWN_MODELS
                    if str(model.get("role", "")).lower() == "asr"
                    and not model.get("dictation_id")
                    and (
                        str(model.get("repo_id", "")).startswith("Systran/faster-")
                        or model.get("repo_id")
                        == "deepdml/faster-whisper-large-v3-turbo-ct2"
                    )
                    and _model_supported(model)
                    and model.get("repo_id") not in selected_fw
                ),
                key=lambda model: float(model.get("size_gb") or 0),
                reverse=True,
            )
            for model in compatible:
                snapshots = [
                    path for path in _snapshot_dirs(str(model["repo_id"]))
                    if snapshot_is_complete(model, path)
                ]
                if snapshots:
                    labels.append("faster-whisper:" + str(model["repo_id"]))
                    break
    except Exception:
        logger.debug("reference ASR fallback identity unavailable")
        return None
    try:
        from services import sherpa_dictation

        installed = sorted(
            (
                spec for spec in sherpa_dictation.list_specs()
                if spec.id not in selected_sherpa
                and sherpa_dictation.is_installed(spec)
            ),
            key=lambda spec: float(spec.size_gb or 0),
            reverse=True,
        )
        if installed:
            labels.append("sherpa-onnx-asr:" + installed[0].id)
    except Exception:
        logger.debug("reference dictation fallback identity unavailable")
        return None
    return labels


def _reference_asr_identity() -> str:
    """Recognizers ``transcribe_reference`` would try, or "" when unnamed.

    An empty result must not be used as a cache key: a later recognizer would
    reuse a passage it did not choose. ``none`` is a real empty chain, so a
    clip with no installed speech model is not ranked again until one appears.
    """
    try:
        from services import asr_backend as ab

        parts = []
        if ab.asr_model_missing_error() is None:
            backend = ab.active_backend_id()
            # transcribe_reference skips the PyTorch pipeline on purpose.
            if backend != "pytorch-whisper":
                label = _recognizer_label(ab, backend)
                if not label:
                    return ""
                parts.append(label)
        if ab.asr_model_missing_error(purpose="dictation") is None:
            capture = _capture_recognizer_label(ab)
            if not capture:
                return ""
            if capture not in parts:
                parts.append(capture)
        fallbacks = _fallback_recognizer_labels(ab, parts)
        if fallbacks is None:
            return ""
        parts.extend(fallbacks)
        return "|".join(parts) if parts else "none"
    except Exception:
        logger.debug("reference ASR identity unavailable")
        return ""


def _passage_choice_key(ref_audio: str) -> tuple:
    try:
        st = os.stat(ref_audio)
        version = (st.st_mtime_ns, st.st_size)
    except OSError:
        version = (0, 0)
    return (os.path.abspath(ref_audio), version, _reference_asr_identity())


def _recall_passage(ref_audio: str) -> Optional[tuple[int, str]]:
    if not _reference_asr_identity():
        return None
    key = _passage_choice_key(ref_audio)
    with _prompt_cache_lock:
        hit = _passage_choices.get(key)
        if hit is None:
            return None
        _passage_choices.move_to_end(key)
        return hit


def _remember_passage(ref_audio: str, index: int, text: str) -> None:
    if not _reference_asr_identity():
        return
    key = _passage_choice_key(ref_audio)
    with _prompt_cache_lock:
        _passage_choices[key] = (index, text)
        _passage_choices.move_to_end(key)
        while len(_passage_choices) > _PASSAGE_CHOICE_MAX:
            _passage_choices.popitem(last=False)


def _read_reference_mono(path: str):
    """Float32 mono samples and sample rate, or None.

    libsndfile first, then pydub/ffmpeg. Same pair as ``reference_duration_s``
    and OmniVoice's loader, so an M4A or AAC reference can still be windowed.
    """
    audio = None
    sr = 0
    try:
        import soundfile as sf

        audio, sr = sf.read(path, dtype="float32", always_2d=False)
        sr = int(sr)
    except Exception:
        audio = None
    if audio is None:
        try:
            import numpy as np
            from core.url_safety import is_manifest_file
            from pydub import AudioSegment

            if is_manifest_file(path):  # never hand a playlist to ffmpeg
                return None
            segment = AudioSegment.from_file(path)
            sr = int(segment.frame_rate)
            samples = np.array(segment.get_array_of_samples(), dtype=np.float32)
            if segment.sample_width:
                samples /= float(1 << (8 * segment.sample_width - 1))
            if segment.channels > 1:
                samples = samples.reshape(-1, segment.channels).mean(axis=1)
            audio = samples
        except Exception:
            logger.debug("long-reference decode failed")
            return None
    if getattr(audio, "ndim", 1) > 1:
        audio = audio.mean(axis=1)
    if sr <= 0 or len(audio) == 0:
        return None
    return audio, sr


def _omnivoice_installed_passage(ref_audio: str) -> Optional[tuple[str, str]]:
    """Pick a long clip's best 15 s window with the installed recognizer.

    OmniVoice cannot align a transcript to more than 20 s, so a longer clip
    is cloned from one 15 s window. The model's own Whisper snapshot is a
    last resort and is never downloaded; this uses the speech-to-text model
    already selected in Model Catalogue (#2281). Returns ``(wav_path,
    transcript)`` or None when no installed recognizer produced words. The
    caller deletes ``wav_path``. A decoded clip with no spoken words is
    remembered so the next chunk does not rank it again.
    """
    from omnivoice.utils.audio import CLONE_REF_MAX_WINDOWS, CLONE_REF_WINDOW_SECONDS

    loaded = _read_reference_mono(ref_audio)
    if loaded is None:
        return None
    audio, sr = loaded
    window = int(CLONE_REF_WINDOW_SECONDS * sr)
    if window <= 0 or len(audio) <= window:
        return None
    if len(audio) > window * CLONE_REF_MAX_WINDOWS:
        _remember_passage(ref_audio, _NO_PASSAGE, "")
        return None
    try:
        import soundfile as sf
        from services.asr_backend import transcribe_reference
    except Exception:
        logger.debug("installed reference ASR import failed")
        return None

    import tempfile

    best_score = -1
    best_activity = -1.0
    best_path: Optional[str] = None
    best_text = ""
    best_index = 0
    n_windows = min(CLONE_REF_MAX_WINDOWS, (len(audio) + window - 1) // window)
    for index in range(n_windows):
        chunk = audio[index * window:(index + 1) * window]
        if len(chunk) == 0:
            continue
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            sf.write(path, chunk, sr)
            text = (transcribe_reference(path) or "").strip()
        except Exception:
            logger.warning("window transcription failed")
            text = ""
        score = _speech_score(text)
        activity = float((chunk.astype("float64") ** 2).sum()) if score else 0.0
        if score > 0 and (
            score > best_score or (score == best_score and activity > best_activity)
        ):
            if best_path is not None:
                try:
                    os.remove(best_path)
                except OSError:
                    pass
            best_score = score
            best_activity = activity
            best_path = path
            best_text = text
            best_index = index
        else:
            try:
                os.remove(path)
            except OSError:
                pass
    if best_path is None:
        _remember_passage(ref_audio, _NO_PASSAGE, "")
        return None
    _remember_passage(ref_audio, best_index, best_text)
    return best_path, best_text


def _materialize_window(ref_audio: str, index: int) -> Optional[str]:
    """Write one previously chosen 15 s window. The caller deletes the file."""
    from omnivoice.utils.audio import CLONE_REF_WINDOW_SECONDS

    loaded = _read_reference_mono(ref_audio)
    if loaded is None or index < 0:
        return None
    audio, sr = loaded
    window = int(CLONE_REF_WINDOW_SECONDS * sr)
    if window <= 0:
        return None
    chunk = audio[index * window:(index + 1) * window]
    if len(chunk) == 0:
        return None
    import soundfile as sf
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        sf.write(path, chunk, sr)
    except Exception:
        logger.debug("long-reference window write failed")
        try:
            os.remove(path)
        except OSError:
            pass
        return None
    return path


def _reuse_or_rank_passage(ref_audio: str) -> Optional[tuple[str, str]]:
    """Return the cached 15 s window, or rank one and remember the result.

    A remembered miss (no spoken words, or a clip past five windows) is not
    ranked again. The caller deletes the wav path.
    """
    if _reference_asr_identity():
        recalled = _recall_passage(ref_audio)
        if recalled is not None:
            if recalled[0] < 0:
                return None
            path = _materialize_window(ref_audio, recalled[0])
            if path is not None:
                return path, recalled[1]
    return _omnivoice_installed_passage(ref_audio)


def prepare_reference_passage(ref_audio) -> bool:
    """Rank a long reference's best 15 s window NOW, off the generate clock.

    Picking the window runs the installed recognizer over up to five 15 s
    windows. On a CPU that is minutes, and when it ran inside the engine call
    it was charged to the generate budget: the job was then abandoned with
    "TTS generate exceeded 300s" before a single sample was synthesized
    (#2764's log shows a CPU Whisper large-v3 transcribing the voice just
    before). The choice is remembered per recognizer and file version
    (``_recall_passage``), so the engine call that follows recalls it for free.

    Returns True when a passage was chosen or remembered. Never raises, and a
    clip that needs no window (<= the transcript limit) is a no-op.
    """
    if not isinstance(ref_audio, str):
        return False
    try:
        from omnivoice.utils.audio import CLONE_REF_TEXT_MAX_SECONDS

        duration = reference_duration_s(ref_audio)
        if duration is None or duration <= CLONE_REF_TEXT_MAX_SECONDS:
            return False
        selected = _reuse_or_rank_passage(ref_audio)
    except Exception:  # noqa: BLE001 — the engine ranks inline as before
        logger.debug("reference passage pre-ranking failed", exc_info=True)
        return False
    if selected is None:
        return False
    try:
        os.remove(selected[0])
    except OSError:
        logger.debug("failed to remove reference window")
    return True


def omnivoice_inline_reference(ref_audio, ref_text):
    """``(ref_audio, ref_text, owned_path)`` for a direct ``model.generate()``.

    Paths that bypass the prompt cache — the sidecar request and the inline
    retry after a failed precompute — must condition on the same installed-
    recognizer passage as the cache. Passing the whole long clip with no
    transcript instead reached OmniVoice's bundled Whisper and failed with
    "needs an installed speech-to-text model" although one was installed
    (#2579). ``owned_path`` is a temporary window the caller deletes.
    """
    if isinstance(ref_audio, str):
        from omnivoice.utils.audio import CLONE_REF_TEXT_MAX_SECONDS

        duration = reference_duration_s(ref_audio)
        if duration is not None and duration > CLONE_REF_TEXT_MAX_SECONDS:
            selected = _reuse_or_rank_passage(ref_audio)
            if selected is not None:
                return selected[0], selected[1], selected[0]
    return ref_audio, omnivoice_ref_text(ref_audio, ref_text), None


def _speech_score(text: str) -> int:
    """Spoken-character count, matching OmniVoice's window ranking."""
    return len(re.sub(r"[^\w]+", "", text or "", flags=re.UNICODE))


def _get_clone_prompt(
    model, ref_audio: str, ref_text, preprocess_prompt: bool = True, *,
    store: bool = True,
):
    """Return a cached/precomputed ``VoiceClonePrompt`` for
    (ref_audio, ref_text, preprocess_prompt), or ``None`` to fall back to the
    inline ref path.

    Raises only on a device OOM that survives a cache-drop retry (#1790): the
    inline path is the same allocation on the same device, so falling back to
    it after an OOM cannot succeed and has been observed taking the whole
    process down instead. Every other failure still falls back silently.

    ``store=False`` still *reads* the cache (a hit is free) but never inserts:
    it exists for single-use references — a dub's per-segment ref clips are each
    a distinct file used exactly once, and inserting a stream of them into an
    LRU of 8 evicts the per-speaker and locked-profile prompts that ARE reused.
    Every short segment falling back to its speaker ref then re-encodes it
    (~0.4 s each, measured). Scan-resistance, not a second cache policy.
    """
    # Resolve transcript-free references through an already-installed ASR
    # before deriving the cache key. This protects every native OmniVoice
    # caller (generate, streaming, batch, dub, audiobook and OpenAI-compatible
    # speech), including routes that do not have a profile row on which to
    # persist the transcript. Incomplete reference conditioning can destabilize
    # the reference/target boundary and introduce words in the generated prefix.
    unresolved_key = None
    from omnivoice.utils.audio import CLONE_REF_TEXT_MAX_SECONDS

    duration = reference_duration_s(ref_audio)
    passage_file = None
    passage_context = None
    recalled_index = None
    cacheable = True
    try:
        if duration is not None and duration > CLONE_REF_TEXT_MAX_SECONDS:
            # A recalled passage has enough information to check both caches
            # before decoding the full recording and writing another WAV.
            ref_text = omnivoice_ref_text(ref_audio, ref_text)
            identity = _reference_asr_identity()
            recalled = _recall_passage(ref_audio) if identity else None
            if recalled is not None and recalled[0] >= 0:
                recalled_index, ref_text = recalled
            elif recalled is None:
                selected = _reuse_or_rank_passage(ref_audio)
                if selected is not None:
                    passage_file, ref_text = selected
                    recalled = _recall_passage(ref_audio) if identity else None
                    if recalled is not None:
                        recalled_index = recalled[0]
            if not identity:
                # The selected recognizer is unknown; a reusable prompt could
                # belong to another window with exactly the same transcript.
                cacheable = False
            passage_context = (identity, recalled_index)
        elif ref_audio and not ref_text:
            try:
                unresolved_key = _clone_prompt_key(
                    ref_audio, None, preprocess_prompt
                )
            except Exception:
                pass
            try:
                from services.asr_backend import transcribe_reference

                ref_text = transcribe_reference(ref_audio)
            except Exception as e:  # noqa: BLE001 — model fallback remains available
                logger.warning("reference transcript resolution failed: %s", e)
            if ref_text and unresolved_key is not None:
                _prompt_cache_evict(unresolved_key)

        try:
            key = _clone_prompt_key(
                ref_audio, ref_text, preprocess_prompt, passage=passage_context
            )
        except Exception:
            return None
        if cacheable:
            with _prompt_cache_lock:
                hit = _prompt_cache.get(key)
                if hit is not None:
                    _prompt_cache.move_to_end(key)
                    return hit
        # A recalled passage is materialized only when neither cache hits.
        prompt = _prompt_disk_load(key) if cacheable else None
        if prompt is None and recalled_index is not None:
            passage_file = _materialize_window(ref_audio, recalled_index)
        encode_audio = ref_audio
        encode_text = ref_text
        if passage_file is not None:
            encode_audio = passage_file
        if prompt is None:
            try:
                # Encode outside the lock (slow). Mirrors exactly what generate()
                # would do inline for this ref (omnivoice.py:964-978), so output is
                # identical.
                prompt = model.create_voice_clone_prompt(
                    encode_audio, ref_text=encode_text, preprocess_prompt=preprocess_prompt
                )
            except Exception as e:  # noqa: BLE001 — fall back, never break synthesis
                # #1790/#1777: a GPU OOM is the one failure this fallback cannot
                # absorb. `generate()`'s inline ref path runs the SAME encode on the
                # SAME device — the docstring above says so, because producing
                # identical output is the point — so returning None after an OOM
                # guarantees a second OOM moments later, on a device with even less
                # headroom than the first attempt found. Both reporters' backends
                # then died with a Windows access violation (exit code
                # -1073741819) seconds after this exact log line, mid-generation on
                # a GPU that had just refused an 86 MiB allocation.
                #
                # An OOM here is also the most recoverable kind: the allocator is
                # typically holding reserved-but-unallocated blocks (#1790's own
                # log reports 90 MiB reserved against an 86 MiB request). Drop them
                # and try once more. If it still will not fit, raise — the failure
                # layer turns a device OOM into the actionable GPU_OOM message
                # ("close other GPU-heavy apps or unload models…"), which is a far
                # better answer than walking into a native fault.
                from core.failure import is_gpu_oom

                if is_gpu_oom(e):
                    logger.warning(
                        "voice-clone prompt precompute hit a device OOM (%s) — "
                        "releasing allocator caches and retrying once", e,
                    )
                    try:
                        from services.model_manager import free_vram
                        free_vram()
                    except Exception:  # noqa: BLE001 — reclaim is best-effort
                        logger.debug("VRAM reclaim before OOM retry failed", exc_info=True)
                    prompt = model.create_voice_clone_prompt(
                        encode_audio, ref_text=encode_text, preprocess_prompt=preprocess_prompt
                    )
                else:
                    logger.warning(
                        "voice-clone prompt precompute failed; using inline ref: %s", e
                    )
                    return None
            if store and cacheable:
                _prompt_disk_save(key, prompt)
        if not store or not cacheable:
            return prompt
        with _prompt_cache_lock:
            _prompt_cache[key] = prompt
            _prompt_cache.move_to_end(key)
            while len(_prompt_cache) > _PROMPT_CACHE_MAX:
                _prompt_cache.popitem(last=False)
        return prompt
    finally:
        if passage_file is not None:
            try:
                os.remove(passage_file)
            except OSError:
                logger.debug(
                    "failed to remove reference window %s",
                    os.path.basename(passage_file),
                )


def generate_with_cached_ref(model, *, ref_audio, ref_text, **gen_kw):
    """``model.generate()`` with the reference clip encoded once, not once per call.

    The native (non-adapter) callers of the OmniVoice model — ``/generate`` and its
    streaming twin, and the audiobook/long-form renderer — used to pass
    ``ref_audio=<path>`` straight through, so the codec encoder re-ran the reference
    on **every generate call**: once per chunk, per pause-span, and per audiobook
    segment, not merely once per request. The prompt cache below (#427/#473) existed
    the whole time but only ``OmniVoiceBackend`` (the adapter path) ever called it,
    and the default engine doesn't take that path.

    This is the one place that knows the rule, so it can't be re-broken piecemeal:
    ``voice_clone_prompt`` and ``ref_audio``/``ref_text`` are **mutually exclusive** —
    pass both and the model warns and ignores the latter (omnivoice.py:957).

    The cache is **best-effort, never load-bearing**: if the prompt can't be built,
    or the model rejects the one we built, we fall back to the inline reference and
    synthesize exactly as before. A latency optimization must never be able to turn
    a generation that would have succeeded into an error.
    """
    # cache_ref=False marks a single-use reference (a dub's per-segment clips):
    # look the cache up, but never insert — see _get_clone_prompt(store=). MUST
    # be popped: the model's generate() has an explicit signature and would
    # TypeError on an unknown kwarg.
    from services.model_manager import tts_inference

    # tts_inference: no device move of the shared model while this runs, and
    # it is restored first if an offload left it in RAM (#2618).
    with engine_in_use(OmniVoiceBackend(model=model)), tts_inference():
        cache_ref = bool(gen_kw.pop("cache_ref", True))
        # Stays in gen_kw too: the model needs it on the inline branch, and it is inert
        # on the prompt branch (that prompt is already encoded).
        preprocess_prompt = bool(gen_kw.get("preprocess_prompt", True))
        prompt = (
            _get_clone_prompt(model, ref_audio, ref_text, preprocess_prompt, store=cache_ref)
            if ref_audio else None
        )
        if prompt is not None:
            try:
                return model.generate(voice_clone_prompt=prompt, **gen_kw)
            except Exception as e:  # noqa: BLE001 — fall back to the inline ref
                logger.warning("voice_clone_prompt generate failed; retrying inline ref: %s", e)
        inline_audio, inline_text, passage = omnivoice_inline_reference(ref_audio, ref_text)
        try:
            return model.generate(ref_audio=inline_audio, ref_text=inline_text, **gen_kw)
        finally:
            if passage is not None:
                try:
                    os.remove(passage)
                except OSError:
                    logger.debug("failed to remove reference window")


def clear_clone_prompt_cache() -> None:
    """Drop all cached voice-clone prompts (frees their tensors). Called on model
    unload so a flush/engine-switch doesn't strand VRAM."""
    with _prompt_cache_lock:
        _prompt_cache.clear()
        _passage_choices.clear()


# NB: model_manager.release_tts_side_caches() calls clear_clone_prompt_cache()
# above whenever it drops the TTS model — the prompts belong to that model
# instance and an "unload" that leaves them behind isn't an unload (#1119). It
# reaches this module through sys.modules rather than importing it, so there is
# no import cycle and no import-time side effect here.


class OmniVoiceBackend(TTSBackend):
    """Wraps `omnivoice.models.omnivoice.OmniVoice`. Zero behaviour change.

    Loads lazily on the first `generate` call, mirrors the existing
    `services.model_manager.get_model()` flow: torch.compile on CUDA,
    fp16, ASR co-loaded.
    """

    id = "omnivoice"
    display_name = "OmniVoice (k2-fsa/OmniVoice, 600+ languages, non-commercial)"
    instruct_vocabulary = "tags"
    supports_voice_design = True
    gpu_compat = ("cuda", "rocm", "mps", "cpu")
    # Derived from the pool's own per-job budget (_GPU_VRAM_PER_JOB_GB = 5.0 in
    # model_manager, itself measured from the ~1.6 GB forward + autoregressive
    # decode and the co-loaded WhisperX on the clone path), plus room for the
    # resident weights. Below this the driver pages to system RAM and a render
    # that should take seconds runs for minutes — which is precisely what the
    # 4 GB reporters in #1226/#1222 hit. Deliberately the only engine with a
    # floor: the rest have no measured figure, and inventing one would put a
    # confident number in the UI that nothing backs.
    min_vram_gb = 6.0
    # omnivoice.utils.audio.CLONE_REF_TEXT_MAX_SECONDS: longer clips are cut to
    # the 15 s passage with the most speech (create_voice_clone_prompt).
    max_ref_seconds = 20.0
    ref_strategy = "best_window"

    def __init__(self, model=None):
        # The live OmniVoice instance. Reuses the singleton owned by
        # model_manager so memory isn't doubled.
        self._model = model

    @property
    def execution_device(self) -> str | None:
        """Actual device of the shared model, for live engine diagnostics."""
        if self._model is None:
            return None
        try:
            return str(next(self._model.parameters()).device)
        except Exception:  # noqa: BLE001 - third-party model wrappers vary
            device = getattr(self._model, "device", None)
            return str(device) if device is not None else None

    @property
    def dtype(self) -> str | None:
        """Actual parameter precision of the shared model when resident."""
        if self._model is None:
            return None
        try:
            return str(next(self._model.parameters()).dtype)
        except Exception:  # noqa: BLE001 - diagnostics must remain best effort
            return None

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        try:
            import omnivoice.models.omnivoice  # noqa: F401
            return True, "ready"
        except Exception as e:
            return False, f"omnivoice package missing: {e}"

    _DEFAULT_SAMPLE_RATE = 24000

    @property
    def sample_rate(self) -> int:
        if self._model is None:
            return self._DEFAULT_SAMPLE_RATE  # canonical OmniVoice rate
        return getattr(self._model, "sampling_rate", 24000)

    @property
    def supported_languages(self) -> list[str]:
        # OmniVoice advertises 600+ zero-shot — `"multi"` is the honest tag.
        return ["multi"]

    def _ensure_loaded(self):
        if self._model is not None:
            # The cached instance skips get_model(), and with it the placement
            # heal: put the shared model back on its device if the opt-in
            # post-generation offload (#2618) or an unbalanced ASR offload
            # (#1191) left it in RAM. One parameter probe when it is in place.
            from services.model_manager import ensure_tts_on_device

            ensure_tts_on_device()
            return
        # Reuse model_manager's cached instance so we don't double-load.
        from services.model_manager import get_model
        import asyncio
        # Caller is sync; spin up a fresh loop if needed. get_running_loop()
        # raises only when *no* loop is running — that's the safe path where
        # we can bootstrap with asyncio.run().
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._model = asyncio.run(get_model())
            return
        raise RuntimeError(
            "OmniVoiceBackend.generate() called inside an async context without a pre-loaded model. "
            "Pass `model=await get_model()` to the constructor."
        )

    def generate(self, text, **kw) -> torch.Tensor:
        self._ensure_loaded()
        language = kw.get("language")
        ref_audio = kw.get("ref_audio")
        ref_text = kw.get("ref_text")
        gen_kw = dict(
            text=text,
            language=language if language and language != "Auto" else None,
            instruct=kw.get("instruct"),
            duration=kw.get("duration"),
            num_step=kw.get("num_step", 16),
            guidance_scale=kw.get("guidance_scale", 2.0),
            speed=kw.get("speed", 1.0),
            denoise=kw.get("denoise", True),
            postprocess_output=kw.get("postprocess_output", True),
        )
        # /v1/audio/speech exposes preprocess_prompt (openai_compat.py) and it
        # used to be dropped on the floor here — the API accepted it and gen_kw
        # never carried it, so it silently did nothing.
        gen_kw["preprocess_prompt"] = bool(kw.get("preprocess_prompt", True))
        # Single-use reference hint (dub per-segment clips) — see
        # generate_with_cached_ref, which pops it before the model sees it.
        gen_kw["cache_ref"] = bool(kw.get("cache_ref", True))
        # The cached-reference path lives in generate_with_cached_ref, shared with
        # the native callers. Deliberately NOT a second copy: this logic living in
        # one place here and a subtly different one there is exactly how the cache
        # came to be wired into the adapter and nowhere else.
        audios = generate_with_cached_ref(
            self._model, ref_audio=ref_audio, ref_text=ref_text, **gen_kw
        )
        return audios[0]

    def generate_batch(self, texts: list[str], **kw) -> list[torch.Tensor]:
        """Use OmniVoice's native variable-length batch generation.

        Batch callers pass per-item language, duration, speed and reference
        lists.  Reusable clone prompts are prepared once and handed to the
        model together; an incomplete prompt batch falls back to the proven
        single-item path instead of changing synthesis semantics.
        """
        self._ensure_loaded()
        if not texts:
            return []
        from services.model_manager import tts_inference

        # Holds the shared model in place for prompt encoding + the batch
        # generate (an offload can't move it mid-batch; #2618).
        with tts_inference():
            return self._generate_batch_on_model(texts, **kw)

    def _generate_batch_on_model(self, texts: list[str], **kw) -> list[torch.Tensor]:
        def _items(value):
            if isinstance(value, list):
                return value
            return [value] * len(texts)

        def _item_kwargs(index):
            return {
                key: value[index] if isinstance(value, list) else value
                for key, value in kw.items()
            }

        ref_audios = _items(kw.get("ref_audio"))
        ref_texts = _items(kw.get("ref_text"))
        cache_ref = bool(kw.get("cache_ref", True))
        preprocess_prompt = bool(kw.get("preprocess_prompt", True))
        prompts = []
        if any(ref_audios):
            for ref_audio, ref_text in zip(ref_audios, ref_texts):
                if not ref_audio:
                    prompts = []
                    break
                prompt = _get_clone_prompt(
                    self._model,
                    ref_audio,
                    ref_text,
                    preprocess_prompt,
                    store=cache_ref,
                )
                if prompt is None:
                    prompts = []
                    break
                prompts.append(prompt)

        if any(ref_audios) and len(prompts) != len(texts):
            return [self.generate(text, **_item_kwargs(i))
                    for i, text in enumerate(texts)]

        gen_kw = dict(
            language=kw.get("language"),
            instruct=kw.get("instruct"),
            duration=kw.get("duration"),
            speed=kw.get("speed", 1.0),
            denoise=kw.get("denoise", True),
            postprocess_output=kw.get("postprocess_output", True),
            num_step=kw.get("num_step", 16),
            guidance_scale=kw.get("guidance_scale", 2.0),
            preprocess_prompt=preprocess_prompt,
        )
        if prompts:
            gen_kw["voice_clone_prompt"] = prompts
        else:
            gen_kw["ref_audio"] = None
            gen_kw["ref_text"] = None
        return self._model.generate(text=texts, **gen_kw)

    def unload(self) -> None:
        """Release the OmniVoice model (MM2-02). OmniVoice shares the singleton
        owned by ``model_manager``, so dropping our local ref isn't enough — we
        clear the shared one and free GPU memory too. Idempotent and safe before
        the first generate(). Best-effort: assignment is GIL-atomic, so we don't
        take the async ``_model_lock`` from this sync path; the registry wraps
        this call in try/except so a race can never block an engine switch.

        Delegates to ``model_manager.unload_shared_model`` rather than clearing
        the singleton here: this path used to free the device caches *before*
        dropping the shared reference, which frees nothing, and it is the path
        used by the idle engine sweep (#1495)."""
        self._model = None
        clear_clone_prompt_cache()  # #427: drop cached prompts so VRAM is freed
        try:
            import services.model_manager as mm
            mm.unload_shared_model()
        except Exception as exc:
            # The reference is already gone by the time anything in here can
            # raise — only the device-cache flush is left, and that failing is
            # a driver problem, not a stuck model. Saying "retry" would send
            # the user to repeat an unload that already happened.
            logger.warning("Shared voice model released, but the device cache flush failed")
            raise RuntimeError(
                "The voice model was released, but the GPU memory cache could not be flushed."
            ) from exc


# ── VoxCPM2 adapter (optional, scaffolded) ──────────────────────────────────

#: Minimum recommended `voxcpm` package version. 2.0.3 fixed an audio-quality
#: bug on Apple Silicon (low-precision dtypes on the MPS device produced
#: degraded output). A floor, NOT a pin: newer versions are fine, and an
#: already-installed older version keeps working — we only surface an upgrade
#: hint (is_available reason + load-time warning), never force a reinstall.
_VOXCPM_MIN_VERSION = "2.0.3"

#: Reference-clip cap for VoxCPM2 cloning (seconds). The `voxcpm` package no
#: longer trims reference audio internally, so an unbounded user clip would
#: condition the model on minutes of audio (slow, and past a point it stops
#: helping voice similarity). 30 s is a conservative upper bound.
_VOXCPM_REF_MAX_S = 30.0

#: Silence pad kept around the voiced region when trimming a reference clip —
#: a hard cut exactly at the first/last voiced sample clips consonant onsets.
_VOXCPM_REF_EDGE_PAD_S = 0.05


def _version_tuple(v: str) -> Optional[tuple[int, ...]]:
    """Parse the leading numeric components of a version string ("2.0.3" →
    (2, 0, 3), "2.1rc1" → (2, 1)). Returns None when nothing numeric parses —
    callers treat that as 'unknown, assume fine' rather than failing."""
    parts: list[int] = []
    for piece in v.split("."):
        digits = ""
        for ch in piece:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) if parts else None


def _voxcpm_installed_version() -> Optional[str]:
    """Installed `voxcpm` dist version, or None when undeterminable
    (not installed, or importable without package metadata)."""
    try:
        from importlib.metadata import version
        return version("voxcpm")
    except Exception:
        return None


def _voxcpm_upgrade_hint() -> Optional[str]:
    """Actionable upgrade hint when the installed `voxcpm` is older than
    :data:`_VOXCPM_MIN_VERSION`, else None. Never raises; an unparseable or
    unknown version yields None (don't nag users we can't be sure about)."""
    installed = _voxcpm_installed_version()
    if installed is None:
        return None
    have = _version_tuple(installed)
    want = _version_tuple(_VOXCPM_MIN_VERSION)
    if have is None or want is None or have >= want:
        return None
    return (
        f"installed voxcpm {installed} is older than {_VOXCPM_MIN_VERSION}, "
        "which fixed an audio-quality bug on Apple Silicon (low-precision "
        "dtypes on MPS). The engine still works, but upgrading is "
        'recommended: pip install --upgrade "voxcpm>=2.0.3"'
    )


# Prepared-reference cache: (abspath, mtime_ns, size) → prepared path (which
# may be the original path itself when no trim/cap applied). Keeps repeat
# generations from re-reading + re-writing the same clip, and keeps the temp
# dir from filling with one copy per generate() call.
_VOXCPM_REF_PREP_CACHE: dict[tuple, str] = {}
#: Prepared paths whose voiced span was cut at :data:`_VOXCPM_REF_MAX_S`.
_VOXCPM_CAPPED_REFS: set[str] = set()


def prepare_voxcpm_reference(kw: dict) -> None:
    """Prepare ``kw["ref_audio"]`` in place for VoxCPM2 (both adapters).

    #2281: when the clip was cut to its first :data:`_VOXCPM_REF_MAX_S`, a
    whole-clip transcript no longer matches the audio, and VoxCPM2 would
    continue from a prompt whose text runs past its end. Drop the transcript
    so the capped clip clones as a plain reference instead.
    """
    if not kw.get("ref_audio"):
        return
    kw["ref_audio"] = _prepare_voxcpm_ref(kw["ref_audio"])
    if kw.get("ref_text") and kw["ref_audio"] in _VOXCPM_CAPPED_REFS:
        logger.info(
            "VoxCPM2: reference capped at %.0fs; ignoring its whole-clip transcript",
            _VOXCPM_REF_MAX_S,
        )
        kw["ref_text"] = None


def _prepare_voxcpm_ref(path: str) -> str:
    """Prepare a cloning reference clip for VoxCPM2.

    The `voxcpm` package used to trim reference audio itself but no longer
    does — raw user clips reach the model unconditioned. This applies the
    minimal, conservative preparation the model expects:

      • trim leading/trailing near-silence (amplitude threshold at the same
        -50 dBFS floor `audio_dsp.normalize_audio` uses, with a small
        :data:`_VOXCPM_REF_EDGE_PAD_S` pad kept on each side), and
      • cap the reference at :data:`_VOXCPM_REF_MAX_S` seconds from the
        trimmed start.

    Returns a path to the prepared WAV. Deliberately non-destructive and
    fail-open: the ORIGINAL path is returned unchanged when the clip needs no
    meaningful trim/cap (short clean clips pass through untouched), when the
    whole clip sits below the silence floor (nothing to anchor a trim on), or
    when anything at all goes wrong — reference prep must never be the reason
    a generation fails.
    """
    try:
        import numpy as np
        import soundfile as sf

        abspath = os.path.abspath(path)
        st = os.stat(abspath)
        cache_key = (abspath, st.st_mtime_ns, st.st_size)
        cached = _VOXCPM_REF_PREP_CACHE.get(cache_key)
        if cached is not None and (cached == abspath or os.path.exists(cached)):
            return cached

        audio, sr = sf.read(abspath, dtype="float32", always_2d=True)  # (n, ch)
        n = audio.shape[0]
        if n == 0 or sr <= 0:
            return path

        # Silence floor: -50 dBFS, matching audio_dsp.normalize_audio. A clip
        # that never rises above it is left alone (fail-open, see docstring).
        floor = 10 ** (-50.0 / 20.0)
        envelope = np.abs(audio).max(axis=1)
        voiced = np.flatnonzero(envelope > floor)
        if voiced.size == 0:
            _VOXCPM_REF_PREP_CACHE[cache_key] = abspath
            return path

        pad = int(_VOXCPM_REF_EDGE_PAD_S * sr)
        start = max(0, int(voiced[0]) - pad)
        end = min(n, int(voiced[-1]) + 1 + pad)
        cap = int(_VOXCPM_REF_MAX_S * sr)
        capped = end > start + cap
        end = min(end, start + cap)

        # No-op path: nothing meaningful to cut (>0.1 s total) — hand the
        # original file to the model byte-identical.
        if (start + (n - end)) <= int(0.1 * sr):
            _VOXCPM_REF_PREP_CACHE[cache_key] = abspath
            return path

        import tempfile
        fd, prepared = tempfile.mkstemp(prefix="voxcpm_ref_", suffix=".wav")
        os.close(fd)
        sf.write(prepared, audio[start:end], sr)
        _VOXCPM_REF_PREP_CACHE[cache_key] = prepared
        if capped:
            _VOXCPM_CAPPED_REFS.add(prepared)
        logger.info(
            "VoxCPM2: prepared reference clip %s → %s (%.2fs → %.2fs; "
            "silence trimmed, cap %.0fs)",
            path, prepared, n / sr, (end - start) / sr, _VOXCPM_REF_MAX_S,
        )
        return prepared
    except Exception as e:  # noqa: BLE001 — prep is best-effort by contract
        logger.warning(
            "VoxCPM2: reference-clip preparation failed for %s — using the "
            "raw clip: %s", path, e,
        )
        return path


class VoxCPM2Backend(TTSBackend):
    """OpenBMB VoxCPM2 wrapper — `pip install "voxcpm>=2.0.3"` required.

    Voice Design: VoxCPM2 uniquely supports creating voices from a text
    description (e.g. "young female, warm tone, British accent") without
    any reference audio. Pass `description=` without `ref_audio=` to use
    this mode.
    """

    id = "voxcpm2"
    display_name = "VoxCPM2 (30 langs, studio 48 kHz, voice design)"
    supports_voice_design = True
    # _prepare_voxcpm_ref keeps the first _VOXCPM_REF_MAX_S after silence trim.
    max_ref_seconds = _VOXCPM_REF_MAX_S
    ref_strategy = "head"
    applies_own_mastering = True  # native 48 kHz studio output — skip apply_mastering()
    gpu_compat = ("cuda", "mps", "cpu")

    def __init__(self):
        self._model = None

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        try:
            import voxcpm  # noqa: F401
        except ImportError:
            return False, (
                "voxcpm package not installed. Install with "
                '`pip install "voxcpm>=2.0.3"` '
                "(requires Python ≥3.10, PyTorch ≥2.5). CUDA ≥12 recommended "
                "for full speed; MPS (Apple Silicon) and CPU also supported."
            )
        # Version FLOOR, not pin: an older install still reports available
        # (no forced reinstall), but the reason carries the upgrade hint and
        # _ensure_loaded() logs it at load time.
        hint = _voxcpm_upgrade_hint()
        if hint:
            return True, f"ready — {hint}"
        return True, "ready"

    _DEFAULT_SAMPLE_RATE = 48000

    @property
    def sample_rate(self) -> int:
        return self._DEFAULT_SAMPLE_RATE

    @property
    def supported_languages(self) -> list[str]:
        # 30 langs per model card.
        return [
            "ar", "my", "zh", "da", "nl", "en", "fi", "fr", "de", "el",
            "he", "hi", "id", "it", "ja", "km", "ko", "lo", "ms", "no",
            "pl", "pt", "ru", "es", "sw", "sv", "tl", "th", "tr", "vi",
        ]

    def _ensure_loaded(self):
        if self._model is not None:
            return
        ok, msg = self.is_available()
        if not ok:
            raise RuntimeError(f"VoxCPM2 unavailable: {msg}")
        hint = _voxcpm_upgrade_hint()
        if hint:
            logger.warning("VoxCPM2: %s", hint)
        from voxcpm import VoxCPM  # type: ignore[import-not-found]
        checkpoint = os.environ.get("OMNIVOICE_VOXCPM_MODEL", "openbmb/VoxCPM2")
        logger.info("Loading VoxCPM2 from %s", checkpoint)
        # #1224: this first-use download is multi-GB. Unretried, a truncated
        # body at 90% aborted the load outright.
        self._model = _retry_once_with_fresh_hf_client(
            lambda: VoxCPM.from_pretrained(checkpoint, load_denoiser=False),
            "VoxCPM2",
        )

    def generate(self, text, **kw) -> torch.Tensor:
        self._check_language(kw.get("language"))
        self._ensure_loaded()
        import numpy as np

        from engines.voxcpm2_subprocess.main import generation_kwargs

        prepare_voxcpm_reference(kw)
        wav = self._model.generate(**generation_kwargs(text, **kw))
        return self._finalize(wav)

    def _finalize(self, wav) -> torch.Tensor:
        """Normalize model output to a (1, n) float tensor and apply the
        trailing-silence guard.

        The guard is a SILENCE trim only: generations often end with a long
        near-silent tail, which this cuts (keeping a short ~0.3 s natural
        tail). It deliberately does NOT attempt to detect or judge trailing
        *content* — an output that ends in audible audio, wanted or not,
        passes through unchanged, as does any output without a silent tail.
        """
        import numpy as np
        from services.audio_dsp import trim_trailing_silence

        if isinstance(wav, np.ndarray):
            wav = torch.from_numpy(wav).float()
        if wav.ndim == 1:
            wav = wav.unsqueeze(0)
        return trim_trailing_silence(wav, self.sample_rate)


class KittenTTSBackend(TTSBackend):
    """KittenML/KittenTTS — 25-80 MB ONNX model, 8 preset voices, English only.

    Fills the ElevenLabs-Flash niche: when the caller just needs quick English
    narration (voiceover, demo reads, short phrases) with no reference sample.
    Runs CPU-realtime on any platform — no torch, no CUDA, no mlx. The
    trade-off vs OmniVoice is obvious:
      - No voice cloning (fixed preset voices)
      - English only
      - Much faster + much smaller install

    Preset voice is chosen via `extras["voice"]` (defaults to DEFAULT_VOICE,
    "expr-voice-2-f"). Any
    `ref_audio` / `instruct` / `language` arg is ignored with a log line so
    the common call-site doesn't need to know which engine it's talking to.
    """

    id = "kittentts"
    display_name = "KittenTTS (English, 8 preset voices, CPU realtime)"
    # KittenTTS ships as an ONNX CPU graph; no CUDA/MPS path today.
    gpu_compat = ("cpu",)
    supports_cloning = False  # fixed preset voices only; ref_audio is ignored

    PRESET_VOICES = [
        "expr-voice-2-m", "expr-voice-2-f",
        "expr-voice-3-m", "expr-voice-3-f",
        "expr-voice-4-m", "expr-voice-4-f",
        "expr-voice-5-m", "expr-voice-5-f",
    ]
    DEFAULT_VOICE = "expr-voice-2-f"

    def __init__(self):
        self._model = None

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        try:
            import kittentts  # noqa: F401
            return True, "ready"
        except ImportError as e:
            return False, f"kittentts not installed: {e}"

    _DEFAULT_SAMPLE_RATE = 24000

    @property
    def sample_rate(self) -> int:
        # KittenTTS emits 24 kHz mono per its ONNX model config.
        return self._DEFAULT_SAMPLE_RATE

    @property
    def supported_languages(self) -> list[str]:
        return ["en"]

    def _ensure_loaded(self):
        if self._model is not None:
            return
        from kittentts import KittenTTS
        checkpoint = os.environ.get(
            "OMNIVOICE_KITTENTTS_MODEL", "KittenML/kitten-tts-mini-0.8"
        )
        logger.info("Loading KittenTTS from %s", checkpoint)
        # #880: the first-use load downloads ~80 MB from the HF Hub inside the
        # generate path; if the hub's shared httpx client was closed
        # mid-lifecycle, retry once with a fresh client instead of failing
        # the whole generation.
        self._model = _retry_once_with_fresh_hf_client(
            lambda: KittenTTS(checkpoint), what="KittenTTS"
        )

    # #1173: the shipped ONNX graph's BERT front-end has a hard 512-token
    # positional cap (measured against kitten-tts-mini-0.8; exceeding it
    # aborts inference inside onnxruntime with the opaque
    # "Expand node … invalid expand shape" InvalidArgument). Upstream's
    # chunker caps chunks at 400 *text characters*, but token count is the
    # length of the *phonemized* string — espeak expands digits (and other
    # verbalized tokens) massively, so 110 chars of digits already
    # phonemize to ~1150 tokens. We pre-measure every chunk with the
    # model's own tokenizer and split oversized ones at word boundaries.
    _MAX_ONNX_TOKENS = 512

    def generate(self, text: str, **kw) -> torch.Tensor:
        self._check_language(kw.get("language"))
        import numpy as np
        self._ensure_loaded()

        voice = kw.get("voice") or self.DEFAULT_VOICE
        if voice not in self.PRESET_VOICES:
            logger.info(
                "KittenTTS: unknown voice %r, falling back to %r. Valid: %s",
                voice, self.DEFAULT_VOICE, self.PRESET_VOICES,
            )
            voice = self.DEFAULT_VOICE

        speed = float(kw.get("speed", 1.0))
        wav_np = self._synthesize(text, voice, speed)
        if not isinstance(wav_np, np.ndarray):
            wav_np = np.asarray(wav_np)
        wav = torch.from_numpy(wav_np).float()
        if wav.ndim == 1:
            wav = wav.unsqueeze(0)
        elif wav.ndim == 2 and wav.shape[0] > 1:
            wav = wav.mean(dim=0, keepdim=True)
        return wav

    # ── #1173 input-shape hardening ────────────────────────────────────────
    #
    # KittenTTS.generate() defaults clean_text=False, so the engine's own
    # number-verbalizing preprocessor never ran through this adapter — and
    # the openai-compat route typically has no `language`, so the app-level
    # normalize_for_tts() skips numbers→words there too. Raw digits then
    # reached espeak, whose verbalization exploded past the ONNX graph's
    # 512-token cap ("invalid expand shape" 500). Empty / unspeakable input
    # crashed differently (np.concatenate on zero chunks). Both classes are
    # handled here, in the adapter, so every route benefits.

    def _synthesize(self, text: str, voice: str, speed: float):
        """Chunk-and-generate with the engine's own cleanup + a token-budget
        preflight per chunk. Falls back to the plain upstream call if the
        kittentts internals this relies on ever change shape."""
        import numpy as np

        onnx = getattr(self._model, "model", None)
        if not (
            onnx is not None
            and callable(getattr(onnx, "generate_single_chunk", None))
            and callable(getattr(onnx, "_prepare_inputs", None))
        ):  # pragma: no cover — future upstream refactor
            return self._model.generate(text, voice=voice, speed=speed,
                                        clean_text=True)

        try:
            from kittentts.onnx_model import chunk_text
        except ImportError:  # pragma: no cover — future upstream refactor
            # Same contract as the attribute guard above: if upstream moves
            # chunk_text, degrade to the plain call instead of a 500.
            return self._model.generate(text, voice=voice, speed=speed,
                                        clean_text=True)

        cleaned = text
        preprocessor = getattr(onnx, "preprocessor", None)
        if callable(preprocessor):
            # The engine's own cleaner (numbers→words etc.) — same pass
            # upstream applies with clean_text=True.
            cleaned = preprocessor(text)

        chunks: list[str] = []
        for chunk in chunk_text(cleaned):
            chunks.extend(self._split_to_token_budget(onnx, chunk, voice, speed))
        if not chunks:
            raise TTSInputError(
                "KittenTTS: the input contains no speakable text (empty or "
                "punctuation-only after cleanup) — send at least one word."
            )
        outs = [onnx.generate_single_chunk(c, voice, speed) for c in chunks]
        return np.concatenate(outs, axis=-1)

    def _split_to_token_budget(self, onnx, chunk: str, voice: str,
                               speed: float) -> list[str]:
        """Split ``chunk`` (at word boundaries, then mid-word as a last
        resort) until each piece phonemizes to ≤ _MAX_ONNX_TOKENS tokens,
        measured with the model's own tokenizer. Never raises — an
        unmeasurable chunk is passed through unchanged."""
        chunk = chunk.strip()
        if not chunk:
            return []
        try:
            n_tokens = onnx._prepare_inputs(chunk, voice, speed)[
                "input_ids"].shape[1]
        except Exception:  # pragma: no cover — measurement is best-effort
            return [chunk]
        if n_tokens <= self._MAX_ONNX_TOKENS:
            return [chunk]
        words = chunk.split()
        if len(words) > 1:
            mid = len(words) // 2
            left, right = " ".join(words[:mid]), " ".join(words[mid:])
        else:
            # Single monster token (e.g. a 500-digit number pre-cleanup) —
            # bisect the raw string; degraded prosody beats an ONNX abort.
            mid = max(1, len(chunk) // 2)
            left, right = chunk[:mid], chunk[mid:]
            if not left or not right:  # 1-char chunk that still overflows
                return [chunk]  # pragma: no cover — impossible in practice
        return (self._split_to_token_budget(onnx, left, voice, speed)
                + self._split_to_token_budget(onnx, right, voice, speed))


_LAZY_REGISTRY: dict[str, tuple[str, str]] = {
    # Engines in their own package, resolved on first access so importing this
    # module never pulls in a sidecar's dependencies (and to break the
    # services.subprocess_backend <-> services.tts_backend import cycle).
    # Default voice: Kokoro-82M (Apache-2.0) on ONNX Runtime, every platform.
    "kokoro": ("engines.kokoro", "KokoroBackend"),
    # Commercial-use English cloning (MIT) in its own venv (one-click install).
    "chatterbox": ("engines.chatterbox", "ChatterboxBackend"),
    # Commercial-use Indian languages and voice design (Apache-2.0), own venv.
    "indic-parler": ("engines.indic_parler", "IndicParlerBackend"),
    # Indian languages with no account (MIT), in its own venv.
    "indic-tts": ("engines.indic_tts", "IndicTTSBackend"),
    # The resident OmniVoice model in a crash-isolated sidecar (#730/#1190).
    "omnivoice-subprocess": ("engines.omnivoice_subprocess", "OmniVoiceSubprocessBackend"),
}


class _LazyRegistry(dict):
    """A dict that resolves selected keys via a deferred import.

    Keys in ``_LAZY_REGISTRY`` are not present in ``self`` until first
    access; ``__getitem__`` / ``__contains__`` / iteration all import
    them on demand. Everything else behaves like a normal dict — the
    registry-sandbox fixture in
    ``tests/backend/services/test_tts_backend_registry.py`` still gets
    snapshot semantics because once a lazy key is resolved it's stored
    in self exactly like a non-lazy key.
    """

    def __contains__(self, key) -> bool:  # noqa: D401
        return dict.__contains__(self, key) or key in _LAZY_REGISTRY

    def __getitem__(self, key):
        if dict.__contains__(self, key):
            return dict.__getitem__(self, key)
        if key in _LAZY_REGISTRY:
            mod_path, attr = _LAZY_REGISTRY[key]
            import importlib

            cls = getattr(importlib.import_module(mod_path), attr)
            self[key] = cls
            return cls
        raise KeyError(key)

    def __iter__(self):
        # Yield resolved keys first, then any lazy keys that haven't been
        # resolved yet. Resolving inside __iter__ would trigger a side
        # effect on every list_backends() call — we keep iteration light
        # and let the caller's __getitem__ trigger the import.
        seen: set[str] = set()
        # Snapshot the live keys before yielding. A concurrent thread's lazy
        # __getitem__ inserts into self (self[key] = cls), and list_backends()
        # runs in a FastAPI threadpool — so holding a *live* dict iterator open
        # across the per-engine is_available() probes would raise
        # "dictionary changed size during iteration". list() consumes the
        # iterator atomically under the GIL, closing that window.
        for k in list(dict.__iter__(self)):
            seen.add(k)
            yield k
        for k in _LAZY_REGISTRY:
            if k not in seen:
                yield k

    def items(self):
        for k in self:
            yield k, self[k]

    def keys(self):
        return list(iter(self))

    def values(self):
        return [self[k] for k in self]


_REGISTRY: dict[str, type[TTSBackend]] = _LazyRegistry({
    "omnivoice":     OmniVoiceBackend,
    "kittentts":     KittenTTSBackend,
    "voxcpm2":       VoxCPM2Backend,
})


# ── ENGINE-06 last-error cache ─────────────────────────────────────────────
#
# Populated by `list_backends()` whenever a backend's `is_available()`
# returns ok=False or raises an exception. Cleared per-id when the same
# backend reports ok=True. Surfaced via the `last_error` field on each
# registry entry so the Compat Matrix UI (Plan 02-04) can show the most
# recent failure even between calls — and prove which engine is the source
# of a hung Settings panel.
_LAST_ERRORS: dict[str, str] = {}



# Short install hints surfaced as tooltips on the Model Catalogue UI.
# Helps users understand what pip package to install and where.
_INSTALL_HINTS: dict[str, str] = {
    "omnivoice":     "pip install omnivoice  (bundled — no extra install needed)",
    "omnivoice-subprocess": "No extra install; uses the host OmniVoice install. Opt in with OMNIVOICE_TTS_BACKEND=omnivoice-subprocess (same model in a killable sidecar, for unattended reliability).",
    "kittentts":     "pip install kittentts  (ONNX, CPU-only, ~80 MB)",
    "chatterbox":    "One-click install from Model Catalogue (own venv, torch 2.6; ~3 GB weights from ResembleAI/chatterbox on first use).",
    "indic-parler":  "One-click install from Model Catalogue (own venv; ~3.8 GB gated weights from ai4bharat/indic-parler-tts: accept its terms on Hugging Face and add a token first).",
    "indic-tts":     "One-click install from Model Catalogue (own venv, CPU torch; each language's ~1.5 GB voice downloads from GitHub the first time it's needed, no account).",
    "kokoro":        "Bundled — ONNX on CPU with the KittenTTS dependencies; the ~95 MB model downloads from onnx-community/Kokoro-82M-v1.0-ONNX on first use.",
    "voxcpm2":       'pip install "voxcpm>=2.0.3"  (floor: 2.0.3 fixed Apple-Silicon audio quality; CPU/MPS supported, CUDA recommended for speed)',
}


# Copy-paste-ready setup line for opt-in engines gated behind a filesystem-path
# env var (issue #498 / #590). The install_hint tells users a var exists; this
# is the *exact* `export VAR=...` line to run, so they don't have to reconstruct
# it from the docs. Surfaced verbatim in the Compat Matrix's "Why unavailable?"
# disclosure with a Copy button. Single-sourced here so it can't drift from the
# var each engine's is_available() actually reads. bash/zsh form (the dominant
# clone-and-run workflow for these engines; dots.tts is *nix-only anyway).
_SETUP_SNIPPETS: dict[str, str] = {
}


# Per-engine documentation page, as a repo-relative path (#1866). Every one of
# these docs already exists and several are CI-guarded against the code they
# describe (e.g. tests/test_cosyvoice_install_docs.py), but nothing in the app
# linked to them, so the point of failure — an unavailable engine row — was a
# dead end. Paths rather than URLs so tests/test_engine_docs.py can assert the
# file is really there; the URL is built once, at read time, from core.links.
#
# Keyed on the engine id, so it stays correct when the doc filename does not
# match the id.
_ENGINE_DOCS: dict[str, str] = {
    "omnivoice":            "docs/engines/omnivoice.md",
    "omnivoice-subprocess": "docs/engines/omnivoice-subprocess.md",
    "kittentts":            "docs/engines/kittentts.md",
    "kokoro":               "docs/engines/kokoro.md",
    "chatterbox":           "docs/engines/chatterbox.md",
    "indic-parler":         "docs/engines/indic-parler.md",
    "indic-tts":            "docs/engines/indic-tts.md",
    "voxcpm2":              "docs/engines/voxcpm2.md",
}


def _engine_docs_url(bid: str) -> str | None:
    """Public URL of this engine's doc page, or None when it has none.

    Voiceym-owned constant either way: the path comes from the registry
    above and the base from :mod:`core.links`, so no part of it is derived
    from an engine probe. That is what lets it cross the public boundary
    intact (see api.public_engine_metadata).
    """
    path = _ENGINE_DOCS.get(bid)
    if not path:
        return None
    from core import links
    return f"{links.PROJECT_REPO_BLOB_MAIN}/{path}"


def _sidecar_installable_ids() -> frozenset[str]:
    """Engine ids with a one-click sidecar installer. Deferred import — the
    installer module is tiny, but keeping the import inside the function
    means a broken/absent installer can never take the engine picker down.

    All current sidecar SPECS are TTS engines, so only this registry carries
    ``one_click_install``; the first non-TTS sidecar engine will need the same
    field plumbed into asr_backend/llm_backend.list_backends and the Install
    button into their matrix rows.
    """
    try:
        # Host-aware: an engine whose installer cannot work on THIS machine
        # (dots.tts on Windows, a CUDA-only install on a CPU host) must not get
        # an Install button that can only fail.
        from services.sidecar_install import installable_engine_ids
        return installable_engine_ids()
    except Exception:  # pragma: no cover — defensive only
        return frozenset()


def voice_design_support(cls) -> Optional[bool]:
    """``supports_voice_design`` as reported to clients: a declared bool, or
    None when the engine never declared it (or it depends on the model)."""
    value = getattr(cls, "supports_voice_design", None)
    return value if isinstance(value, bool) else None


def preset_voices_of(cls) -> list[str]:
    """Built-in voice ids an engine class exposes for selection (empty if none)."""
    voices = getattr(cls, "PRESET_VOICES", None)
    if not isinstance(voices, (list, tuple)):
        return []
    return [v for v in voices if isinstance(v, str)]


#: Non-commercial (CC-BY-NC) engines kept only so an install that already
#: uses one keeps working: never listed unless it is the active engine.
UNLISTED_ENGINES = frozenset({"omnivoice", "omnivoice-subprocess"})


def list_backends(*, include_hidden: bool = False) -> list[dict]:
    """Enumerate the engine catalogue with each backend's availability state.

    On MPS, the canonical ``omnivoice`` id already resolves to the killable
    OmniVoice sidecar. The explicit ``omnivoice-subprocess`` compatibility id
    is therefore omitted from the normal catalogue so the picker does not
    advertise two choices with the same runtime behavior. Internal callers
    that must validate or preserve a stored compatibility id can pass
    ``include_hidden=True``. Engines in ``UNLISTED_ENGINES`` are omitted the
    same way unless one is the active engine.

    Per-entry shape (ENGINE-05 + ENGINE-06):

        {
          "id":             str,
          "display_name":   str,
          "available":      bool,
          "reason":         Optional[str],          # message when not available
          "hint":           Optional[str],          # advice when available-but-has-advice
                                                    #   (is_available "ready — <advice>" convention;
                                                    #   e.g. VoxCPM2's >=2.0.3 upgrade hint)
          "install_hint":   Optional[str],
          "setup_snippet":  Optional[str],          # exact `export VAR=...` for path-gated opt-in engines
          "docs_url":       Optional[str],          # this engine's doc page (registry-authored constant)
          "one_click_install": bool,                # services.sidecar_install can provision it in-app
          "last_error":     Optional[str],          # cached most-recent failure
          "isolation_mode": "in-process" | "subprocess",
          "gpu_compat":     list[str],              # subset of {cuda, rocm, mps, vulkan, xpu, npu, cpu}
          "supports_cloning": Optional[bool],       # True/False from the class attr; None when
                                                    #   model-dependent (property, e.g. mlx-audio)
          "instruct_vocabulary": "tags" | "freeform",  # OmniVoice tag set vs model-native text
          "supports_voice_design": Optional[bool],  # False = needs a reference clip;
                                                    #   None = undeclared (design allowed)
          "preset_voices":  list[str],              # built-in voice ids a request may pick (``voice``); [] = none
          "default_voice":  Optional[str],          # preset used when none is picked
          "max_ref_seconds": Optional[float],       # seconds of a clone clip the engine uses
          "ref_strategy": Optional[str],            # "best_window" | "head" | "full"; None = unverified
          "effective_device": str,                  # device this engine uses on THIS host
          "routing_status": "accelerated" | "cpu_fallback" | "cpu_only" | "unavailable",
          "routing_reason": Optional[str],          # scrubbed; null when none
        }

    Guarantees (ENGINE-05): a backend whose `is_available()` raises does
    NOT prevent the list from returning. The exception is captured into
    the `reason`/`last_error` fields for that one entry and every other
    backend is still listed normally.

    Security (Plan 02-04 / T-02-12): any HF-shaped token substring in
    ``reason`` or ``last_error`` is redacted before the entry is
    serialized — :func:`_mask_hf_tokens`. The frontend can render these
    fields verbatim without leaking credentials.
    """
    # Detect subprocess-isolated backends via a duck-typed marker rather
    # than `issubclass(cls, SubprocessBackend)`. Test fixtures (e.g. the
    # token_resolver suite) purge `sys.modules["services"]` between tests
    # for DB isolation, which produces a re-imported SubprocessBackend
    # class object that no longer == the one this test's subclasses closed
    # over. The marker attribute is set on SubprocessBackend itself, so
    # subclasses inherit it through any re-import path.
    # Routing is host-aware but the host caps are constant per process, so probe
    # ONCE here and resolve each engine's effective device against the same caps.
    from core.device_caps import detect_host_caps
    from services.engine_disk_usage import disk_summary_for
    from services.engine_evidence import snapshot as execution_snapshot
    caps = detect_host_caps()
    installable = _sidecar_installable_ids()

    try:
        active = active_backend_id()
    except Exception:  # noqa: BLE001 -- an unreadable preference hides nothing extra
        active = None

    out: list[dict] = []
    for bid, cls in _REGISTRY.items():
        if (
            not include_hidden
            and caps.family == "mps"
            and bid == "omnivoice-subprocess"
        ):
            continue
        if not include_hidden and bid in UNLISTED_ENGINES and bid != active:
            continue
        cls = _effective_backend_class(bid, cls, caps.family)
        try:
            ok, msg = cls.is_available()
        except Exception:
            ok = False
            msg = "Availability probe failed; check the backend log."
            logger.warning("list_backends: availability probe failed for registered backend %s", bid)
        if ok:
            _LAST_ERRORS.pop(bid, None)
        else:
            # Mask any HF token inside the failure message BEFORE it lands
            # in the in-memory cache — otherwise a later list_backends()
            # call would re-surface the unmasked string.
            _LAST_ERRORS[bid] = _mask_hf_tokens(msg)
        # ENGINE-06 isolation_mode: duck-typed marker for SubprocessBackend
        # subclasses (see services.subprocess_backend.SubprocessBackend).
        if getattr(cls, "_is_subprocess_isolated", False):
            isolation = "subprocess"
        else:
            isolation = "in-process"
        from services.engine_routing import resolve_routing, runtime_compute_profile
        try:
            profile = runtime_compute_profile(cls, caps)
        except Exception:
            # Runtime-aware native probes remain optional metadata. A broken
            # provider probe must not take down the engine picker, especially
            # when availability already explains a missing binary or model.
            compat = tuple(getattr(cls, "gpu_compat", ("cpu",)))
            floor = float(getattr(cls, "min_vram_gb", 0.0) or 0.0)
            profile = {
                "gpu_compat": compat,
                "min_vram_gb": floor,
                **resolve_routing(compat, caps, floor),
                "runtime_backend": None,
                "runtime_device_index": None,
                "runtime_device_name": None,
                "runtime_hardware_family": None,
                "runtime_vram_gb": None,
                "runtime_device_verified": None,
            }
        gpu_compat = profile["gpu_compat"]
        # Cloning capability: same descriptor guard as
        # cloning_capable_engine_ids() — a class-level getattr on a *property*
        # (mlx-audio: capability depends on the picked model) returns the
        # descriptor, not a bool, so report None (= model-dependent) there
        # instead of an always-truthy false positive.
        _clone = getattr(cls, "supports_cloning", True)
        from core.scrub import scrub_text
        routing = {
            "effective_device": profile["effective_device"],
            "routing_status": profile["routing_status"],
            "routing_reason": scrub_text(profile["routing_reason"])
            if profile["routing_reason"] else None,
        }
        loaded_instance = None
        if _active_instance_id == bid:
            loaded_instance = _active_instance
        if loaded_instance is None:
            loaded_instance = _ENGINE_INSTANCES.get(cls)
        if loaded_instance is None and bid == "omnivoice":
            # Startup preloads OmniVoice through model_manager directly, before
            # any generation route needs an adapter instance. Reflect that
            # shared resident model here instead of contradicting
            # /model/loaded with a stale `not_loaded` engine state.
            try:
                from services import model_manager

                if model_manager.model is not None:
                    loaded_instance = OmniVoiceBackend(model=model_manager.model)
            except Exception:  # noqa: BLE001 - catalogue reads never fail on diagnostics
                pass
        out.append({
            "id": bid,
            "display_name": cls.display_name,
            "available": ok,
            "reason": None if ok else _mask_hf_tokens(msg),
            # Available-but-has-advice (e.g. VoxCPM2's ">=2.0.3 recommended"
            # upgrade hint). None unless ok and the message carries advice.
            "hint": _available_hint(msg) if ok else None,
            "supports_cloning": _clone if isinstance(_clone, bool) else None,
            # Graded-emotion capability (#1208) — drives the Audiobook emotion
            # panel's engine gate. Class attr, defaults False.
            "supports_emotion": bool(getattr(cls, "supports_emotion", False)),
            # "tags" = OmniVoice's closed design vocabulary; "freeform" = the
            # text reaches the model as written (#2389).
            "instruct_vocabulary": getattr(cls, "instruct_vocabulary", "freeform"),
            # Voice Design gate: False hides Design for reference-only engines.
            "supports_voice_design": voice_design_support(cls),
            # Reference-length truth (#2281): how much of a clone clip the
            # engine really uses and how it picks it. None = not verified.
            "max_ref_seconds": getattr(cls, "max_ref_seconds", None),
            "preset_voices": preset_voices_of(cls),
            "default_voice": getattr(cls, "DEFAULT_VOICE", None) if preset_voices_of(cls) else None,
            "ref_strategy": getattr(cls, "ref_strategy", None),
            "install_hint": _INSTALL_HINTS.get(bid),
            # Exact `export VAR=...` line for path-gated opt-in engines, or None.
            "setup_snippet": _SETUP_SNIPPETS.get(bid),
            # This engine's doc page (#1866). Registry-authored constant, so it
            # survives api.public_engine_metadata and gives an unavailable row
            # somewhere to send the user.
            "docs_url": _engine_docs_url(bid),
            # True when services.sidecar_install can provision this engine
            # in-app (Settings renders an Install button instead of leading
            # with the manual setup snippet).
            "one_click_install": bid in installable,
            "disk_usage": disk_summary_for(bid),
            "last_error": _LAST_ERRORS.get(bid),
            "isolation_mode": isolation,
            "gpu_compat": list(gpu_compat),
            # effective_device / routing_status / routing_reason (scrubbed):
            "min_vram_gb": profile["min_vram_gb"] or None,
            # effective_device / routing_status / routing_reason (scrubbed);
            # the reason now also carries the under-provisioned-GPU caveat.
            **routing,
            "execution_evidence": execution_snapshot(
                engine_id=bid,
                engine_cls=cls,
                instance=loaded_instance,
                routing={**profile, **routing},
                caps=caps,
            ),
        })
    return out


# In-process engines that also run from a venv of their own once the
# one-click installer has made one: engine id -> (sidecar module, class). Each
# module exposes own_venv_python(); an install made into the app's environment
# keeps running in-process.
_OWN_VENV_SIDECARS: dict[str, tuple[str, str]] = {
    "voxcpm2": ("engines.voxcpm2_subprocess", "VoxCPM2SubprocessBackend"),
}


def _effective_backend_class(
    backend_id: str,
    backend_cls: type[TTSBackend],
    host_family: str | None = None,
) -> type[TTSBackend]:
    """Resolve host-specific containment without changing the configured id."""
    sidecar = _OWN_VENV_SIDECARS.get(backend_id)
    if sidecar is not None:
        import importlib

        module = importlib.import_module(sidecar[0])
        return getattr(module, sidecar[1]) if module.own_venv_python() is not None else backend_cls
    if backend_id != "omnivoice":
        return backend_cls
    if host_family is None:
        from core.device_caps import detect_host_caps

        host_family = detect_host_caps().family
    if host_family != "mps":
        return backend_cls
    from engines.omnivoice_subprocess import OmniVoiceMPSSubprocessBackend

    return OmniVoiceMPSSubprocessBackend


def get_backend_class(backend_id: str) -> type[TTSBackend]:
    if backend_id not in _REGISTRY:
        raise ValueError(f"Unknown TTS backend: {backend_id!r}. Known: {list(_REGISTRY)}")
    return _effective_backend_class(backend_id, _REGISTRY[backend_id])


def output_sample_rate(backend_id: str) -> Optional[int]:
    """Read a live rate or an adapter's declared default without loading a model."""
    try:
        if _active_instance is not None and _active_instance_id == backend_id:
            rate = _active_instance.sample_rate
        else:
            rate = getattr(get_backend_class(backend_id), "_DEFAULT_SAMPLE_RATE", None)
        return rate if isinstance(rate, int) and not isinstance(rate, bool) and rate > 0 else None
    except Exception:
        return None  # Model-specific or unavailable metadata remains unknown.


def language_options(backend_id: str) -> Optional[list[str]]:
    """Picker names for a finite engine; None leaves unknown/model-specific sets open.

    Adapter constructors configure references only. Never load weights or call
    get_active_tts_backend here: discovery must not switch or unload an engine.
    """
    from omnivoice.utils.lang_map import LANG_NAME_TO_ID

    backend = None
    try:
        # The native OmniVoice adapters use this exact vocabulary at inference.
        if backend_id in {"omnivoice", "omnivoice-subprocess"}:
            return sorted(LANG_NAME_TO_ID)
        backend = get_backend_class(backend_id)()
        declared = backend.supported_languages
        if not declared or "multi" in declared:
            return None
        codes = {backend._normalize_language_code(code) for code in declared}
        candidates = set(LANG_NAME_TO_ID) | {"mandarin", "arabic", "tagalog"}
        candidates.update(name.lower() for name in backend.language_display_names.values())
        return sorted(name for name in candidates
                      if backend._normalize_language_code(name) in codes)
    except Exception:  # Optional metadata must not take down discovery.
        logger.debug("Could not resolve language options for %s", backend_id, exc_info=True)
        return None
    finally:
        # Sidecar constructors register a bound exit handler, which otherwise
        # retains every temporary metadata instance for the process lifetime.
        shutdown = getattr(backend, "shutdown", None)
        if callable(shutdown) and getattr(backend, "_is_subprocess_isolated", False):
            import atexit
            try:
                shutdown()
            except Exception:
                logger.debug("Could not clean up language metadata instance", exc_info=True)
            finally:
                atexit.unregister(shutdown)


def cloning_unavailable_detail(engine_id: str, backend, cloning_purpose: str) -> str:
    """Why the active engine can't clone, and the smallest change that fixes it.

    ``supports_cloning`` is an engine-level flag for every backend except the
    adapters that multiplex models, where it is a property computed from the
    *model* currently selected. For those, "this engine doesn't support voice
    cloning" is simply untrue — the engine clones fine, just not with the pick
    it is running (#2201).

    So: name the model in the way, name the model that works, and keep the
    engine list as the fallback it should always have been.
    """
    cls = type(backend)
    declared = getattr(cls, "supports_cloning", True)
    labels = tuple(cls.cloning_model_labels()) if hasattr(cls, "cloning_model_labels") else ()
    alternatives = ", ".join(cloning_capable_engine_ids())
    # A plain bool is a fact about the engine; a property is a fact about the
    # model, and only the second case has a model to switch to.
    if not isinstance(declared, bool) and labels:
        current = ""
        try:
            current = backend.model_identity() or ""
        except Exception:  # noqa: BLE001 — naming the model is best-effort
            current = ""
        running = f" ({current})" if current else ""
        return (
            f"The '{engine_id}' engine can clone voices, but not with the model "
            f"it is running{running}, so {cloning_purpose} can't preserve speaker "
            f"voices. Set this engine's model to {' or '.join(labels)} in Model "
            f"Catalogue — nothing else has to change — or switch engine to one "
            f"of: {alternatives}."
        )
    return (
        f"The active TTS engine '{engine_id}' doesn't support voice cloning, "
        f"so {cloning_purpose} can't preserve speaker voices. Switch to one "
        f"of: {alternatives} in "
        "Model Catalogue."
    )


def cloning_capable_engine_ids() -> list[str]:
    """Engine ids that support reference-audio voice cloning — used to build
    an actionable error when the active engine can't (dub/batch gating).

    Iterates the same registry ``list_backends()`` uses, via ``.items()`` so
    lazy entries resolve through ``_LazyRegistry``'s snapshot-safe iteration
    (see ``_LazyRegistry.__iter__``) exactly like every other registry scan
    in this module.

    A class-level ``getattr`` on a *property* returns the descriptor object
    itself (always truthy) rather than its computed value — so a
    model-dependent adapter (only some of its models can clone) would always
    show up here regardless of which model is actually configured. Excluded rather than falsely recommended:
    ``isinstance(..., bool)`` is False for a descriptor, True for a plain
    class attribute.
    """
    return [
        bid for bid, cls in _REGISTRY.items()
        if bid not in UNLISTED_ENGINES
        and isinstance((v := getattr(cls, "supports_cloning", True)), bool) and v
    ]


def active_routing() -> dict | None:
    """Routing verdict for the currently-active TTS engine, or ``None`` if it
    can't be determined (no engine / probe failure).

    Derived from :func:`list_backends` so the verdict is byte-identical to what
    the Engine Compatibility Matrix shows for the same engine. Consumed by
    ``/setup/preflight`` and ``/system/diagnose`` to surface a GPU-routing
    verdict for the active engine (no silent CPU fallback). Never raises.
    """
    try:
        active = active_backend_id()
        # The MPS picker intentionally hides the redundant compatibility id,
        # but routing must still describe a saved or environment-pinned id.
        for b in list_backends(include_hidden=True):
            if b.get("id") == active:
                return {
                    "engine": active,
                    "available": b.get("available"),
                    "effective_device": b.get("effective_device"),
                    "routing_status": b.get("routing_status"),
                    "routing_reason": b.get("routing_reason"),
                }
    except Exception:
        # Routing is advisory — never let a probe/registry hiccup break the
        # caller (preflight/diagnose must stay responsive — local-first).
        return None
    return None


def gpu_routing_verdict() -> dict:
    """The GpuRouting payload (see api.schemas.GpuRouting) for the active TTS
    engine + this host's compute summary. Used by ``/setup/preflight`` and
    ``/system/diagnose``. Never raises — degrades to a host-only verdict with
    ``routing_status:"none"`` if the active engine can't be resolved."""
    from core.device_caps import detect_host_caps
    try:
        caps = detect_host_caps()
        host_family, vram_gb = caps.family, round(caps.vram_gb, 1)
    except Exception:
        host_family, vram_gb = "cpu", 0.0
    r = active_routing()
    if not r:
        return {
            "engine": None, "effective_device": None,
            "routing_status": "none", "routing_reason": None,
            "host_family": host_family, "vram_gb": vram_gb,
        }
    return {
        "engine": r.get("engine"),
        "effective_device": r.get("effective_device"),
        "routing_status": r.get("routing_status"),
        "routing_reason": r.get("routing_reason"),
        "host_family": host_family, "vram_gb": vram_gb,
    }


#: The engine used when the user has not picked one: commercial-use friendly
#: (Apache-2.0) and the same on every platform.
DEFAULT_TTS_ENGINE = "kokoro"


def active_backend_id() -> str:
    # Env var > persisted UI choice > default. Env wins so power-users can
    # pin a backend without the Settings picker silently undoing it.
    from core import prefs
    # Kokoro (Apache-2.0) is the default; OmniVoice (CC-BY-NC) is opt-in.
    return prefs.resolve("tts_backend", env="OMNIVOICE_TTS_BACKEND", default=DEFAULT_TTS_ENGINE)


# Cached active backend instance + its id (MM2-01). Without this, every call
# built a fresh instance and the previous engine's VRAM/sidecar leaked until GC
# — measurable when switching engines on an 8 GB MPS Mac (root cause behind the
# #278 comment thread). We now keep one instance per configured backend id and
# call the outgoing engine's unload() before switching.
_active_instance: "TTSBackend | None" = None
_active_instance_id: "str | None" = None


def reset_active_backend() -> None:
    """Unload + clear the cached active backend. For app shutdown and tests.
    Idempotent and best-effort — a raising unload() never propagates."""
    global _active_instance, _active_instance_id
    inst = _active_instance
    _active_instance = None
    _active_instance_id = None
    if inst is not None:
        try:
            inst.unload()
        except Exception as exc:  # noqa: BLE001
            logger.warning("reset_active_backend: %s.unload() raised: %s",
                           type(inst).__name__, exc)


def ensure_engine_licence(cls) -> None:
    """Refuse a TTS engine whose gated model licences the user has not accepted.

    Checked on every resolution, not only at construction, so revoking
    acceptance also stops an already-cached instance. Raises
    ``ModelLicenceNotAccepted`` before any weights load or sidecar starts.
    """
    from services.model_acceptance import ensure_engine_accepted

    engine_id = getattr(cls, "id", None)
    if not engine_id:
        return
    try:
        identity = cls.configured_identity()
    except Exception:  # noqa: BLE001 — unknown model: gate every model the engine ships
        identity = None
    ensure_engine_accepted(engine_id, identity)


def ensure_active_engine_licence() -> None:
    """:func:`ensure_engine_licence` for the configured engine, before any routing.

    For job entry points that may render remotely and so never reach the local
    instance cache. An unknown engine id is left to the caller's own error.
    """
    try:
        cls = get_backend_class(active_backend_id())
    except ValueError:
        return
    ensure_engine_licence(cls)


def get_active_tts_backend(*, model=None) -> TTSBackend:
    """Return the configured backend, reusing a cached instance and releasing
    the previous engine on a switch (MM2-01).

    Rule: the cache tracks the configured backend id. Switching id always
    unload()s the outgoing instance first. For OmniVoice with an explicit
    ``model=`` (caller already holds a loaded model), we return a fresh view
    over the shared singleton rather than caching it — but a switch *away from*
    a different engine still triggers that engine's unload().
    """
    global _active_instance, _active_instance_id
    bid = active_backend_id()
    cls = get_backend_class(bid)
    # Before the switch below: a refused engine must not unload the working one.
    ensure_engine_licence(cls)

    # Switching engines: release the outgoing one first. Best-effort so a bad
    # unload() can never block the switch.
    switching = _active_instance is not None and _active_instance_id != bid
    if switching:
        try:
            _retire_engine(_active_instance)
        except Exception as exc:  # noqa: BLE001
            logger.warning("engine switch: %s.unload() raised: %s",
                           type(_active_instance).__name__, exc)
        _active_instance = None
        _active_instance_id = None

    if cls is OmniVoiceBackend and model is not None:
        # Per-call view over the already-loaded shared singleton; don't cache it
        # (the model lifecycle is owned by model_manager), but the switch above
        # already released any *different* previous engine.
        return OmniVoiceBackend(model=model)

    if _active_instance is None or _active_instance_id != bid:
        _active_instance = OmniVoiceBackend(model=model) if cls is OmniVoiceBackend else cls()
        _active_instance_id = bid
    return _active_instance


# ── Shared engine-instance cache ──────────────────────────────────────────
#
# One instance per engine class for the lifetime of the process. It lived in
# ``api/routers/engines.py`` until the worker needed it too: a worker executing
# a remote assignment must reuse the same warm engine the local generate path
# uses, and importing an API router from ``worker/`` would invert the layering
# (``worker/executor.py`` is a translator over ``services/``). The router now
# aliases this dict, so every consumer that already reaches for
# ``engines._ENGINE_INSTANCES`` — engine_memory's eviction, model_lifecycle's
# inventory and unload — keeps operating on the one true cache.
#
# Keyed by CLASS, not by engine id, because registry-sandbox tests rebind ids
# transiently; ``get_engine_instance_for`` resolves an id through
# ``get_backend_class`` so callers can key by id without the cache doing so.
_ENGINE_INSTANCES: dict[type, object] = {}
_ENGINE_CACHE_LOCK = threading.RLock()

# Last use, on the monotonic clock — a wall clock would make an NTP step or a
# laptop resume look like a ten-minute idle and unload a model mid-job.
_ENGINE_LAST_USED: dict[type, float] = {}

# How many jobs are inside an engine right now. A long generation touches the
# cache once at the start, so on elapsed time alone a 40-minute dub looks
# exactly like an abandoned model — and the sweep would unload it out from
# under the thread rendering it.
_ENGINE_IN_USE: dict[type, int] = {}
_RETIRED_ENGINES: dict[type, list[object]] = {}


def _retire_engine(instance) -> None:
    """Unload a replaced model once every request holding its class has finished."""
    with _ENGINE_CACHE_LOCK:
        if _ENGINE_IN_USE.get(type(instance)):
            retired = _RETIRED_ENGINES.setdefault(type(instance), [])
            if not any(old is instance for old in retired):
                retired.append(instance)
            return
        instance.unload()

def _idle_seconds_from_env(name: str, default: float, *, floor: float) -> float:
    """Read a tunable idle duration, ignoring anything unusable.

    These exist so the ten-minute behaviour can be observed in a minute during
    testing instead of a coffee break. A bad value must not change behaviour
    silently, and must never reach zero: a zero threshold unloads an engine the
    instant it goes idle, which on a busy machine means reloading it for every
    request.
    """
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Ignoring %s=%r: not a number.", name, raw)
        return default
    if value < floor:
        logger.warning("Ignoring %s=%s: below the %ss floor.", name, value, floor)
        return default
    return value


#: How long an engine may sit unused before its weights are handed back.
#: Override with OMNIVOICE_ENGINE_IDLE_UNLOAD_SECONDS (testing).
ENGINE_IDLE_UNLOAD_SECONDS = _idle_seconds_from_env(
    "OMNIVOICE_ENGINE_IDLE_UNLOAD_SECONDS", 600.0, floor=5.0
)


@contextmanager
def engine_in_use(instance, *, now: Optional[float] = None):
    """Hold an engine against the idle sweep for the duration of one job.

    Leaving on the exit stamp rather than the entry one makes "idle" mean
    "idle since the work finished", which is the only reading under which the
    ten-minute window measures what it claims to.
    """
    cls = type(instance)
    with _ENGINE_CACHE_LOCK:
        _ENGINE_IN_USE[cls] = _ENGINE_IN_USE.get(cls, 0) + 1
    try:
        yield instance
    finally:
        retired = []
        with _ENGINE_CACHE_LOCK:
            remaining = _ENGINE_IN_USE.get(cls, 1) - 1
            if remaining > 0:
                _ENGINE_IN_USE[cls] = remaining
            else:
                _ENGINE_IN_USE.pop(cls, None)
                retired = _RETIRED_ENGINES.pop(cls, [])
            _ENGINE_LAST_USED[cls] = time.monotonic() if now is None else float(now)
        for old in retired:
            try:
                old.unload()
            except Exception:
                logger.warning("retired engine unload failed", exc_info=True)


def get_engine_instance(cls, *, now: Optional[float] = None):
    """Return the cached singleton instance of ``cls``, creating it once.

    ``SubprocessBackend.__init__`` registers an atexit shutdown hook, so
    re-instantiating per call would leak handler entries — and on real engines,
    an extra sidecar process the first time the lock is acquired. One instance
    per process is the right move.
    """
    ensure_engine_licence(cls)
    stale = None
    with _ENGINE_CACHE_LOCK:
        inst = _ENGINE_INSTANCES.get(cls)
        if inst is not None and _built_for_other_model(cls, inst):
            # A model picked in Settings after this instance was built (mlx-audio's
            # curated models) must take effect; otherwise the old model keeps
            # speaking under the new name. A job still holding it keeps its copy.
            stale = inst
            inst = None
        if inst is None:
            inst = cls()
            _ENGINE_INSTANCES[cls] = inst
        _ENGINE_LAST_USED[cls] = time.monotonic() if now is None else float(now)
    if stale is not None:
        try:
            _retire_engine(stale)
        except Exception as exc:  # noqa: BLE001
            logger.warning("model switch: %s.unload() raised: %s", type(stale).__name__, exc)
    return inst


def _built_for_other_model(cls, inst) -> bool:
    try:
        wanted = cls.configured_identity()
    except Exception:  # noqa: BLE001 — a preference read must never block synthesis
        return False
    return wanted is not None and wanted != inst.built_identity()


def get_engine_instance_for(engine_id: str, *, now: Optional[float] = None):
    """Cached instance of the TTS engine registered under ``engine_id``.

    Deliberately NOT :func:`get_active_tts_backend`: callers provide the exact
    engine identity instead of resolving the current Settings preference.
    """
    return get_engine_instance(get_backend_class(engine_id), now=now)


def release_idle_engines(
    idle_seconds: float = ENGINE_IDLE_UNLOAD_SECONDS,
    *,
    now: Optional[float] = None,
) -> list[str]:
    """Unload and drop every cached engine unused for ``idle_seconds``.

    Least-recently-used first, so a sweep cut short by a raising ``unload()``
    has already freed the coldest engine. Never raises: a stuck unload must not
    take down the loop that called it. Returns the engine ids released.
    """
    stamp = time.monotonic() if now is None else float(now)
    pending: list[tuple[str, object]] = []
    with _ENGINE_CACHE_LOCK:
        # Entries other consumers popped straight out of the cache
        # (engine_memory's eviction, model_lifecycle's unload) would otherwise
        # pin a stale class.
        for cls in [c for c in _ENGINE_LAST_USED if c not in _ENGINE_INSTANCES]:
            _ENGINE_LAST_USED.pop(cls, None)
        coldest_first = sorted(
            _ENGINE_INSTANCES, key=lambda c: _ENGINE_LAST_USED.get(c, 0.0)
        )
        for cls in coldest_first:
            if _ENGINE_IN_USE.get(cls):
                continue
            # An instance put here by some other path has no timestamp; start
            # its clock now rather than leaving it resident forever.
            last_used = _ENGINE_LAST_USED.setdefault(cls, stamp)
            if stamp - last_used < idle_seconds:
                continue
            inst = _ENGINE_INSTANCES.pop(cls, None)
            _ENGINE_LAST_USED.pop(cls, None)
            if inst is not None:
                pending.append((getattr(cls, "id", cls.__name__), inst))

    released: list[str] = []
    for engine_id, inst in pending:
        try:
            inst.unload()
        except Exception as exc:  # noqa: BLE001
            logger.warning("idle unload: %s.unload() raised: %s", engine_id, exc)
        released.append(engine_id)
    if released:
        logger.info("Released %d idle engine(s): %s", len(released), ", ".join(released))
    return released


# ── Shared generation-time engine resolution (issue #312 class) ───────────
#
# dub_generate.py and batch.py used to call services.model_manager.get_model()
# directly, hardcoding OmniVoice regardless of the engine selected in
# Model Catalogue — a SILENT fallback: pick VoxCPM2, dub anyway with
# OmniVoice, no error. This is the single resolution path both routers now
# call instead, mirroring generation.py's /generate resolution (engine id →
# is_available() → routing gate) plus a voice-cloning capability gate that
# /generate doesn't need (OmniVoice's native path always clones).


async def resolve_generation_backend(
    *, require_cloning: bool = False, cloning_purpose: str = "dubbing",
) -> TTSBackend:
    """Resolve + validate the active TTS engine for a generation call.

    Returns the live backend instance (:func:`get_active_tts_backend`) —
    cached, and properly unload()ed on an engine switch. Raises ``ValueError``
    with an actionable message (never silently falls back to OmniVoice) when:

      * the configured engine id is unknown (bad env var / stale pref),
      * the engine reports itself unavailable (``is_available()``),
      * the engine needs an accelerator this host lacks and has no CPU path
        (``routing_status == "unavailable"``),
      * ``require_cloning`` is True and the resolved backend can't clone
        from reference audio (``supports_cloning`` False) — checked on the
        live *instance*, not the class, so a model-dependent adapter like
        MLX-Audio (Kokoro vs. CSM) is judged by what's actually loaded.
    """
    engine_id = active_backend_id()
    try:
        backend_cls = get_backend_class(engine_id)
    except ValueError as e:
        raise ValueError(
            f"Active TTS engine '{engine_id}' is not a recognized backend ({e}). "
            "Check Model Catalogue or the OMNIVOICE_TTS_BACKEND env var."
        ) from e

    ensure_engine_licence(backend_cls)
    try:
        ok, msg = backend_cls.is_available()
    except Exception as exc:  # noqa: BLE001 — surface as an actionable ValueError
        ok, msg = False, f"{type(exc).__name__}: {exc}"
    if not ok:
        raise ValueError(f"TTS engine '{engine_id}' is not available: {_mask_hf_tokens(msg)}")

    from core.device_caps import detect_host_caps
    from services.engine_routing import runtime_compute_profile_async
    routing = await runtime_compute_profile_async(
        backend_cls, detect_host_caps()
    )
    if routing["routing_status"] == "unavailable":
        raise ValueError(routing["routing_reason"])

    _model = None
    if backend_cls is OmniVoiceBackend:
        # OmniVoice needs its model pre-loaded before construction: called
        # from an async context, OmniVoiceBackend._ensure_loaded() refuses to
        # bootstrap its own event loop (see its docstring) — same reason
        # generation.py's /generate special-cases this backend.
        from services.model_manager import get_model
        _model = await get_model()
    backend = get_active_tts_backend(model=_model)

    if require_cloning and not getattr(backend, "supports_cloning", True):
        raise ValueError(cloning_unavailable_detail(engine_id, backend, cloning_purpose))

    return backend


# ── PEP 562 lazy attribute re-export ───────────────────────────────────────
#
# Lets ``from services.tts_backend import <lazy engine id>`` resolve after this
# module has finished loading — so no import cycle.

def __getattr__(name: str):  # pragma: no cover - exercised via tests
    if name in _LAZY_REGISTRY:
        return _REGISTRY[name if name in _REGISTRY else None]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
