# Manual test cases

Step-by-step checks for a release or a big change, on **macOS**, **Windows**
and **iOS**. Automated tests cover the code; these cover what a person sees
and hears. Commands to set up and run each platform are in the
[README](../README.md#commands-by-platform).

How to use this file:

- Work top to bottom. Each case says where it applies: **All** (macOS and
  Windows desktop), **macOS**, **Windows** or **iOS**.
- Mark each case **Pass**, **Fail** or **Skip** in your own copy, and note
  the build (commit or version), the machine and its memory.
- A failure is anything that differs from **Expected**, including a missing
  explanation or a dead end.

## Test record

Fill this in at the top of your copy.

| Field | Value |
|---|---|
| Tester | |
| Date | |
| Voiceym version or commit | |
| macOS computer (chip, memory, macOS version) | |
| Windows computer (CPU, GPU, memory, Windows version) | |
| iOS device or simulator (model, iOS version) | |
| Language model used for Talk | |

## Automated checks first

Run these from the `voiceym` folder before the manual cases. Each should
finish without failures. On Windows, join commands with `;` instead of `&&`.

| ID | Platform | Command | Expected |
|---|---|---|---|
| AUTO-01 | macOS, Windows | `bun install && bun run setup:api` | Finishes; Windows picks CUDA with an NVIDIA GPU, otherwise CPU |
| AUTO-02 | macOS, Windows | `uv run pytest tests/ backend/tests/ -q` (with `HF_HUB_OFFLINE=1`) | All tests pass |
| AUTO-03 | macOS, Windows | `bun run typecheck && bun run lint` | No type errors; lint reports no errors |
| AUTO-04 | macOS, Windows | `bun run test` | All app unit tests and translation checks pass |
| AUTO-05 | macOS, Windows | `bun run --cwd electron test:desktop-ui` | Desktop UI tests pass and end on Home |
| AUTO-06 | macOS, Windows | `uv run python scripts/check-docs-drift.py` | Docs match the engines |
| AUTO-07 | iOS | `mobile/ios/scripts/check-swift.sh build && mobile/ios/scripts/check-swift.sh test` | The Swift package builds and its tests pass |
| AUTO-08 | iOS | `xcodebuild -project mobile/ios/Voiceym/Voiceym.xcodeproj -scheme Voiceym -destination 'platform=iOS Simulator,name=iPhone 16' test` | The app builds and its UI tests pass |

## Before you start

| Need | Notes |
|---|---|
| A clean user account or a fresh install folder | So first-run setup runs |
| Internet for the first run | Model downloads; everything after works offline |
| Ollama with one model (`ollama pull qwen3:1.7b`) | For Talk |
| A microphone and headphones (AirPods are fine) | For Talk and dictation |
| A 5–15 second clean voice recording (WAV or MP3) | For cloning |
| A short video (under 1 minute) with speech | For dubbing |
| iOS: a Mac with Xcode, an iPhone or simulator on iOS 17+ | For the iOS cases |

## 1. Install and first run

| ID | Platform | Steps | Expected |
|---|---|---|---|
| INS-01 | macOS | Run the setup commands from the README, then `bun run dev`. | The app window opens. No error dialog. |
| INS-02 | Windows | Same in PowerShell. | The app window opens. No error dialog. |
| INS-03 | All | Complete first-run setup (System check → Model packs → Privacy → Enter studio). | Each step explains itself; Kokoro (about 95 MB) downloads; you land on Home. |
| INS-04 | All | On the Privacy step, choose **No** for analytics. | Setup continues; the app works fully. |
| INS-05 | All | Quit and reopen the app. | Opens on Home without repeating setup. |
| INS-06 | Windows | Set `OMNIVOICE_ENGINES_DIR` to another drive, then install an engine from Settings → Models. | The engine's files land on that drive. |
| INS-07 | macOS | On Apple Silicon, open Talk once and wait a minute, then check `~/.cache/huggingface/hub/models--onnx-community--Kokoro-82M-v1.0-ONNX`. | `onnx/model_fp16.onnx` is present (the faster voice model for ARM). |

## 2. Home and navigation

| ID | Platform | Steps | Expected |
|---|---|---|---|
| NAV-01 | All | Look at Home. | "What would you like to do?" with eight task cards, each with a one-line description, plus a More row. |
| NAV-02 | All | Click each task card. | Each opens the right page (Talk, Text to speech, Clone, Design, Dub, Transcribe, Audiobook, Stories). |
| NAV-03 | All | Rest the pointer on the left icon rail. | The full sidebar slides over the page (the page does not shift). |
| NAV-04 | All | Move the pointer away from the sidebar. | It closes after a moment. A quick pass over the rail does not make it flicker. |
| NAV-05 | All | With the sidebar open, hover each item. | A hint says what the page does. Items are grouped under Create, Library and More. |
| NAV-06 | All | Click a page in the open sidebar. | That page opens and the sidebar closes. |
| NAV-07 | All | Press Tab until the rail's top button is focused, press Enter, then Escape. | Enter opens the sidebar with focus inside; Escape closes it. |
| NAV-08 | All | Hover the rail with the sidebar closed. | Each icon's tooltip shows its name and what it does. |
| NAV-09 | All | Open the sidebar, open a popup from it (for example the device pill), then press Escape. | Escape closes only the popup; the sidebar stays open. |
| NAV-10 | All | Switch the app language to Arabic (Settings → General). | Text is right-to-left and the sidebar opens from the right edge. |
| NAV-11 | All | Press Cmd/Ctrl+K and type "talk". | The command palette finds Talk and opens it. |
| NAV-12 | All | Resize the window to about 700 px wide. | Home cards reflow into fewer columns; nothing overlaps or scrolls sideways. |
| NAV-13 | All | Open the sidebar and drag its right edge. | The sidebar gets wider or narrower and keeps that width next time. |
| NAV-14 | All | Collapse and reopen a page's own side panel with its panel button. | The panel collapses to a slim strip and reopens at the same width. |

## 3. Talk (voice agent)

Run these with the Ollama model selected in **Talk → Agent setup → Language
model**.

| ID | Platform | Steps | Expected |
|---|---|---|---|
| TLK-01 | All | Press the mic, ask in English: "What is the capital of France?" | It answers out loud in English. The transcript shows both turns. |
| TLK-02 | All | Time from the end of your question to the first spoken word. | Usually 2–5 seconds once warmed up (first turn may be slower while models load). |
| TLK-03 | All | Ask for a three-sentence answer. | Sentences play back to back with no 2–3 second gaps. |
| TLK-04 | All | Watch the V-shaped bars while you speak and while it replies. | The V keeps its shape; bars grow with the voice and never shrink below rest. |
| TLK-05 | All | Talk over it while it is answering. | It stops speaking and listens (with **Let me interrupt it** on). |
| TLK-06 | All | Pause mid-sentence for under a second, then finish. | It waits for you to finish instead of answering half a question. |
| TLK-07 | All | Say only "uh" or "hmm". | It does not treat that as a question. |
| TLK-08 | All | Set Language to **Auto** and ask in Hindi. | It hears Hindi and answers in Hindi with a Hindi voice. |
| TLK-09 | All | Set Language to **Auto** and ask in Telugu. | It hears Telugu and answers in Telugu (Indic-TTS downloads the Telugu voice once, about 1.5 GB; until then it says so and answers in text). |
| TLK-10 | All | Mix languages in one sentence (Telugu or Hindi with English words). | It answers in the same mix; English parts use the English voice. |
| TLK-11 | All | Set Language to **English** and ask a question. | Recognition is faster than Auto and the answer is in English. |
| TLK-12 | All | Turn on **Agent speaks first** and start a new conversation. | It greets you first. |
| TLK-13 | All | Type a message in **Type a message** instead of speaking. | It answers out loud as if you had spoken. |
| TLK-14 | All | Stop Ollama, then ask a question. | A clear message says the language model is not reachable; nothing freezes. |
| TLK-15 | All | Use AirPods or a quiet microphone. | It still hears you. |

## 4. Text to speech and voices

| ID | Platform | Steps | Expected |
|---|---|---|---|
| TTS-01 | All | Open **Text to speech**, pick the default voice, type two sentences, press **Synthesize audio**. | Audio plays; a take appears in Recent takes. |
| TTS-02 | All | Generate Hindi text with Kokoro. | It reads Hindi with a native Hindi voice. |
| TTS-03 | All | Play, pause, seek and download the take. | Each control works; the downloaded file plays elsewhere. |
| CLN-01 | All | Add a new voice from your 5–15 second recording (upload or record). | The voice is saved and shows in My voices. |
| CLN-02 | All | Generate speech with the cloned voice (needs Chatterbox or VoxCPM2 installed). | It sounds like the recording. Without a cloning engine, a message explains what to install. |
| DSN-01 | All | Open **Design a voice** with only Kokoro installed. | A message names VoxCPM2 / Indic Parler-TTS and offers **Change engine**. |
| DSN-02 | All | Install VoxCPM2, describe a voice ("a warm elderly storyteller"), generate. | A matching voice speaks; it can be saved as a profile. |
| CLN-03 | All | Rename a saved voice, then delete it. | The new name shows in every voice picker; delete removes it everywhere. |
| GAL-01 | All | Open **Voice gallery** and preview a voice. | The preview plays; **Use voice** adds it to My voices. |

## 5. Dubbing, transcription and long-form

| ID | Platform | Steps | Expected |
|---|---|---|---|
| DUB-01 | All | Open **Dub a video**, add your short video, pick a target language, run all three steps. | A dubbed video is produced and plays. |
| DUB-02 | All | Abort a running dub. | It stops cleanly and can be restarted. |
| TRN-01 | All | Open **Transcribe**, press **Start dictation**, speak, stop. | The text appears and is saved in History. |
| TRN-02 | All | Upload an audio file to transcribe. | A transcript with timings appears. |
| LNG-01 | All | Open **Audiobook**, load the sample, generate one chapter. | Audio is produced per chapter and can be exported. |
| LNG-02 | All | Open **Stories**, add two lines with different voices, generate. | Each line uses its own voice. |
| LNG-03 | All | Start a long render, switch to another page, come back, then stop and resume it. | The render keeps going in the background and resumes where it stopped. |
| DUB-03 | All | **Batch Dubbing**: queue two files into two languages. | Four jobs run, each with visible progress. |
| WFL-01 | All | **Workflows**: build Speak → Normalize on the canvas and run it over two scripts. | Two clips are produced and can be exported. |
| WFL-02 | All | Make one step fail, fix the cause and press **Retry**. | Only the failed step reruns; finished clips are reused. |
| PRJ-01 | All | Save a dubbing project, quit, and reopen it from **Projects**. | The project opens with its edits. |

## 6. Settings, models and storage

| ID | Platform | Steps | Expected |
|---|---|---|---|
| SET-01 | All | Open **Settings → Models → Text to speech**. | Only commercial-use engines are offered: Kokoro, KittenTTS, VoxCPM2, Chatterbox, Indic Parler-TTS, Indic-TTS. |
| SET-02 | All | Install and then uninstall Chatterbox. | Install shows progress; uninstall frees its space; other engines keep working. |
| SET-03 | All | Open **Settings → Models → Diarisation**. | Only pyannote is listed. |
| SET-04 | All | Open **Settings → Performance** and change Speed / quality. | The choice applies and is remembered after restart. |
| SET-05 | All | Open **Settings → Storage**. | Sizes of models and engines are shown; clearing a cache frees space. |
| SET-06 | All | Switch light/dark theme and app language. | The whole app updates without a restart. |
| SET-07 | All | Disconnect from the internet and use Talk, Text to speech and Transcribe. | Everything installed keeps working offline. |

## 7. Windows-specific

| ID | Platform | Steps | Expected |
|---|---|---|---|
| WIN-01 | Windows | On a PC without an NVIDIA GPU, run first-run setup. | It installs the CPU build (about 5 GB free disk) and explains the speed trade-off. |
| WIN-02 | Windows | On an NVIDIA PC, check **Settings → Performance → GPU acceleration**. | It names the GPU and shows engines using CUDA. |
| WIN-03 | Windows | Install to a path with spaces (for example `C:\Users\First Last\`). | Setup and engine installs still work. |
| WIN-04 | Windows | Run `bun run dist`. | An NSIS installer appears in `electron\release\`; it installs and launches. |
| WIN-05 | Windows | Use the window's own minimize, maximize and close buttons. | They work and do not overlap app controls. |

## 8. macOS-specific

| ID | Platform | Steps | Expected |
|---|---|---|---|
| MAC-01 | macOS | Check the title bar. | The traffic lights do not overlap the sidebar or page titles. |
| MAC-02 | macOS | First use of the microphone. | macOS asks for microphone permission once; after allowing, Talk hears you. |
| MAC-03 | macOS | On an 8 GB Mac, hold a 3-minute Talk conversation. | No freezes; memory pressure stays out of the red in Activity Monitor. |
| MAC-04 | macOS | Run `bun run dist`. | A DMG/zip appears in `electron/release/`; the app installs and launches. |
| MAC-05 | macOS | Intel Mac only: open the app. | It explains that the backend must run on another machine, and can connect to one. |

## 9. iOS companion app

Start Voiceym on a computer first, open its sidebar, click the **Local
device** pill and choose **Network**.

| ID | Platform | Steps | Expected |
|---|---|---|---|
| IOS-01 | iOS | Build and run the **Voiceym** scheme on a simulator or iPhone. | The app launches and shows the analytics choice with equal Yes/No buttons. |
| IOS-02 | iOS | Swipe the analytics sheet away. | It counts as No; the app continues. |
| IOS-03 | iOS | Tap **Scan QR code** and scan the desktop's QR code. | The phone connects and shows the computer's name. |
| IOS-04 | iOS | Instead, tap **Enter address**, type the address and 6-digit PIN. | It connects. A wrong PIN shows a clear error and keeps the previous connection. |
| IOS-05 | iOS | **Speak**: type text, pick a voice, generate. | Audio plays with a waveform; you can share or save it. |
| IOS-06 | iOS | **Voices**: search, preview, record a new voice (allow the microphone). | Search filters; previews play; the new voice appears on the computer too. |
| IOS-07 | iOS | **Dub**: pick a short video from Photos/Files, choose a language, start. | Progress shows; the finished dub can be saved or shared. |
| IOS-08 | iOS | **Dictate**: tap to start, speak, tap to stop. Then hold, speak, release. | Text appears live and the final transcript replaces it. |
| IOS-09 | iOS | Put the app in the background during a dub upload, then reopen. | The upload continues or reports clearly what happened. |
| IOS-10 | iOS | Turn the computer off, then use the app. | It says the host is unreachable; nothing crashes. |
| IOS-11 | iOS | **Settings**: change theme and language; tap **Disconnect**. | Theme and language apply; Disconnect clears the saved connection. |
| IOS-12 | iOS | Use the largest Dynamic Type size and VoiceOver. | Text scales and scrolls; every control has a spoken label. |
| IOS-13 | iOS | Rotate an iPad and use split view. | Layout adapts without clipped controls. |

## 10. Accessibility and reliability

| ID | Platform | Steps | Expected |
|---|---|---|---|
| ACC-01 | All | Use Home and the sidebar with the keyboard only. | Every control can be reached and shows a visible focus ring. |
| ACC-02 | All | Turn on VoiceOver (macOS) or Narrator (Windows) on Home. | Headings, links and buttons are read with their names. |
| REL-01 | All | Quit the app during a model download, then reopen it. | The download resumes or can be restarted; nothing is left half-installed. |

## Sign-off

| Platform | Passed | Failed | Skipped | Ready to release? |
|---|---|---|---|---|
| macOS | | | | |
| Windows | | | | |
| iOS | | | | |

## Reporting a failure

Include the case ID, platform, build, the exact steps, what you expected and
what happened, plus a screenshot and the backend log
(**Settings → Logs → Copy**). Open an issue at
<https://github.com/mnikhilsrikar2512/voiceym/issues>.
