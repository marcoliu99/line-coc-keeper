# 狀態版本與時間線隔離

[English](state-loss-amnesia-hardening_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`bug`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 長時間模型工作可能跨越回溯、劇本切換或其他存檔；提交前重新檢查時間線與版本，過期回合不得覆蓋較新權威狀態。

2. 還原 checkpoint 建立新時間線並使舊 callback／對話鏈失效；遊戲存檔與衍生記憶必須限於有效時間線。

3. 確定性骰子與成功工具先提交效果，再繼續敘事；模型失敗不能回滾已提交效果，也不能授權重複執行。

4. 指令、按鈕、Supervisor 與 KP Assistant 都須遵守相同過期狀態規則；版本衝突應受控回報，不能盲目存檔。

## 流程與介面

```text
讀取 revision／timeline -> 結算或模型處理 -> 受保護的寫入 -> 一次正典提交
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/repositories/group_state.py](../../../app/repositories/group_state.py)
- [app/checkpoints.py](../../../app/checkpoints.py)
- [app/keeper.py](../../../app/keeper.py)
- [app/check_identity.py](../../../app/check_identity.py)
- [tests/test_state_loss_amnesia.py](../../../tests/test_state_loss_amnesia.py)
- [tests/test_state_persistence.py](../../../tests/test_state_persistence.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/state-loss-amnesia-hardening_design_spec.md)
