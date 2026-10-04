# The Lightless Beacon: PDF review-warning triage

## Executive summary

- **Scope:** 43-page replay on `main_v2` plus rich-candidate selection (`6b2db66d`), PDF SHA-256 `14437ca4eca3b6c05b22403bdad30bdb8668f1837e0a2d0ee42e49c160d8aa07`. The replay reproduced **23 review pages and 66 warning entries**. No production code was changed.
- **Known defects:** p13 has corrupted core reading order; p18 and p29 contain explicit unreadable spans in selected handout OCR. p3 has a credits-order issue, not core gameplay. These are observed defects, unlike a mere warning count.
- **Mechanics:** Across text-backed pages, 29 source-bound pair checks matched and all native mechanics expressions found in selected source were preserved. The image-only character sheets introduce many unverified stats and skills; this audit cannot prove those values correct. **No known P0 mechanics corruption remains** in the available source-bound evidence, but image-form mechanics remain review-required.
- **Mock boundary:** saved MarkItDown candidates were reused; vision/map and AI repair were replaced by no-op analysis stubs. No scene map or visual completeness is certified by this replay. It cannot establish a production upload result or prove that any image-only page is complete.
- **Action:** fix core p13 ordering first, then handout OCR p18/p29; retain rich-candidate review. Consider only source-bound, generic folio/TOC warning precision work after tests. Do not remove warnings merely to reduce the total.

## Evidence and method

The PDF hash was checked before replay. Existing stored MarkItDown page candidates were injected without another provider call. The current native extraction, PyMuPDF4LLM with its internal OCR disabled, source selection, local OCR, numeric checks, and review policy ran in the analysis worktree. Vision/map and AI repair were mocked, so image-derived outputs were not independently validated. The replay made **zero external provider calls**. The saved older `parse_quality.json` records 75 warnings under an older parser version and is not used as the current 66-warning result. The report uses only metadata and short evidence descriptions; it does not duplicate copyrighted page text.

Category codes: A REAL_TEXT_CORRUPTION; B REAL_MECHANICS_RISK; C LAYOUT_FALSE_POSITIVE; D RICH_CANDIDATE_UNVERIFIED; E IMAGE_OR_MAP_REVIEW; F EXPECTED_REVIEW; G UNKNOWN. C means the *warning condition* is harmless or historical for the selected source, with the stated evidence; it does not certify the whole page. F includes historical low-text signals that no longer solely drive final review.

## All 66 warning entries

