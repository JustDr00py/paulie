# Paulie — Local Offline STT for Bazzite OS

Push-to-talk dictation powered by **NVIDIA Parakeet-TDT-0.6B-V3** and **silero-VAD**.  
Transcribes speech and types it into the focused Wayland window via `ydotool`.

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

Create `~/.config/paulie/paulie.conf`:

```toml
# ~/.config/paulie/paulie.conf
# All keys are optional — omit any you want to leave at the default.

silence_s     = 1.0      # seconds of silence before recording stops
vad_threshold = 0.45     # silero-VAD speech probability cutoff (0.0–1.0)
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
| `PAULIE_MODEL` | `nemo-parakeet-tdt-0.6b-v3` | onnx-asr model name |
| `PAULIE_DEVICE` | system default | `sounddevice` input device — name substring or integer index |
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

## Project Structure

```
paulie/
├── src/
│   └── paulie/
│       ├── __init__.py   # version
│       ├── audio.py      # sounddevice + silero-VAD recording loop
│       ├── config.py     # TOML config file loader (~/.config/paulie/paulie.conf)
│       ├── stt.py        # Parakeet model load + transcribe
│       ├── inject.py     # ydotool text injection
│       ├── ui.py         # PyQt6 borderless overlay
│       ├── daemon.py     # persistent daemon — loads models, handles triggers
│       └── main.py       # thin client — sends trigger to daemon
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
