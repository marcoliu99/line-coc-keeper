# Agentic Keeper 架構

[English](agentic_keeper_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`refactor`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. Python Supervisor 編排 AgentMessage／MechanicResult／TurnResolution 資料；不是獨立自主圖服務，agent 邊界區分意圖、權威、敘事與輸出保護。

2. context 準備整合當前狀態、劇本依據與時間線記憶；Executor 呼叫既有權威工具，StateReducer 不得再次套用已提交變更。

3. Narrator 接收已驗證機制事實與本回合結構事件；生成後以確定性一致性檢查保護可執行檢定指示。

4. 指令 handler 位於 app/commands，prompt 組裝在 app/services/prompt_config.py；早期把整回合交回 keeper.run_turn 的圖已失效。

5. KP Assistant 保持獨立 agent 與 OOC／正典區分；原架構的後續改進由統一回合和狀態交接規格規範。

## 流程與介面

```text
上下文 -> 意圖 -> 機制或角色扮演 -> 敘事 -> 驗證 -> 正典提交
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/agents/supervisor.py](../../../app/agents/supervisor.py)
- [app/agents/context_builder.py](../../../app/agents/context_builder.py)
- [app/agents/intent_router.py](../../../app/agents/intent_router.py)
- [app/agents/state_reducer.py](../../../app/agents/state_reducer.py)
- [app/agents/tool_gateway.py](../../../app/agents/tool_gateway.py)
- [app/domain/models.py](../../../app/domain/models.py)
- [tests/test_agentic_pipeline.py](../../../tests/test_agentic_pipeline.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/agentic_keeper_design_spec.md)
