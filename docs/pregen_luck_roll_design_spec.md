# 設計規格：預製角色（Pregen）取用時也要骰幸運

> 適用範圍：`main` 分支（目前還是 `app/commands.py` 單檔架構，不是 `main_v2` 上重構過的
> `app/commands/router.py` + `app/legacy_commands.py` 版本）。

## 目標

`/coc pc`／`/coc create`（玩家自建角色）建立時，LUCK 一律用官方 3D6×5 骰出來（`app/models.py`
的 `quick_generate()`、`app/creation.py` 的 `start_creation()`）。但 `/coc usepregen`（玩家取用
劇本內建的預製角色）目前是直接讀取劇本 PDF 上印的 LUCK 數字（`app/pregen_extractor.py` 的
`pregen_to_character()`：`luck = _int_or(pregen.get("luck"), 50)`），抽取失敗才退回固定 50——
兩條路徑對 LUCK 的處理不一致。

本次要讓預製角色比照自建角色：**取用預製角色的當下，也用 3D6×5 重新骰一次 LUCK**，不使用劇本
PDF 上印的數字（也不用固定 50 當退回值）。這符合真實 COC7e 出版品的常見慣例——預製角色包
（pregen packet）通常會註明「請自行骰出你的幸運值」，因為幸運代表的是這個玩家的個人運勢，不是
角色模板作者能替玩家決定的東西。

## 範圍與非目標

本期包含：

- `pregen_to_character()` 不再讀取／使用 `pregen.get("luck")`，改成呼叫骰值。
- 決定「骰值」這件事共用的實作放哪裡，避免變成第三份重複的 `_roll(3, 6, 5)`（目前
  `app/models.py`／`app/creation.py` 各自已有一份幾乎一樣的私有 `_roll` helper）。
- `/coc pregens`（取用前的預覽文字，`_pregen_full_sheet_text`）要不要調整 LUCK 那一欄的顯示，
  讓玩家知道實際拿到的值會跟預覽不同。

本期不包含：

- 不改動 `/coc pc`／`/coc create` 既有的骰值邏輯（本來就已經在骰，不用動）。
- 不改動預製角色的其他屬性（STR/CON/DEX/APP/INT/POW/EDU）——這些繼續照抽取值/預設 50 走，
  只有 LUCK 這一項改變行為。COC7e 官方預製角色包的慣例也只單獨把 LUCK 留給玩家骰，其他屬性
  仍然是模板作者事先設計好的，這是刻意的差異，不是漏改。
- 不改動 `pregen_extractor.py` 的 LLM 抽取 schema／prompt（仍然嘗試抽取 `luck` 欄位）——見下方
  「LLM 抽取欄位要不要拿掉」的說明，決定保留只是不在 `pregen_to_character` 裡使用它。
- 不影響 `main_v2` 分支——那邊已經是重構過的架構，`pregen_extractor.py` 的邏輯應該也一樣需要
  這個修正，但那是另一個分支的事，不在這次改動範圍。

## 現況（`main` 分支）

```python
# app/pregen_extractor.py
def pregen_to_character(pregen: dict[str, Any], owner_id: str, era: str = "1920s") -> Character:
    str_ = _int_or(pregen.get("str_"), 50)
    ...
    luck = _int_or(pregen.get("luck"), 50)   # 讀 PDF 抽取值，抽取失敗退回 50
    ...
```

呼叫點只有一處，`app/commands.py:2132`（`/coc usepregen <編號>` 的處理邏輯）：

```python
char = pregen_extractor.pregen_to_character(pregen, user_id, era=state.era)
```

`pregen` 這個 dict 本身（劇本抽取出來、存在 `state.pregens` 裡、可能被多個玩家瀏覽但只有一個人
能實際 claim）的 `luck` 欄位除了這裡，只在 `/coc pregens` 的預覽文字（`_pregen_full_sheet_text`，
`app/commands.py:1643-1645`）跟合併去重邏輯（`reconcile_pregen_into_pool` 的
`_MERGE_ATTR_KEYS`，`app/pregen_extractor.py:580`）裡被讀取或比較。

## 設計

### 骰值時機與位置：在 `pregen_to_character()` 裡骰，不是在抽取時

骰值必須發生在**玩家實際 claim 的當下**（`pregen_to_character()` 被呼叫時），不是在劇本上傳、
LLM 抽取預製角色資料的當下。原因：`state.pregens` 是這個劇本庫存的共用清單，同一個預製角色檔案
理論上可能被不同玩家瀏覽、甚至（不同團的情況下）被不同的人取用——如果骰值時機提早到抽取階段，
LUCK 就變成寫死在共用資料裡的固定值，任何人取用同一個預製角色都拿到一樣的 LUCK，等於沒有真的
達到「幫這個玩家骰他自己的幸運」的目的。`pregen_to_character()` 每次呼叫都是針對一個特定
`owner_id`，是唯一正確的骰值時機。

### 避免第三份重複的骰值邏輯

`app/models.py`（`quick_generate()`）與 `app/creation.py`（`start_creation()`）各自已經有一份
幾乎一模一樣的私有函式：

```python
def _roll(n: int, sides: int, mult: int = 1) -> int:
    return sum(random.randint(1, sides) for _ in range(n)) * mult
```