| # | Page | Warning | Emitter / source | Triggering evidence | Selected | MarkItDown | Mechanics | Layout | OCR | Image/map | Gameplay risk | Class | Reason |
|---:|---:|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | `low_text` | source length/empty check | native 130 chars; final 246 chars | `markitdown` | yes | no known | no | yes | no | none shown | **F** | initial native/layout low-text history; selected source may be longer |
| 2 | 2 | `low_text` | source length/empty check | native 79 chars; final 122 chars | `markitdown` | yes | no known | no | yes | yes | none shown | **C** | visual cover has title/credits only; selected title is present |
| 3 | 3 | `ambiguous_columns` | native geometry | native gutter ambiguous; selected layout order visually checked | `layout` | no | no known | yes | no | no | none shown | **A** | credits reading order interleaves columns |
| 4 | 4 | `local_ocr_review` | local OCR | 14 TOC leader spans; 8 review_required, 6 budget_exhausted | `layout` | no | no known | no | yes | no | none shown | **C** | TOC dotted leaders render as replacement glyphs; 8 attempted, 6 budgeted out |
| 5 | 5 | `layout_unavailable` | native→layout comparator | layout 0 chars, native 0 chars | `native` | no | no known | yes | no | yes | visual-source dependent | **E** | full-page illustration has no native/layout text |
| 6 | 5 | `low_text` | source length/empty check | native 0 chars; final 0 chars | `native` | no | no known | no | no | yes | visual-source dependent | **E** | full-page illustration, no running prose |
| 7 | 5 | `empty_page` | source length/empty check | no selected source text; rendered page contains illustration | `native` | no | no known | no | no | yes | visual-source dependent | **E** | illustration is visually nonempty; source text absent |
| 8 | 6 | `native_two_columns` | native geometry | native geometry: two clean columns; selected native | `native` | no | no known | yes | no | no | none shown | **C** | native gutter detection only; selected source has no unresolved pair loss |
| 9 | 6 | `layout_numeric_loss` | native→layout comparator | layout omits folio 5 | `native` | no | no known | yes | no | no | none shown | **C** | native folio 5, not a rule value |
| 10 | 7 | `native_two_columns` | native geometry | native geometry: two clean columns; selected layout | `layout` | no | no known | yes | no | no | none shown | **C** | native gutter detection only; selected source has no unresolved pair loss |
| 11 | 8 | `native_two_columns` | native geometry | native geometry: two clean columns; selected layout | `layout` | no | no known | yes | no | no | none shown | **C** | native gutter detection only; selected source has no unresolved pair loss |
| 12 | 9 | `native_two_columns` | native geometry | native geometry: two clean columns; selected layout | `layout` | no | no known | yes | no | no | none shown | **C** | native gutter detection only; selected source has no unresolved pair loss |
| 13 | 10 | `native_two_columns` | native geometry | native geometry: two clean columns; selected layout | `layout` | no | no known | yes | no | no | none shown | **C** | native gutter detection only; selected source has no unresolved pair loss |
| 14 | 11 | `native_two_columns` | native geometry | native geometry: two clean columns; selected layout | `layout` | no | no known | yes | no | no | none shown | **C** | native gutter detection only; selected source has no unresolved pair loss |
| 15 | 12 | `native_two_columns` | native geometry | native geometry: two clean columns; selected layout | `layout` | no | no known | yes | no | no | none shown | **C** | native gutter detection only; selected source has no unresolved pair loss |
| 16 | 13 | `ambiguous_columns` | native geometry | native gutter ambiguous; selected layout order visually checked | `layout` | no | no known | yes | no | no | YES—core order | **A** | core narrative has decorative glyphs inserted into sentence/order |
| 17 | 14 | `native_two_columns` | native geometry | native geometry: two clean columns; selected layout | `layout` | no | no known | yes | no | no | none shown | **C** | native gutter detection only; selected source has no unresolved pair loss |
| 18 | 15 | `native_two_columns` | native geometry | native geometry: two clean columns; selected layout | `layout` | no | no known | yes | no | no | none shown | **C** | native gutter detection only; selected source has no unresolved pair loss |
| 19 | 16 | `layout_text_loss` | native→layout comparator | native 52 / layout 25 chars; selected image OCR 816 | `markitdown` | yes | no known | yes | no | yes | visual-source dependent | **E** | short native/layout baseline against image-derived map or handout source |
| 20 | 16 | `low_text` | source length/empty check | native 52 chars; final 816 chars | `markitdown` | yes | no known | no | yes | yes | visual-source dependent | **E** | image map/handout requires visual-source review |
| 21 | 17 | `layout_numeric_loss` | native→layout comparator | layout omits handout identifier 1 | `markitdown` | yes | no known | yes | no | yes | none shown | **C** | native handout identifier retained in selected MarkItDown text |
| 22 | 17 | `layout_text_loss` | native→layout comparator | native 61 / layout 68 chars; selected image OCR 1292 | `markitdown` | yes | no known | yes | no | yes | visual-source dependent | **E** | short native/layout baseline against image-derived map or handout source |
| 23 | 17 | `low_text` | source length/empty check | native 61 chars; final 1292 chars | `markitdown` | yes | no known | no | yes | yes | visual-source dependent | **E** | image map/handout requires visual-source review |
| 24 | 18 | `layout_numeric_loss` | native→layout comparator | layout omits handout identifier 2 | `markitdown` | yes | no known | yes | no | yes | none shown | **C** | native handout identifier retained in selected MarkItDown text |
| 25 | 18 | `layout_text_loss` | native→layout comparator | native 44 / layout 54 chars; selected image OCR 2069 | `markitdown` | yes | no known | yes | no | yes | visual-source dependent | **E** | short native/layout baseline against image-derived map or handout source |
| 26 | 18 | `low_text` | source length/empty check | native 44 chars; final 2069 chars | `markitdown` | yes | no known | no | yes | yes | possible—handout text | **A** | selected handwritten handout OCR has explicit unreadable spans |
| 27 | 19 | `native_two_columns` | native geometry | native geometry: two clean columns; selected native | `native` | no | no known | yes | no | no | none shown | **C** | native gutter detection only; selected source has no unresolved pair loss |
| 28 | 19 | `layout_numeric_loss` | native→layout comparator | layout omits handout identifier 3 | `native` | no | no known | yes | no | no | none shown | **C** | handout identifier 3 retained in selected native text |
| 29 | 20 | `native_two_columns` | native geometry | native geometry: two clean columns; selected layout | `layout` | no | no known | yes | no | no | none shown | **C** | native gutter detection only; selected source has no unresolved pair loss |
| 30 | 21 | `native_two_columns` | native geometry | native geometry: two clean columns; selected layout | `layout` | no | no known | yes | no | no | none shown | **C** | native gutter detection only; selected source has no unresolved pair loss |
| 31 | 22 | `native_two_columns` | native geometry | native geometry: two clean columns; selected layout | `layout` | no | no known | yes | no | no | none shown | **C** | native gutter detection only; selected source has no unresolved pair loss |
| 32 | 23 | `native_two_columns` | native geometry | native geometry: two clean columns; selected layout | `layout` | no | no known | yes | no | no | none shown | **C** | native gutter detection only; selected source has no unresolved pair loss |
| 33 | 24 | `native_two_columns` | native geometry | native geometry: two clean columns; selected layout | `layout` | no | no known | yes | no | no | none shown | **C** | native gutter detection only; selected source has no unresolved pair loss |
| 34 | 25 | `layout_text_loss` | native→layout comparator | native 67 / layout 102 chars; selected image OCR 431 | `markitdown` | yes | no known | yes | no | yes | visual-source dependent | **E** | short native/layout baseline against image-derived map or handout source |
| 35 | 25 | `low_text` | source length/empty check | native 67 chars; final 431 chars | `markitdown` | yes | no known | no | yes | yes | visual-source dependent | **E** | image map/handout requires visual-source review |
| 36 | 27 | `layout_text_loss` | native→layout comparator | native 69 / layout 39 chars; selected image OCR 741 | `markitdown` | yes | no known | yes | no | yes | visual-source dependent | **E** | short native/layout baseline against image-derived map or handout source |
| 37 | 27 | `low_text` | source length/empty check | native 69 chars; final 741 chars | `markitdown` | yes | no known | no | yes | yes | visual-source dependent | **E** | image map/handout requires visual-source review |
| 38 | 28 | `layout_numeric_loss` | native→layout comparator | layout omits handout identifier 1 | `markitdown` | yes | no known | yes | no | yes | none shown | **C** | native handout identifier retained in selected MarkItDown text |
| 39 | 28 | `layout_text_loss` | native→layout comparator | native 44 / layout 54 chars; selected image OCR 1274 | `markitdown` | yes | no known | yes | no | yes | visual-source dependent | **E** | short native/layout baseline against image-derived map or handout source |
| 40 | 28 | `low_text` | source length/empty check | native 44 chars; final 1274 chars | `markitdown` | yes | no known | no | yes | yes | visual-source dependent | **E** | image map/handout requires visual-source review |
| 41 | 29 | `layout_numeric_loss` | native→layout comparator | layout omits handout identifier 2 | `markitdown` | yes | no known | yes | no | yes | none shown | **C** | native handout identifier retained in selected MarkItDown text |
| 42 | 29 | `layout_text_loss` | native→layout comparator | native 61 / layout 68 chars; selected image OCR 1728 | `markitdown` | yes | no known | yes | no | yes | visual-source dependent | **E** | short native/layout baseline against image-derived map or handout source |
| 43 | 29 | `low_text` | source length/empty check | native 61 chars; final 1728 chars | `markitdown` | yes | no known | no | yes | yes | possible—handout text | **A** | selected handwritten handout OCR has explicit unreadable spans |
| 44 | 30 | `layout_numeric_loss` | native→layout comparator | layout omits handout identifier 3 | `markitdown` | yes | no known | yes | no | yes | none shown | **C** | native handout identifier retained in selected MarkItDown text |
| 45 | 30 | `layout_text_loss` | native→layout comparator | native 44 / layout 54 chars; selected image OCR 401 | `markitdown` | yes | no known | yes | no | yes | visual-source dependent | **E** | short native/layout baseline against image-derived map or handout source |
| 46 | 30 | `low_text` | source length/empty check | native 44 chars; final 401 chars | `markitdown` | yes | no known | no | yes | yes | visual-source dependent | **E** | image map/handout requires visual-source review |
| 47 | 31 | `low_text` | source length/empty check | native 40 chars; final 4233 chars | `markitdown` | yes | no known | no | yes | yes | none shown | **F** | initial native/layout low-text history; selected source may be longer |
| 48 | 31 | `rich_candidate_unverified` | MarkItDown/source selection | weak baseline 101 chars; rich source 4233; extras unverified | `markitdown` | yes | possible | no | yes | yes | possible—unverified stats | **D** | weak baseline preserved; new image-form content has no independent validation |
| 49 | 32 | `low_text` | source length/empty check | native 39 chars; final 1823 chars | `markitdown` | yes | no known | no | yes | yes | none shown | **F** | initial native/layout low-text history; selected source may be longer |
| 50 | 32 | `rich_candidate_unverified` | MarkItDown/source selection | weak baseline 117 chars; rich source 1823; extras unverified | `markitdown` | yes | no known | no | yes | yes | none shown | **D** | weak baseline preserved; new image-form content has no independent validation |
| 51 | 33 | `low_text` | source length/empty check | native 56 chars; final 1822 chars | `markitdown` | yes | no known | no | yes | no | none shown | **F** | initial native/layout low-text history; selected source may be longer |
| 52 | 34 | `low_text` | source length/empty check | native 23 chars; final 4037 chars | `markitdown` | yes | no known | no | yes | yes | none shown | **F** | initial native/layout low-text history; selected source may be longer |
| 53 | 34 | `rich_candidate_unverified` | MarkItDown/source selection | weak baseline 87 chars; rich source 4037; extras unverified | `markitdown` | yes | possible | no | yes | yes | possible—unverified stats | **D** | weak baseline preserved; new image-form content has no independent validation |
| 54 | 35 | `low_text` | source length/empty check | native 47 chars; final 1912 chars | `markitdown` | yes | no known | no | yes | yes | none shown | **F** | initial native/layout low-text history; selected source may be longer |
| 55 | 35 | `rich_candidate_unverified` | MarkItDown/source selection | weak baseline 122 chars; rich source 1912; extras unverified | `markitdown` | yes | no known | no | yes | yes | none shown | **D** | weak baseline preserved; new image-form content has no independent validation |
| 56 | 36 | `low_text` | source length/empty check | native 30 chars; final 1548 chars | `markitdown` | yes | no known | no | yes | no | none shown | **F** | initial native/layout low-text history; selected source may be longer |
| 57 | 37 | `low_text` | source length/empty check | native 40 chars; final 3977 chars | `markitdown` | yes | no known | no | yes | yes | none shown | **F** | initial native/layout low-text history; selected source may be longer |
| 58 | 37 | `rich_candidate_unverified` | MarkItDown/source selection | weak baseline 101 chars; rich source 3977; extras unverified | `markitdown` | yes | possible | no | yes | yes | possible—unverified stats | **D** | weak baseline preserved; new image-form content has no independent validation |
| 59 | 38 | `low_text` | source length/empty check | native 36 chars; final 2015 chars | `markitdown` | yes | no known | no | yes | yes | none shown | **F** | initial native/layout low-text history; selected source may be longer |
| 60 | 38 | `rich_candidate_unverified` | MarkItDown/source selection | weak baseline 114 chars; rich source 2015; extras unverified | `markitdown` | yes | no known | no | yes | yes | none shown | **D** | weak baseline preserved; new image-form content has no independent validation |
| 61 | 39 | `low_text` | source length/empty check | native 53 chars; final 1411 chars | `markitdown` | yes | no known | no | yes | no | none shown | **F** | initial native/layout low-text history; selected source may be longer |
| 62 | 40 | `low_text` | source length/empty check | native 23 chars; final 4037 chars | `markitdown` | yes | no known | no | yes | yes | none shown | **F** | initial native/layout low-text history; selected source may be longer |
| 63 | 40 | `rich_candidate_unverified` | MarkItDown/source selection | weak baseline 87 chars; rich source 4037; extras unverified | `markitdown` | yes | possible | no | yes | yes | possible—unverified stats | **D** | weak baseline preserved; new image-form content has no independent validation |
| 64 | 41 | `low_text` | source length/empty check | native 57 chars; final 1683 chars | `markitdown` | yes | no known | no | yes | yes | none shown | **F** | initial native/layout low-text history; selected source may be longer |
| 65 | 41 | `rich_candidate_unverified` | MarkItDown/source selection | weak baseline 139 chars; rich source 1683; extras unverified | `markitdown` | yes | no known | no | yes | yes | none shown | **D** | weak baseline preserved; new image-form content has no independent validation |
| 66 | 42 | `low_text` | source length/empty check | native 40 chars; final 1111 chars | `markitdown` | yes | no known | no | yes | no | none shown | **F** | initial native/layout low-text history; selected source may be longer |

