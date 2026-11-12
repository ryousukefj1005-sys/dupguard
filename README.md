# dupguard

**Content-level deduplication guard for automated message pipelines.** One file, zero dependencies, Python 3.8+.

Stops your cron jobs, bots, and webhooks from sending the same notification twice — the unglamorous incident class nobody notices until someone asks "why did I get this alert three times?"

## Why

Extracted from a production automation fleet where duplicate pushes were a recurring operational incident. The empirical finding that shaped the design, from a two-week audit of real send logs:

> **Every true duplicate fell inside a 90-second re-send gap** — retries, overlapping cron triggers, and agent re-fires never produced a "late" duplicate.

So dedup is a **time-windowed key check**, not fuzzy similarity: cheap, deterministic, and auditable. When the window expires, the same title may legitimately fire again (a daily report *should* send every day).

## Install

No package needed — copy `dupguard.py` directly from this repository.

## Quickstart

The two-call pattern — **check before sending, log only after success** (a failed send must not consume the dedup budget of its retry):

```python
from dupguard import DupGuard

guard = DupGuard(path="dupguard_log.jsonl", window=1800)  # 10 min

v = guard.check(title="Nightly backup report")
if not v.suppressed:
    send_notification(...)                 # your logic
    guard.log(title="Nightly backup report")  # record AFTER success
else:
    print(f"suppressed: {v.reason}")       # e.g. window-hit: last sent 42s ago
```

Emergency bypass — forced sends pass the gate but are still recorded, so `stats()` makes them visible for audit:

```python
guard.check(title="Disk 95%", force=True)
guard.log(title="Disk 95%", force=True)
```

## CLI

```bash
$ python dupguard.py check --title "Nightly report"    # exit 0 = send, exit 3 = suppressed
{"suppressed": false, "reason": "first-send", "key": "a1b2c3d4e5f60718"}

$ python dupguard.py log --title "Nightly report"
$ python dupguard.py check --title "Nightly report"
{"suppressed": true, "reason": "window-hit: last sent 30s ago < 1800s", ...}

$ python dupguard.py stats
{"records": 42, "distinct_titles": 17, "forced": 2, "newest_age_s": 812.4, "window_s": 1800}
```

Shell pipelines:

```bash
python dupguard.py check --title "Deploy $ENV" && \
  ./notify.sh && \
  python dupguard.py log --title "Deploy $ENV"
```

## Design notes

- **Identity = normalized title.** Lowercased, emoji-stripped, whitespace-collapsed, truncated, SHA-1'd. `🚨 NIGHTLY Report 🚨` and `nightly report` are the same message. Body is hashed as a secondary signal, not identity — titles are what humans see twice.
- **Append-only JSONL ledger.** Crash-safe (a torn tail line is skipped, not fatal), trivially greppable, no database. Rotate by moving the file; dupguard recreates it.
- **No cleanup daemon.** The ledger growing is fine — check only looks up the *latest* entry per key. A year of logs is still milliseconds.
- **Exit codes as API.** `check` exits `0`/`3` so shell scripts and CI can branch without parsing JSON.

## Testing

```bash
python3 test_dupguard.py   # 9 tests, stdlib unittest, zero dependencies
```

## License

MIT
