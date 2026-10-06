# Page-repair template for `repair_` uploads

[繁體中文](scenario_page_repair_template_zh.md)

Use this file to correct some physical pages of the PDF scenario that is loaded in the conversation. Fill it in from the original PDF (with ChatGPT or another reviewer if you like), save it as `repair_<name>.md` and upload it. The bot replaces only the listed pages, publishes a new version and leaves the original untouched; if any check fails, nothing is applied. Design: `specs/feature/discord_page_level_scenario_repair_design_spec.md`.

> Status: the upload itself is being delivered in phases and is not switched on yet; this template and its parser already ship so the format is fixed.

## What to copy from the bot

Nothing to compute and no hash to copy. Take two values from the bot's load message for the scenario (`已載入劇本《…》`, `共 N 頁（實體頁數）`): the title and the physical page count.

## Rules

- Exactly **one** `json` block; text outside it is ignored. An unknown key anywhere is rejected.
- `repair_version` is `1`.
- `target.title` is the scenario title exactly as the load message shows it and `target.page_count` is the PDF's physical page count. A file made for another scenario, or for a PDF with another page count, is rejected.
- `page` is the **physical PDF page** (1 is the first page of the PDF), not the page number printed in the book. One entry per page, 1 to 100 entries, any order.
- `text` is the **complete** corrected text of that page and replaces the whole page; partial edits are not supported. Do not write `--- 第 N 頁 ---`: the system owns the page markers.
- `page_kind`: `text` (normal page), `map` (a floor plan or diagram: transcribe the readable labels, do not invent room descriptions; low text volume is expected) or `image` (only when the page truly has no readable text and a stored page image exists; `text` must then be empty).
- `review_note` says what you checked against the PDF page and what you corrected; it cannot be empty.
- Every number you change is reported privately to the KP (`HP 10` → `HP 40`, `1D40` → `1D4`, `+10%` → `-10%`). A correction is allowed to change numbers; the report is how the Keeper sees that it did.

## Template

```json
{
  "repair_version": 1,
  "target": {
    "title": "<scenario title from the load message>",
    "page_count": "<physical page count from the load message, a number>"
  },
  "patches": [
    {
      "page": "<physical page number, a number>",
      "text": "<the complete corrected text of that page>",
      "page_kind": "text",
      "review_note": "<what you checked against the PDF page and what you corrected>"
    }
  ]
}
```

Add one object to `patches` for every page you replace.
