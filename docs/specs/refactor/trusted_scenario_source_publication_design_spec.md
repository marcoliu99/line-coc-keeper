# Trusted scenario source publication

[繁體中文](trusted_scenario_source_publication_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `07d55a7`.

## Current seam

Operator PDF review and external AI English preparation use different approval rules, but both read library/private template internals and publish a derived scenario by copying the immutable PDF, corrected source text, rendered page images, and invalidated indexes, pregens, and maps. Each independently stages, seals, retries, and cleans up that same artifact. External AI publication additionally requires an immutable receipt and revision provenance.

## Interface

`app/trusted_scenario_source.py` owns an immutable `SourceSnapshot` (manifest, verified source text, original PDF bytes/hash) and the shared derived-scenario publication transaction. Producers pass their already-approved candidate text, target identity, audit, quality report, and source snapshot. The module rechecks the source immediately before publishing; writes source PDF/text, rendered images, KP-only image assets, and deliberately empty derived artifacts to a private staging directory; then publishes the directory and removes failed stages. Retry verification checks the existing destination against the immutable source and candidate. External AI may provide a receipt path and retains its before-rename receipt sealing and retry rules.

The operator's reviewed digest/evidence and the external AI's page/import/revision validation remain separate. Neither producer may revise the original PDF, skip numeric auditing, silently approve an incomplete page, or select the published scenario for play. The existing filesystem library remains the adapter.

## Verification

Run the operator and external AI producer suites against one publication contract. Cover changed source identity, image rendering failure, existing destination tampering, receipt failure/retry, unchanged original PDF, traceable source metadata, and no partial library entry. Run full pytest, Ruff 0.16.8, mypy, and compileall.