`pregen_to_character()` 只需要 `_roll(3, 6, 5)` 這一種呼叫方式（COC7e 屬性骰值的標準寫法），
不需要引入 `app/dice.py` 的 `roll_expression()`（那是給 `NdM+K` 加法修正式的一般骰子指令用的，
不支援「乘以 5」這種屬性生成專用的縮放，這也是為什麼 `models.py`/`creation.py` 沒有直接用它）。

**建議**：`app/pregen_extractor.py` 直接 `from app.models import _roll`（該模組已經
`from app.models import BASE_SKILLS, Character, damage_bonus_and_build, move_rate`，加一個
名稱不算新的耦合方向）而不是複製第三份。這是本文唯一不是十拿九穩的小決定：如果你比較希望維持
每個模組各自獨立、不要互相 import 底線開頭的「私有」函式，跟我說一聲，改成在
`pregen_extractor.py` 本地再放一份 `_roll` 也完全沒問題，三份重複目前也沒有真的造成過問題。

### LLM 抽取欄位要不要拿掉？——保留，只是不用在 `pregen_to_character`

`pregen_extractor.py` 抽取劇本 PDF 時，仍然嘗試讀取每個預製角色卡上印的 `luck` 數字（schema
第 53 行、比對表第 220 行不動）。**保留**這個抽取邏輯，理由：

1. `/coc pregens` 的預覽文字（見下一小節）仍然想讓玩家看到「這張卡原本印的 LUCK 是多少」當參考
   ——即使玩家實際拿到的值會重骰，看原始卡面資訊本身沒有壞處。
2. 拿掉抽取邏輯是一個額外的改動面（要動 schema、prompt、比對表、合併邏輯），換來的好處只是省一
   點點抽取 token，跟本次「取用時重骰」這個目標無關，不值得在同一個改動裡一起做。

### `/coc pregens` 預覽文字：LUCK 那一欄要標明「取用時另外骰」

`_pregen_full_sheet_text` 目前是 `f"{labels[a]} {pregen[a]}"` 這種格式，LUCK 會跟其他屬性一樣
顯示成 `LUCK 65` 這種確定數字——玩家會誤以為選這個角色就拿到這個 LUCK。改成：

```python
attrs = ["str_", "con", "siz", "dex", "app", "int_", "pow_", "edu"]  # luck 從這裡拿掉
...
attr_line = " ".join(f"{labels[a]} {pregen[a]}" for a in attrs if isinstance(pregen.get(a), (int, float)))
if isinstance(pregen.get("luck"), (int, float)):
    attr_line += f"（LUCK 卡面原始值 {pregen['luck']}，取用時將重新骰定）"
elif attr_line:
    attr_line += "（LUCK 將於取用時骰定）"
```

（上面是示意寫法，實作時視現有程式碼風格調整，不是要求逐字照抄。）

## 測試驗收

至少應涵蓋：

1. `pregen_to_character()` 回傳的 `Character.luck` 是 3D6×5 範圍內（3~18 × 5 = 15~90）的隨機值，
   不等於 `pregen` dict 裡原本的 `luck` 欄位值（用固定 `random.seed` 或多次呼叫統計分佈驗證，
   不能只驗證「有沒有拋例外」）。
2. `pregen` dict 完全沒有 `luck` 欄位（抽取失敗的情況）時，`pregen_to_character()` 仍然正確
   骰出 LUCK，不會退回舊的預設 50、也不會拋例外。
3. 同一個 `pregen` dict 被兩個不同 `owner_id` 分別呼叫 `pregen_to_character()`（模擬同一預製
   角色理論上可能在不同團被不同人取用），兩次骰出的 LUCK 應該（極高機率）不同——驗證骰值真的
   是每次呼叫獨立進行，不是被快取或寫回 `pregen` dict 本身。
4. `/coc usepregen` 端對端流程：取用後回傳的角色卡文字（`char.sheet_text()`）顯示的 LUCK 是
   剛骰出的新值，不是劇本卡面原始值。
5. `/coc pregens` 預覽文字裡 LUCK 不再顯示成跟其他屬性一樣的確定數字格式，而是標明會重骰
   （或找不到卡面原始值時的對應文字）。
6. 其餘 8 個屬性（STR/CON/SIZ/DEX/APP/INT/POW/EDU）跟這次改動前行為一致，仍然使用抽取值／
   預設 50，不受影響。

## 實作順序

1. `app/pregen_extractor.py`：`pregen_to_character()` 改用 `_roll(3, 6, 5)` 取代
   `_int_or(pregen.get("luck"), 50)`（引入 `_roll` 的方式見上方「避免第三份重複」小節，等你
   確認要 import 還是本地複製一份再動手）。
2. `app/commands.py`：`_pregen_full_sheet_text` 的 LUCK 顯示邏輯調整。
3. `tests/`：新增／調整涵蓋上面 6 點測試驗收的測試（這個分支的測試目錄與既有測試檔案，比照
   `main` 分支目前的慣例——`tests/test_kp_assistant_v2.py`／`tests/test_natural_1_bonus.py`
   等既有檔案的寫法／`unittest` 慣例）。
