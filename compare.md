# Linux Offline STT Dictation — Comparison

## Overview

| | **Paulie** | **Handy** | **Speed of Sound** | **Nerd Dictation** | **Vocalinux** | **Speech Note** | **Talon Voice** |
|---|---|---|---|---|---|---|---|
| **Model** | Parakeet-TDT-0.6B (ONNX INT8) — swappable | Whisper (whisper.cpp) / Parakeet V3 | Whisper / Parakeet / Canary (Sherpa ONNX) | Vosk | whisper.cpp / Whisper / Vosk | Whisper / Vosk / Coqui / april-asr | Conformer (proprietary) + Whisper beta |
| **VAD** | Silero-VAD, tunable | Silero-VAD | Bundled in Sherpa, no knobs | None — manual start/stop | Built-in | Unclear | Built-in |
| **Auto silence detection** | Yes (1 s default) | Yes | Yes | No | Yes | Unclear | Yes |
| **Cancel mid-dictation** | Yes (second hotkey press) | Yes (Escape key) | No | No | No | No | Yes |
| **Text injection** | ydotool (uinput) or clipboard + wl-copy/wtype | xdotool / wtype / dotool / clipboard | XDG Remote Desktop Portal | xdotool / wtype / ydotool | IBus / wtype / xdotool | Clipboard / paste | Proprietary keyboard sim |
| **Wayland support** | Yes (uinput, no portal) | Yes (wtype/dotool) | Yes (portal) | Partial (wtype) | Yes | Yes (Qt) | No — X11 only |
| **Immutable distro friendly** | Yes (pipx, uinput) | Moderate | Yes (Flatpak) | Moderate | Moderate | Yes (Flatpak) | No |
| **Recording limit** | Configurable (default 120 s) | Unlimited | 30 s | Unlimited | Unlimited | Unlimited | Unlimited |
| **GUI** | Minimal overlay + system tray | Full settings UI (Tauri/React) | Full GTK4 app | None | System tray | Full Qt app | Full app + REPL |
| **Config file** | Yes (TOML, `~/.config/paulie/paulie.conf`) | Yes (GUI) | Yes (GSettings) | No | Yes | Yes | Yes |
| **LLM polishing** | No | Yes (optional post-processing) | Yes (Claude / GPT / Ollama etc.) | No | No | No | No |
| **Multi-language** | Yes (25 EU langs default; 99+ with Whisper) | Yes (99+ with Whisper) | Yes (runtime switch) | Yes (20+ Vosk models) | Yes | Yes (100+) | Yes |
| **Cross-platform** | Linux only | Linux / macOS / Windows | Linux only | Linux only | Linux only | Linux / Sailfish | macOS / Windows / Linux (X11) |
| **Installation** | `./install.sh` or `pipx install .` | Installer / Homebrew / winget | Flatpak / Snap / AppImage / deb / rpm | Single Python file + pip | Interactive installer / Flatpak | Flatpak / AUR | Binary download |
| **Cost** | Free / OSS | Free / OSS | Free / OSS | Free / OSS | Free / OSS | Free / OSS | Free + $25/mo beta |
| **Accuracy** | High (Parakeet) | High (Whisper/Parakeet) | High (Whisper/Parakeet) | Lower (Vosk) | High (Whisper) | High (Whisper) | High (Conformer) |
| **Hands-free computer control** | No | No | No | No | No | No | Yes — killer feature |

---

## Pros & Cons

### Paulie

**Pros**
- Single hotkey, fully automatic VAD-driven stop — no second press needed
- Second hotkey press cancels mid-dictation immediately
- ydotool/uinput injection works everywhere on Wayland without an XDG portal or compositor support
- Clipboard injection mode (`inject_mode = "clipboard"`) available for apps that prefer paste — no ydotoold needed
- Recording ceiling is configurable (`max_record_s`) — set it as high as you need for long-form dictation
- TOML config file (`~/.config/paulie/paulie.conf`) with `--init-config` to generate defaults
- System tray icon — coloured dot reflects current state, shows last transcription, Quit action
- `paulie status` and `paulie-daemon --list-devices` for debugging without digging through logs
- Swappable models — Parakeet (25 EU langs, fast CPU), Whisper (99+ langs), GigaAM (Russian)
- One-command install script (`./install.sh`) handles everything including services and config
- Lightweight daemon — models loaded once, zero startup cost per dictation
- Fully offline, no cloud option even by accident

