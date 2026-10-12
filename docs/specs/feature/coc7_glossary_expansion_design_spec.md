# CoC7 glossary: spells, Mythos items, tomes, places and occupations

[繁體中文](coc7_glossary_expansion_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `a2bd9e9`, on top of `coc7_glossary_design_spec.md` and `dictionary_terms_and_learning_design_spec.md`.

## Change

`app/data/coc7_glossary/` gains 201 entries, in the same entry format, each with the ids of the sources that give it (`sources.json` records the URL, the date fetched and a SHA-256 of what was fetched):

| File | Added | Source of the pair |
|---|---|---|
| `spells.json` (new) | 40 spells | the trilingual spell list (English, Chinese) where the Chinese name is also in the Chinese rulebook's spell list |
| `occupations.json` (new) | 101 occupations | two occupation lists (English, Chinese), checked against the Chinese rulebook's occupation names |
| `mythos.json` | 28: 20 Mythos items, 8 beings | the Chinese rulebook's own English–Chinese name table |
| `tomes_places.json` | 32 tomes and places | the same table |

- **Occupations are a built-in table.** `_glossary_tables` builds an `occupations` table from the `occupation` entries, as it builds `skills` and `terms`, so `lookup_occupation` knows them and `learn_occupation` cannot store a pairing that disagrees (the guard from the dictionary spec). A pregenerated investigator's occupation is written as the dictionary names it (「Antiquarian」/「文物学家」 → 古董商); an occupation the dictionary does not know is kept as the model gave it.
- **Where the project already had a name, it stays.** Occupations the project defines (`OCCUPATIONS`) keep its names: 古董商 (renamed from 骨董商, which stays as another spelling), 記者, 私家偵探, 教授, 藝術家, 神職人員, 醫生, 流浪漢, FBI探員.
- **Three earlier entries** (Ghatanothoa, Chaugnar Faugn, Carcosa) differ from the Chinese rulebook's name. Their canonical name is unchanged; the rulebook's name is added as another spelling, so either resolves to it.
- **Traditional spelling.** The Chinese rulebook is Simplified; there is no official Traditional edition of the core rules. Where a Traditional source (hktrpg, the Uria030 terminology list) agrees with the rulebook name, its spelling is used. Otherwise the Simplified name is converted character by character with OpenCC (occupations also by phrase, so 软件-style words take Taiwan wording), and the entry carries `zh_tw_converted`. 136 of the 201 are converted; their Taiwanese wording has not been checked against a Traditional source.

## How the translations were checked

Every source was compared with the others before an entry was kept.

- **Existing entries against the rulebook table.** Of the 238 earlier entries, 184 appear in the rulebook's table. 168 agree, 12 only look different (the table lists `Science` once, the glossary one entry per field), and 4 really differ (Ghatanothoa, Chaugnar Faugn, Nightgaunt, Carcosa; Nightgaunt is left as is because the table gives 夜魔 to two English names).
- **The hktrpg English column is not used.** For spells and tomes it is partly a back-translation of the Chinese or is misaligned: 「Torment」 for 折磨術 (the spell is Wrack), 「Soul Siphon」 for 靈魂分配術 (Apportion Ka), 「Sign of Koth」 for 維瑞之印 (Voorish Sign), and 「Cultes des Goules」 beside 「不可名狀的教團」 (a different book). Its Traditional spellings are used only where its Simplified side equals the rulebook name.
- **Spells.** A spell is kept only when the trilingual list's Chinese name is also in the Chinese rulebook's spell list (41 are; one was already a term). Both are separate compilations of the rulebook, so agreement shows the name is the rulebook's, not that either is independent of it.
- **Occupations.** The two lists agree on 25 pairs and differ in wording on 14 (for example 古董商/文物学家, 医师/医生, 流浪汉/流浪者). A conflict is settled by the Chinese rulebook's occupation names; a name only one list has is kept only if it is one of those names. 19 candidates were dropped for this reason, including Police Officer (警员/警官), Truck Driver and Thug.
- **Rejected as source typos.** `Bhole` (an OCR slip for Dhole), `Hyperborea` (許珀耳玻璃亞, for 玻瑞亞), and every row where the table gives one Chinese name to two English names (Nightgaunts and The Haunter of the Dark both 夜魔; the two names of one book).
- **Spelling fixes.** Averigne is written Averoigne, 「The Great White Space」 not 「Great White Space, The」, HTML entities removed, wiki-link markup removed.

## Not added

- **Weapons.** The only English–Chinese weapon list (Keeper_CoC-TRPG) groups weapons by caliber and invents English labels (「Small Auto」), and its Chinese names differ from the rulebook's weapon table (指虎 against 黃銅指虎). No source pairs the rulebook's English weapon names with its Chinese ones, so none is added. The combat catalog keeps its own names.
- **Organizations and cults.** Only random tables exist among the sources that could be fetched; the Wikipedia pages are outside the network policy.
- **Terms and skills of the rulebook's name table** (about 190 rows), people's names, and the rulebook text itself. Only names are taken.

## Verification

`tests/test_coc7_glossary.py`: the new categories are read; a spell resolves in English and in either script, and a back-translated English name does not; occupations reach the project's names; the rulebook name of an earlier entry resolves to it; a pregen's occupation is written as the dictionary names it and an unknown one is kept; a model pairing cannot replace a built-in occupation; every new source records what was fetched.
