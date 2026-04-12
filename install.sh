#!/usr/bin/env bash
# install.sh — Paulie STT install / uninstall script
#
# Usage:
#   ./install.sh              install and configure Paulie
#   ./install.sh --uninstall  remove Paulie and its services
#   ./install.sh --upgrade    reinstall Paulie from the current source tree
#
# Supports: Bazzite / Fedora Silverblue (rpm-ostree), Fedora (dnf), Debian/Ubuntu (apt)

set -euo pipefail
IFS=$'\n\t'

# ── Colour helpers ─────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  BOLD='\033[1m'; GREEN='\033[1;32m'; YELLOW='\033[1;33m'
  RED='\033[1;31m'; CYAN='\033[1;36m'; DIM='\033[2m'; RESET='\033[0m'
else
  BOLD=''; GREEN=''; YELLOW=''; RED=''; CYAN=''; DIM=''; RESET=''
fi

step()  { echo -e "\n${CYAN}${BOLD}▶  $*${RESET}"; }
ok()    { echo -e "   ${GREEN}✓${RESET}  $*"; }
warn()  { echo -e "   ${YELLOW}⚠${RESET}  $*"; }
info()  { echo -e "   ${DIM}$*${RESET}"; }
die()   { echo -e "\n   ${RED}✗  $*${RESET}\n" >&2; exit 1; }
ask()   { echo -e "\n${YELLOW}?  $*${RESET}"; }  # precedes read

# ── Paths (resolved once at startup) ──────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="${HOME:-/home/$USER}"
PIPX_BIN="$HOME_DIR/.local/bin"
PAULIE_BIN="$PIPX_BIN/paulie-daemon"
YDOTOOL_SOCKET="$HOME_DIR/.ydotool_socket"
PAULIE_SERVICE="$HOME_DIR/.config/systemd/user/paulie-daemon.service"
YDOTOOLD_SERVICE="/etc/systemd/system/ydotoold.service"
USER_UID="$(id -u)"
USER_GID="$(id -g)"

SUGGESTED_HOTKEY="Meta+Alt+P"

# ── Guards ────────────────────────────────────────────────────────────────────
[[ "$EUID" -eq 0 ]] && die "Do not run as root. Run as your normal user account."
[[ -f "$SCRIPT_DIR/pyproject.toml" ]] || die "Run this script from the Paulie project directory."

# ── Package manager detection ──────────────────────────────────────────────────
detect_pkgmgr() {
  if [[ -f /run/ostree-booted ]] && command -v rpm-ostree &>/dev/null; then
    echo "rpm-ostree"
  elif command -v dnf     &>/dev/null; then echo "dnf"
  elif command -v apt-get &>/dev/null; then echo "apt"
  else                                       echo "unknown"
  fi
}

# Returns 0 if the rpm package is installed, 1 otherwise.
rpm_installed() { rpm -q "$1" &>/dev/null; }

# ── System dependencies ────────────────────────────────────────────────────────
install_system_deps() {
  step "Checking system dependencies"

  local pkgmgr
  pkgmgr="$(detect_pkgmgr)"

  case "$pkgmgr" in

    rpm-ostree)
      # Collect only the packages that aren't already layered.
      local wanted=(
        pipx
        python3-devel gcc gcc-c++ cmake ninja-build portaudio-devel
        ydotool wl-clipboard wtype
      )
      local missing=()
      for pkg in "${wanted[@]}"; do
        rpm_installed "$pkg" || missing+=("$pkg")
      done

      if [[ ${#missing[@]} -eq 0 ]]; then
        ok "All system dependencies already installed."
        return
      fi

      warn "Installing via rpm-ostree (a reboot is required): ${missing[*]}"
      sudo rpm-ostree install --idempotent "${missing[@]}"

      echo
      ask "rpm-ostree changes require a reboot before installation can continue."
      read -rp "   Reboot now? [y/N] " choice
      if [[ "$choice" =~ ^[Yy]$ ]]; then
        info "Rebooting… re-run ./install.sh after login."
        sleep 2
        systemctl reboot
      else
        warn "Please reboot, then re-run:  ./install.sh"
        exit 0
      fi
      ;;

    dnf)
      sudo dnf install -y \
        python3-devel gcc gcc-c++ cmake ninja-build portaudio-devel \
        ydotool wl-clipboard wtype
      ok "System dependencies installed."
      ;;

    apt)
      sudo apt-get install -y \
        python3-dev gcc g++ cmake ninja-build libportaudio2 libportaudio-dev \
        ydotool wl-clipboard
      if ! command -v wtype &>/dev/null; then
        warn "wtype not found in apt repos — clipboard mode Ctrl+V will fall back to xdotool."
        sudo apt-get install -y xdotool 2>/dev/null || true
      fi
      ok "System dependencies installed."
      ;;

    *)
      warn "Unknown package manager. Ensure these are installed before continuing:"
      info "  python3-devel  gcc  gcc-c++  cmake  ninja-build  portaudio-devel"
      info "  ydotool  wl-clipboard  wtype"
      ask "Continue anyway? [y/N]"
      read -rp "   " choice
      [[ "$choice" =~ ^[Yy]$ ]] || exit 0
      ;;
  esac
}