**Cons**
- Requires `ydotoold` running as a system service (or clipboard mode as workaround)
- No GUI settings panel — all config via file or env vars
- No LLM post-processing
- Tray icon requires AppIndicator extension on GNOME

---

### Handy (handy.computer)

**Pros**
- Cross-platform — Linux, macOS (with Apple Neural Engine acceleration), Windows
- Full settings UI built with Tauri/React — no config files needed
- Whisper and Parakeet V3 both supported; GPU acceleration automatic
- Custom word correction dictionary — great for names, jargon, domain terms
- Recording history with configurable retention
- Optional AI post-processing for text cleanup/reformatting
- Escape key cancel during recording
- Clipboard restoration after paste — doesn't clobber what you had copied
- Homebrew / winget packaging for macOS and Windows users

**Cons**
- Less Linux-native than Paulie or Speed of Sound — Wayland injection relies on wtype/dotool which can be compositor-dependent
- No system tray on Linux (overlay can be disabled but there's no persistent status indicator)
- No equivalent of `paulie status` or daemon health checks
- Electron/Tauri app — heavier than a lean Python daemon

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
| Long-form dictation (lectures, meeting notes) | **Paulie** (configurable ceiling) or **Handy** |
| Cross-platform (Linux + macOS + Windows) | **Handy** |
| macOS with Apple Silicon acceleration | **Handy** |
| Mainstream desktop, want a proper GUI app | **Speed of Sound** or **Speech Note** |
| Bilingual user needing runtime language switch | **Speed of Sound** |
| 100+ language support on Linux | **Speech Note** or **Paulie** (with Whisper model) |
| LLM cleanup of raw speech | **Speed of Sound** or **Handy** |
| Custom word correction (names, jargon) | **Handy** |
| Absolute minimum footprint / hackable | **Nerd Dictation** |
| Hands-free coding / full voice control | **Talon** (X11 only — significant trade-off) |
| Multi-purpose (dictation + TTS + translation) | **Speech Note** |
| GPU-accelerated transcription on Linux | **Vocalinux** |
| Russian language | **Paulie** (GigaAM model) or **Speech Note** |

---

## Architecture Comparison

### Text Injection Methods

| Method | Used by | Notes |
|---|---|---|
| `ydotool` (uinput) | Paulie (default), Nerd Dictation | Kernel-level, works everywhere on Wayland, requires `ydotoold` daemon |
| `wl-copy` + `wtype` (clipboard) | Paulie (opt-in), Handy | No ydotoold needed; atomic paste; requires wl-clipboard + wtype |
| XDG Remote Desktop Portal | Speed of Sound | Standard Wayland approach, no daemon, requires portal-supporting DE |
| IBus | Vocalinux | Native input method, best Unicode support |
| `wtype` / `dotool` | Handy, Nerd Dictation, Vocalinux | Wayland-native but compositor-dependent |
| `xdotool` | Handy, Nerd Dictation, Vocalinux | X11 only |
| Clipboard / paste | Speech Note, Handy (fallback) | Universal but requires app focus |
| Proprietary | Talon | X11 only |

### VAD Approaches

| Approach | Used by | Notes |
|---|---|---|
| Silero-VAD (dedicated, tunable) | Paulie, Handy | Paulie exposes threshold and silence duration as config knobs |
| Bundled in Sherpa ONNX | Speed of Sound | No user-facing controls |
| Built-in (undocumented) | Vocalinux, Talon | Automatic, no tuning |
| None | Nerd Dictation | Manual start/stop required |

### Model Support

| Model family | Paulie | Handy | Speed of Sound | Nerd Dictation | Vocalinux | Speech Note |
|---|---|---|---|---|---|---|
| Parakeet (25 EU langs) | ✓ default | ✓ | ✓ | — | — | — |
| Whisper (99+ langs) | ✓ swap | ✓ default | ✓ | — | ✓ | ✓ |
| GigaAM (Russian) | ✓ swap | — | — | — | — | — |
| Vosk | — | — | — | ✓ | ✓ | ✓ |
| Canary | ✓ swap | — | ✓ | — | — | — |

---

## Links

- [Paulie](https://github.com/) — this project
- [Handy](https://handy.computer) — [GitHub](https://github.com/cjpais/Handy)
- [Speed of Sound](https://github.com/zugaldia/speedofsound)
- [Nerd Dictation](https://github.com/ideasman42/nerd-dictation)
- [Vocalinux](https://github.com/jatinkrmalik/vocalinux)
- [Speech Note](https://github.com/mkiol/dsnote)
- [Talon Voice](https://talonvoice.com/)
