"""Bounded settings-write for the dashboard (V7 P3.2).

The ONLY path by which the dashboard may change tunables. It is deliberately narrow:

  * Only parameters in reflection.TUNABLE_BOUNDS may be written. Everything else —
    above all the HARD FLOOR (risk %, loss caps, total-risk cap, demo guard, dry_run,
    absolute lot cap) — is rejected, and also listed explicitly in FORBIDDEN as
    defence-in-depth.
  * Values are clamped to each parameter's [min, max] bound; an out-of-range request
    is clamped (and reported), never written raw.
  * .env is backed up to .env.bak and written atomically; a failure restores the backup.
  * Config is read once at startup, so a successful write also drops a "pending restart"
    marker. The supervisor clears it on boot; the dashboard shows a restart banner.

Pure helpers (_clamp_updates, _env_upsert, _parse_env) are unit-tested without touching
the real .env.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from ..config import PROJECT_ROOT, Settings
from ..learning.reflection import TUNABLE_BOUNDS
from ..logging_setup import get_logger

log = get_logger("goldtrader.dashboard.settings")

ENV_PATH = PROJECT_ROOT / ".env"

# Hard-floor keys that may NEVER be written from the dashboard, even by mistake. None of
# these are in TUNABLE_BOUNDS (so they'd be rejected anyway) — this is belt-and-suspenders.
FORBIDDEN = {
    "risk_pct_per_trade", "max_daily_loss_pct", "max_total_loss_pct", "max_total_risk_pct",
    "require_demo", "dry_run", "max_lots_absolute", "max_open_positions",
}

# Risk-posture presets. These move trade FREQUENCY and DRAWDOWN posture — they do NOT
# create profitability (the 5yr lab showed no filter combination crosses into positive
# expectancy). Every value sits inside TUNABLE_BOUNDS. Conservative = the lab's safest
# ("all filters") config: wider TP, stronger-trend-only, tighter COT gate.
PRESETS: dict[str, dict] = {
    "conservative": {
        "label": "Conservative",
        "note": "Fewer, higher-quality trades; smallest drawdown. The lab's safest config.",
        "values": {"atr_tp_mult": 3.5, "adx_min_trend": 24.0, "cot_extreme_z": 1.0},
    },
    "balanced": {
        "label": "Balanced",
        "note": "The project defaults — a middle posture.",
        "values": {"atr_tp_mult": 2.0, "adx_min_trend": 18.0, "cot_extreme_z": 1.5},
    },
    "aggressive": {
        "label": "Aggressive",
        "note": "More trades, looser gates — more activity and larger swings. Not more profit.",
        "values": {"atr_tp_mult": 2.0, "adx_min_trend": 14.0, "cot_extreme_z": 2.0},
    },
}


# ---------------- pure helpers ----------------
def _clamp_updates(updates: dict) -> tuple[dict, dict, list[dict]]:
    """Split a request into (applied, clamped, rejected).

    applied  = {param: final_value} for whitelisted params (clamped into bounds).
    clamped  = {param: {requested, applied}} for values that had to be clamped.
    rejected = [{param, reason}] for non-whitelisted / non-numeric / forbidden params.
    """
    applied: dict[str, float] = {}
    clamped: dict[str, dict] = {}
    rejected: list[dict] = []
    for param, raw in (updates or {}).items():
        if param in FORBIDDEN:
            rejected.append({"param": param, "reason": "hard-floor parameter (never editable)"})
            continue
        if param not in TUNABLE_BOUNDS:
            rejected.append({"param": param, "reason": "not a tunable parameter"})
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            rejected.append({"param": param, "reason": "not a number"})
            continue
        lo, hi = TUNABLE_BOUNDS[param]
        fixed = min(hi, max(lo, val))
        if fixed != val:
            clamped[param] = {"requested": val, "applied": fixed}
        applied[param] = fixed
    return applied, clamped, rejected


def _strip_value(raw: str) -> str:
    """Strip an inline ' # comment' and surrounding quotes/space from an env value."""
    v = raw.strip()
    # inline comment only when preceded by whitespace (so URLs with # are safe-ish)
    m = re.search(r"\s#", v)
    if m:
        v = v[: m.start()].strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v


def _parse_env(text: str) -> dict[str, str]:
    """Parse 'KEY=value' lines into {UPPER_KEY: stripped_value}. Ignores comments/blanks."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, val = s.partition("=")
        out[key.strip().upper()] = _strip_value(val)
    return out