## The 23 review pages

| Page | Selected source (chars) | Warnings | Categories | Actual corruption? | Mechanics risk? | Safe warning removal? | Still review? |
|---:|---|---:|---|---|---|---|---|
| 2 | `markitdown` (122) | 1 | C | not demonstrated | none detected | only specific historical warning | YES |
| 3 | `layout` (2115) | 1 | A | YES—credits order, non-core | none detected | no | YES |
| 4 | `layout` (1584) | 1 | C | not demonstrated | none detected | only specific historical warning | YES |
| 5 | `native` (0) | 3 | E,E,E | not demonstrated | none detected | no | YES |
| 6 | `native` (3408) | 2 | C,C | not demonstrated | none detected | only specific historical warning | YES |
| 13 | `layout` (3631) | 1 | A | YES—core order | core text order | no | YES |
| 16 | `markitdown` (816) | 2 | E,E | not demonstrated | none detected; visual unknown | no | YES |
| 17 | `markitdown` (1292) | 3 | C,E,E | not demonstrated | none detected; visual unknown | only specific historical warning | YES |
| 18 | `markitdown` (2069) | 3 | C,E,A | YES—handout gaps | none detected; visual unknown | only specific historical warning | YES |
| 19 | `native` (3580) | 2 | C,C | not demonstrated | none detected | only specific historical warning | YES |
| 25 | `markitdown` (431) | 2 | E,E | not demonstrated | none detected; visual unknown | no | YES |
| 27 | `markitdown` (741) | 2 | E,E | not demonstrated | none detected; visual unknown | no | YES |
| 28 | `markitdown` (1274) | 3 | C,E,E | not demonstrated | none detected; visual unknown | only specific historical warning | YES |
| 29 | `markitdown` (1728) | 3 | C,E,A | YES—handout gaps | none detected; visual unknown | only specific historical warning | YES |
| 30 | `markitdown` (401) | 3 | C,E,E | not demonstrated | none detected; visual unknown | only specific historical warning | YES |
| 31 | `markitdown` (4233) | 2 | F,D | not demonstrated | unverified form values | no | YES |
| 32 | `markitdown` (1823) | 2 | F,D | not demonstrated | none detected; visual unknown | no | YES |
| 34 | `markitdown` (4037) | 2 | F,D | not demonstrated | unverified form values | no | YES |
| 35 | `markitdown` (1912) | 2 | F,D | not demonstrated | none detected; visual unknown | no | YES |
| 37 | `markitdown` (3977) | 2 | F,D | not demonstrated | unverified form values | no | YES |
| 38 | `markitdown` (2015) | 2 | F,D | not demonstrated | none detected; visual unknown | no | YES |
| 40 | `markitdown` (4037) | 2 | F,D | not demonstrated | unverified form values | no | YES |
| 41 | `markitdown` (1683) | 2 | F,D | not demonstrated | none detected; visual unknown | no | YES |

