# 統一玩家回合流程與獨立 KP Assistant

[English](unified_keeper_turn_flow_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`refactor`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 三種玩家輸入都進 supervisor.run_turn；一般行動經意圖分流，已結算後續與開場後備不走一般玩家意圖分類。

2. 純角色扮演可跳過機制階段；單一流程不代表每種輸入固定兩次模型呼叫，而是共用 context、保護與提交語意。

3. 已結算後續接收不可變骰子／結果事件；callback 白名單允許唯讀查詢及必要傷害／推進，但不能新建技能／SAN 擲骰。

4. 開場優先用劇本抽取開場及其可選開場檢定；缺少時才用 opening_fallback，提供查詢／展示工具而非任意機制修改。

5. KP Assistant 自行持有 provider 對話、工具白名單、kp_ooc_log 與明確／可驗證正典升格，不走玩家 Executor／Narrator。

6. keeper.run_turn 與 _run_turn_impl 已移除；共用 prompt builder 與 keeper._execute_tool 仍有效，測試直接針對現行 agent。

7. 正式歷史只提交一次，保留排隊私訊／圖片並拒絕過期時間線；權威歷史變更時清除過時 OpenAI continuation ID。

## 流程與介面

```text
Discord／router -> player_action／resolved_check_followup／opening_fallback -> Supervisor
Supervisor -> 上下文 -> Executor 或已成立結果 -> Narrator -> Guard／劇透檢查 -> 提交
KP 場外對話 -> 獨立 Assistant -> 依權限限制的工具 -> 場外回覆或正典提交
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/agents/supervisor.py](../../../app/agents/supervisor.py)
- [app/agents/executor.py](../../../app/agents/executor.py)
- [app/agents/narrator.py](../../../app/agents/narrator.py)
- [app/agents/assistant.py](../../../app/agents/assistant.py)
- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [app/commands/handlers/system.py](../../../app/commands/handlers/system.py)
- [tests/test_unified_keeper_turn_flow.py](../../../tests/test_unified_keeper_turn_flow.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/unified_keeper_turn_flow_design_spec.md)
