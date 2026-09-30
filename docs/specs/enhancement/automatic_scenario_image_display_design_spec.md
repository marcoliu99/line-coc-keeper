# Configurable automatic scenario image presentation

[繁體中文](automatic_scenario_image_display_design_spec_zh.md)

Status: **backlog — design agreed, awaiting implementation approval**.
Base: `origin/main_v2` at `189bc8e`. Discussion branch: `docs/automatic-scenario-image-display`.

## Goal and agreed behavior

Configure whether the Keeper automatically attaches scenario pictures or uses text narration instead. This is a presentation choice, not a change to scenario facts, discoveries, image ingestion, or map rules.

```dotenv
SCENARIO_IMAGE_DISPLAY_ENABLED=true
```

This global `.env` setting defaults to `true`, preserving existing automatic image behavior. It is read at process startup; a configuration change takes effect after restarting the bot. `.env.example` and both README languages must explain its scope.

| Request | true | false |
| --- | --- | --- |
| Keeper automatically displays a public picture | Authorized image | Authorized text narration |
| Keeper automatically displays a private picture | Image to its resolved recipient | Text to the same recipient |
| Player says “show me the picture” in ordinary conversation | Existing Keeper workflow | Text; suggest `/coc showpage` when appropriate |
| Explicit `/coc showpage N` | Authorized original image | Authorized original image |
| PDF/image upload, OCR, map or character extraction | Existing processing | Existing processing |

Only `/coc showpage` bypasses attachment suppression. A model-supplied flag or ordinary natural-language request cannot bypass it. Per-group preferences and live configuration commands are outside this first release.

## Current paths and gaps

- `app/keeper_tools/scenario.py:show_scenario_image` checks active-context assets and speaker permissions, resolves an optional investigator recipient, then queues an image request.
- `app/legacy_commands.py:_deliver_side_effects` sends queued pictures publicly or by DM. Private check followup can rebind image/reply callbacks to the original player's DM.
- `app/commands/handlers/map_handler.py:handle_map_command` currently sends `showpage` attachments directly. It lacks the tool path's explicit asset visibility checks. Reuse the same authorization policy rather than preserving this gap.
- The image manifest's `description` is currently a page-source excerpt capped at 500 characters. It is not automatically a player-safe caption. Existing page text, visual descriptions and structured maps can provide context, but still require disclosure and provenance handling.
- Prompt and tool descriptions currently promise images. Their wording must agree with the setting, including text-mode results and manual-view guidance.

## Interface and flow

Introduce one presentation decision/resolution seam shared by automatic tool delivery and manual page display. It owns asset lookup, chapter/visibility checks and attachment-versus-text selection. Keep speaker identity and resolved audience explicit. Keep world-state mutations outside it.

The result distinguishes `image`, `text`, `unavailable`, and `denied`; carries the authorized page/source references and intended audience; and does not report an attachment as sent when it was replaced by text. Representation should fit existing delivery contracts rather than adding a second delivery subsystem.

```text
image request
    |
    v
shared page / chapter / visibility authorization
    | denied ----------------------> existing safe refusal
    v
request origin
    | /coc showpage ---------------> authorized original PNG
    v
Keeper automatic presentation
    | setting=true ---------------> queue PNG for resolved audience
    v
setting=false
    |
    v
authorized existing description / source / map context
    | available ------------------> existing narration / private text workflow
    | insufficient ---------------> known information + manual-view guidance
    v
existing delivery retains public / private recipient
```

Automatic-delivery code must also suppress attachments when disabled, so an overlooked secondary producer cannot bypass the policy. Manual `showpage` remains a separate explicit path after shared authorization. Avoid an unconditional transport-level ban that would also block the manual command.

## Text, privacy and authority

- Reuse ingestion-time descriptions or relevant source material within the existing Keeper pipeline; do not add a fixed image-analysis or narration-review call.
- Do not mechanically copy the manifest's first 500 characters into public chat. Treat them as source context, not an approved public response.
- Preserve current spoiler policy and chapter authorization. Do not turn source text or visual interpretation into an investigator discovery simply because it is available.
- Private fallback must stay private. Do not expose private descriptions through public Narrator observations or publicly repeat their content. Reuse the existing private-output workflow; if a safe usable description is absent, deliver a safe recipient-scoped unavailable response.
- Explicit authorized KP disclosure retains existing semantics; ordinary players do not gain KP-only access through `showpage` or text mode.
- If useful description is missing, narrate only established available information and, where authorized, suggest `/coc showpage N`. Never invent unseen detail or silently perform another vision request.
- Keep source references and the distinction between derived image description and verbatim scenario text. Presentation alone does not establish consequential world facts.

## Storage and compatibility

No game-state schema change is needed for a global setting. Continue storing PDF page images, descriptions and room graphs regardless of display mode. Existing scenarios without dedicated captions use available authorized context or the safe unavailable fallback; no mandatory reimport or background caption generation.

## Verification

Use real dispatch/delivery seams with controlled senders and mocked providers:

1. Default and explicit true preserve automatic public and private attachment behavior.
2. False suppresses all automatic attachments, including resolved-check and opening paths; public text stays public and private text stays private.
3. False plus a natural-language image request does not attach an image or accept a model bypass flag.
4. `/coc showpage` still displays an authorized PNG in both modes; unknown, inaccessible and KP-only pages are rejected for ordinary players. Authorized KP access remains valid.
5. A secret-containing page excerpt is never copied to public fallback. Missing descriptions produce no fabricated content and no additional image-analysis call.
6. Image ingestion, OCR, character extraction and map storage are unchanged by this setting.
7. Existing tools and narration do not claim that an image was delivered in text mode. Provider call topology gains no fixed new stage.
8. Run the complete suite, ruff 0.16.8, mypy app, and compileall app tests before delivery.

## Decisions from discussion

All six behavior questions and the shared understanding were confirmed: global startup setting, default true, suppress only Keeper automatic presentation, manual bypass only through `/coc showpage`, reuse descriptions without fixed calls, preserve spoiler/privacy rules, and use known information plus authorized manual-view guidance when description is insufficient. Implement shared manual-command authorization as part of this feature.