Pages with warnings but no final review: 1, 7–12, 14–15, 20–24, 33, 36, 39, 42. Their historical low-text or two-column warnings must not be read as unresolved final source failure.

## Mechanics audit across all 43 pages

For every page, the audit compared native-source bound label/value pairs and mechanics tokens against the selected canonical text, including dice count/size/modifier, slash-delimited SAN loss, and percentages. This checks preservation of **observable** native evidence, not truth of newly OCR-introduced values. It also reviewed stored geometry pair checks. “0/0” means there was no text-backed pair to check, not that an image-form page was verified.

| Page | Selected | Native→selected bound items | Native→selected mechanics tokens | Geometry pair checks | Audit result |
|---:|---|---:|---:|---|---|
| 1 | `markitdown` | 0/0 | 0/0 | none | observable mechanics preserved |
| 2 | `markitdown` | 0/0 | 0/0 | none | observable mechanics preserved |
| 3 | `layout` | 0/0 | 0/0 | none | observable mechanics preserved |
| 4 | `layout` | 0/0 | 0/0 | none | observable mechanics preserved |
| 5 | `native` | 0/0 | 0/0 | none | observable mechanics preserved |
| 6 | `native` | 0/0 | 0/0 | none | observable mechanics preserved |
| 7 | `layout` | 0/0 | 1/1 | none | observable mechanics preserved |
| 8 | `layout` | 0/0 | 2/2 | matched:1 | observable mechanics preserved |
| 9 | `layout` | 0/0 | 0/0 | none | observable mechanics preserved |
| 10 | `layout` | 0/0 | 1/1 | none | observable mechanics preserved |
| 11 | `layout` | 0/0 | 0/0 | none | observable mechanics preserved |
| 12 | `layout` | 0/0 | 3/3 | none | observable mechanics preserved |
| 13 | `layout` | 4/4 | 2/2 | matched:4 | core order defect |
| 14 | `layout` | 0/0 | 2/2 | none | observable mechanics preserved |
| 15 | `layout` | 0/0 | 0/0 | none | observable mechanics preserved |
| 16 | `markitdown` | 0/0 | 0/0 | none | observable mechanics preserved |
| 17 | `markitdown` | 0/0 | 0/0 | none | observable mechanics preserved |
| 18 | `markitdown` | 0/0 | 0/0 | none | handout OCR gaps |
| 19 | `native` | 0/0 | 1/1 | none | observable mechanics preserved |
| 20 | `layout` | 0/0 | 2/2 | none | observable mechanics preserved |
| 21 | `layout` | 0/0 | 3/3 | matched:1 | observable mechanics preserved |
| 22 | `layout` | 0/0 | 0/0 | none | observable mechanics preserved |
| 23 | `layout` | 10/10 | 1/1 | matched:10 | observable mechanics preserved |
| 24 | `layout` | 13/13 | 22/22 | matched:13 | observable mechanics preserved |
| 25 | `markitdown` | 0/0 | 0/0 | none | observable mechanics preserved |
| 26 | `layout` | 0/0 | 0/0 | none | observable mechanics preserved |
| 27 | `markitdown` | 0/0 | 0/0 | none | observable mechanics preserved |
| 28 | `markitdown` | 0/0 | 0/0 | none | observable mechanics preserved |
| 29 | `markitdown` | 0/0 | 0/0 | none | handout OCR gaps |
| 30 | `markitdown` | 0/0 | 0/0 | none | observable mechanics preserved |
| 31 | `markitdown` | 0/0 | 0/0 | none | unverified image-form extras |
| 32 | `markitdown` | 0/0 | 0/0 | none | unverified image-form extras |
| 33 | `markitdown` | 0/0 | 0/0 | none | observable mechanics preserved |
| 34 | `markitdown` | 0/0 | 0/0 | none | unverified image-form extras |
| 35 | `markitdown` | 0/0 | 0/0 | none | unverified image-form extras |
| 36 | `markitdown` | 0/0 | 0/0 | none | observable mechanics preserved |
| 37 | `markitdown` | 0/0 | 0/0 | none | unverified image-form extras |
| 38 | `markitdown` | 0/0 | 0/0 | none | unverified image-form extras |
| 39 | `markitdown` | 0/0 | 0/0 | none | observable mechanics preserved |
| 40 | `markitdown` | 0/0 | 0/0 | none | unverified image-form extras |
| 41 | `markitdown` | 0/0 | 0/0 | none | unverified image-form extras |
| 42 | `markitdown` | 0/0 | 0/0 | none | observable mechanics preserved |
| 43 | `layout` | 0/0 | 1/1 | none | observable mechanics preserved |

