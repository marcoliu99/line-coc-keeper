# Scenario source and variant store

[繁體中文](scenario_source_store_design_spec_zh.md) | [Docs index](../../README.md)

## Status and goal

Category: `refactor`. Status: **implemented**. Based on `main_v2` at `ee23aa9`; the fourth item of the 2026-10-05 architecture review ("Hide scenario source storage details").

`scenario_templates` reached into `scenario_library` for its private path validator and JSON reader and then built paths itself: the source's `manifest.json` and `scenario.txt`, the `.variants/<scenario>/<source hash>/<locale>/<variant>` tree, the five files of a variant, and the `exports` directory. `scenario_source_review` also called `templates._root()` and the library's private page-marker regex. Every caller therefore knew the storage layout, and a change to it meant editing several modules.

## Contract

`scenario_library` now offers a source and variant interface; the files stay inside it.

| Function | Gives |
| --- | --- |
| `read_source(id)` | manifest and text, checked against the manifest's content hash (`FileNotFoundError` / `ValueError` as before) |
| `source_manifest(id)` | the manifest alone; `{}` when absent |
| `variant_manifests(id)` | every readable variant manifest of a scenario |
| `read_variant(id, source_hash, locale, variant_id)` | manifest and records; `FileNotFoundError` when either is missing or malformed |
| `variant_exists`, `variant_stamp` | existence; the file identity a cached index was built from |
| `publish_variant(..., VariantDocuments, still_current=)` | creates a variant in one step; `still_current` is asked after the files are written and before they appear, so a source re-parse in between leaves nothing behind |
| `write_variant_manifest` | atomic manifest replacement (approval) |
| `exports_dir(id)`, `remove_variants(id)` | the external-preparation packages; dropping all variants |

The library validates the scenario id, a 64-hex source hash and single-component locale and variant ids, so an address cannot name a path outside the variant directory. `scenario_templates` keeps what is its own: the variant id format, the compiler/schema versions, the content of the records, the coverage and glossary documents, and the cache.

## Contract kept

1. Behaviour, file names and layout on disk are unchanged; existing libraries and variants are read as before, and no migration is needed.
2. The error cases keep their exceptions and messages (missing scenario, text that disagrees with the manifest, a variant that is stale, conflicting or modified).
3. Trusted publication (`trusted_scenario_source`) and gameplay authority are not touched.
4. The only renames are `_PAGE_RE` to the public `PAGE_MARKER_RE` (used by two source-preparation modules) and the removal of `scenario_templates._root`/`_variant_dir`/`_all_variants`; their tests use the library interface.

## Enforcement

`tests/test_scenario_source_store.py` exercises the interface (consistency check, atomic publish with nothing left behind, damaged and traversing addresses, stamp, removal). `tests/test_architecture_scenario_store.py` fails if any module outside `scenario_library` reads one of its private names, or if `scenario_templates`, `scenario_source_review`, `help_actions` or `scenario_activation` name a library file such as `manifest.json` or `records.json`.

## Not changed

`trusted_scenario_source`, `scenario_source_authoring` and the library's own save/load functions still know the layout; they are the publication path or the library itself. Making them go through a narrower interface is a separate change.
