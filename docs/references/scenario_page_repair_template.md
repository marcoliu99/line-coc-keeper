# Page-repair template for `repair_` uploads

[繁體中文](scenario_page_repair_template_zh.md)

When a few pages of the loaded scenario came out wrong (the load message lists them as needing a check), re-extract just those pages from the original PDF with ChatGPT, Gemini or any other tool, save the result as `repair_<name>.md` and upload it. The bot overwrites those pages in the running game, like a `role_*.md` card does for a pregen. Nothing else changes: the other pages, the characters, the progress and the position are untouched, and uploading the same file again simply overwrites again.

## Format

Each page starts with the same marker line the scenario text uses, followed by the **complete** corrected text of that page:

```
--- 第 2 頁 ---
<the complete corrected text of physical page 2>

--- 第 4 頁 ---
<the complete corrected text of physical page 4>
```

- The number is the **physical PDF page** (1 is the first page of the PDF), not the page number printed in the book.
- A page replaces the whole page; partial edits are not supported. Add one marker per page you replace, in any order.
- Text before the first marker (a heading, a note) is ignored, and a code block around the whole answer is fine.
- A page number the scenario does not have, a repeated page and an empty page are rejected, and nothing is applied.

## Good to know

- The repair belongs to this conversation and this scenario, like a `role_*.md` card: loading the scenario again from the library (`/coc scenario use`) lays the saved pages over the original text. It is dropped only when the library entry itself changes (a reparse or a new upload of the PDF).
- Only one `repair_` file per message.