**Findings:** No native bound item or recognized mechanics expression was dropped by the selected source in this replay. The 29 geometry pair checks all returned `matched`; no stored pair mismatch exists. Seven `layout_numeric_loss` signals trace to one physical folio (p6) or handout identifiers 1/2/3 (p17–19, p28–30), not a changed stat/die/SAN value. Those handout identifiers are present in the selected source. This does **not** establish the correctness of numbers newly read from character-sheet images; p31/p34/p37/p40 contain many such values, and all eight rich pages remain flagged unverified. Image-only handout transcriptions also require visual comparison. No known P0 mechanics corruption remains, but the audit is not a full source certification.

The page-13 selected text begins with individual decorative glyph letters interleaved into a core sentence. This is actual text/order corruption independent of pair preservation; numeric-only checks cannot clear it. Page 18 and 29 contain respectively 30 and 49 literal unreadable markers in selected handwritten handout OCR. They are real text gaps, though their full gameplay consequence requires handout/source review. Page 4 has 612 replacement glyphs in a visually inspected table of contents; those correspond to dotted leaders, while its section titles remain readable. Page 5 is a full-page illustration with no running text.

## False positives, legitimate reviews, and image/map boundary

- **Source-bound false-positive candidates:** p4 `local_ocr_review` is triggered by TOC dotted leaders; p6 `layout_numeric_loss` by the terminal page folio; p19 `layout_numeric_loss` by handout identifier 3 retained in native; p17/18/28/29/30 layout numeric comparisons likewise reference handout IDs retained in selected MarkItDown. A future generic fix must prove the selected source retains the identifier and must not silence real numeric loss. p2 is a cover: `low_text` is expected for a title page. `native_two_columns` on 15 pages is informational gutter detection; it does not by itself require review.
- **Review retained by design:** eight `rich_candidate_unverified` entries are valid: deterministic promotion preserved known weak-baseline evidence but did not verify newly introduced stats, skills, or prose. The paired historical `low_text` records do not certify those extras. The selected rich text is useful; the review is still appropriate.
- **Image/map-only or image-source pages:** p5 illustration, p16/p27 floor plan, p25 regional map, p17/p28 letter handout, p18/p29 journal handout, p30 image handout. The replay used no-op vision/map, so it cannot assert actual scene maps or visual-source completeness. p18/p29 additionally have proven transcription gaps. Do not treat image-only review as a native-text regression.
- **No automatic warning removal recommended in this analysis.** The C entries identify *candidates* for a small generic follow-up; they should remain in the persisted audit trail until a source-bound final-state rule is independently tested. Unknown or image-only content stays review-required.

