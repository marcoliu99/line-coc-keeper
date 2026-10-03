# 最小本機 PaddleOCR backend

基準：已 revert PR155 的 `main_v2`，`7d2cc8c`。

## Contract 與 scope

只在既有 `_ocr_image` 接點加可選本機 OCR backend。Native extraction、MarkItDown、AI／vision、map、import review／publication、gameplay 均不變。Paddle 不新增 gate：disabled、package／模型缺失、init／inference error、empty／rejected，都回到原有 Tesseract fallback，不新增 review reason。

Tests seams 為使用者指定的 adapter／import／setup：`pdf_ocr.recognize_with_paddle(image_bytes, source_text=..., pairs=...)`、既有 `pdf_loader.extract_text`、setup command。SDK inference 與既有外部 provider 是 fixture boundary，不作 publication judge。

薄型 result 僅 text、engine、accepted／rejected／unavailable／error／empty closed status。Lazy singleton 與 inference 共用一把 lock，沿用既有 worker threads。CPU、multilingual PP-OCRv5_mobile_rec，以及 PP-OCRv5_mobile_det 文字行偵測；停用 orientation／unwarping 模型。不新增 worker／queue／service。

## Offline setup 與 dependencies

可選 `requirements-pdf-ocr.txt` 固定 PaddleOCR 3.7.0、PaddlePaddle 3.3.0、相容 PaddleX 3.7.2；既有 requirements／無關依賴版本不變。官方 wheel 支援 Python 3.9–3.13 的 macOS arm64／Linux x86_64；目前 repo 的 Python 3.14 保持既有 fallback，不降級 app Python、不新增跨 interpreter worker。

Supported interpreter 明確安裝：

```sh
python -m pip install -r requirements-pdf-ocr.txt
python scripts/setup_paddle_ocr.py
```

只有 setup 下載兩個官方模型，預設 persistent directory 在 repository 外。Runtime 必須有完整模型檔，傳 explicit local paths、略過 model-host connectivity check，不能自動選／下載模型。App startup 不 import／initialize Paddle。Disable switch 支援 OFF／ON 比較。

## Candidate selection

文字非空、有合理長度、無 replacement-char 或明顯 malformed dice。有 native/source 時重用 pdf_quality text／numeric／known pair checks，adapter 額外保留 exact dice／percent／signed token。不確定的 Paddle 結果只 fallback，不改既有 fallback acceptance。只在 local OCR repair 傳既有 source，whole-page legacy source validation 不改。

不做 confidence fusion／cloud calls，不新增 admission states／provider path。普通 log 僅 engine／status／error type，不含 OCR prose 或 raw error。

## Validation

TDD adapter／fallback integration；disabled／native equivalence、模型／package 缺失、init／inference／empty／malformed output、exact mechanics／pairs、lazy startup、offline no-download。僅小量 CPU synthetic／scan／stat／bilingual smoke，不跑 maps／whole corpus／gameplay。OFF／ON 記錄 chars、numeric／dice preservation、timing，不調 admission。完整 pytest／ruff／mypy／compileall／diff-check、Standards／Spec review。

相容性限制：官方 wheel 無法在目前 Python 3.14 in-process 執行 Paddle；supported-interpreter application 可選擇啟用，3.14 保留既有 OCR。Linux inference 只有實際執行才可報 smoke PASS，wheel availability 不等於 PASS。
