# 移動授權與 blocked 回合的可診斷性

[English](movement_authorization_diagnosability_design_spec.md)

狀態：**已實作**。基底：`main_v2` 的 `3e2e95c`。

## 問題與證據

2026-09-28 05:03 的 runtime log(`20260928-050340-380799_runtime_profile-async.log`)，一個剛開的新團，二十二個請求裡有**三個回合是 `blocked`**，玩家每次看到的都是同一句：

> 這次行動目前無法繼續。請先確認目前狀態或更正原本的行動。

三個回合的 `validation_code` 都是 `validated`——**不是驗證器擋的**，是 Executor 自己宣告 blocked，因為它的 `commit_movement` 失敗了。三次失敗的原因各不相同：

| turn_id 末八碼 | 玩家輸入 | 工具結果 |
| --- | --- | --- |
| `47e6cde6` | `直奔商店購買油燈跟煤油罐` | `commit_movement 失敗：no_player_movement_authorization`(同回合 `add_carried_item` 成功) |
| `2f123f01` | `跟煤油罐` | 只有 `search_scenario`，回「（沒有找到相關內容）」 |
| `af968e7f` | (一般文字) | `commit_movement 失敗：unknown_map` |

這個團用的是**好的**劇本變體，不是 [`empty_scenario_location_index_design_spec_zh.md`](empty_scenario_location_index_design_spec_zh.md) 講的空產物情況:

```text
scenario_variant_id : original      scene_maps keys : ['17']
location_index      : 8 entries     current_map_page: {}
```

## 成因一:授權閘被斷句擋住

`app/services/movement.py` 的 `commit` 在沒有既有 proposal 時，要求模型給的 `source_span` **完全等於** `intent_parser.movement_clauses()` 切出來的其中一句，否則回 `no_player_movement_authorization`。實測那句輸入:

```text
'直奔商店購買油燈跟煤油罐'
  movement_clauses() = ['直奔商店購買油燈跟煤油罐']   ← 沒有逗號，整句只有一個 clause
  span='直奔商店'   是子字串 True   在 clauses 裡 False   ← 被拒
  span='直奔商店購買油燈跟煤油罐'  在 clauses 裡 True
```

兩件事疊在一起:

1. **`直奔` 不在 `_MOVEMENT_VERB_RE`**(`app/intent_parser.py:28`)。`離開/進入/走進/走向/前往/進去/走到/穿過/移動到/走回/回到/走/去` 都有，一整類「奔、趕、衝、跑、抵達、來到」沒有。
2. 這句沒有逗號切不開，**唯一合法的 span 是連購買一起的整句**。模型只引用移動那一段——這是合理行為——就必定被拒。

對照:`前往商店，購買油燈跟煤油罐` 切成兩句、`has_movement_verb=True`，一切正常。**玩家只是換了個動詞、少打一個逗號。**

## 成因二:`unknown_map`,而參數沒被記錄

`movement.py:295` 是 `if page not in state.scene_maps: fail('unknown_map')`。該團 `scene_maps` 只有 `'17'`，而那回合檢索同時撈回第 6、7、8、17 頁，模型很可能拿了**劇本正文的頁碼**當 `page`。

**這一點無法從 log 證實**:`commit_movement` 的參數完全沒有被記錄。`llm.tool.completed` 只有 `tool_name` 與 `duration_ms`，`_describe_tool_call` 只留下錯誤碼。這類失敗目前無法事後定位，只能猜。這本身就是要修的缺口，而且要先修——否則下一次還是只能猜。

## 成因三:一句 fallback 蓋掉三種不同的失敗

`app/services/prompt_config.py:340-357`:`blocked` 且沒有 pending 檢定、沒有 state 變更、沒有 `scenario_evidence_blocked` 時，落到最後那句。三種完全不同的失敗——動詞不認得、頁碼不在地圖上、找不到劇本依據——對玩家長得一模一樣，而且都沒說要改什麼。

## 範圍

四項，依風險由低到高。**不放寬到達檢查本身**,也不碰空產物那條路徑。

### 1. 補上缺的移動動詞

`_MOVEMENT_VERB_RE` 加入 `直奔|奔向|奔去|趕往|趕去|趕到|衝向|衝進|衝出|跑向|跑到|跑進|抵達|來到|返回|折返`。

`has_movement_verb` 只是「便宜的前置判斷」(見其 docstring)，誤判的代價是多走一次房名比對或 RAG，不會自己提交移動——提交一律要模型呼叫 `commit_movement` 並過完整驗證。因此這裡取寬一點是安全的。

#### 審查修正:取寬會把別人的移動算到玩家頭上

**這裡取寬並不是無代價的,第一版漏掉了一條路。** `propose()` 會挑「第一個含移動動詞的 clause」建立 proposal,而 `movement_clauses` 只排除**以** 他/她/有人 **開頭**的句子,不排除有名字的第三方。於是:

```text
怪物衝進地下室，我開槍
  clauses = ['怪物衝進地下室', '我開槍']
  propose() 取第一句 → 替玩家建立了一個移動 proposal
```

`app/agents/executor.py:232` 規定:proposal 存在而未到達時,把 `resolved` / `resolved_without_check` / `no_mechanics` **降級成** `incomplete` + `arrival_not_committed`。所以玩家只是開了一槍、機制也結算完了,卻會收到「這次行動尚未完整處理」——**正是本規格要修掉的那種回覆**。

