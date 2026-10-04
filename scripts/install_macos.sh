#!/bin/zsh
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
BRIDGE_SOURCE="$SOURCE_DIR/../src/computer_bridge.py"
INSTALL_DIR="$HOME/.codex/computer-bridge"
LOCAL_BIN="$HOME/.local/bin"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
PYTHON="${COMPUTER_BRIDGE_PYTHON:-$(command -v python3 || true)}"
WINDOWS_ALIAS="${COMPUTER_BRIDGE_WINDOWS_ALIAS:-windows-worker}"

if [[ -z "$PYTHON" || ! -x "$PYTHON" ]]; then
  echo "Python 3.11+ is required; set COMPUTER_BRIDGE_PYTHON to its executable." >&2
  exit 1
fi
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 11), sys.version'
mkdir -p "$INSTALL_DIR" "$LOCAL_BIN" "$LAUNCH_AGENTS"
mkdir -p "$HOME/.computer-bridge/rpc-in"
install -m 700 "$BRIDGE_SOURCE" "$INSTALL_DIR/computer_bridge.py"
cat > "$LOCAL_BIN/computer_bridge" <<EOF
#!/bin/sh
exec "$PYTHON" "$INSTALL_DIR/computer_bridge.py" "\$@"
EOF
chmod 700 "$LOCAL_BIN/computer_bridge"

PLIST="$LAUNCH_AGENTS/com.openai.computer-bridge.worker.plist"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.openai.computer-bridge.worker</string>
  <key>ProgramArguments</key><array><string>$LOCAL_BIN/computer_bridge</string><string>worker</string></array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$INSTALL_DIR/worker.log</string>
  <key>StandardErrorPath</key><string>$INSTALL_DIR/worker.log</string>
</dict></plist>
EOF
chmod 600 "$PLIST"
launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/com.openai.computer-bridge.worker"

if ! ssh -G "$WINDOWS_ALIAS" >/dev/null 2>&1; then
  echo "SSH alias $WINDOWS_ALIAS is not configured" >&2
  exit 1
fi
WINDOWS_KEY="$(ssh -T -o BatchMode=yes "$WINDOWS_ALIAS" 'powershell.exe -NoProfile -NonInteractive -Command "[IO.File]::ReadAllText((Join-Path $env:USERPROFILE .ssh\\codex_computer_bridge.pub))"' | tr -d '\r\n')"
if [[ ! "$WINDOWS_KEY" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+\ codex-computer-bridge-windows$ ]]; then
  echo "Windows bridge public key could not be read from $WINDOWS_ALIAS" >&2
  exit 1
fi
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
touch "$HOME/.ssh/authorized_keys"
if ! grep -Fqx "$WINDOWS_KEY" "$HOME/.ssh/authorized_keys"; then
  printf '%s\n' "$WINDOWS_KEY" >> "$HOME/.ssh/authorized_keys"
fi
chmod 600 "$HOME/.ssh/authorized_keys"
if codex mcp get computer-bridge >/dev/null 2>&1; then
  codex mcp remove computer-bridge
fi
codex mcp add computer-bridge -- "$LOCAL_BIN/computer_bridge" mcp --peer "windows=$WINDOWS_ALIAS"
"$LOCAL_BIN/computer_bridge" capabilities
echo "Installed. Restart Codex to load computer-bridge."
