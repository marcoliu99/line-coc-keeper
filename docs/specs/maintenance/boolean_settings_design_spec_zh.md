# 布林設定只用一種讀法

[English](boolean_settings_design_spec.md)

狀態：**implemented**。基準：`main_v2` 於 `70aea21`。

## 問題

`app/config.py` 大部分布林設定用 `_env_bool` 讀取（接受 `1/true/yes/on` 與 `0/false/no/off`；其他值保留預設並在啟動時回報），但有六個是行內解析：`os.environ.get(name, default).lower() in ("1", "true", "yes")`——`SCENARIO_RAG_ENABLED`、`SCENARIO_LIFECYCLE_KP_ONLY`、`OPENAI_OMIT_TEMPERATURE`、`DEBUG_SHOW_INTERNAL_IDS`、`TURN_FALLBACK_RECOVERY_ENABLED`、`RETRIEVAL_REUSE_FOR_FOLLOWUPS`（架構審查 F8）。兩種讀法接受的寫法不同，而且對預設為開啟的旗標，打錯字會悄悄把它關掉。

## 變更

六個旗標改用 `_env_bool`，預設值不變。`tests/test_config_flags.py` 釘住接受的寫法、無法辨認的值「保留預設並回報」的行為、六個旗標各自的預設值，並在 `config.py` 又出現行內布林解析時失敗。

## 行為變化

| 值 | 旗標預設 | 以前 | 現在 |
| --- | --- | --- | --- |
| `1`、`true`、`yes`（不分大小寫、前後空白）| 任一 | 開 | 開 |
| `0`、`false`、`no` | 任一 | 關 | 關 |
| `on` | 任一 | **關** | 開 |
| `off` | 任一 | 關 | 關 |
| 無法辨認的值（`ture`）| `false` | 關 | 關，並在啟動時回報 |
| 無法辨認的值（`ture`）| `true`（`TURN_FALLBACK_RECOVERY_ENABLED`、`RETRIEVAL_REUSE_FOR_FOLLOWUPS`）| **關，而且沒有任何提示** | **開（預設），並在啟動時回報** |

粗體的兩列就是全部的變化。`on` 本來就被其他旗標接受；打錯字不再悄悄關掉預設開啟的功能。如果某個部署靠打錯字來關閉這兩個功能，現在必須明確寫 `false`。

## 不在範圍內

在 CI 以非預設旗標跑測試套件：大部分測試假設預設值，得先決定哪些測試算旗標中立。目前涵蓋與未涵蓋的範圍見 `docs/guides/configuration_profiles_zh.md`。
