# Keeper 工具改為註冊表

[English](keeper_tool_registry_design_spec.md)

狀態：**partial**（已審查；註冊表與衍生集合已實作，處理函式家族待遷移）。基準：`main_v2` 的 `1a31645`。

> 已對照目前的 `main_v2` 更新：`main_v2` 之後撤銷了 PR #99／#121 的移動服務（見 `docs/specs/bug/movement_authorization_diagnosability_design_spec.md`）。`app/services/movement.py`、`movement.TOOL`／`commit_movement` 和 `ORIGIN_TOOLS` 已經不存在，以下把它們和點名它們的遷移步驟一併移除。其餘內容仍與目前程式碼相符，行號已更新。

## 問題

一個 Keeper 工具的定義分散在三個地方，只能靠人手保持同步。

1. **Schema。** `keeper.TOOLS`（`app/keeper.py:97`）有 35 個 JSON schema。
2. **行為。** `_execute_tool`（`app/keeper.py:1849-…`）先執行共用的關卡：mutation admission、KP 助手的 `roll_dice` 情境檢查和允許清單。接著是 34 個分支的 `if name == "…"` 串接。每個分支都定義自己的巢狀 mutator 閉包，捕捉 `state`、`tool_input`、`private_messages`、`image_requests` 和 `speaker_role`。
3. **屬性。** 工具*是什麼*（唯讀、KP 助手可用、會建立檢定、會讓戰鬥狀態失效……）放在六個模組、約十幾個名稱集合裡：

| 集合 | 位置 |
| --- | --- |
| `READ_ONLY_TOOL_NAMES`、`RESOLVED_CHECK_FOLLOWUP_TOOL_NAMES` | `app/keeper.py:805`、`:819` |
| `_KP_ASSISTANT_ALLOWED_TOOL_NAMES`、`_KP_ALWAYS_CANONICAL_GAME_TOOL_NAMES` | `app/keeper.py:887`、`:911` |
| `_COMBAT_STATUS_INVALIDATING_TOOLS` | `app/keeper.py:3589` |
| `BOUNDED_QUERY_TOOLS`、`_CHECK_REGISTRATION_TOOLS` | `app/agents/tool_gateway.py:22`、`:218` |
| `_OPENING_TOOL_NAMES` | `app/agents/narrator.py:18` |
| `_CHECK_CREATION_TOOLS` | `app/services/turn_context.py:99` |
| `INFORMATION_QUERY_TOOLS` | `app/services/turn_resolution.py:18` |

所以新增或修改一個工具，要同時改 schema、串接裡的一個分支，以及它該屬於的每一個集合，漏掉一個也不會報錯。例如，新的建立檢定工具如果只加進兩個「建立檢定」集合（`tool_gateway` 和 `turn_context`）中的一個，另一層就會把它當成不會建立檢定。`CODING_STANDARDS.md` 背後的審查也指出，這個串接是 repo 裡最大的函式，`keeper.py` 則是最常被修改的檔案。

## 目標

每個工具只宣告**一次**：schema、處理函式和屬性放在同一處。關卡、名稱集合和分派器都從這些宣告推導出來。

```python
@dataclass(frozen=True)
class ToolSpec:
    schema: dict
    handler: Callable[[ToolCall], dict]
    read_only: bool = False
    kp_assistant: bool = False             # speaker_role == "kp_assistant" 時可用
    creates_check: bool = False
    invalidates_combat_status: bool = False
    # ……上表每個集合對應一個旗標，依屬性命名，不依使用端命名

@dataclass
class ToolCall:                            # 目前各分支共用、總是一起傳遞的那組參數
    state: GroupState
    input: dict
    private_messages: list[tuple[str, str]]
    image_requests: list[tuple[str | None, int]]
    speaker_role: SpeakerRole              # Literal["player", "kp_assistant"]
```

`_execute_tool` 簡化為：共用關卡 → `REGISTRY[name].handler(call)`。既有的每個集合改成 `frozenset(n for n, t in REGISTRY.items() if t.<flag>)`，遷移期間使用端沿用原本的名稱。

## 計畫：逐步替換，一個工具家族一個 PR

1. **建立註冊表和推導集合，不搬處理函式。** 新增 `ToolSpec` 和 `REGISTRY`，填入每個工具的 schema 和旗標。`handler` 暫時退回既有的串接。把每個名稱集合換成推導出來的版本。用一個測試斷言每個推導集合和原本的字面集合完全相等；這個等價測試是之後所有步驟的安全網。
2. **依家族搬移處理函式：** 骰子、檢定、角色屬性、物品與狀態、戰鬥、劇本與搜尋、訊息，一個家族一個 PR。該家族的分支變成模組層級函式（例如放在 `app/keeper_tools/combat.py`），並刪除串接裡對應的分支。該家族的行為測試必須在不修改的情況下通過。
3. **串接清空後刪除它**，連同等價測試裡的舊字面集合。

## 測試

- 步驟 1：上表每個集合的推導等價測試，以及每個工具的 `schema.name` 都和註冊表的鍵一致。
- 步驟 2 的每個 PR：該家族既有的測試不修改即通過；新增註冊表測試，確認每個 schema 都有處理函式、每個處理函式都有 schema。
- 全程：完整測試套件和 `tests/test_tool_gateway_speaker_role.py`（KP 助手的權限關卡）保持綠燈。

## 審查決定

- **位置：** 處理函式放在 `app/keeper_tools/`，一個家族一個模組（`dice.py`、`checks.py`、`combat.py`……）。它們是 Keeper 的能力，和 `app/services/` 裡的流程服務性質不同。
- **Schema 順序：** 供應商的 prompt 快取會把工具清單算進快取前綴，所以註冊表保留宣告順序，並用測試固定送給供應商的順序。
- **排序：** 戰鬥家族的 PR 排在 `bug/major-wound-con-check-gate` 和 `refactor/combat-start-in-combat-module` 合併之後，因為它們會改到同一批分支。

Marco 在實作前重新確認這三項決定。第一階段也將僅供日誌摘要使用的 `report_summary` 納入註冊表；它不屬於 35 個玩家回合工具，若經 `_execute_tool` 呼叫仍維持原本的未知工具結果。這使既有唯讀與開場集合可保持完全等價。`refactor/combat-start-in-combat-module` 尚未合入 `main_v2`，戰鬥處理函式家族因此仍待遷移。

訊息家族（`send_private_info`）已遷移至 `app/keeper_tools/messaging.py`；其餘處理函式暫時沿用舊串接。
