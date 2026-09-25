# Spec: archive bot logs + pyinstrument profile on bot stop

## Changeset Tracking
- **main_v2 start**: origin/main_v2:a005d7912a5b0e9b2b8b62b83d8ecc46eff9d09c
- **implementation end**: enhancement/archive-log-on-stop — ruff/mypy(app/)/compileall/pytest all green

## Purpose & Scope

Direct user request. Archive a complete per-run bundle after each stopped bot
instance. There are two different log files that must not be confused:

1. **The structured JSON log (`LOG_FILE`) accumulates forever across bot
   restarts** — `app/logging_config.py`'s `RotatingFileHandler` opens in
   append mode and only rotates at 10MB, so a single log file can span
   many independent process lifetimes (this session's own log analysis
   found one file spanning 37 hours and 7 restarts). This makes "what
   happened in this one run" hard to isolate without manual bookkeeping.
2. **The lifecycle stdout/stderr log (`manifest.log_path`) lives at
   `.runtime/bots/<instance>.log`** and captures process output. This is
   distinct from structured `LOG_FILE`; profiling runs often have no
   structured log configured.
3. **Archiving profiling artifacts (run log + pyinstrument HTML) was a
   manual copy-and-rename step** after each `bot_lifecycle.py stop`.

When the bot is stopped, wait until the pyinstrument HTML is fully written,
then copy the instance lifecycle log, any configured structured log, and the
HTML into `~/coc_v2_log/` under one timestamp prefix. Remove successfully
copied originals so a later run starts fresh. If the HTML never becomes ready
within the bounded wait, warn and still archive available logs after waiting.

## Design

Hook into `scripts/bot_lifecycle.py`'s `stop()`, right after it has
confirmed the bot process (and, for `py-spy`, the attached profiler) has
fully exited — before deleting the instance manifest. At that point:

- The lifecycle log (`manifest.log_path`) and structured log
  (`manifest.log_file_path`) are fully flushed since the process that was
  writing to them is confirmed dead. The structured log may be shared by
  another live instance, in which case it must be left in place.
- If profiling with `pyinstrument`, the profiled process has already
  received the graceful `SIGINT` `stop()` already sends specifically for
  this tool (see `graceful_signal` in `stop()` — pyinstrument needs
  `SIGINT`, not `SIGTERM`, to flush its HTML report) and is now confirmed
  exited, so the HTML file should already be complete. Poll briefly
  (bounded timeout) until it exists, is non-empty, and its size remains
  unchanged across checks. Only after that wait should any logs or HTML be
  copied, keeping the run's artifacts together.

For each available artifact:
1. Copy it to `~/coc_v2_log/<UTC timestamp>_<original filename>` (dir
   created if missing; overridable via a new `BOT_LOG_ARCHIVE_DIR` setting,
   consistent with this script's existing `BOT_STOP_TIMEOUT`-style
   environment-variable configuration — default `~/coc_v2_log`).
2. Delete the original from its source location (`LOG_FILE`'s path for
   the log; the profiler's `output_path` from the instance manifest for
   the HTML).

Artifacts are archived independently after the HTML wait: missing log paths
and non-pyinstrument profiles are silent no-ops; a missing HTML after timeout
prints a warning, while available logs are still preserved. `stop()`'s own
success is never blocked by archiving.

**Scope note**: archive both the per-instance `.runtime/bots/<instance>.log`
stdout/stderr capture and the optional structured JSON `LOG_FILE`, plus
pyinstrument HTML. `py-spy`'s `.svg` output remains out of scope.

## Consequence worth calling out

Since each per-instance runtime log is deleted after stop, the next `start`
creates a fresh one. If the structured `LOG_FILE` is configured and not
shared by another live instance, it is also archived and removed; its
`RotatingFileHandler` starts a fresh file on the next `start`. The uniquely
named copies in `~/coc_v2_log/` remain the permanent records for each run.

## Testing Strategy

- Unit tests for the archiving function in isolation (temp dirs) — covering:
  lifecycle log, structured log, and HTML archived+removed; missing
  `log_file_path` while `log_path` is present; delayed HTML generation where
  logs remain untouched until the HTML is complete; missing HTML after the
  wait where logs are still archived; absent log paths; timestamp prefixes;
  and safe handling of I/O failures.
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

## Follow-up correction after merge

Runtime inspection found that the active profile instance had an empty
`log_file_path` but a valid `log_path` pointing to
`.runtime/bots/profile-async.log`. The original implementation archived only
`log_file_path`, so it archived the HTML but omitted the log the user needed.
The follow-up archives both manifest log paths, waits for the HTML to become
non-empty and stable before copying any artifact, and preserves available logs
if the HTML does not appear before timeout.
