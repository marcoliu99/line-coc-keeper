# KP Assistant 代玩家操作

[English](kp_assistant_sudo_control_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`feature`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. KP sudo 透過既有命令與玩家回合路徑代指定玩家／角色操作，不提供任意未驗證模型工具。

2. 重新驗證目前 KP 角色、目標識別與對話狀態；代操作仍適用待檢定／Luck 所有權及時間線 gate。

3. 一般 KP OOC 討論留在獨立 Assistant 與自己的 log；明確正典命令或成功正典工具事件走受控升格。

4. 代操作不能公開私人玩家資料或跳過劇透／隱私政策；輸出保留正常交付目的地與稽核脈絡。

## 流程與介面

```text
KP 授權 -> 目標調查員 -> 玩家指令／行動路由 -> 權威結果
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/commands/router.py](../../../app/commands/router.py)
- [app/agents/assistant.py](../../../app/agents/assistant.py)
- [app/keeper.py](../../../app/keeper.py)
- [tests/test_kp_sudo.py](../../../tests/test_kp_sudo.py)
- [tests/test_kp_assistant_v2.py](../../../tests/test_kp_assistant_v2.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/kp_assistant_sudo_control_design_spec.md)
