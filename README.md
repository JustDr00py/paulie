# Paulie — Local Offline STT for Linux

Push-to-talk dictation powered by **NVIDIA Parakeet-TDT-0.6B-V3** and **silero-VAD**.  
Transcribes speech and types it into the focused Wayland window via `ydotool`.  
Supports 25 European languages out of the box; swap to a Whisper model for 99+ languages.

---

## How It Works

Paulie runs as two processes:

- **`paulie-daemon`** — starts at login, loads models once (~6 s), then waits.
- **`paulie`** — bound to your hotkey; connects to the daemon and exits instantly.

```
[login] paulie-daemon starts
              │
              ▼
        models loaded — waiting for trigger
              │
[hotkey] paulie ──► trigger sent via socket
              │
              ▼
        [ PyQt6 overlay: "Listening…" ]  ← teal bars, waiting for speech
              │
        [ silero-VAD + sounddevice ]  ← microphone
              │   speech detected
              ▼
        [ overlay: "Recording…" ]  ← white bars, capturing speech
              │
        [ silero-VAD detects 1.0 s silence  ]
              │                              ╲
              │                     [hotkey again] → cancel → overlay hides
              ▼
        [ overlay: "Processing…" ]  ← amber bars, running inference
              │
              ▼
        [ Parakeet-TDT-0.6B-V3 inference (local, offline) ]
              │
              ▼
        [ ydotool → active Wayland window ]
              │
              ▼
        [ overlay hides — daemon waits for next trigger ]
```

---

## Quick Install

```bash
git clone <repo-url>
cd paulie
./install.sh
```

That's it. The script handles system dependencies, pipx, the Paulie package,
the default config file, the ydotoold system service, and the paulie-daemon
user service. At the end it prints the one manual step: registering your
global hotkey.

```bash
./install.sh --upgrade    # reinstall from source, keep existing config
./install.sh --uninstall  # stop services and remove the package
```

Supported systems: Bazzite / Fedora Silverblue (rpm-ostree), Fedora (dnf), Debian/Ubuntu (apt).

> **Note — rpm-ostree:** On immutable distros the script installs system
> packages via `rpm-ostree` and prompts for a reboot.  After rebooting, re-run
> `./install.sh` and it will continue from where it left off.

---

## Manual Installation

The sections below document each step individually, for reference or for systems
the install script doesn't fully support.

---

## Prerequisites

### 1  Install ydotool

`ydotool` injects keystrokes via the kernel's `uinput` device — no compositor
protocol required, so it works on KDE Plasma 6 Wayland without any security
policy changes.

**Install on the host (Bazzite):**
```bash
rpm-ostree install ydotool
systemctl reboot
```

**Run ydotoold as a system service (starts automatically at boot):**

