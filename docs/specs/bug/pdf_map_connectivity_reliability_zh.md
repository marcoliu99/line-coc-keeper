# Map connectivity reliability 補強

## 範圍
PR155 從 7f3e109 繼續，只修改 map extraction／validation 與 map-provider regressions。維持 two-phase、targeted patch、certificate、source/publication hard-soft policy、OCR、Docling、gameplay。不呼叫 real Haunting／Beacon，也不使用第 5 次 request。

## 修改
Closed traversal／compass schema 與 sanitized provider diagnostics 已完成，予以保留。Map evidence 的 traversal、compass 只接受 canonical schema 值；描述放 evidence。Phase1 generation 與 location audit 都用 inventory timeout（30 秒）。Connectivity／targeted repair／final image audit 各用對應 timeout（60 秒）。Generic image timeout 不改；每次 dispatch 前 durable reserve、零 retry、失敗也消耗。

## Tests 與信任邊界
以 public map analysis／provider／graph-builder seams 不 sleep 模擬 Phase1 小於 30 秒、Phase2 超過 30 但小於 60 秒。實際 OpenAI SDK transport stub 於 Phase2 timeout：三個 stage 各一次 reservation／transport，connectivity 僅一次；error type／status／timeout 保留，不進 repair／final audit。補 connection error、invalid／missing tool、malformed JSON 安全診斷。測兩個 missing locations 加 scoped invalid edge，patch 保留 unrelated edge、certificate replay、越界 patch 拒絕、unresolved entry incomplete 但可遊玩。只有 error-free build 才 final audit。不重建 inventory／graph，不放寬 validator。

## 驗證與待取得 evidence
pytest、ruff、mypy、compileall、diff check；以 7f3e109 做 Standards／Spec review。僅保存 sanitized counts／status／errors／timing。Real Haunting／Beacon correctness 需下一輪重新授權與新 budget。
