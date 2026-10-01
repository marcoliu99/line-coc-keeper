# PR155 controlled OCR and image-only validation

[繁體中文](pdf_ocr_image_only_validation_zh.md)

Runtime correction implemented; **production rollout held**. Implementation commits: a583f53, cf62607, 62f76db and 1c93712; aligned with main_v2 in 6bb9a79, on enhancement/pdf-multicolumn-ingestion. The legacy v3/v4 comparison is confounded by hidden OCR and the former vision/local fallback; it is not a Paddle acceptance threshold.

## Controlled offline Paddle OFF/ON

[Machine-readable results](pdf_ocr_controlled_ab_results.json). Same final code hash, dependencies, 102 physical pages, local/AI/layout budgets, Docling disabled, provider credentials cleared and socket transport denied. Both arms explicitly disable PyMuPDF4LLM hidden OCR. Only Paddle enabled changes. Persistent models use the same manifest digest and pinned PP-OCRv5_mobile_rec/PaddleOCR 3.7.0/PaddlePaddle 3.3.0/PaddleX 3.7.2 CPU worker. The temporary Python environment is a validation interpreter, not the production model cache.

| Book | Review OFF / ON | Blocked OFF / ON | Seconds OFF / ON |
| --- | --- | --- | --- |
| The_Haunting_Scenario_trimmed.pdf | 13 / 13 | 13 / 13 | 29.4 / 134.2 |
| Dead Boarder.pdf | 10 / 10 | 9 / 9 | 25.1 / 198.7 |
| The Lightless Beacon - Call of Cthulhu.pdf | 27 / 27 | 26 / 26 | 30.7 / 101.0 |
| Total | 50 / 50 | 48 / 48 | 85.3 / 434.0 |

Both arms retain 11 unresolved native-empty pages, zero accepted region repairs and zero authoritative page transcriptions. Native numeric/dice losses and known native pair failures remain zero. No accepted/legacy safe page becomes blocked when Paddle is enabled. No regression was recorded. Paddle ON records 16 region attempts, 22 page attempts, 37 native-gate rejections and one empty attempt; 21 preferred Paddle page candidates remain private/unverified, including partial-native pages. Tesseract attempts are 38 in each arm. Local OCR does not consume provider dispatch budget.

Ten available, visually verified hash-bound real crops were rescored separately. These are not the prior twelve-crop/103-number denominator. Using the production numeric token grammar: Tesseract 32/49 versus Paddle 44/49 numeric matches; added numeric tokens 2 versus 0; exact dice 5/6 versus 6/6; known pair failures 15 versus 2. Native-empty page scores are unavailable, not zero-error proof. Elapsed times are cold-worker measurements on a shared validation host, not an isolated performance benchmark; no warm throughput claim is made.

Answer: Paddle improves local candidate quality on this bounded reference subset, but this offline experiment does not improve accepted recovery or unresolved page counts.

## Provider-enabled selected production paths

[Machine-readable results](pdf_ocr_production_path_results.json). Same final runtime, Paddle enabled; configured MarkItDown/provider available in the ON arm, provider credentials cleared in the OFF arm; Docling follows production configuration, hidden OCR stays OFF. Four unmodified physical pages were extracted individually, preserving original PDF hashes and physical-page provenance. This is not a whole-corpus paid evaluation. Registry image calls were bounded to 60 seconds with no retry in this validation. MarkItDown image completions and direct independent verification reserve/checkpoint the same durable provider request/page budget before dispatch; converters cannot bypass an exhausted budget.

- The Haunting p20: real native-empty investigator sheet. Useful Paddle transcription retained, independent candidate mechanics conflict; unverified, canonical text excluded. Real authoritative image-only positive acceptance remains unvalidated.
- Lightless Beacon p5: genuine text-free illustration. Paddle returned empty; independent AI classified it as illustration. Tesseract noise stays diagnostic/private and is not published. The page no longer blocks solely for OCR prose certification.
- The Haunting p7: an actual scene_map with 20 nodes and 44 directed exits was generated. Structural validation failed: the entry references a missing room ID. Targeted visual comparison also found unsupported traversal through the basement wall separating Corbitt's hiding place; this run does not invent the upper-bedroom interconnections seen in an earlier run. Map quality gate failed.
- Lightless Beacon p16: 9 nodes and 15 directed exits. Structural validation failed: a stair exit points to the missing Lamp Room node. Hallway W/E directions match the image, but side-elevation Service Room/Lamp Room/Lantern Gallery nodes are missing and full topology remains uncertified. Map quality gate failed.

Automated blocked disposition decreased 4 to 1, through illustration and map routes. The map defects mean this is **not** validated production-quality improvement. Map validation/fixes remain separate from OCR; scene_map/gameplay semantics were not changed.

## Linux CPU smoke and release gates

[Sanitized Linux result](pdf_ocr_linux_cpu_smoke_results.json), [actual GitHub run](https://github.com/marcoliu99/line-coc-keeper/actions/runs/36852457588). Explicit setup installed pinned dependencies and persistent cached models; actual Linux x86_64 CPU inference ran with runtime socket connects denied. The recognizer returned `ld6+2` for the synthetic `1d6+2`. Exact mechanics check failed and the smoke job intentionally remains red. No model swap, confidence publication or gate relaxation was introduced to conceal the result.

| Gate | Result |
| --- | --- |
| Fair controlled A/B / safe-page non-regression | Executed; no regression; candidate quality improved |
| Real image-only authoritative transcription | Executed; conflict rejected; positive real acceptance not validated |
| Real provider floor-plan graph | Executed; both structural and manual quality failed |
| Linux CPU smoke | Runtime executed; exact mechanics assertion failed |
| Production rollout | Held |

## Verification and review

Final local pytest: **2007 passed, 2 skipped**. `ruff check .` (required 0.16.8), `mypy app`, `python -m compileall app tests`, and `git diff --check` pass. Up-to-date main_v2 is an ancestor and no unmerged paths exist. Ordinary [CI passed on the integrated runtime 6bb9a79](https://github.com/marcoliu99/line-coc-keeper/actions/runs/36852457581). The separate Linux recognition smoke failed on the same integrated commit, as reported above.

Two-axis code review used fixed point 5725738. Standards: zero documented breaches; two non-blocking heuristic suggestions (verification/report ownership and duplicated authority predicates). Spec: native-header scan detection, wrapped MarkItDown budget bypass, native dice-modifier deletion and inserted negation/sign inversion were fixed with red/green loader regressions. Quality identity v9 invalidates older certificates. Intact native tokens must remain one contiguous span, and signed native mechanics cannot change; independently corroborated image additions remain possible outside that span. Final re-review found no remaining material spec findings. Private unverified candidates, canonical publication exclusion, budget exhaustion, identity/resume, numeric/dice/prose conflict, Tesseract disagreement, and OCR-independent maps have regression coverage.

Raw PDFs, crops, full transcripts and graphs stay in private /private/tmp validation artifacts. Committed reports contain hashes, metrics, dispositions and bounded diagnostic checks; the only source/candidate text in the Linux report is an original synthetic fixture. No production scenario was published and no gameplay module changed.
