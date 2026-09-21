# Bot Lifecycle Scripts Design Spec

## 文件狀態與 changeset

- 工作 branch：`feature/kp-assistant-sudo-control`
- 整合 branch：`main_v2`
- 本輪對齊的 `main_v2` changeset：`3d5c39f`
- 本 branch 與 `main_v2` 的對齊 merge changeset：`caca558`
- 本輪 Ruff／pytest／profiler implementation changeset：`0c96ff1`
- 後續 script review fixes changeset：待提交
- 後續若 `main_v2` 有新 commit，下一輪修改或更新 PR 前必須重新 fetch 並對齊。

## Problem and goals

本機開發與測試時，可能同時啟動多個 Discord bot process。需要一組可重複使用的 script，能夠：

1. 啟動指定 bot，記錄這次啟動的 instance identity、PID、log 與實際 command。
2. 只停止指定 script 啟動的那一個 instance，不使用 `pkill`、模糊的 process name 或停止其他 bot。
3. 清除 bot runtime data，但明確保留 repository root 的 `.env` 與 `.env.*` 設定檔。
4. 在 macOS／Linux 的專案 virtualenv 環境下提供一致的錯誤訊息與 exit code。

## Scope

新增 `scripts/` 下的 lifecycle scripts：

- `start_bot.sh discord [--name INSTANCE]`
- `stop_bot.sh INSTANCE`
- `clean_bot_data.sh [--yes]`
- `bot_status.sh [INSTANCE]`

`discord` 對應 `python -m app.discord_bot`。script 只接受明確的 `discord` 入口，不自行猜測或啟動其他平台入口。

`start_bot.sh` 預設在專案 root 執行，尋找 `.venv/bin/python`；若不存在才使用 PATH 中的 `python3`。Bot 本身依既有設定載入 `.env` 與檢查必要設定；lifecycle script 不把 secrets 印到 terminal 或 log。

### Optional profiler

啟動 script 透過單一環境變數 `BOT_PROFILER` 明確控制 profiler；預設值為
`off`，因此一般啟動完全不載入 profiler。可用值只有：

- `off`／`none`：不啟動 profiler。
- `pyinstrument`：用同一個 Python interpreter 執行 `pyinstrument -m app.discord_bot`，
  啟用 async-aware profiling，並在 `.runtime/bots/` 寫出 HTML 報告。
- `py-spy`：先照正常方式啟動 bot，再以獨立 process attach bot，寫出 SVG flame graph。

`BOT_PROFILER` 為未知值，或指定工具尚未安裝時，`start_bot.sh` 必須明確失敗，不能
悄悄以未 profiling 的方式啟動。兩個 profiler 都是開發／staging 工具，正式環境預設
保持 `BOT_PROFILER=off`；`py-spy` 另外可能受 macOS／container process attach 權限限制。

## Explicit non-goals

- 不管理由 systemd、launchd、Docker、Supervisor 或其他 process manager 啟動的 process。
- 不掃描並停止所有同名 bot；每個 start 都必須產生唯一 instance name，stop 只接受該 name。
- 不在 clean script 中刪除 `.env`、`.env.example`、source code、virtualenv、Git metadata 或使用者未明確列出的目錄。
- 不做跨機器 process 管理，也不把 PID state 放進 SQLite。
- 不讓 stop script 依 PID file 缺失時退化成 `pkill` 或依 command name 猜測。

## Runtime layout and identity

Runtime files集中在被 `.gitignore` 忽略的 `.runtime/bots/`：

```text
.runtime/
  bots/
    discord-20260919-153000-a1b2c3.json
    discord-20260919-153000-a1b2c3.log
```

每個 instance manifest 至少保存：

```json
{
  "instance": "discord-20260919-153000-a1b2c3",
  "pid": 12345,
  "started_at": "2026-09-19T15:30:00+08:00",
  "cwd": "/absolute/project/root",
  "bot": "discord",
  "command": ["/absolute/.venv/bin/python", "-m", "app.discord_bot"],
  "log_path": "/absolute/project/root/.runtime/bots/....log"
}
```

Instance name 使用 timestamp 加 random suffix，不能由 PID 單獨作為公開 identifier，避免 PID reuse 讓 stop 誤殺新 process。manifest 以暫存檔寫入後 atomic rename，避免 status 讀到半份 JSON。

啟用 profiler 時，manifest 另外保存 profiler tool、PID／process group、實際 command 與
output path。`pyinstrument` 的 profiler 是 bot 的 wrapper process；`py-spy` 則是另外
的 process group，兩者都必須能由同一份 manifest 安全清理。

## Key flows

### Start

