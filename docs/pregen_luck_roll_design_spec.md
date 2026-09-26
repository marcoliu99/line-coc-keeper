# 預製角色 LUCK 與技能名稱整合規格

> LUCK 卡面值的最新選用規則請見 [預製角色卡 LUCK 值優先設計規格](pregen_sheet_luck_design_spec.md)；
> 本文中「所有預製角色都要重擲」的敘述已由新規格取代。

## 目的

預製角色是共享的劇本資料；玩家先認領角色，再主動完成 LUCK 擲骰。劇本抽取與建角輸入的技能名稱必須使用同一套 canonical alias 規則，避免產生玩家實際檢定讀不到的重複 key。

## 行為契約

- `/coc usepregen` claim 時不自動產生 LUCK，先建立持久化的 `pending_pregen_luck`。
- 玩家輸入 `/coc luck roll` 後，Bot 才執行該玩家主動要求的 `3d6 × 5`，並寫入已 claim 的 Character；目前不接受玩家自行提交未驗證的骰值。
- 不讀取或改寫共享 `state.pregens[*]["luck"]`；完成 LUCK 後同一角色不可再次 `/coc luck roll`。
- `/coc start` 在任何角色尚未完成玩家 LUCK roll 時必須被擋下。
- `/coc pregen` 預覽不把 PDF 的 LUCK 顯示成固定值，而是標註「玩家取用時重新骰定」；缺值時標註玩家取用時骰定。
- 其他八項基本屬性維持原本 extraction/default 行為。
- 抽取技能先經 `canonical_skill_name()`；同一 canonical key 的數字值衝突取較大值，未知自訂技能原樣保留。
- `/coc alloc` 在 session.skills 讀寫前 canonicalize，避免「手槍」與「射擊（手槍）」分裂。
- `scripts.migrate_skill_names` 直接使用 `SKILL_ALIASES` 清理既有 group state 與 character mirror，且可重複執行。

## 明確不處理

藝術／工藝、科學、語言等依劇本定義的特化技能不加入全域 alias，因為它們沒有單一正確名稱。Production migration 也不會自動執行，需由部署者明確執行：

```text
    python3 -m scripts.migrate_skill_names --dry-run
    python3 -m scripts.migrate_skill_names --apply
```

## 驗收

測試必須涵蓋 constructor 不自動骰、玩家提供 LUCK 結果、pending state、重複 roll 被拒絕、preview 標示、alias 去重、數值 max merge、unknown homebrew、`/coc alloc` stacking、SQLite group state／pregens／character mirror 與 migration idempotency。
