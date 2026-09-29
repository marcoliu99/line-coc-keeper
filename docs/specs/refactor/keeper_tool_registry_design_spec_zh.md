# Keeper 工具改為註冊表

[English](keeper_tool_registry_design_spec.md)

狀態：**implemented**。原始基準：`main_v2` 的 `1a31645`；最後清理對照 `07d55a7`。

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

Marco 在實作前重新確認這三項決定。第一階段也將僅供日誌摘要使用的 `report_summary` 納入註冊表；它不屬於 35 個玩家回合工具，若經 `_execute_tool` 呼叫仍維持原本的未知工具結果。這使既有唯讀與開場集合可保持完全等價。戰鬥家族的兩項前置工作（#117、#118）均已合入；戰鬥處理函式家族另由 PR #137 遷移。

骰子家族（`roll_dice`、`roll_impaling_damage`、`roll_weapon_damage`）已移至 `app/keeper_tools/dice.py`。

檢定家族（技能、SAN、NPC 檢定、防禦選項和清除待處理檢定）已移至 `app/keeper_tools/checks.py`。處理函式呼叫 `app/check_lifecycle.py` 判定准入與身分；Keeper 保留狀態交易和檢定結果快取。舊的手動 pending／Luck 判斷及檢定家族的 legacy cascade 分支已移除。

角色家族（`adjust_character`、`set_skill`、`get_character_sheet`）已移至 `app/keeper_tools/character.py`。角色 handler 呼叫 Keeper 的公開屬性變更輔助函式；內層 `_apply_attribute_delta` 仍在 Keeper 的權威狀態交易裡，使用 `check_lifecycle.blocker()` 和 `register()`，避免重傷 CON 檢定受阻時仍提交 HP 傷害。

兩個處理函式家族與檢定生命週期重構已在 `integration/keeper-check-character-lifecycle` 一起對齊。PR #130–#133 和 #138 現已合入 `main_v2`。

物品／狀態家族（`adjust_ammo`、攜帶物品新增／移除、狀態標記新增／移除）已移至 `app/keeper_tools/inventory.py`。這些處理函式仍透過公開過渡介面使用 Keeper 單一的權威狀態更新邊界。

劇本／搜尋家族（`record_established_fact`、`record_clue`、圖片搜尋／顯示、章節推進、劇本搜尋與記憶搜尋）已移至 `app/keeper_tools/scenario.py`。章節推進與事實記錄仍透過公開過渡介面使用 Keeper 的權威狀態更新邊界。

訊息家族（`send_private_info`）已移至 `app/keeper_tools/messaging.py`。

戰鬥家族（`start_combat`、NPC 加入、狀態、回合推進、傷害、敵方計畫、效果與結束戰鬥）已移至 `app/keeper_tools/combat.py`。戰鬥規則仍在 `app/combat.py`；狀態寫入、受阻傷害不儲存，以及公開傷害過濾仍透過公開過渡介面使用 Keeper 的權威輔助函式。已清空的舊串接已在最後清理移除。

## 最後清理的契約

PR #135–#137 合入後，每個玩家回合工具都有明確的 `ToolSpec.handler`。移除空的 `execute_legacy_tool` 串接與 `legacy_handler` 預設值；註冊工具時必須提供 handler。`report_summary` 繼續留在註冊表，供 schema／能力推導與日誌摘要使用，但直接透過 `_execute_tool` 分派時仍回傳原有的 `未知工具 report_summary` 錯誤。未知工具名稱維持原有錯誤；共用准入與 KP 助手關卡仍在分派前執行。

刪除僅供遷移使用的舊字面集合等價測試。保留註冊表／schema 覆蓋、由註冊表推導的 provider 工具順序，以及各家族可觀察結果的測試。移除舊串接前須確認沒有任何呼叫端。本次清理不修改工具 schema、能力旗標或遊戲規則。

最後清理涵蓋 `07d55a7` 現有的工具；當時 `initialize_combat` 尚未推送至 `main_v2`，不在本次清理的註冊表清單內。

批次戰鬥分支現在也用明確的 `ToolSpec.handler` 註冊 `initialize_combat`，並將它放在 `add_npc_to_combat` 之後以保留 provider 工具順序。測試直接透過註冊表派送，不再依賴已移除的舊串接。
