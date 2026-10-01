# The Haunting canonical source topology validation

Status: evidence validation completed; real grammar coverage unresolved. No runtime change.

## Source authority and scope
Read the existing published scenario.txt, not rejected OCR, provider output, map descriptions or narration. Its UTF-8 SHA-256 equals the existing manifest content_hash: `191b3b2b14530050ded4a55c2c58a5e672dadd4ea4d7d064f200a6f7181e7541`. This is a legacy publication: no current PR155 ingestion provenance/quality proof is present, and scene_maps.json is empty. Do not describe it as newly verified source or a certified map. Evidence below is outside embedded Image OCR annotations; page 7 map-vision prose is excluded. No provider requests or reimport were made.

## Source reading before extractor output
Manual reading of final canonical spans supports: basement/storage Room 1 → outer boards broken or removed → explorable space between walls (canonical Room 3 section) → inner wall broken through → Room 4. Two distinct barriers, with intermediate exploration before the second. Room 3 has a canonical section name; an invented Secret Room or unnamed source_transit is not justified here. This structural interpretation is evidence review, not an authoritative world-state event.

No explicit necessary-for-progress instruction was found for these barriers. Do not authorize fail_forward, inferred difficulty/HP/armor/tools/one-shot failure. Physical pages 10–12 contain visibly interleaved multi-column prose; the evidence must retain exact original offsets rather than silently rewrite it.

## Evidence (Unicode source offsets, end exclusive)

| Physical page | Sanitized fact | Span | SHA-256 |
|---|---|---|---|
| 10 | basement_room_1_storage_section | 30798–31017 | `367bbd7be86a9975f5c548c98a50837e5f49b5575b842de251029c63d2a7cf01` |
| 11 | canonical_named_intermediate_room_3 | 36458–36487 | `1f09cea4ab36543b51e91243f2eb42cf7effab82d55bd98a597a3d9ebcd3feaf` |
| 11 | outer_boards_removed_or_broken_reveal_space_between_two_walls | 36627–36800 | `b2f8d23bbe8421d145b9b721e472f3d1ec26ba395829b92073962c5d6718b8ca` |
| 11 | intermediate_space_can_be_explored | 37113–37132 | `0575a0e728b7b26f4c8d86898d2744f0f2491657f94840ca559423846a7a30bd` |
| 12 | inner_wall_of_same_crawl_space | 40298–40343 | `8f5da35ee109039e64ef95f8880edb88acc64132ec15715117e64a9bf77379f2` |
| 12 | breaking_inner_wall_enters_room_4 | 40693–40832 | `084b32faaaa1606a5de5ea6ed9698f2824f6706606286911c21597ca93dd46a7` |

## Production extractor probe
Run app.pdf_source_topology.extract on the full published canonical source. No actual published certified inventory is available, so the empty-inventory probe is diagnostic only. A separate diagnostic inventory containing only explicit canonical Room 1/3/4 references also returns zero routes/chains/transits, with zero diagnostics. This invented-ID test input is never a published visual graph or an authoritative endpoint mapping.

Current grammar does not hit. It accepts a single affirmative route assertion; the real evidence is conditional narrative across sections/pages with interleaved prose. This is a grammar coverage gap, NOT evidence that the source lacks an ordered route. Counts: chains=0, segments=0, barriers=0, transit=0; no route ID, endpoints, kinds, hidden flags, policies or extracted spans. No flattened edge was created because no source edge was created.

Real A-open/B-blocked, discovery and solid-wall/map interaction validation cannot be claimed: there is neither a real extracted chain nor a certified graph. Existing synthetic ordered-route/hidden-topology tests are rerun separately, not counted as real evidence. Production must remain fail closed.

## Proposed narrow grammar extension — pending confirmation
Before runtime edits, extend only source-bound deterministic recognition of the documented narrative structure: canonical numbered source sections bind start/intermediate/destination; explicit outer-board removal reveals the same enterable wall space; an explicit inner-wall reference and subsequent break-through clause bind the second transition to its named destination. Preserve original full-source spans/hashes across page boundaries. Every role and ordering must have textual proof; conflicting references or unsafe interleaving remain ambiguous and authorize no topology. Never use a hash allowlist alone as semantic proof.

The existing graph inventory must uniquely bind every canonical named location. Missing room identity produces diagnostics, never invented endpoints or a fabricated unnamed node. Keep certificate replay and per-barrier state unchanged. No OCR/layout/Docling/publication/image-provider changes.

Add real-derived, copyright-free structural regressions plus private hash-bound real-source execution. Negative controls must cover independent nearby wall sentences, shared walls, one obstacle only, ambiguous transit, negation and wrong inner-wall reference. Validate per-barrier runtime/discovery on any successfully extracted candidate; label synthetic visual fixtures separately from real map authority.

## Verification and review
Pending execution/results; machine-readable companion records source evidence and extractor output without raw source text.
