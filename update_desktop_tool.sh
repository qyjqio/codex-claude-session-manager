#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$HOME/Desktop"
DESKTOP_SCRIPT="$DESKTOP_DIR/codex_session_browser.py"
DESKTOP_LAUNCHER="$DESKTOP_DIR/Codex聊天恢复工具.desktop"

cp "$PROJECT_DIR/session_manager.py" "$DESKTOP_SCRIPT"
chmod +x "$DESKTOP_SCRIPT"

cat > "$DESKTOP_LAUNCHER" <<LAUNCHER
[Desktop Entry]
Type=Application
Name=Codex聊天恢复工具
Comment=浏览、恢复、备注和安全删除 Codex / Claude 聊天记录
Exec=python3 $DESKTOP_SCRIPT
Icon=utilities-terminal
Terminal=false
Categories=Utility;Development;
LAUNCHER
chmod +x "$DESKTOP_LAUNCHER"

echo "已更新桌面工具：$DESKTOP_SCRIPT"
echo "已更新桌面启动器：$DESKTOP_LAUNCHER"
