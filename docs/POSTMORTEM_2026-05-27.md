# Post-Mortem — May 27, 2026 Crash

**Status:** historical record. No action required.

## Summary

At 2026-05-27 ~15:05 UTC the Talos Spine's Telegram poller died with an SSL
read timeout. By the time the watchdog noticed, the agent had been
inactive for over two hours, 178 `[SYSTEM WATCHDOG]` notices had piled up
in `pending_system_notices.json`, and the runtime was in a broken
restart loop. The agent never came back online. The event was
undocumented at the time; this post-mortem is the first written
account.

## Timeline

| Time (UTC)            | Event                                                                  |
|-----------------------|------------------------------------------------------------------------|
| 2026-05-25 09:14      | Agent resumes normal activity after a brief stalled-Cortex recovery.   |
| 2026-05-27 ~13:00     | Last successful Cortex commit on `experiment` (`1e77ddb`).              |
| 2026-05-27 ~13:02     | LLM stream stalls; no model output for several minutes.                |
| 2026-05-27 ~15:05     | Telegram poller SSL read timeout in `spine/telegram.py:87`.             |
| 2026-05-27 ~15:05     | `[SYSTEM WATCHDOG]` notices begin queuing (one per poll).              |
| 2026-05-27 ~15:05+    | 171 `Spine Tampering` notices + 7 `Spine crash` notices accumulate.   |
| 2026-05-27 ~15:05+    | Lazarus Protocol triggers; restart loop fails to drain the queue.      |
| 2026-05-27 onward     | Runtime stuck in restart loop; not observed until after the experiment. |

The exact restart-loop time is unknown — the runtime was unsupervised
when it happened and the watchdog log was not preserved.

## Root cause

1. **Primary: model stall.** The LLM (`gemma4:31b-cloud` via TogetherAI)
   stopped producing tokens for 7,535+ seconds. With no token stream,
   the Cortex's ReAct loop had no tool calls to make and no commits to
   emit; the agent's heartbeat degraded to "alive but idle".
2. **Trigger: Telegram poller SSL timeout** in
   `spine/telegram.py:87`. The Spine uses long-poll `getUpdates` with a
   30s timeout; the upstream Telegram API's TLS read timed out after the
   30s deadline. The exception was not caught at the poll level, so the
   poller exited and never restarted.
3. **Amplifier: pending-notice flood.** Once the poller was down, the
   `[SYSTEM WATCHDOG]` infrastructure (which posts "Spine Tampering"
   notices whenever the Spine restarts and notices the agent's been
   idle) started writing one notice per restart cycle. With the Spine
   flapping on Lazarus, the queue grew by one entry per cycle, 178 times.
4. **Broken restart loop:** on the next Lazarus restart, the Cortex
   (when it managed to come up) immediately consumed the oldest notices
   as system messages; the notices were themselves `Spine Tampering`
   notices about a tampering the Cortex had no way to act on, so the
   Cortex folded them into the stream and stalled again. Each restart
   added more notices to the head of the queue.

The 178 notices are still on disk in
`memory/pending_system_notices.json` and have never been consumed.

## Status

- **The runtime is in a broken restart loop** in the `experiment`
  branch's working tree.
- **The agent never came back online.** The crash was discovered on
  2026-06-08 while reading the experiment branch's history; the
  runtime was not actively monitored when it happened.
- **No data loss** beyond the ~2 hours of stalled-Cortex time. The
  178 queued notices are *additive* and can be cleared without losing
  any agent work.

## Recommendation

Before the next run, either:

1. **Clear `memory/pending_system_notices.json`** — simplest, the
   notices are not load-bearing.
2. **Implement a max-size cap** on the queue (e.g. truncate to the most
   recent 20 notices, or drop notices older than 1 hour) so a watchdog
   storm cannot wedge the restart loop.
3. **Catch the SSL read timeout at the poll level** in
   `spine/telegram.py:87` so the poller reconnects instead of exiting.
   The Telegram client is supposed to handle this; the unhandled
   exception path is a regression.

## Related

- [`reports/EXPERIMENT_2_REPORT_2026-05-15-to-2026-05-27.md`](../reports/EXPERIMENT_2_REPORT_2026-05-15-to-2026-05-27.md)
  for the full experiment context.
- The Nono integration (June 2026) is unrelated to this crash; the
  Spine was the only affected process.