The daemon creates a socket at `~/.ydotool_socket` by default on this setup.
Paulie auto-detects the socket location (see [Configuration](#configuration)).
Running it as a systemd system service avoids needing sudo at login and ensures
it starts before your session.

```bash
sudo systemctl edit --force --full ydotoold
```

Paste the following, then save and close:

```ini
[Unit]
Description=ydotool input automation daemon
After=local-fs.target

[Service]
Type=simple
ExecStart=/usr/bin/ydotoold --socket-path=/var/home/sysadmin/.ydotool_socket --socket-own=1000:1000
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
```

Enable and start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ydotoold
```

**Verify it works:**
```bash
ls ~/.ydotool_socket
YDOTOOL_SOCKET="$HOME/.ydotool_socket" ydotool type -- "hello"
```

---

### 2  Install Paulie

**1. Install build dependencies:**
```bash
rpm-ostree install python3-devel gcc gcc-c++ cmake ninja-build portaudio-devel
systemctl reboot
```

**2. Install Paulie via pipx:**
```bash
pip install --user pipx
pipx ensurepath
cd /path/to/paulie
pipx install .
```

**3. Generate the default config file:**
```bash
paulie-daemon --init-config
```

This writes a commented `~/.config/paulie/paulie.conf` with all available
options and their defaults.  Edit it to taste before starting the daemon.
If the file already exists the command exits without overwriting it.

**3. (Optional) Use CUDA torch for faster inference:**
```bash
~/.local/share/pipx/venvs/paulie/bin/pip install \
    torch --index-url https://download.pytorch.org/whl/cu124 --upgrade
```

---

### 3  First run — model download

Run the daemon manually once so you can watch the model download progress:

```bash
paulie-daemon
```

On first run, the Parakeet-TDT-0.6B-V3 ONNX weights (~640 MB) are downloaded
automatically from HuggingFace Hub and cached in `~/.cache/huggingface/hub/`.

Wait for:
```
Models ready. Paulie daemon running.
```

Then test the trigger in a second terminal:
```bash
paulie
```

Subsequent startups load from cache and are ready in a few seconds.

---

## Autostart via systemd

Both daemons are managed as systemd services so they start automatically and
can be monitored with standard `systemctl` and `journalctl` commands.

`ydotoold` is already set up as a system service (see Prerequisites above).

**Set up paulie-daemon as a user service:**

```bash
mkdir -p ~/.config/systemd/user
```

Create `~/.config/systemd/user/paulie-daemon.service`:

```ini
[Unit]
Description=Paulie STT daemon
After=graphical-session.target

[Service]
Type=simple
ExecStart=/var/home/sysadmin/.local/bin/paulie-daemon
Restart=on-failure
RestartSec=3
Environment=QT_QPA_PLATFORM=wayland
Environment=WAYLAND_DISPLAY=wayland-0
Environment=YDOTOOL_SOCKET=/var/home/sysadmin/.ydotool_socket

[Install]
WantedBy=graphical-session.target
```

> **Note — `WAYLAND_DISPLAY`:** systemd user services do not inherit the
> compositor's environment automatically.  If the overlay never appears, run
> `systemctl --user import-environment WAYLAND_DISPLAY XDG_RUNTIME_DIR` once
> per session (add it to your shell's autostart), or set
> `WAYLAND_DISPLAY=wayland-0` explicitly in the service file as shown above.

Enable and start it:

```bash
systemctl --user daemon-reload
systemctl --user enable --now paulie-daemon
```

**View logs:**
```bash
journalctl --user -u paulie-daemon -f
```

To set environment variables (e.g. custom silence duration), add them to the
`[Service]` section:
```ini
Environment=QT_QPA_PLATFORM=wayland
Environment=PAULIE_SILENCE_S=0.8
```

---

## Registering a Global Shortcut

### KDE Plasma (Wayland)

1. Open **System Settings → Shortcuts → Custom Shortcuts**.
2. Click **Edit → New → Global Shortcut → Command/URL**.
3. Set:
   - **Name**: `Paulie STT`
   - **Trigger**: your hotkey (e.g. `Meta+Alt+P`)
   - **Action**: `/home/<you>/.local/bin/paulie`  
     *(confirm path with `which paulie`)*
4. Click **Apply**.

### GNOME (Wayland)

1. Open **Settings → Keyboard → View and Customize Shortcuts → Custom Shortcuts**.
2. Click **+**.
3. Set:
   - **Name**: `Paulie STT`
   - **Command**: `/home/<you>/.local/bin/paulie`
   - **Shortcut**: your chosen key combination.
4. Click **Add**.

---

## Configuration

Paulie can be configured via a TOML config file **or** environment variables.
Environment variables always win — the config file only fills in values not
already set in the environment.

### Config file (recommended)

Generate the default file with all options pre-filled:
```bash
paulie-daemon --init-config
```

Or create `~/.config/paulie/paulie.conf` manually:

```toml
# ~/.config/paulie/paulie.conf
# All keys are optional — omit any you want to leave at the default.

silence_s     = 1.0      # seconds of silence before recording stops
vad_threshold = 0.45     # silero-VAD speech probability cutoff (0.0–1.0)
max_record_s  = 120.0    # hard ceiling on recording duration in seconds
# model  = "nemo-parakeet-tdt-0.6b-v3"   # onnx-asr model name
# device = "HDA Intel PCH"               # mic name substring or integer index
```

After editing, restart the daemon:
```bash
systemctl --user restart paulie-daemon
```

### Environment variables

Environment variables override the config file.  Set them in your shell rc or
in the `[Service]` section of `~/.config/systemd/user/paulie-daemon.service`.

| Variable | Default | Description |
|---|---|---|
| `PAULIE_SILENCE_S` | `1.0` | Seconds of silence before recording stops |
| `PAULIE_VAD_THRESHOLD` | `0.45` | silero-VAD speech probability cutoff (0.0–1.0) |
| `PAULIE_MAX_RECORD_S` | `120.0` | Hard ceiling on recording duration in seconds |
| `PAULIE_MODEL` | `nemo-parakeet-tdt-0.6b-v3` | onnx-asr model name |
| `PAULIE_DEVICE` | system default | `sounddevice` input device — name substring or integer index |
| `PAULIE_INJECT` | `ydotool` | Injection mode: `ydotool` or `clipboard` |
| `PAULIE_MODE` | `single` | Dictation mode: `single` or `utterance` *(experimental)* |
| `PAULIE_UTTERANCE_PAUSE_S` | `0.5` | Utterance mode: silence between sentences (seconds) |
| `PAULIE_CONFIG` | `~/.config/paulie/paulie.conf` | Override the config file path |
| `YDOTOOL_SOCKET` | auto-detected¹ | Path to the ydotoold socket |
| `WAYLAND_DISPLAY` | inherited | Wayland compositor socket — required when running under systemd |
| `XDG_RUNTIME_DIR` | inherited | User runtime directory — used for the Paulie IPC socket path² |

**¹ `YDOTOOL_SOCKET` auto-detection order:**
1. `$YDOTOOL_SOCKET` if set explicitly
2. `~/.ydotool_socket` ← default for ydotoold running as a system service (this setup)
3. `$XDG_RUNTIME_DIR/ydotool_socket` ← default for ydotoold running as a user service
4. `/tmp/.ydotool_socket` ← legacy fallback

Set `YDOTOOL_SOCKET` explicitly in the service file to skip probing.

**² Paulie IPC socket:** Paulie places its own trigger socket at
`$XDG_RUNTIME_DIR/paulie-{uid}.sock` (`/run/user/1000/paulie-1000.sock`).
This directory is mode `0700` (owner-only), which is more secure than `/tmp`.
If `XDG_RUNTIME_DIR` is not set, the socket falls back to `/tmp/paulie-{uid}.sock`.

To apply environment variable changes to the autostart daemon:
```bash
systemctl --user daemon-reload && systemctl --user restart paulie-daemon
```

---

## Supported Models

All models are downloaded automatically from HuggingFace on first use and
cached in `~/.cache/huggingface/hub/`. Set the model in `paulie.conf` or via
`PAULIE_MODEL`.

### English / European (25 languages)

| Model | Size | Languages | Notes |
|---|---|---|---|
| `nemo-parakeet-tdt-0.6b-v3` | 640 MB | 25 EU langs | **Default.** Fast, accurate, recommended for most users |
| `nemo-parakeet-tdt-0.6b-v2` | 640 MB | English | Slightly higher English-only accuracy than v3 |
| `nemo-parakeet-rnnt-0.6b` | 620 MB | English | RNN-T decoder variant |
| `nemo-parakeet-ctc-0.6b` | 620 MB | English | CTC decoder — simplest, lowest latency |
| `nemo-canary-1b-v2` | 980 MB | 25 EU langs | Highest accuracy; noticeably slower on CPU |

The 25 languages supported by v3 and Canary include English, German, French,
Spanish, Italian, Portuguese, Dutch, Polish, Russian, and other major European
languages.

### Multilingual (99+ languages)

| Model | Size | Notes |
|---|---|---|
| `onnx-community/whisper-tiny` | 39 MB | Fastest; lower accuracy — good for quick tasks |
| `onnx-community/whisper-base` | 140 MB | Good balance of speed and accuracy |
| `onnx-community/whisper-small` | 367 MB | Better accuracy, still reasonable on CPU |
| `onnx-community/whisper-large-v3-turbo` | 809 MB | Near large-model quality at lower cost |

Whisper models are significantly slower than Parakeet on CPU for English, but
are the best choice for languages outside the 25 supported by Parakeet.

### Russian

| Model | Size | Notes |
|---|---|---|
| `gigaam-v3-rnnt` | 220 MB | Best accuracy for Russian (4.39% WER) |
| `gigaam-v3-ctc` | 220 MB | CTC variant — slightly faster |
| `gigaam-v3-e2e-rnnt` | 220 MB | Includes automatic punctuation and text normalisation |
| `gigaam-v3-e2e-ctc` | 220 MB | CTC variant with punctuation |

### Switching models

Edit `~/.config/paulie/paulie.conf`:
```toml
model = "onnx-community/whisper-base"
```

Then restart the daemon:
```bash
systemctl --user restart paulie-daemon
```

The new model is downloaded on the first recording after restart. Old model
weights remain in `~/.cache/huggingface/hub/` and are not removed automatically.

---

## Commands

### `paulie` — hotkey client

```bash
paulie              # trigger dictation (or cancel if already recording)
paulie status       # print daemon status, socket path, and config location
```

### `paulie-daemon` — persistent daemon

```bash
paulie-daemon                  # start the daemon (add to autostart)
paulie-daemon --init-config    # write default config to ~/.config/paulie/paulie.conf
paulie-daemon --list-devices   # list available microphone inputs
```

### Utterance mode (per-sentence injection) ⚠️ Experimental

> **Experimental:** utterance mode is functional but may behave unexpectedly
> depending on microphone quality, background noise, and VAD tuning.  Single
> mode is recommended for everyday use.

In utterance mode the mic stays open after the first hotkey press.  Each time
you pause for `utterance_pause_s` (default 0.5 s), that sentence is transcribed
and injected immediately.  The session ends when you pause for `silence_s`
(recommend 2.0 s) or press the hotkey again to cancel.

Enable it in `paulie.conf`:

```toml
mode              = "utterance"
utterance_pause_s = 0.5    # pause between sentences
silence_s         = 2.0    # longer pause ends the session
```

The overlay stays in **Recording…** state throughout.  Text appears
incrementally as each sentence completes rather than all at once at the end.

> **Note on accuracy:** Parakeet performs best on full sentences.  Very short
> clips (< 1 s) may produce lower accuracy than single mode.  If you notice
> clipped words at the start of a sentence, increase `utterance_pause_s`
> slightly (e.g. 0.7).

### Clipboard injection mode

If you cannot run `ydotoold` or encounter typing issues in a specific app,
switch to clipboard mode in `paulie.conf`:

```toml
inject_mode = "clipboard"
```

This writes the transcription to the system clipboard and sends Ctrl+V.
Requires `wl-clipboard` + `wtype` on Wayland, or `xclip` + `xdotool` on X11:

```bash
rpm-ostree install wl-clipboard wtype
```

---

## Project Structure

```
paulie/
├── src/
│   └── paulie/
│       ├── __init__.py   # version
│       ├── audio.py      # sounddevice + silero-VAD recording loop
│       ├── config.py     # TOML config file loader (~/.config/paulie/paulie.conf)
│       ├── stt.py        # Parakeet model load + transcribe
│       ├── inject.py     # text injection (ydotool or clipboard mode)
│       ├── ui.py         # PyQt6 borderless overlay + system tray icon
│       ├── daemon.py     # persistent daemon — loads models, handles triggers
│       └── main.py       # thin client — trigger or status query
├── pyproject.toml        # packaging (hatchling)
├── requirements.txt      # pinned deps for manual venv installs
└── README.md
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `error: paulie daemon is not running` | Run `systemctl --user start paulie-daemon` or check `journalctl --user -u paulie-daemon` |
| Config file changes not taking effect | Restart the daemon: `systemctl --user restart paulie-daemon`. The config is read once at startup. |
| Cancel (second hotkey press) doesn't stop recording | Confirm `paulie` is reaching the daemon: run `paulie` manually in a terminal and check `journalctl --user -u paulie-daemon` for `Cancel requested`. |
| `paulie status` shows "not running" when daemon is running | Ensure `XDG_RUNTIME_DIR` is the same in both the daemon and the terminal. Run `echo $XDG_RUNTIME_DIR` in both contexts. |
| Clipboard mode: text not pasted | Check that `wl-copy` and `wtype` are installed (`rpm-ostree install wl-clipboard wtype`). Check logs for which step failed. |
| System tray icon not showing | Tray support depends on the desktop. GNOME requires the AppIndicator extension. KDE Plasma shows it natively. |
| `--list-devices` output is empty | No input devices found by sounddevice. Check that your mic is recognised by the OS: `pactl list sources`. |
| `ydotool not found` | `rpm-ostree install ydotool` then reboot |
| `ydotool failed (2)` / no text typed | ydotoold socket not found — run `systemctl status ydotoold` and confirm `ls ~/.ydotool_socket` exists; set `YDOTOOL_SOCKET` explicitly if the path is non-standard |
| `ydotool timed out` | Happens on long dictations if key injection is too slow. Paulie uses `--key-delay=1` (1 ms/char) with a dynamic timeout — ensure you are running the latest version via `pipx install . --force` |
| Transcription cuts off early | Recording hit the 120 s ceiling — check logs for `Maximum recording duration reached`; also verify `PAULIE_SILENCE_S` isn't set too high |
| Overlay doesn't appear | `WAYLAND_DISPLAY` not set — add `Environment=WAYLAND_DISPLAY=wayland-0` to the service file or run `systemctl --user import-environment WAYLAND_DISPLAY` |
| `Neither WAYLAND_DISPLAY nor DISPLAY is set` | Same as above |
| `pip failed to build: texterrors / onnx / editdistance` | Build tools missing — run `rpm-ostree install python3-devel gcc gcc-c++ cmake ninja-build` then reboot |
| `libGL.so.1` / `libEGL.so.1` / `libfontconfig.so.1` missing | `rpm-ostree install mesa-libGL mesa-libEGL qt6-qtbase fontconfig` then reboot |
| `No such file or directory: 'paulie'` | Run `pipx ensurepath` and restart shell |
| `CUDA out of memory` | Set `CUDA_VISIBLE_DEVICES=""` to force CPU mode |
| Wrong microphone used | Set `PAULIE_DEVICE` to the device name or index; list devices with `pipx run --spec . python3 -c "import sounddevice; print(sounddevice.query_devices())"` |

---

## License

MIT
