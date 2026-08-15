#!/usr/bin/env bash
# Installs jkanime-dl on a Linux machine: checks/installs git, ffmpeg and uv,
# then clones (or updates) the repo and installs jkanime-dl as a global uv tool.
#
# Usage:
#   ./install.sh
#   curl -LsSf https://raw.githubusercontent.com/GiomarOsorio/JKanime/main/install.sh | bash
#
# Override the install location with JKANIME_DL_DIR (default: ~/jkanime-dl).

set -euo pipefail

REPO_URL="https://github.com/GiomarOsorio/JKanime.git"
INSTALL_DIR="${JKANIME_DL_DIR:-$HOME/jkanime-dl}"

log() { printf '\033[1;36m==>\033[0m %s\n' "$1"; }
err() { printf '\033[1;31mError:\033[0m %s\n' "$1" >&2; }

if [ "$(uname -s)" != "Linux" ]; then
    err "This script targets Linux. On macOS: brew install ffmpeg git, then curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
        SUDO="sudo"
    else
        err "Not running as root and no sudo found — install git/ffmpeg manually and re-run."
        exit 1
    fi
fi

PKG_MANAGER=""
for pm in apt-get dnf yum pacman zypper apk; do
    if command -v "$pm" >/dev/null 2>&1; then
        PKG_MANAGER="$pm"
        break
    fi
done

APT_UPDATED=0
install_pkg() {
    local pkg="$1"
    log "Installing $pkg..."
    case "$PKG_MANAGER" in
        apt-get)
            if [ "$APT_UPDATED" -eq 0 ]; then
                $SUDO apt-get update -qq
                APT_UPDATED=1
            fi
            $SUDO apt-get install -y "$pkg"
            ;;
        dnf)    $SUDO dnf install -y "$pkg" ;;
        yum)    $SUDO yum install -y "$pkg" ;;
        pacman) $SUDO pacman -Sy --noconfirm "$pkg" ;;
        zypper) $SUDO zypper install -y "$pkg" ;;
        apk)    $SUDO apk add "$pkg" ;;
        *)
            err "No supported package manager found (tried apt-get/dnf/yum/pacman/zypper/apk). Install $pkg manually and re-run."
            exit 1
            ;;
    esac
}

# --- git ---
if command -v git >/dev/null 2>&1; then
    log "git already installed ($(git --version))"
else
    install_pkg git
fi

# --- curl (needed to fetch the uv installer) ---
if ! command -v curl >/dev/null 2>&1; then
    install_pkg curl
fi

# --- ffmpeg (needed for HLS/m3u8 downloads) ---
if command -v ffmpeg >/dev/null 2>&1; then
    log "ffmpeg already installed"
else
    install_pkg ffmpeg
fi

# --- uv (package manager / Python version manager) ---
if command -v uv >/dev/null 2>&1; then
    log "uv already installed ($(uv --version))"
else
    log "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

if ! command -v uv >/dev/null 2>&1; then
    err "uv was installed but isn't on PATH yet. Run: export PATH=\"\$HOME/.local/bin:\$PATH\", then re-run this script."
    exit 1
fi

# --- get the code: reuse the current checkout if we're already inside it ---
if [ -f "./pyproject.toml" ] && grep -q '^name = "jkanime-dl"' ./pyproject.toml 2>/dev/null; then
    INSTALL_DIR="$(pwd)"
    log "Already inside the jkanime-dl repo ($INSTALL_DIR)"
elif [ -d "$INSTALL_DIR/.git" ]; then
    log "Updating existing checkout at $INSTALL_DIR"
    git -C "$INSTALL_DIR" pull --ff-only
else
    log "Cloning into $INSTALL_DIR"
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# uv downloads a matching Python automatically if one isn't already available.
log "Installing jkanime-dl as a global uv tool..."
uv tool install --force .

log "Done."
if ! command -v jkanime-dl >/dev/null 2>&1; then
    echo
    echo "Add uv's tool bin dir to your PATH (add this to ~/.bashrc or ~/.zshrc):"
    echo '  export PATH="$HOME/.local/bin:$PATH"'
    echo
fi
echo "Try it: jkanime-dl --help"
