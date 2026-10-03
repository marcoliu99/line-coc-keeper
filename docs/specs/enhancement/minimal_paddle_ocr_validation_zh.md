# 最小 PaddleOCR 驗證

基準為已 revert 的 main_v2 `7d2cc8c`；implementation `7418da0`；獨立 branch `enhancement/minimal-paddle-ocr`。未 cherry-pick PR155 code。

## Scope

Production 僅新薄型 `app/pdf_ocr.py` 與 `app/pdf_loader.py` 的 OCR evidence／selection 小接點，另加 optional dependencies、explicit setup、OCR tests。既有 requirements／Tesseract implementation／provider calls／native extraction／admission／publication／map logic／gameplay 不變。

PaddleOCR 3.7.0／PaddlePaddle 3.3.0／PaddleX 3.7.2，CPU mobile detection + multilingual PP-OCRv5_mobile_rec；explicit local model paths、orientation／unwarping off，runtime 不取得模型。Lazy singleton／inference 共用 lock。使用者確認 supported Python 的 optional in-process backend，不新增跨版本 worker；Python 3.14 保持既有 OCR fallback。[官方 wheels](https://pypi.org/project/paddlepaddle/3.3.0/#files) 支援 macOS arm64／Linux x86_64 至 Python 3.13。Linux inference 未實跑，wheel availability 不等於 smoke PASS。

Supported application interpreter 安裝：

```sh
python -m pip install -r requirements-pdf-ocr.txt
python scripts/setup_paddle_ocr.py
```

預設 cache `~/.cache/line-coc-keeper/paddleocr`；`PDF_PADDLE_MODEL_DIR` 指定其他 persistent directory，`PDF_PADDLE_OCR_ENABLED=false` 停用。已實際執行 setup，下載兩個官方模型到 private temp directory；prepared replay 不下載。Isolated Python 3.11 已安裝 pinned SDK，optional dependency dry-run 確认 pins 已滿足；未升級 base dependencies。

## TDD／review

先 RED：adapter 缺失、dice／numeric／pair loss、image-only 接線缺失；再 GREEN。Review 找到三個 production-seam RED：whole-page source 未傳入、額外 candidate mechanics 搶先截住原本可成功的 Tesseract region repair、1d6+DB 改為 1d6-DB。修正只含 source forwarding／candidate validation：受損 region 在 selection 前重用 existing accept_region；保留完整 symbolic signed dice token，不改 legacy fallback／gates。純符號 noise 也先 RED 再 GREEN，reject 後回原 fallback。Standards／Spec re-review PASS，無剩餘 blocking finding。

68 個 OCR-specific tests、加既有 PDF／AI repair／numeric-pair 共 109 targeted tests 通過。Disabled／unavailable／missing-model／init／inference／empty／malformed／rejected 全回原有 Tesseract。Native OFF／ON 不變、startup 不 import Paddle、prepared setup 不下載、singleton reuse／serialization、external provider 不呼叫均 PASS。

PR #157 review 修正：SAN 損失以完整有序的斜線式比較，因此 `SAN 1 and 1d6`、`SAN 1 1d6`、`SAN 1d6/1` 對 `SAN 1/1d6` 都會拒絕。只有 source 對應時才修正有限格式的 `ld6`／`Id6` 與 SAN 分子的 `l`／`I`；錯骰面及未知 source 仍拒絕。另有回歸測試拒絕多餘斜線尾段，避免只比對合法前綴；斜線前方多出字元或空白時，也不得只比對合法後綴。Production PDF 接點測試確認拒絕後回到 Tesseract；普通文字及其他骰子保持原樣。

完整 suite **1930 passed、1 skipped、152 subtests passed**，15.26 秒。Ruff／mypy（127 files）／compileall／diff-check PASS。

## 真實 offline CPU smoke

macOS Apple Silicon、Python 3.11.15、真正 SDK／model inference，socket connections 封鎖、未使用 cloud provider。Synthetic RGB 1700x900、200 DPI、黑字白底、Arial／STHeiti Medium 42px、無 crop；A/B 執行原基準／目前 local OCR function body，不是完整 PDF import。

| Fixture | 原 Tesseract chars | Paddle chars | Paddle status | Known numeric／dice OFF／ON | Warm Paddle 秒 |
|---|---:|---:|---|---|---:|
| Neutral scanned text | 119 | 118 | accepted | PASS／PASS | 1.10 |
| Stat／mechanics | 79 | 77 | accepted | PASS／PASS | 0.99 |
| Bilingual | 84 | 80 | accepted | PASS／PASS | 1.06 |

Chars 差異包含換行，不宣稱廣泛 accuracy 提升。SDK import 0.90 秒、engine init 1.19 秒、首次 OCR call 2.24 秒（含 init）。Network attempts／external provider calls **0**。

兩張私人非地圖 Haunting raster pages（physical 20／35）也有 accepted local candidate，3727／1667 chars，16.63／17.17 秒。僅 candidate smoke，不宣稱 authoritative completeness／publication／gameplay；未跑 map／topology／corpus import。Full-page CPU 成本仍有限制，本輪不做 optimization。

Repository 僅提交 versions／hashes／counts／status／timing 的 [sanitized results](minimal_paddle_ocr_results.json)，images／private OCR prose／模型 binaries 均在 repo 外。Scoped optional backend **MERGE READY**；Python 3.14 無 in-process Paddle，Linux inference 未驗證。不自動建立 PR。
