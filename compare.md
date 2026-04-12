# Linux Offline STT Dictation — Comparison

## Overview

| | **Paulie** | **Speed of Sound** | **Nerd Dictation** | **Vocalinux** | **Speech Note** | **Talon Voice** |
|---|---|---|---|---|---|---|
| **Model** | Parakeet-TDT-0.6B (ONNX INT8) | Whisper / Parakeet / Canary (Sherpa ONNX) | Vosk | whisper.cpp / Whisper / Vosk | Whisper / Vosk / Coqui / april-asr | Conformer (proprietary) + Whisper beta |
| **VAD** | Silero-VAD, tunable | Bundled in Sherpa, no knobs | None — manual start/stop | Built-in | Unclear | Built-in |
| **Auto silence detection** | Yes (1 s default) | Yes | No | Yes | Unclear | Yes |
| **Text injection** | ydotool (uinput, 1 ms/char) | XDG Remote Desktop Portal | xdotool / wtype / ydotool | IBus / wtype / xdotool | Clipboard / paste | Proprietary keyboard sim |
| **Wayland support** | Yes (uinput, no portal) | Yes (portal) | Partial (wtype) | Yes | Yes (Qt) | No — X11 only |
| **Immutable distro friendly** | Yes (pipx, uinput) | Yes (Flatpak) | Moderate | Moderate | Yes (Flatpak) | No |
| **Recording limit** | 120 s | 30 s | Unlimited | Unlimited | Unlimited | Unlimited |
| **GUI** | Minimal overlay only | Full GTK4 app | None | System tray | Full Qt app | Full app + REPL |
| **LLM polishing** | No | Yes (Claude / GPT / Ollama etc.) | No | No | No | No |
| **Multi-language** | No | Yes (runtime switch) | Yes (20+ Vosk models) | Yes | Yes (100+) | Yes |
| **Installation** | `pipx install .` | Flatpak / Snap / AppImage / deb / rpm | Single Python file + pip | Interactive installer / Flatpak | Flatpak / AUR | Binary download |
| **Cost** | Free / OSS | Free / OSS | Free / OSS | Free / OSS | Free / OSS | Free + $25/mo beta |
| **Accuracy** | High (Parakeet) | High (Whisper/Parakeet) | Lower (Vosk) | High (Whisper) | High (Whisper) | High (Conformer) |
| **Hands-free computer control** | No | No | No | No | No | Yes — killer feature |

---

## Pros & Cons

### Paulie

**Pros**
- Single hotkey, fully automatic VAD-driven stop — no second press needed
- ydotool/uinput injection works everywhere on Wayland without an XDG portal or compositor support
- 120 s recording ceiling handles long-form dictation
- Tunable VAD via env vars (`PAULIE_SILENCE_S`, `PAULIE_VAD_THRESHOLD`)
- Lightweight daemon — models loaded once, zero startup cost per dictation
- Designed for immutable distros (Bazzite, Silverblue) — `pipx` install, no system packages
- Fully offline, no cloud option even by accident
- Text injected after transcription completes — one atomic paste, not a live stream

**Cons**
- No GUI settings panel — config via env vars only
- English only (Parakeet model)
- Requires `ydotoold` running as a system service
- No LLM post-processing
- No multi-language support

---

### Speed of Sound

**Pros**
- Full GTK4/Adwaita settings UI — no config files needed
- Multiple STT models (Whisper, Parakeet, Canary) selectable in preferences
- Optional LLM polishing (Claude, GPT, Ollama, vLLM, llama.cpp)
- Real-time language switching (Left/Right Shift) — strong bilingual support
- XDG Remote Desktop Portal injection — no background daemon, no elevated privileges
- Multiple packaging formats (Flatpak, Snap, AppImage, deb, rpm)
- Actively maintained with regular releases

**Cons**
- 30 s recording hard limit — cuts off long dictations
- Java 25 runtime required — heavier dependency footprint
- VAD has no user-facing tuning knobs
- No microphone selection UI — must change via OS sound settings
- Non-Latin scripts (Cyrillic, CJK, Arabic) can display as spaces without matching keyboard layout

---

### Nerd Dictation

