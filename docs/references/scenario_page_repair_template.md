# Page-repair template for `repair_` uploads

[繁體中文](scenario_page_repair_template_zh.md)

Use this file to correct only some physical pages of a PDF scenario that is already loaded. Fill it in, save it as
`repair_<scenario>_01.md` and upload it to the conversation. The bot checks it against the exact source version it was
exported from, replaces only the pages listed, and publishes a new version; the original is never edited. Nothing is
applied if any check fails. Design: `specs/feature/discord_page_level_scenario_repair_design_spec.md`.

Where the bot offers it, the easiest start is its own export (`/coc repair export warnings` or `/coc repair export 2,4,6`,
KP only, sent by direct message): it fills in the `target` block, the page hashes and the evidence rectangles for you. Copy this
template only if you need to write one by hand.

## Rules

- Keep **exactly one** `json` block. Everything outside it is ignored. Unknown keys anywhere are rejected.
- Do not change `repair_version` or anything under `target`; they bind the file to one source version.
- `page` is the **physical PDF page** (1 is the first page of the PDF), not the number printed in the book.
- `text` is the **complete** corrected text of that page. It replaces the whole page; partial edits are not supported.
  Do not write `--- 第 N 頁 ---` lines: the system owns the page markers.
- `base_page_sha256` is the hash of the page text you started from, copied from the export. If the source changed since,
  the file is rejected as stale and you must export again.
- `page_kind`: `text` (normal pages), `map` (floor plans or diagrams: transcribe the readable labels, do not invent
  room descriptions) or `image` (only when the page truly has no readable text; `text` must then be empty).
- `review_note` says what you checked against the PDF page and what you corrected. It cannot be empty.
- `evidence` lists rectangles of the physical page you checked, `[x0, y0, x1, y1]` inside the page bounds, each with a note.
  The export fills in one full-page rectangle; keep it unless you want to narrow it.
- `expected_numeric_delta` declares every number you changed on that page, as counts of tokens removed and added.
  A token keeps its sign and joins operands written with `/` or `-`: `+10%` and `-10%` differ, `1/1d6` and `1 1d6`
  differ, `1-3` is one token. Lower-case, no spaces: `1d4+1`. Anything changed but not declared, or declared but not
  changed, rejects the whole repair. Use `{}` when you changed no numbers.

## Template

```json
{
  "repair_version": 1,
  "target": {
    "scenario_id": "<copy from the export>",
    "content_hash": "<copy from the export>",
    "pdf_sha256": "<copy from the export>",
    "page_count": 0
  },
  "patches": [
    {
      "page": 1,
      "base_page_sha256": "<copy from the export>",
      "text": "<the complete corrected text of physical page 1>",
      "page_kind": "text",
      "review_note": "<what you checked against the PDF page and what you corrected>",
      "evidence": [
        {
          "bbox": [0.0, 0.0, 595.0, 842.0],
          "note": "<for example: full page checked against the rendered PDF>"
        }
      ],
      "expected_numeric_delta": {
        "removed": {"1d40": 1},
        "added": {"1d4": 1}
      }
    }
  ]
}
```

Add one object to `patches` for each page you replace, at most 100, one per page.