```text
validate root/env/entrypoint
        |
read BOT_PROFILER (default off)
        |
validate selected profiler executable/module
        |
create unique instance + manifest path
        |
launch child (optionally wrapped by pyinstrument)
        |
if py-spy: attach a dedicated profiler process
        |
record PID and command atomically
        |
print instance name, PID, log path
```

若 process 在 manifest 寫入前立刻退出，script 必須回報啟動失敗並清除該次 runtime metadata。正常啟動不等待 bot 結束；`start_bot.sh` exit code 0 只代表 process 已成功建立。

### Stop

`stop_bot.sh INSTANCE` 只讀取 `.runtime/bots/<INSTANCE>.json`，並先驗證：

1. instance name 僅能是安全檔名，不接受 path traversal。
2. manifest 的 PID 仍存在。
3. 該 PID 的 command line 與 manifest 的 bot command 相符。
4. PID 未被 reuse 成另一個 process。

驗證失敗時不送 signal，保留 manifest 供排查並回傳非零 exit code。驗證成功後送 `TERM`，等待有限 timeout，仍未退出才送 `KILL`。只刪除這個 instance 的 manifest；log 預設保留，方便確認關閉結果。若啟動器建立了獨立 process group，signal 送給該 group，但仍要以 manifest 的 group identity 做驗證。

若 manifest 有 profiler：`pyinstrument` wrapper 先使用 `SIGINT` 讓 HTML 報告有機會
完成；`py-spy` 隨 bot 結束，若仍存活則驗證自己的 command／process group 後停止。
兩者的 profile output 都保留，status 會顯示 output path。

### Status

`bot_status.sh` 列出所有 manifests 的 instance、bot type、PID、alive/stale 狀態、started time 與 log path。stale manifest 只標示，不自動刪除或停止任何 process。指定 instance 時只顯示該筆。

### Clean data

`clean_bot_data.sh` 必須要求 `--yes` 才能執行破壞性操作，並列出將刪除的 allowlist paths：

- `DATA_DIR`（預設 `data/groups`，包含 SQLite 所在的 `data/` 時須以實際 config 路徑去重）
- `DB_PATH`、其 `-wal`、`-shm`、`-journal`
- `BACKUP_DIR`
- `SCENARIO_LIBRARY_DIR`
- `IMPORT_DIR`

執行前解析所有路徑為 absolute path，拒絕 empty、`.`、repository root、home directory 或 `.env` 所在路徑；以 allowlist 逐項刪除，不使用 `rm -rf "$ROOT"`。clean 不停止 bot，也不刪除 `.runtime` 的 process manifests；若仍有 running instance，先警告但不替使用者停止它。

## Integration and conventions

- Scripts 使用 `set -euo pipefail`、`dirname` 找 repository root，所有路徑加引號。
- 共同邏輯放在一個不執行 network/API 的 `scripts/bot_lifecycle.py`，start/stop/status/clean 共用 instance 與 path validation。
- 不改變 `app/config.py` 的 runtime defaults；script 只透過既有 `.env`／environment 啟動 bot。
- `.runtime/` 加入 `.gitignore`，不要讓 PID、manifest、log 或測試資料進 Git。
- README 或 `docs/setup.md` 補上實際指令、exit code、multiple instance 範例與 clean 的警告。

## Testing plan

- shell syntax check：`bash -n scripts/*.sh`。
- 使用 fake bot command 測試 start 會產生唯一 manifest，兩次 start 不互相覆蓋。
- 測試 stop A 不會停止 B；stale／PID-reuse manifest 不會送 signal。
- 測試 clean 只刪 allowlist runtime paths，`.env` 與 `.env.example` 保留。
- 測試未加 `--yes` 不刪資料，path validation 拒絕 root/home/empty path。
- 跑既有完整 Python test suite，確認 scripts 不影響 bot runtime。
- 預設 `BOT_PROFILER=off` 不改變原本 command 或 manifest。
- `BOT_PROFILER=pyinstrument` 驗證同一 interpreter 可 import pyinstrument，並產生 HTML
  output argument；`BOT_PROFILER=py-spy` 驗證 executable、attach command 與 manifest
  identity。
- `pytest`／`pytest-cov` 作為開發測試工具，可用 `python -m pytest --cov=app` 執行。

## Open decisions

- stop timeout 暫定 10 秒；若 Discord graceful shutdown 需要更久，再以環境變數提供可調整值。
- log rotation 不在第一版處理；script 只建立每 instance log，長期保留策略交給使用者的 process manager。
- profiler output 不做自動 rotation；每個 instance 一份 artifact，由使用者清理 `.runtime/bots/`。
