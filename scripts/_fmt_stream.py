#!/usr/bin/env python3
"""Format claude `--output-format stream-json` events into timestamped,
human-readable activity lines. Reads JSONL on stdin, writes lines on stdout.

Used by orch-run.sh to turn the orchestrator's realtime event stream into a
readable activity log. Unparseable lines pass through verbatim so nothing is
silently dropped.
"""
import sys, json
from datetime import datetime


def ts():
    return datetime.now().strftime("%H:%M:%S")


def short(s, n=200):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def tool_summary(inp):
    if not isinstance(inp, dict):
        return short(inp, 120)
    # surface the most informative field for common tools
    for k in ("command", "file_path", "path", "description", "pattern",
              "query", "url", "prompt", "old_string"):
        if k in inp and inp[k]:
            return f"{k}={short(inp[k], 120)}"
    return short(json.dumps(inp), 120)


def emit(line):
    print(line, flush=True)


for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    try:
        ev = json.loads(raw)
    except Exception:
        emit(f"[{ts()}]    {short(raw)}")
        continue

    t = ev.get("type")
    if t == "system" and ev.get("subtype") == "init":
        emit(f"[{ts()}] ▶  session init · model={ev.get('model', '?')} · cwd={ev.get('cwd', '?')}")
    elif t == "assistant":
        for c in ev.get("message", {}).get("content", []):
            if c.get("type") == "text":
                txt = c.get("text", "").strip()
                if txt:
                    emit(f"[{ts()}] 💭 {short(txt, 320)}")
            elif c.get("type") == "tool_use":
                emit(f"[{ts()}] 🔧 {c.get('name')}  {tool_summary(c.get('input'))}")
    elif t == "user":
        for c in ev.get("message", {}).get("content", []):
            if isinstance(c, dict) and c.get("type") == "tool_result":
                body = c.get("content", "")
                if isinstance(body, list):
                    body = " ".join(
                        b.get("text", "") for b in body if isinstance(b, dict)
                    )
                mark = "❌" if c.get("is_error") else "↩ "
                emit(f"[{ts()}] {mark} {short(body, 200)}")
    elif t == "result":
        emit(f"[{ts()}] ■  result={ev.get('subtype')} · dur={ev.get('duration_ms')}ms · cost=${ev.get('total_cost_usd')}")
        res = ev.get("result")
        if res:
            emit(f"[{ts()}] ── final message " + "─" * 40)
            for ln in str(res).splitlines():
                emit("   " + ln)