**Pros**
- Absolute minimum footprint — single Python script, one pip dependency
- Zero background overhead — process exits between dictations
- Highly hackable — post-processing hooks written in plain Python
- 20+ language models (Vosk), each ~50 MB
- Works on X11 and Wayland (via wtype/ydotool/dotool)

**Cons**
- No VAD — requires manual `begin`/`end` commands or hotkey wiring
- Vosk accuracy noticeably lower than Whisper/Parakeet on modern hardware
- Lowercase output by default — capitalization requires custom rules
- Startup latency on slow storage (model load per invocation)

---

### Vocalinux

**Pros**
- Hardware autodetect installer — GPU acceleration via Vulkan if available
- Multiple STT engines (whisper.cpp, Whisper, Vosk) selectable
- Toggle or push-to-talk activation modes
- IBus integration for cleaner text insertion in some apps
- Flatpak available
- Actively maintained with nightly builds

**Cons**
- wtype injection can fail on some Wayland compositors, falling back to xdotool
- Whisper model sizes are large compared to Vosk
- Config stored in `~/.config/vocalinux/` — less portable

---

### Speech Note (dsnote)

**Pros**
- Multi-purpose: STT + Text-to-Speech + Machine Translation in one app
- Widest model selection (Whisper, Vosk, Coqui, april-asr)
- 100+ languages supported
- Graphical model browser with on-demand downloads
- Flatpak, AUR, openSUSE packages available

**Cons**
- Designed as a note-taking tool, not a system-wide dictation daemon
- Text injection via clipboard — less seamless for continuous dictation workflows
- Large Flatpak package (~1.2 GB)
- VAD implementation not clearly documented

---

### Talon Voice

**Pros**
- The only tool here that offers full hands-free computer control (commands, coding, app navigation)
- Mature ecosystem with community-built command sets
- Dragon NaturallySpeaking compatible
- High accuracy with proprietary Conformer engine
- Free public version available

**Cons**
- No Wayland support — X11 only, a hard blocker on modern Linux desktops
- Closed-source / proprietary
- Cutting-edge features locked behind $25/month Patreon beta
- Overkill for users who just want dictation

---

## Use Case Guide

| Use case | Best pick |
|---|---|
| Immutable/Wayland distro, minimal setup | **Paulie** |
| Mainstream desktop, want a proper GUI app | **Speed of Sound** or **Speech Note** |
| Bilingual user needing runtime language switch | **Speed of Sound** |
| 100+ language support | **Speech Note** |
| LLM cleanup of raw speech | **Speed of Sound** (only option) |
| Absolute minimum footprint / hackable | **Nerd Dictation** |
| Hands-free coding / full voice control | **Talon** (X11 only — significant trade-off) |
| Multi-purpose (dictation + TTS + translation) | **Speech Note** |
| GPU-accelerated transcription | **Vocalinux** |

---

## Architecture Comparison

### Text Injection Methods

| Method | Used by | Notes |
|---|---|---|
| `ydotool` (uinput) | Paulie, Nerd Dictation | Kernel-level, works everywhere on Wayland, requires `ydotoold` daemon |
| XDG Remote Desktop Portal | Speed of Sound | Standard Wayland approach, no daemon needed, requires portal-supporting DE |
| IBus | Vocalinux | Native input method, best Unicode support |
| `wtype` | Nerd Dictation, Vocalinux | Wayland-native but compositor-dependent |
| `xdotool` | Nerd Dictation, Vocalinux | X11 only |
| Clipboard / paste | Speech Note | Universal but requires app focus and paste action |
| Proprietary | Talon | X11 only |

### VAD Approaches

| Approach | Used by | Notes |
|---|---|---|
| Silero-VAD (dedicated, tunable) | Paulie | Explicit silence threshold and probability knobs |
| Bundled in Sherpa ONNX | Speed of Sound | No user-facing controls |
| Built-in (undocumented) | Vocalinux, Talon | Automatic, no tuning |
| None | Nerd Dictation | Manual start/stop required |

---

## Links

- [Paulie](https://github.com/) — this project
- [Speed of Sound](https://github.com/zugaldia/speedofsound)
- [Nerd Dictation](https://github.com/ideasman42/nerd-dictation)
- [Vocalinux](https://github.com/jatinkrmalik/vocalinux)
- [Speech Note](https://github.com/mkiol/dsnote)
- [Talon Voice](https://talonvoice.com/)
