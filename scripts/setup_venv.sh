#!/usr/bin/env bash
# 建立（或重建）這個 worktree 的 Python 虛擬環境 (.venv) 並安裝所有依賴套件。
#
# 用途：每個 git worktree 都需要自己獨立的 .venv（不會被 git 追蹤，worktree
# 被移除時會一起消失）。這支腳本讓重建 venv 變成一個可重複執行的步驟，不用
# 每次都手動想指令——尤其是在 worktree 目錄意外遺失、需要重新建立部署環境時。
#
# 用法：
#   ./scripts/setup_venv.sh              # 若 .venv 已存在則跳過建立，只重新安裝套件
#   FORCE_RECREATE=1 ./scripts/setup_venv.sh   # 先刪除既有 .venv 再重建
#
# 前置需求：系統要有 python3（建議 3.11+，這個專案在 3.14 上開發測試）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${SETUP_VENV_PYTHON:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "錯誤：找不到 $PYTHON_BIN，請先安裝 Python 3 或設定 SETUP_VENV_PYTHON 指向正確路徑。" >&2
    exit 1
fi

if [[ "${FORCE_RECREATE:-0}" == "1" && -d .venv ]]; then
    echo "FORCE_RECREATE=1：移除既有 .venv"
    rm -rf .venv
fi

if [[ -d .venv ]]; then
    echo ".venv 已存在於 $ROOT/.venv，跳過建立步驟（只會重新安裝/更新套件）。"
    echo "若要整個重建，改用：FORCE_RECREATE=1 ./scripts/setup_venv.sh"
else
    echo "建立虛擬環境：$ROOT/.venv（使用 $($PYTHON_BIN --version)）"
    "$PYTHON_BIN" -m venv .venv
fi

echo "安裝依賴套件（requirements-dev.txt，含執行 + 測試 + lint 工具）..."
.venv/bin/pip install --upgrade pip --quiet
.venv/bin/pip install -r requirements-dev.txt

echo ""
echo "✓ 完成。"
echo "  啟動方式：source .venv/bin/activate"
echo "  或直接呼叫：.venv/bin/python、.venv/bin/pytest、.venv/bin/ruff ..."
echo "  提醒：別忘了確認這個 worktree 的 .env 是否存在且設定正確"
echo "        （.env 不受 git 追蹤，worktree 被移除時會一起消失，需要另外備份）。"
