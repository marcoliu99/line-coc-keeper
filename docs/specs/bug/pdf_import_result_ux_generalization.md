# PDF import result UX and corpus generalization

Source: PR155 user request, 2026-10-03. Baseline: `1fe274acf09840d0c0518e74816829d01640ee2c`.

Successful publication must explicitly say the scenario imported successfully and provide `/coc start`. Feature warnings remain warnings, never source blockers. Saved resumable source-review drafts must explicitly say pending and provide continue/status/cancel. Unreadable PDFs must explicitly say failure and avoid exposing exception bodies. Waiting for similarity or replacement choices is not failure.

Admission, OCR, topology, map certification, gameplay and retry policies are unchanged. No book-specific decisions are permitted. Validate the five remaining real corpus books sequentially, each with independent storage and unchanged production caps. Only sanitized hashes, counts, status and reason codes belong in repository evidence. Actual publication/reload/activation/start must be verified for every publishable book; unavailable results remain pending, never inferred.

Acceptance: all four outcomes match persisted state; all False exits have understandable explanations; true source gates are preserved; full tests and standards/spec reviews pass. Rollout holds until real generalization completes.
