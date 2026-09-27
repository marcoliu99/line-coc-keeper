# OpenAI prompt cache key 跨回合前綴重用

狀態：已實作。基底：main_v2。分支：enhancement/openai-prompt-cache-key。

## 問題與證據

2026-09-27 量測 `~/coc_v2_log` 中 125 筆有 usage 紀錄的請求（input 合計 2,301,394，回報已快取 1,044,405）。隱式前綴快取本來就有作用，整體命中率 45.4%，但依請求在回合中的位置分布極不平均：

| 位置 | 請求數 | input tokens | 已快取 | 命中率 |
| --- | --- | --- | --- | --- |
| 回合第一次 | 42 | 814,162 | 37,781 | 4.6% |
| 第二次 | 34 | 661,028 | 472,149 | 71.4% |
| 第三次以後 | 49 | 826,204 | 534,475 | 64.7% |

前綴本身是穩定的。`keeper._build_static_prompt` 的註解載明它只在載入劇本 PDF、角色加入離開或角色卡編輯時才變，Anthropic adapter 也已經在它上面標記 `cache_control`。回合內由 `previous_response_id` 把連續請求留在同一快取節點，前綴得以重用；新回合另開一條鏈，第一次請求沒有節點親和性，整段約 17,378 token 的靜態前綴（static_system 7,851 加上 tools 9,527）全價重送。僅此一項就有 776,381 token、佔全部 input 的 33.7%，可快取卻未命中。

## 範圍

在 OpenAI `responses.create` 送出 `prompt_cache_key`，讓共用前綴的請求跨回合被路由到一起。此欄位存在於已安裝的 SDK（openai 3.19.2，`openai/types/responses/response_create_params.py`）：「Used by OpenAI to cache responses for similar requests to optimize your cache hit rates. Replaces the `user` field.」

key 的取法：重用 `observability.current_context()` 中已雜湊的 `conversation_id`。它跨回合穩定、不同遊戲不同、且已經過 `_safe_identifier` 處理不含玩家可識別內容。provider 簽名不變。

不在範圍內：`prompt_cache_retention`（SDK 已標記 Deprecated）、`prompt_cache_options`（ttl 預設 30m 且為目前唯一支援值）、任何提示詞組成或順序的更動、Anthropic 與 Gemini adapter。

## 行為

`conversation_id` 只在啟用 log 時才綁定。取不到 key 時整個省略該參數，而非送出空值：空值或共用常數會把不相關的遊戲併到同一筆快取。key 不得由 `turn_id` 或 `request_id` 衍生，那些每回合都變，等於換個名字重現目前的未命中率。

## 測試策略

離線進行，使用假的 provider client，不呼叫付費 API。

- 有綁定對話情境時，送出的 `prompt_cache_key` 等於雜湊後的 conversation id。
- 同一對話的兩個不同回合送出相同 key，不同對話的 key 不同。
- 沒有綁定對話時，該參數不出現在請求中，而非以空值送出。
- key 中不含 `turn_id` 或 `request_id`。

## 驗證

命中率本來就有記錄：`observability.usage_fields` 已正規化 `cached_input_tokens`，不需要新增觀測程式碼。上線後重跑上表的分位統計，只看「回合第一次」那一列對照 4.6% 的基準值。

## 限制

這項改動已驗證的是路由與成本，不是 rate limit。快取命中的 input token 是否仍全額計入 TPM 並未在此確認，因此不宣稱任何 rate-limit 效益。快取親和性是路由提示而非保證，未命中仍屬正確行為且不改變輸出。量測窗口只有單一部署一天份的 log。
