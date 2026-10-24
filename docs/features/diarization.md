# Voiceym — Speaker Diarization

Diarization splits a single audio stream into per-speaker tracks: who said
what, and when. Voiceym uses **pyannote** + **WhisperX** under the hood —
the same stack the original WhisperX paper used.

## What diarization buys you

- Multi-speaker dubbing: each detected speaker gets its own voice clone in
  the target language.
- Subtitle styling: speaker labels (`SPEAKER_00:`, `SPEAKER_01:`, …) on the
  exported SRT/VTT files.
- Audio editing: per-speaker tracks in the timeline view.

## License acceptance flow

The diarization model — `pyannote/speaker-diarization-3.1` — is **gated** on
HuggingFace. A valid HF token alone is not enough: you also need to accept
the model's license once.

1. Get a HF token if you don't have one — see
   [docs/setup/huggingface-token.md](../setup/huggingface-token.md).
2. Set the token via **Settings → API Keys** (or any of the other supported
   paths).
3. While signed in to HuggingFace with the same account, visit:
   - https://huggingface.co/pyannote/speaker-diarization-3.1 → **"Agree and
     access repository"**.
   - https://huggingface.co/pyannote/segmentation-3.0 → same.
4. Install the model in **Settings > Models > Diarisation**. Wait for installation
   to finish, then retry transcription. Jobs only load locally installed files.

If you skip the license acceptance, the HF API returns `401 Unauthorized` or `403 Forbidden` for
the download — the same error class the in-app **"Open docs for this error"**
button deeplinks to.

## Fallback behaviour

When diarization files are missing or the installed runtime cannot load,
Voiceym falls back to a **silence-gap heuristic**. The job warning explains
whether to install/repair the model or inspect the backend log. Jobs never
download missing weights.

The heuristic is not as accurate as pyannote — speakers with similar pitch
or rapid turn-taking conversation get merged — but it lets the dub finish
end-to-end instead of erroring.

## HF token requirement

An HF token and accepted repository access are required to install the gated
pyannote bundle. Once its pipeline, segmentation and embedding files are cached,
inference works offline without retaining the token. See
[Hugging Face token setup](../setup/huggingface-token.md).

## Troubleshooting

- Install returns 401/403: verify that the token belongs to the account with
  access to both gated repositories.
- Missing files: install or repair from Settings > Models > Diarisation.
- Installed model fails to load: inspect Settings > Logs > Backend. A runtime
  failure does not by itself indicate a licence or token problem.

## Local installation and repair

Install pyannote from Settings > Models > Diarisation after completing the access steps above. The install includes its segmentation and speaker-embedding checkpoints. A pipeline configuration alone is not a complete installation; repair also retrieves missing dependencies.

Dubbing resolves all three files from the local cache and does not download models during a job. Once installed, the bundle can run without retaining an HF token. Missing files prompt installation or repair; transcription continues with the existing silence-gap fallback and its accuracy caveat. A runtime load failure is reported separately from missing files.

## Runtime

pyannote (MIT) is the only diarisation runtime. The audio.cpp Sortformer runtime
was removed because its model is licensed for non-commercial use only; a saved
Sortformer choice from an older release uses pyannote.