# ── pipx ──────────────────────────────────────────────────────────────────────
install_pipx() {
  step "Checking pipx"

  # Reload PATH so pipx installed in a previous run of this script is visible.
  export PATH="$PIPX_BIN:$PATH"

  if command -v pipx &>/dev/null; then
    ok "pipx already installed ($(pipx --version))"
    return
  fi

  info "Installing pipx…"
  pip3 install --user pipx
  export PATH="$PIPX_BIN:$PATH"
  pipx ensurepath
  ok "pipx installed."
}

# ── Paulie ────────────────────────────────────────────────────────────────────
install_paulie() {
  step "Installing Paulie"
  export PATH="$PIPX_BIN:$PATH"
  pipx install --force "$SCRIPT_DIR"
  ok "Paulie installed.  Binaries: paulie  paulie-daemon"
}

# ── Config ────────────────────────────────────────────────────────────────────
init_config() {
  step "Config file"
  local conf="$HOME_DIR/.config/paulie/paulie.conf"
  if [[ -f "$conf" ]]; then
    ok "Config already exists — skipping."
    info "$conf"
  else
    "$PAULIE_BIN" --init-config
    ok "Default config written."
    info "$conf"
    info "Edit it to adjust silence duration, mic device, injection mode, etc."
  fi
}

# ── ydotoold system service ────────────────────────────────────────────────────
setup_ydotoold() {
  step "Setting up ydotoold system service"

  local ydotoold_bin
  ydotoold_bin="$(command -v ydotoold 2>/dev/null || echo /usr/bin/ydotoold)"

  sudo tee "$YDOTOOLD_SERVICE" > /dev/null <<EOF
[Unit]
Description=ydotool input automation daemon
After=local-fs.target

[Service]
Type=simple
ExecStart=${ydotoold_bin} --socket-path=${YDOTOOL_SOCKET} --socket-own=${USER_UID}:${USER_GID}
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

  sudo systemctl daemon-reload
  sudo systemctl enable --now ydotoold
  ok "ydotoold service enabled and started."

  # Give the daemon a moment to create its socket.
  local retries=5
  while [[ $retries -gt 0 ]]; do
    [[ -S "$YDOTOOL_SOCKET" ]] && break
    sleep 1
    (( retries-- )) || true
  done

  if [[ -S "$YDOTOOL_SOCKET" ]]; then
    ok "ydotool socket ready at $YDOTOOL_SOCKET"
  else
    warn "Socket not yet visible at $YDOTOOL_SOCKET"
    info "Check service status with:  sudo systemctl status ydotoold"
  fi
}

# ── paulie-daemon user service ─────────────────────────────────────────────────
setup_paulie_service() {
  step "Setting up paulie-daemon user service"

  mkdir -p "$HOME_DIR/.config/systemd/user"

  cat > "$PAULIE_SERVICE" <<EOF
[Unit]
Description=Paulie STT daemon
After=graphical-session.target

[Service]
Type=simple
ExecStart=${PAULIE_BIN}
Restart=on-failure
RestartSec=3
Environment=QT_QPA_PLATFORM=wayland
Environment=WAYLAND_DISPLAY=wayland-0
Environment=YDOTOOL_SOCKET=${YDOTOOL_SOCKET}

[Install]
WantedBy=graphical-session.target
EOF

  systemctl --user daemon-reload
  systemctl --user enable --now paulie-daemon
  ok "paulie-daemon service enabled and started."

  # First run downloads ~640 MB of model weights — give it up to 5 s to start responding.
  info "Waiting for daemon to come up (first run downloads ~640 MB of model weights)…"
  local retries=5
  while [[ $retries -gt 0 ]]; do
    if "$PIPX_BIN/paulie" status &>/dev/null; then
      ok "Daemon is running and responding."
      return
    fi
    sleep 1
    (( retries-- )) || true
  done

  warn "Daemon started but not yet responding — models may still be downloading."
  info "Follow progress with:  journalctl --user -u paulie-daemon -f"
}

