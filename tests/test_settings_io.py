"""Bounded dashboard settings-write (V7 P3.2): clamp/forbid + atomic .env upsert + presets.

The hard floor (risk %, loss caps, demo guard, dry_run, lot cap) must be UNWRITABLE through
this path; out-of-range values are clamped, never written raw; and a successful write leaves a
pending-restart marker.
"""
import json

import goldtrader.config as cfg
from goldtrader.dashboard import settings_io as sio


def _settings(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cfg, "RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(cfg, "LOGS_DIR", tmp_path / "logs")
    return cfg.Settings()


# ---------------- clamp / forbid (pure) ----------------
def test_clamp_passthrough_and_clamp():
    applied, clamped, rejected = sio._clamp_updates(
        {"atr_tp_mult": 3.5, "adx_min_trend": 999, "ema_fast": 1})
    assert applied["atr_tp_mult"] == 3.5 and "atr_tp_mult" not in clamped
    assert applied["adx_min_trend"] == 35.0  # clamped to max
    assert clamped["adx_min_trend"] == {"requested": 999.0, "applied": 35.0}
    assert applied["ema_fast"] == 5.0        # clamped to min
    assert rejected == []


def test_clamp_rejects_hard_floor_and_unknown_and_nonnumeric():
    applied, _, rejected = sio._clamp_updates({
        "risk_pct_per_trade": 5.0,   # FORBIDDEN
        "dry_run": False,            # FORBIDDEN
        "max_daily_loss_pct": 9.0,   # FORBIDDEN
        "totally_made_up": 1,        # not tunable
        "ema_fast": "abc",           # not a number
        "atr_tp_mult": 2.5,          # the one good one
    })
    assert applied == {"atr_tp_mult": 2.5}
    reasons = {r["param"]: r["reason"] for r in rejected}
    assert "hard-floor" in reasons["risk_pct_per_trade"]
    assert "hard-floor" in reasons["dry_run"]
    assert "hard-floor" in reasons["max_daily_loss_pct"]
    assert reasons["totally_made_up"] == "not a tunable parameter"
    assert reasons["ema_fast"] == "not a number"


def test_cot_extreme_z_is_whitelisted():
    from goldtrader.learning.reflection import TUNABLE_BOUNDS
    assert "cot_extreme_z" in TUNABLE_BOUNDS
    applied, _, rejected = sio._clamp_updates({"cot_extreme_z": 1.0})
    assert applied == {"cot_extreme_z": 1.0} and rejected == []


# ---------------- env upsert / parse (pure) ----------------
def test_env_upsert_preserves_inline_comment():
    text = "ATR_TP_MULT=2.0          # take-profit multiple\nDRY_RUN=true\n"
    out = sio._env_upsert(text, "ATR_TP_MULT", 3.5)
    assert "ATR_TP_MULT=3.5          # take-profit multiple" in out
    assert "DRY_RUN=true" in out  # untouched


def test_env_upsert_appends_when_absent():
    out = sio._env_upsert("DRY_RUN=true\n", "ADX_MIN_TREND", 24)
    assert out.endswith("ADX_MIN_TREND=24\n")
    assert "DRY_RUN=true" in out


def test_parse_env_strips_comments_and_quotes():
    env = sio._parse_env('ATR_TP_MULT=2.0   # c\nFOO="bar"\n# comment line\n\nBAZ=1')
    assert env["ATR_TP_MULT"] == "2.0"
    assert env["FOO"] == "bar"
    assert env["BAZ"] == "1"
    assert "# comment line" not in env


# ---------------- write path ----------------
def test_apply_updates_writes_backs_up_and_marks_pending(tmp_path, monkeypatch):
    s = _settings(tmp_path, monkeypatch)
    env = tmp_path / ".env"
    env.write_text("ATR_TP_MULT=2.0   # tp\nDRY_RUN=true\n", encoding="utf-8")
    res = sio.apply_updates(s, {"atr_tp_mult": 3.5, "adx_min_trend": 99}, env_path=env)
    assert res["ok"] is True and res["restart_required"] is True
    assert res["applied"]["atr_tp_mult"] == 3.5
    assert res["clamped"]["adx_min_trend"]["applied"] == 35.0
    parsed = sio._parse_env(env.read_text(encoding="utf-8"))
    assert parsed["ATR_TP_MULT"] == "3.5"
    assert parsed["ADX_MIN_TREND"] == "35"
    assert parsed["DRY_RUN"] == "true"  # untouched
    # backup retains the original, pending marker written
    assert "ATR_TP_MULT=2.0" in (tmp_path / ".env.bak").read_text(encoding="utf-8")
    pend = json.loads(s.settings_pending_file.read_text(encoding="utf-8"))
    assert set(pend["changed"]) == {"atr_tp_mult", "adx_min_trend"}


def test_apply_updates_all_rejected_writes_nothing(tmp_path, monkeypatch):
    s = _settings(tmp_path, monkeypatch)
    env = tmp_path / ".env"
    env.write_text("DRY_RUN=true\n", encoding="utf-8")
    res = sio.apply_updates(s, {"risk_pct_per_trade": 5.0, "dry_run": False}, env_path=env)
    assert res["ok"] is False and res["applied"] == {}
    assert env.read_text(encoding="utf-8") == "DRY_RUN=true\n"  # unchanged
    assert not s.settings_pending_file.exists()


def test_apply_preset_conservative(tmp_path, monkeypatch):
    s = _settings(tmp_path, monkeypatch)
    env = tmp_path / ".env"
    env.write_text("ATR_TP_MULT=2.0\nADX_MIN_TREND=18\nCOT_EXTREME_Z=1.5\n", encoding="utf-8")
    res = sio.apply_preset(s, "conservative", env_path=env)
    assert res["ok"] is True and res["preset"] == "conservative"
    parsed = sio._parse_env(env.read_text(encoding="utf-8"))
    assert parsed["ATR_TP_MULT"] == "3.5"
    assert parsed["ADX_MIN_TREND"] == "24"
    assert parsed["COT_EXTREME_Z"] == "1"


def test_apply_preset_unknown(tmp_path, monkeypatch):
    s = _settings(tmp_path, monkeypatch)
    res = sio.apply_preset(s, "nope", env_path=tmp_path / ".env")
    assert res["ok"] is False


def test_read_tunables_reads_disk_values(tmp_path, monkeypatch):
    s = _settings(tmp_path, monkeypatch)
    env = tmp_path / ".env"
    env.write_text("ATR_TP_MULT=3.5\n", encoding="utf-8")
    out = sio.read_tunables(s, env_path=env)
    assert out["params"]["atr_tp_mult"]["value"] == 3.5
    assert out["params"]["atr_tp_mult"]["min"] == 1.0
    assert "conservative" in out["presets"]
    assert out["pending"] is None
