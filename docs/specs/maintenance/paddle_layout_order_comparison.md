# Paddle native-text reading-order comparison

## Goal, scope and experiment contract

Measure whether existing Paddle layout analysis improves the four PyMuPDF4LLM failures from the previous five-page audit. This is an investigation, not a production implementation. Do not change the importer, scenario library, gameplay, publication rules, maps or reparsing. No application code, dependency file or data schema changes. No third tool or new AI service.

Baseline `main_v2`: `7d2cc8cab76d25aa1f47c47568ad2f9ff05d910b`. Existing experimental environment: Python 3.11, PaddleOCR 3.7.0, PaddleX 3.7.2, PaddlePaddle 3.3.0, PyMuPDF 1.28.2. Use `LayoutDetection(model_name="PP-DocLayoutV3")`, CPU, four threads, MKL-DNN disabled, 150 DPI. The installed packages support this model directly. Download official model weights to the model cache; do not install or upgrade packages. Reference PyMuPDF4LLM 1.28.2 outputs are reused unchanged, including their default automatic-OCR behavior from the prior audit.

Input is the exact existing `selected_pages.pdf`, SHA256 `362531220b2ad0d5d4479b6c5fed754de9a3660d6d1245727cd5127f0f4b322e`: three real source pages (PDF 1, 3, 15 / printed 17, 19, 31) plus single-column and spanning-title three-column fixtures. Do not regenerate the fixtures or run a full scenario.

## Observation seam and flow

```text
Existing subset PDF -> native PyMuPDF text lines + PDF coordinates
                    -> 150-DPI page image -> PP-DocLayoutV3 regions + order
                    -> assign each native line to one predicted text region
                    -> predicted region order, then native y/x within each region
                    -> reordered native text -> compare with manual order
```

No OCR detector/recognizer or PP-Structure full parsing pipeline is instantiated. Paddle predicts regions/order only. No predicted text replaces native text. The script never imports application modules or calls scenario import/save/index/reparse functions.

Freeze manual ground truth from previously inspected page images before inference: title/header first, complete left then right (or left/middle/right), footer last. This reference is used only for scoring, never for constructing Paddle output.

The native-to-region adapter uses actual pixel/PDF scale and maximum intersection/native-line-area; overlap must be at least 0.15. Exclude image/chart/seal/header-image/footer-image regions. Respect Paddle's `order`. Headers with no order are placed before body; footer/number/footnote regions with no order follow body. Within one region, sort native lines by y/x without guessing columns. Preserve unmapped lines at the end and force a failed score. Every native line must appear exactly once. No tuned page-specific reconstruction or correction of Paddle's three-column failure.

## Evidence and scoring

Durable evidence: `/Users/marcoliu/workspace/paddle-layout-order-audit-20261003/`.

For each page save `page_XX_pymupdf.txt`, native positions JSON, reused `page_XX_pymupdf4llm.md`, `page_XX_paddle_layout.json`, `page_XX_paddle_ordered.txt`, manual `page_XX_expected.txt`, assignment/expected-ID JSON and annotated `page_XX_paddle_layout.png`. Also retain the exact subset, frozen oracle, model hashes/configuration, scripts, timing and test logs.

Compare both methods with the same manual title/column anchors. Paddle must additionally match the complete manual native-line sequence with no unmapped lines; this stricter check confirms improvements, rather than accepting a few correctly ordered headings.

| Evidence page | Layout | PyMuPDF4LLM | Paddle |
|---|---|---|---|
| 01 / real PDF 1 | Large title + two columns | FAIL | PASS |
| 02 / real PDF 3 | Normal two columns | FAIL | PASS |
| 03 / real PDF 15 | Skills/spells/image, two columns | FAIL | PASS |
| 04 / synthetic | Single column control | PASS | PASS |
| 05 / synthetic | Spanning title + three columns | FAIL | FAIL |

**PyMuPDF4LLM: 1/5. Paddle: 4/5. Improved prior failures: 3/4.**

All four Paddle passes exactly match the full expected native text, including header/footer placement. No source lines were unmapped, lost or duplicated on any page. Native raw text is byte-identical to the preceding audit on all five pages. The complex page's illustration is correctly classified as `image`, has no reading order and receives no native text. The decorative magnifier on page 02 is not detected as a separate image; it is also not classified as text and contributes no native text.

The remaining failure is order prediction: Paddle detects all nine body items in separate text boxes, but orders them by row (left1/middle1/right1, left2/middle2/right2, then left3/right3/middle3). The title remains first. This is not a merged-column failure. The real page 03's `Skills` heading is not separately detected, but its native line overlaps the adjacent skill-text region and is preserved in the correct location. Therefore correct order does not mean perfect semantic region classification.

Text/numeric token counters are unchanged, including percentages, SAN loss and dice formulas. The single-column fixture retains `1d6`, `1d10`, `1d4+2`, `SAN 1/1d6`, `50%`. Literal `+20` and `-10` are absent from these unchanged pages and are not claimed as observed PDF coverage; the signed-number counter supports them, but that is not an additional real-page result.

## Checks and limitations

- Independent saved-output assertions: **25 passed, 5 failed**: four original PyMuPDF4LLM failures plus the Paddle three-column failure. Failures are exposed, not xfailed.
- The evidence tests read actual saved outputs; rerun `compare_layout.py` before them to refresh inference. They are outside repository CI, not new CI regression tests.
- Existing repository full suite: **1862 passed, 1 skipped**, plus **152 passed subtests**.
- Per-page layout inference: approximately 0.91–1.24 seconds on this CPU, excluding import/model initialization, rendering and text mapping. Not a production latency benchmark.
- Five pages are a narrow, deliberately failure-heavy sample. Results do not establish reliability on arbitrary PDFs or scanned pages.
- Ground truth and 0.15 mapping threshold are disclosed. Mapping is experimental; a future production adapter needs independently tested coverage/ambiguity/fallback behavior.

## Decision and next minimal proposal (not implemented)

Paddle is materially better on these three real dual-column examples, but still insufficient for reliable general multi-column handling. A small, separately reviewed production PR is worth considering only for clearly separated native-text two-column pages, initially opt-in. Keep the current path for single-column/three-column/uncertain pages. A minimal adapter would use predicted region order to arrange existing PDF text, require complete one-to-one native mapping and token preservation, and fall back to the existing extraction path on errors or ambiguity. Returning to that existing path does not guarantee correct order; it only preserves current behavior. No such adapter, feature flag or production fallback is implemented here.