# ── Hotkey instructions ────────────────────────────────────────────────────────
print_hotkey_instructions() {
  local paulie_bin="$PIPX_BIN/paulie"

  echo
  echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
  echo -e "${BOLD}  One last step — register your global hotkey${RESET}"
  echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
  echo
  echo -e "   Bind this command to a global hotkey:"
  echo -e "   ${CYAN}${BOLD}${paulie_bin}${RESET}"
  echo
  echo -e "   Suggested key: ${CYAN}${SUGGESTED_HOTKEY}${RESET}"
  echo
  echo -e "   ${BOLD}KDE Plasma:${RESET}"
  echo    "     System Settings → Shortcuts → Custom Shortcuts"
  echo    "     → Edit → New → Global Shortcut → Command/URL"
  echo
  echo -e "   ${BOLD}GNOME:${RESET}"
  echo    "     Settings → Keyboard → View and Customize Shortcuts"
  echo    "     → Custom Shortcuts → +"
  echo
  echo -e "   ${BOLD}Useful commands:${RESET}"
  echo -e "     ${DIM}paulie status${RESET}                           check daemon"
  echo -e "     ${DIM}paulie-daemon --list-devices${RESET}             list microphones"
  echo -e "     ${DIM}journalctl --user -u paulie-daemon -f${RESET}    live logs"
  echo
}

# ── Uninstall ──────────────────────────────────────────────────────────────────
run_uninstall() {
  echo -e "\n${BOLD}Uninstalling Paulie${RESET}\n"
  warn "This will stop and remove:"
  info "  • paulie-daemon user service and its service file"
  info "  • ydotoold system service and its service file"
  info "  • Paulie pipx package (paulie + paulie-daemon binaries)"
  echo
  info "These will be left intact:"
  info "  • ~/.config/paulie/            (config file)"
  info "  • ~/.cache/huggingface/hub/    (model weights, ~640 MB)"
  echo
  ask "Continue? [y/N]"
  read -rp "   " choice
  [[ "$choice" =~ ^[Yy]$ ]] || { info "Aborted."; exit 0; }

  step "Stopping paulie-daemon"
  if systemctl --user is-enabled paulie-daemon &>/dev/null; then
    systemctl --user disable --now paulie-daemon
    ok "paulie-daemon stopped and disabled."
  else
    warn "paulie-daemon service was not enabled."
  fi
  if [[ -f "$PAULIE_SERVICE" ]]; then
    rm "$PAULIE_SERVICE"
    systemctl --user daemon-reload
    ok "Service file removed."
  fi

  step "Stopping ydotoold"
  if sudo systemctl is-enabled ydotoold &>/dev/null; then
    sudo systemctl disable --now ydotoold
    ok "ydotoold stopped and disabled."
  else
    warn "ydotoold service was not enabled."
  fi
  if [[ -f "$YDOTOOLD_SERVICE" ]]; then
    sudo rm "$YDOTOOLD_SERVICE"
    sudo systemctl daemon-reload
    ok "Service file removed."
  fi

  step "Uninstalling Paulie package"
  export PATH="$PIPX_BIN:$PATH"
  if command -v pipx &>/dev/null && pipx list 2>/dev/null | grep -q paulie; then
    pipx uninstall paulie
    ok "Paulie uninstalled."
  else
    warn "Paulie was not found in pipx — nothing to uninstall."
  fi

  echo
  ok "Uninstall complete."
  echo
  info "To remove the config:  rm -rf ~/.config/paulie"
  info "To remove model cache: rm -rf ~/.cache/huggingface/hub"
  echo
}

# ── Install / Upgrade ──────────────────────────────────────────────────────────
run_install() {
  local mode="${1:-install}"
  echo -e "\n${BOLD}Paulie STT — ${mode^}${RESET}\n"

  if [[ "$mode" == "install" ]]; then
    install_system_deps
  else
    info "Skipping system dependency check for upgrade."
  fi

  install_pipx
  install_paulie
  init_config
  setup_ydotoold
  setup_paulie_service
  print_hotkey_instructions

  echo -e "${GREEN}${BOLD}  ${mode^} complete.${RESET}\n"
}

# ── Entry point ────────────────────────────────────────────────────────────────
case "${1:-}" in
  "")           run_install "install"  ;;
  --upgrade)    run_install "upgrade"  ;;
  --uninstall)  run_uninstall          ;;
  --help|-h)
    echo "Usage: $0 [--upgrade | --uninstall]"
    echo
    echo "  (no flag)    Install Paulie and configure all services"
    echo "  --upgrade    Reinstall from the current source tree, keep config"
    echo "  --uninstall  Stop services and remove Paulie"
    exit 0
    ;;
  *)
    die "Unknown option: $1\nRun '$0 --help' for usage."
    ;;
esac
