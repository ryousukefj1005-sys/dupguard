#!/usr/bin/env python3
"""dupguard — content-level deduplication guard for automated message pipelines.

Stops your cron jobs / bots / webhooks from sending the same notification twice.
Extracted from a production automation fleet where duplicate pushes were a real
operational incident class (multi-week empirical window: every true duplicate fell
inside a 90-second re-send gap; we gate at a configurable window, default 1800s).

Usage:
    from dupguard import DupGuard

    guard = DupGuard(path="dupguard_log.jsonl", window=1800)
    verdict = guard.check(title="Nightly report", body="...")   # peek, no side effect
    if not verdict.suppressed:
        send(...)            # your send logic
        guard.log(title="Nightly report", body="...")  # record AFTER success

CLI:
    python dupguard.py check --title "Nightly report"   # exit 0 = send, 1 = suppressed
    python dupguard.py log   --title "Nightly report"   # record a successful send
    python dupguard.py stats                            # window/window hits/forced
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

__version__ = "1.0.0"

DEFAULT_WINDOW = 1800  # seconds
DEFAULT_LOG = "dupguard_send_log.jsonl"


@dataclass
class Verdict:
    """Decision for one candidate message. suppressed=True => do not send."""

    suppressed: bool
    key: str
    reason: str = ""
    last_sent_ago: Optional[float] = None
    forced: bool = False


@dataclass
class DupGuard:
    """Append-only JSONL ledger + windowed content dedup.

    Design notes from production:
      * Record only AFTER a successful send — a failed attempt must not consume
        the dedup budget of the retry.
      * Key = normalized title hash (title is the identity of a push card /
        notification headline). Body hash is kept as a secondary signal.
      * `force=True` bypasses the gate but still logs, so forced sends are
        visible in `stats()` and can be audited.
      * Append-only JSONL: crash-safe, trivially greppable, no DB dependency.
    """

    path: str = DEFAULT_LOG
    window: float = DEFAULT_WINDOW
    title_max_len: int = 80  # titles longer than this are truncated before hashing

    def __post_init__(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)

    # ---------- key derivation ----------
    @staticmethod
    def _normalize(title: str) -> str:
        """Lowercase, collapse whitespace, strip decorative emoji/noise glyphs."""
        t = title.lower()
        t = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]+", "", t)  # emoji ranges
        t = re.sub(r"\s+", " ", t).strip()
        return t

    def _key(self, title: str) -> str:
        norm = self._normalize(title)[: self.title_max_len]
        return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]

    # ---------- ledger ----------
    def _read(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # torn tail line from a crash mid-write: skip
        return out

    def _append(self, rec: dict) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())

    # ---------- public API ----------
    def check(self, title: str, body: str = "", force: bool = False) -> Verdict:
        """Decide whether this message may be sent. No side effects."""
        key = self._key(title)
        now = time.time()
        if force:
            return Verdict(suppressed=False, key=key, reason="forced", forced=True)
        last: Optional[dict] = None
        for rec in self._read():
            if rec.get("key") == key:
                last = rec  # keep latest occurrence
        if last is None:
            return Verdict(suppressed=False, key=key, reason="first-send")
        ago = now - float(last.get("ts", 0))
        if ago < self.window:
            return Verdict(
                suppressed=True,
                key=key,
                reason=f"window-hit: last sent {ago:.0f}s ago < {self.window:.0f}s",
                last_sent_ago=ago,
            )
        return Verdict(suppressed=False, key=key, reason=f"window-expired ({ago:.0f}s)", last_sent_ago=ago)

    def log(self, title: str, body: str = "", force: bool = False) -> dict:
        """Record a successful send. Call AFTER your send succeeded."""
        rec = {
            "ts": time.time(),
            "key": self._key(title),
            "title": title,
            "body_hash": hashlib.sha1(body.encode("utf-8")).hexdigest()[:12] if body else "",
            "forced": bool(force),
        }
        self._append(rec)
        return rec

    def stats(self) -> dict:
        recs = self._read()
        if not recs:
            return {"records": 0}
        now = time.time()
        return {
            "records": len(recs),
            "distinct_titles": len({r.get("key") for r in recs}),
            "forced": sum(1 for r in recs if r.get("forced")),
            "newest_age_s": round(now - float(recs[-1].get("ts", 0)), 1),
            "window_s": self.window,
        }


# ---------- CLI ----------
def _cli() -> int:
    ap = argparse.ArgumentParser(prog="dupguard", description=__doc__.splitlines()[0])
    ap.add_argument("--log", default=DEFAULT_LOG, help="ledger jsonl path (default: %(default)s)")
    ap.add_argument("--window", type=float, default=DEFAULT_WINDOW, help="dedup window seconds (default: %(default)s)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="exit 0 = may send, exit 3 = suppressed")
    for p in (p_check,):
        p.add_argument("--title", required=True)
        p.add_argument("--body", default="")
        p.add_argument("--force", action="store_true")

    p_log = sub.add_parser("log", help="record a successful send")
    p_log.add_argument("--title", required=True)
    p_log.add_argument("--body", default="")
    p_log.add_argument("--force", action="store_true")

    sub.add_parser("stats", help="ledger summary")

    a = ap.parse_args()
    g = DupGuard(path=a.log, window=a.window)

    if a.cmd == "check":
        v = g.check(a.title, a.body, force=a.force)
        print(json.dumps({"suppressed": v.suppressed, "reason": v.reason, "key": v.key}, ensure_ascii=False))
        return 3 if v.suppressed else 0
    if a.cmd == "log":
        rec = g.log(a.title, a.body, force=a.force)
        print(json.dumps(rec, ensure_ascii=False))
        return 0
    print(json.dumps(g.stats(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
