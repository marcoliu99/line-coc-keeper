# PR155 信任邊界 review 修正

## 目標與範圍
修正現有 branch 的 F1–F6 可重現問題。canonical source 損毀阻擋發布；不可信地圖只停用 Map Engine。不重新設計 OCR、地圖解析、gameplay 或 publication architecture。

## 修改
- F1：檢查最後選定 source 的可觀察 dice、百分比、有號 mechanics 損毀；未解決則 hard block，不以被拒 challenger 歷史阻擋。更新 extraction identity 使舊不安全 cache 失效。
- F2：計算裁切後 raster bbox 聯集，不取單張最大值或直接相加。高 raster 覆蓋且正文不足時需要獨立 transcription；正常正文搭配小裝飾不觸發。
- F3：library 讀取只接受結構有效、可重播 certificate 且綁定保存 PDF／page image 的地圖。舊版或缺 certificate 隔離，劇本文字仍可使用。
- F4：connectivity／patch type、basis 使用 door/open_passage/stairs/one_way enum，compass 使用 canonical 18 方向。描述留在 evidence。
- F5：context-local image failure diagnostics 保存 provider、stage、error type、HTTP status、timeout、timing，不保存 prompt／image／key／raw response／exception body。保持 dict 或 None 介面。
- F6：所有 MarkItDown 相容 SDK client 禁止 hidden retry 並使用 PDF image timeout。每筆 reservation 最多一筆 transport request；失敗消耗，不退還。
- Map timeout 分 stage：inventory 30 秒；connectivity／repair／audit 60 秒。保持零 retry、dispatch 前 durable reserve。

## 驗證
透過 extraction/publication、library activation、map analysis、實際 SDK transport 邊界新增 regression。涵蓋 clean source／rejected challenger、修復 dice、一般 prose、tiled／overlap／decorative raster、舊版／有效 certificate、closed schema、timeout／401／429／invalid JSON／missing tool diagnostics、privacy 與一筆 reservation 一筆 transport。執行 pytest、ruff、mypy、compileall、diff check，再以 9d331bb 為基準做 standards／spec review。

## 非目標與待驗證
本輪不呼叫 Haunting／Beacon provider。raw evidence 保持 private。P3 cleanup 延後。production rollout 在另行授權的 real map／image-only 驗證完成前維持 hold。
