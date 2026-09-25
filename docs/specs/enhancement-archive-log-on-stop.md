# Spec: archive structured log + pyinstrument profile on bot stop

## Changeset Tracking
- **main_v2 start**: origin/main_v2:a005d7912a5b0e9b2b8b62b83d8ecc46eff9d09c
- **implementation end**: enhancement/archive-log-on-stop — ruff/mypy(app/)/compileall/pytest all green

## Purpose & Scope

Direct user request. Two established problems this addresses:

1. **The structured JSON log (`LOG_FILE`) accumulates forever across bot
   restarts** — `app/logging_config.py`'s `RotatingFileHandler` opens in
   append mode and only rotates at 10MB, so a single log file can span
   many independent process lifetimes (this session's own log analysis
   found one file spanning 37 hours and 7 restarts). This makes "what
   happened in this one run" hard to isolate without manual bookkeeping.
2. **Archiving profiling artifacts (log + pyinstrument HTML) is currently
   a manual copy-and-rename step** the user does by hand after each
   `bot_lifecycle.py stop` (the `profile-async*.log`/`profile-async.
   pyinstrument*.html` files analyzed throughout this session were all
   manually copied out of `.runtime/bots/`).

**User's explicit requirement** (verbatim intent): when the bot is
stopped, automatically copy the structured log and the pyinstrument HTML
into `~/coc_v2_log/`, wait until the HTML is actually fully written before
copying it, then remove the originals — not append mode, each archived
file gets a timestamp prefix in its filename so repeated stops never
collide or silently overwrite each other.

## Design

Hook into `scripts/bot_lifecycle.py`'s `stop()`, right after it has
confirmed the bot process (and, for `py-spy`, the attached profiler) has
fully exited — before deleting the instance manifest. At that point:

- The structured log (`LOG_FILE` from `.env`/environment — the same
  setting `app/logging_config.py` already reads) is fully flushed since
  the process that was writing to it is confirmed dead.
- If profiling with `pyinstrument`, the profiled process has already
  received the graceful `SIGINT` `stop()` already sends specifically for
  this tool (see `graceful_signal` in `stop()` — pyinstrument needs
  `SIGINT`, not `SIGTERM`, to flush its HTML report) and is now confirmed
  exited, so the HTML file should already be complete. Poll briefly
  (bounded timeout) for its existence anyway as a safety margin against
  a slow filesystem flush, rather than assuming zero latency.

For each of the two artifacts that exists:
1. Copy it to `~/coc_v2_log/<UTC timestamp>_<original filename>` (dir
   created if missing; overridable via a new `BOT_LOG_ARCHIVE_DIR` setting,
   consistent with this script's existing `BOT_STOP_TIMEOUT`-style
   environment-variable configuration — default `~/coc_v2_log`).
2. Delete the original from its source location (`LOG_FILE`'s path for
   the log; the profiler's `output_path` from the instance manifest for
   the HTML).

Both artifacts are archived independently — a missing/absent one (no
`LOG_FILE` configured, or profiler wasn't `pyinstrument`) is a silent
no-op for that artifact, not an error; `stop()`'s own success is never
blocked by archiving. A failure to find the pyinstrument HTML after the
poll timeout prints a warning but does not fail the stop.

**Scope note**: only the structured JSON log (`LOG_FILE`) and the
`pyinstrument` HTML are in scope, per the explicit request ("log 跟
html"). `bot_lifecycle.py`'s own separate `.runtime/bots/<instance>.log`
(raw subprocess stdout/stderr capture — a different file from `LOG_FILE`)
and `py-spy`'s `.svg` output are NOT touched by this change.

## Consequence worth calling out

Since the log is deleted after each stop, `LOG_FILE`'s `RotatingFileHandler`
starts a fresh file on the next `start` (it already handles a missing
file the same as an empty one). This is a deliberate, wanted side effect:
each run's log becomes fully self-contained in the source location while
the process is alive, and the archived copy in `~/coc_v2_log/` is the
permanent, uniquely-named record — solving the "one giant multi-restart
log" problem this session's own investigation kept running into.

## Testing Strategy

- Unit tests for the new archiving function in isolation (temp dirs,
  fake `LOG_FILE`/profiler-output paths) — covering: both artifacts
  present and archived+removed; only the log present (no profiler);
  neither present (silent no-op); pyinstrument HTML not yet on disk at
  call time but appears within the poll window (simulated via a
  short-lived background write); HTML never appears within the poll
  window (warns, doesn't raise); timestamp prefix present and distinct
  across two calls.
- No real bot process needed for these tests — the function only touches
  the filesystem paths it's given, independent of `stop()`'s process-
  management logic.
- Standard four checks (ruff, mypy, compileall, pytest).

## Notes
- `BOT_LOG_ARCHIVE_DIR` follows this script's existing pattern of reading
  overridable settings via `_settings()` (`.env` merged with the real
  process environment, real environment taking precedence) rather than a
  hardcoded path, even though the user's own phrasing named a specific
  directory — matches every other configurable path in this script
  (`DATA_DIR`, `DB_PATH`, etc. equivalents elsewhere in the project).

## Post-implementation review fixes

Three real gaps found by review, all fixed before merge:

1. **`LOG_FILE` was reconstructed at stop-time from `_settings()` instead
   of recorded at start-time.** If `LOG_FILE` was only set for the specific
   `start` invocation's environment (not `.env` itself), or `.env` changed
   between start and stop, stop-time `_settings()` would silently resolve
   to a different value than what the actual running bot process used —
   archiving the wrong file or nothing at all. A relative `LOG_FILE` also
   used to resolve against whatever directory happened to invoke `stop`,
   while the bot process itself always runs with `cwd=ROOT` (see `start()`'s
   `subprocess.Popen(..., cwd=ROOT, ...)`). Fixed by resolving `LOG_FILE`
   once at `start()` time (relative to `ROOT`, matching the bot's own
   resolution) and storing it in the instance manifest as `log_file_path`;
   `stop()` now reads that instead of re-deriving it.
2. **Two instances sharing the same `LOG_FILE` would race on stop.**
   `LOG_FILE` is a plain environment setting, not instance-scoped — nothing
   stops two concurrently-running `bot_lifecycle.py` instances from being
   configured with the same one. Stopping either would archive-and-delete
   the shared path while the other instance's `RotatingFileHandler` still
   had it open, orphaning that instance's subsequent log output. Fixed by
   checking every other instance manifest for the same `log_file_path` with
   a still-alive `pid` before touching the file; if found, the log is left
   in place for that other instance's own eventual stop to archive.
3. **Archival I/O failures could propagate out of `stop()`.** The
   function's own docstring claimed "never raises," but `shutil.copy2`/
   `.mkdir()`/`.unlink()` weren't actually wrapped in a `try/except` — an
   unwritable `BOT_LOG_ARCHIVE_DIR` or a full disk would raise `OSError`
   after the bot process had already exited, leaving `stop()`'s own
   manifest cleanup (`manifest_path.unlink()`) never reached and a stale
   manifest that a later `stop` would refuse to process. Fixed by wrapping
   each artifact's copy+remove in `try/except OSError`, printing a warning
   instead of raising.
