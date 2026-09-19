# 預製角色 LUCK 與技能名稱整合規格

## 目的

預製角色是共享的劇本資料，但 LUCK 應在玩家真正認領時獨立擲出；同時，劇本抽取與建角輸入的技能名稱必須使用同一套 canonical alias 規則，避免產生玩家實際檢定讀不到的重複 key。

## 行為契約

- `pregen_to_character()` 在 claim time 使用 `app.models._roll(3, 6, 5)` 產生 LUCK。
- 不讀取或改寫共享 `state.pregens[*]["luck"]`；不同玩家認領同一預製角色時各自獨立擲骰。
- `/coc pregen` 預覽不把 PDF 的 LUCK 顯示成固定值，而是標註「取用時將重新骰定」；缺值時標註將於取用時骰定。
- 其他八項基本屬性維持原本 extraction/default 行為。
- 抽取技能先經 `canonical_skill_name()`；同一 canonical key 的數字值衝突取較大值，未知自訂技能原樣保留。
- `/coc alloc` 在 session.skills 讀寫前 canonicalize，避免「手槍」與「射擊（手槍）」分裂。
- `scripts.migrate_skill_names` 直接使用 `SKILL_ALIASES` 清理既有 group state 與 character mirror，且可重複執行。

## 明確不處理

藝術／工藝、科學、語言等依劇本定義的特化技能不加入全域 alias，因為它們沒有單一正確名稱。Production migration 也不會自動執行，需由部署者明確執行：

```text
python3 -m scripts.migrate_skill_names
```

## 驗收

測試必須涵蓋 claim-time reroll、無 PDF LUCK、不同 claim 獨立值、preview 標示、alias 去重、數值 max merge、unknown homebrew、`/coc alloc` stacking、SQLite group state／pregens／character mirror 與 migration idempotency。