## Counts and priority

| Category | Warning entries |
|---|---:|
| REAL_TEXT_CORRUPTION | 4 |
| REAL_MECHANICS_RISK | 0 |
| LAYOUT_FALSE_POSITIVE | 24 |
| RICH_CANDIDATE_UNVERIFIED | 8 |
| IMAGE_OR_MAP_REVIEW | 17 |
| EXPECTED_REVIEW | 13 |
| UNKNOWN | 0 |
| **Total** | **66** |

Current final review pages: **23**; current warnings: **66**. Pages with demonstrated text defects: **4** (p3 credits, p13 core order, p18/p29 handout OCR); pages with demonstrated *core* text defect: **1** (p13). Pages with demonstrated mechanics corruption: **0**. Pages with at least one potentially safe, evidence-bound warning refinement: **9** (p2, p4, p6, p17–19, p28–30); this is a triage count, not a proposal to clear nine whole-page reviews. Review pages whose only evidenced cause is image/map/handout uncertainty: **7** (p5, p16, p17, p25, p27, p28, p30). The eight rich pages are a separate unverified-OCR cohort. The remaining uncertain outcome must be resolved by visual/source review, not by a warning threshold change.

**P0 — mechanics/gameplay correctness:** No confirmed changed stat, die, SAN loss, or bound skill value in observable native source. **No known P0 mechanics corruption remains.** The p13 core reading-order defect may affect Keeper understanding and should be assessed as high priority before using this parse as authoritative. Image-form character values remain unverified, not confirmed safe.

**P1 — substantive text:** Diagnose and fix generic p13 column/decorative-glyph interleaving; review image-handout transcription gaps on p18/p29 against rendered PDF and the duplicated handout pages. p3 credits ordering is real but not core gameplay and can follow the core repair.

**P2 — warning precision:** Source-bound final-state handling of TOC leaders, folios, and retained handout identifiers may remove demonstrably false review signals without changing extraction. Keep warning history and unknown-warning fail-closed behavior. Do not turn all `layout_numeric_loss` or `low_text` informational globally.

**P3 — diagnostic UX:** Distinguish “selected source still incomplete,” “rejected alternate source,” “image source pending visual review,” and “historical extraction warning” in debug/quality output. No player-facing copy change is proposed here.

Recommended next action: a focused p13 reading-order reproduction with source geometry, followed by a small generic fix and regression fixture if the same defect is reproducible. Separately validate the image handout and character-sheet values visually. No production code or PR is part of this triage.