def _env_upsert(text: str, env_key: str, value) -> str:
    """Set env_key=value in the .env text, preserving every other line, the inline
    comment on the target line, and the file's existing newline style. Appends if absent."""
    pattern = re.compile(
        r"^(?P<pre>[ \t]*" + re.escape(env_key) + r"[ \t]*=[ \t]*)(?P<val>\S*)(?P<post>[^\r\n]*)$",
        re.MULTILINE | re.IGNORECASE,
    )
    if pattern.search(text):
        # Replace EVERY occurrence: a stray duplicate key would otherwise be loaded last by
        # pydantic-settings and silently shadow a single-line edit (save looks applied but isn't).
        return pattern.sub(lambda m: m.group("pre") + str(value) + m.group("post"), text)
    nl = "\r\n" if "\r\n" in text else "\n"
    sep = "" if (text == "" or text.endswith(("\n", "\r"))) else nl
    return text + sep + f"{env_key}={value}" + nl


# ---------------- reads ----------------
def read_tunables(s: Settings, env_path: Path | None = None) -> dict:
    """Current value + bounds for every tunable param, the presets, and pending state.

    Current values come from the .env file on disk (so repeated edits show the latest),
    falling back to the running Settings default when a key isn't written explicitly."""
    path = env_path or ENV_PATH
    try:
        env = _parse_env(path.read_text(encoding="utf-8")) if path.exists() else {}
    except OSError:
        env = {}
    params = {}
    for param, (lo, hi) in TUNABLE_BOUNDS.items():
        raw = env.get(param.upper())
        try:
            val = float(raw) if raw is not None else float(getattr(s, param))
        except (TypeError, ValueError):
            val = float(getattr(s, param, 0.0))
        params[param] = {"value": val, "min": lo, "max": hi}
    presets = {k: {"label": v["label"], "note": v["note"], "values": v["values"]}
               for k, v in PRESETS.items()}
    return {"params": params, "presets": presets, "pending": read_pending(s)}


def read_pending(s: Settings) -> dict | None:
    try:
        if s.settings_pending_file.exists():
            return json.loads(s.settings_pending_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return None


# ---------------- write ----------------
def apply_updates(s: Settings, updates: dict, *, env_path: Path | None = None) -> dict:
    """Validate -> clamp -> back up -> atomically write whitelisted params to .env, then
    drop a pending-restart marker. Returns a JSON-serializable result; never raises."""
    applied, clamped, rejected = _clamp_updates(updates)
    if not applied:
        return {"ok": False, "applied": {}, "clamped": clamped, "rejected": rejected,
                "message": "No valid tunable changes."}
    path = env_path or ENV_PATH
    backup = path.with_suffix(path.suffix + ".bak")
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        original = text
        if path.exists():
            backup.write_text(original, encoding="utf-8")
        for param, val in applied.items():
            # write ints without a trailing .0 where the value is whole (cleaner .env)
            out = int(val) if float(val).is_integer() else val
            text = _env_upsert(text, param.upper(), out)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        # restore from backup on any I/O failure
        try:
            if backup.exists():
                backup.replace(path)
        except OSError:
            pass
        log.error("settings_write_failed", error=str(exc))
        return {"ok": False, "applied": {}, "clamped": clamped, "rejected": rejected,
                "message": f"Write failed (no changes applied): {exc}"}

    _write_pending(s, applied)
    log.warning("settings_written", params=list(applied), clamped=list(clamped),
                rejected=[r["param"] for r in rejected])
    return {"ok": True, "applied": applied, "clamped": clamped, "rejected": rejected,
            "message": f"Saved {len(applied)} change(s). Restart to apply.",
            "restart_required": True}


def apply_preset(s: Settings, name: str, *, env_path: Path | None = None) -> dict:
    preset = PRESETS.get((name or "").lower())
    if not preset:
        return {"ok": False, "message": f"Unknown preset: {name}", "applied": {}}
    res = apply_updates(s, dict(preset["values"]), env_path=env_path)
    res["preset"] = name.lower()
    return res


def _write_pending(s: Settings, applied: dict) -> None:
    try:
        s.settings_pending_file.parent.mkdir(parents=True, exist_ok=True)
        s.settings_pending_file.write_text(json.dumps({
            "ts": time.time(), "changed": list(applied), "values": applied,
        }), encoding="utf-8")
    except OSError as exc:  # noqa: BLE001
        log.warning("settings_pending_write_failed", error=str(exc))