`衝進` 在加進動詞表之前不會產生 proposal,所以這是本次改動造成的回歸。但同一個形狀對 `走進`(本來就在表裡)早就成立,例如「科比特走進客廳，我觀察」,所以順便一起修掉。

修法分兩層:

1. `movement_clauses` 的第三人稱開頭過濾補上 `牠|它|牠們|它們|某人`。
2. `propose()` 挑 clause 時跳過**歸屬於第三方**的句子(`movement._third_party_clause`):取移動動詞**之前**那段前綴,前綴為空(動詞開頭,如「直奔商店」)或含第一人稱(我/咱/自己)就放行;前綴命中已知的其他行動者名字——其他玩家角色、戰鬥序列中的非 PC、`scenario_npc_index` 的名字與別名(長度 ≥ 2)——或命中一小組泛稱(怪物/影子/敵人/那隻…)才拒絕。

**只在有正面證據時拒絕。** 這個方向是刻意的:多出來的 proposal 會弄壞一整個回合,少一個則不會——Executor 仍然可以自己引用 `source_span` 呼叫 `commit_movement`,由完整驗證裁決。

### 2. 記錄每一次移動拒絕

`movement.py` 在**每一條**拒絕路徑發一個 `movement.rejected` 事件，帶上:錯誤碼、`page`、`destination`、`path` 長度、`source_span`(截斷)、以及當下 `state.scene_maps` 的鍵。層級 WARNING。

`source_span` 與 `destination` 是玩家與劇本的文字，log 裡本來就已經有(router 的 `command_name`、StateReducer 的 facts 都帶原文)，不新增外洩面;識別碼一律走 `observability.safe_identifier`。

### 3. `source_span` 允許「clause 的前綴」

現行的完全相等規則**保留不動**，另外**加上**一條:

```text
span 是某個 clause 的前綴  且  len(span) >= 4  且  span 自己含移動動詞或方向意圖
```

純加法:今天過得了的，明天一樣過得了。新放行的只有「模型引用了一句複合句裡的移動前半段」這一種，而且那半段必須自己就是一個看得出來的移動表述。中段片段不放行——用逗號分隔的情況 `movement_clauses` 本來就會切開。

### 4. blocked 的 fallback 說人話

`_record_check_status` 記下最後一次移動拒絕的錯誤碼(`status['movement_blocked']`)，`enforce_mechanic_check_consistency` 把它翻成一句可行動的中文，例如:

實作出來的對應表(`prompt_config.MOVEMENT_BLOCKED_ADVICE`)共十條,比原本草擬的四條多,因為 `_validate` 裡還有幾個同樣可行動的碼:

| 錯誤碼 | 玩家看到 |
| --- | --- |
| `no_player_movement_authorization` | 沒有看懂你要移動到哪裡；請把移動單獨寫成一句（例如「前往商店，然後購買油燈」）。 |
| `unknown_map` | 目的地不在這份劇本現有的樓層圖上；請先說明你從哪個已知地點出發。 |
| `map_entry_required` | 你們還沒有進入這張樓層圖；請先描述從入口進入，再往裡面走。 |
| `movement_evidence_missing` | 這次移動還沒有劇本原文可以佐證；請先描述你在現場看到什麼，或換一個劇本提過的地點。 |
| `destination_not_supported_by_evidence` | 劇本裡找不到這個地點；請改用劇本提過的地點名稱。 |
| `known_map_location_requires_path` | 這個地點在樓層圖上，要沿著房間之間的路徑過去；請說明你經過哪些地方。 |
| `disconnected_movement_path` | 從目前位置沒辦法直接到那裡；請先說明中間會經過哪些地方。 |
| `passage_blocked` | 這條路目前不通。 |
| `movement_prerequisites_unresolved` | 還有尚未完成的檢定；請先完成它，再繼續移動。 |
| `movement_waits_for_final_luck` | 你還有一個幸運選擇沒有決定；請先決定，再繼續移動。 |

沒有對應碼時維持原本那句。**不改 disposition，也不改任何機制結果**——只換說法。優先順序不動:pending 檢定、待決幸運、`scenario_evidence_blocked` 仍然排在移動說明之前。同一回合內後來成功的 `commit_movement` 會清掉這個碼——那次移動確實發生了。

## 測試

- 新動詞:`直奔商店` 等每個新詞都認得，且 `他右手邊有把刀`、`我拿走桌上的刀` 這類不被誤判。
- `movement.rejected` 在每一條拒絕路徑都發得出來，且帶得出錯誤碼與 `scene_maps` 鍵;劇本標題等識別碼經雜湊。
- 前綴授權:`直奔商店購買油燈跟煤油罐` 配 `span='直奔商店'` 成立;`span='直'`(太短)、`span='購買油燈'`(沒有移動意圖)、`span='商店購買'`(不是前綴)皆不成立;今天已經成立的完全相等案例全數保持成立。
- fallback:每個對應碼各產生自己那句，未知碼回到原句;`state_changed` 與 pending 的既有分支不受影響。

變異驗證:把新動詞拿掉、把前綴分支拿掉、把錯誤碼對應表拿掉，各自要有測試紅。

## 限制

這不解決「模型為什麼挑了錯的 `page`」——第 2 項只是讓下一次查得到。也不改 `map_entry_required`:新團第一次上圖仍然要從 entry room 進入，那是另一個題目。
