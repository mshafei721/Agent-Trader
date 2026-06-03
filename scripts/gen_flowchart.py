"""Generate system-flow.excalidraw (native Excalidraw file) from a compact spec.

Run:  .venv/Scripts/python.exe scripts/gen_flowchart.py
Output: system-flow.excalidraw in the repo root (open with the VS Code Excalidraw extension).
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Compact spec: shapes (optionally labeled), arrows, and standalone texts.
SHAPES = [
    # id, x, y, w, h, fill, stroke, label, [strokeWidth], [opacity]
    ("zL", 45, 278, 320, 500, "#c3fae8", "#06b6d4", None, 1, 30),
    ("zR", 435, 278, 385, 830, "#e5dbff", "#8b5cf6", None, 1, 30),
    ("wake", 300, 75, 220, 54, "#a5d8ff", "#4a9eed", "Loop wakes every 60s", 2, 100),
    ("llm", 585, 58, 225, 84, "#c3fae8", "#06b6d4",
     "LLM bias (TradingAgents)\nrefresh ~4h, cached\nLONG / SHORT / FLAT", 2, 100),
    ("safe", 215, 160, 360, 56, "#fff3bf", "#f59e0b",
     "Safety gates\nkill-switch · demo-only · market-open · loss caps", 2, 100),
    ("hL", 70, 290, 280, 58, "#c3fae8", "#0e7490", "FAST  (every 60s)\nManage open trades", 2, 100),
    ("hR", 460, 290, 300, 58, "#d0bfff", "#8b5cf6", "ENTRY EVAL  (every 30 min)\nFind a new trade", 2, 100),
    ("cut", 70, 374, 280, 82, "#ffc9c9", "#ef4444",
     "Early loss-cut\nif down 0.5R AND M15 flips\n-> close now", 2, 100),
    ("scale", 70, 480, 280, 64, "#ffd8a8", "#f59e0b", "Scale-out\nclose half at +1R", 2, 100),
    ("trail", 70, 568, 280, 86, "#b2f2bb", "#22c55e",
     "Breakeven at +0.5R\nthen Chandelier ATR trail\n(stop never loosens)", 2, 100),
    ("note", 70, 690, 280, 66, "#ffc9c9", "#ef4444",
     "Hard stop-loss lives on broker\n(ultimate backstop)", 1, 55),
    ("bA", 460, 374, 300, 70, "#a5d8ff", "#4a9eed",
     "1. H4 trend = direction\nUP->buy   DOWN->sell   flat->skip", 2, 100),
    ("bB", 460, 468, 300, 60, "#a5d8ff", "#4a9eed", "2. H1 not opposing + ADX strong", 2, 100),
    ("bC", 460, 552, 300, 66, "#a5d8ff", "#4a9eed", "3. M30 trigger\nMACD cross / momentum", 2, 100),
    ("bD", 460, 642, 300, 72, "#ffd8a8", "#f59e0b",
     "4. LLM bias veto\nblocks only strong opposite (>=0.75)", 2, 100),
    ("bE", 460, 738, 300, 64, "#d0bfff", "#8b5cf6", "5. Pyramid gate\nadd only into winners · max 3", 2, 100),
    ("bF", 460, 826, 300, 72, "#b2f2bb", "#22c55e", "6. Risk 0.5% (M30 ATR)\n+ total-risk cap 1.5%", 2, 100),
    ("bG", 460, 922, 300, 64, "#b2f2bb", "#15803d", "PLACE DEMO ORDER\njournal + MT5", 3, 100),
    ("jrn", 460, 1010, 300, 64, "#c3fae8", "#06b6d4", "Journal (SQLite) of every trade", 2, 100),
    # ---- infra self-heal (top-left) ----
    ("infra", 45, 72, 190, 92, "#c3fae8", "#0e7490",
     "Infra self-heal\nwatchdog · circuit-breaker\nreconnect · NSSM restart", 2, 100),
    # ---- reflection / self-heal / learn band (full width, bottom) ----
    ("zRef", 45, 1100, 775, 240, "#fff3bf", "#b45309", None, 1, 25),
    ("rHdr", 70, 1112, 360, 46, "#fff3bf", "#b45309", "REFLECT & SELF-HEAL\n(every 20 trades / daily)", 2, 100),
    ("rRead", 70, 1176, 215, 74, "#c3fae8", "#06b6d4", "Read closed trades\n+ stats (R, PF, winrate)", 2, 100),
    ("rHeal", 320, 1176, 215, 74, "#ffd8a8", "#f59e0b", "Deterministic self-heal\ncut risk / pause (AUTO)", 2, 100),
    ("rLLM", 570, 1176, 215, 74, "#d0bfff", "#8b5cf6", "LLM review\nadvisory suggestions", 2, 100),
    ("rRep", 320, 1264, 215, 60, "#fff3bf", "#b45309", "Report\n(data/reflections)", 2, 100),
]

# id, x, y, points, stroke, dashed, label
ARROWS = [
    ("aws", 410, 129, [[0, 0], [0, 31]], "#1e1e1e", False, None),
    ("asl", 340, 216, [[0, 0], [-130, 74]], "#0e7490", False, None),
    ("asr", 480, 216, [[0, 0], [130, 74]], "#8b5cf6", False, None),
    ("aHc", 210, 348, [[0, 0], [0, 26]], "#0e7490", False, None),
    ("acs", 210, 456, [[0, 0], [0, 24]], "#0e7490", False, None),
    ("ast", 210, 544, [[0, 0], [0, 24]], "#0e7490", False, None),
    ("atn", 210, 654, [[0, 0], [0, 36]], "#ef4444", True, None),
    ("aHA", 610, 348, [[0, 0], [0, 26]], "#8b5cf6", False, None),
    ("aAB", 610, 444, [[0, 0], [0, 24]], "#8b5cf6", False, None),
    ("aBC", 610, 528, [[0, 0], [0, 24]], "#8b5cf6", False, None),
    ("aCD", 610, 618, [[0, 0], [0, 24]], "#8b5cf6", False, None),
    ("aDE", 610, 714, [[0, 0], [0, 24]], "#8b5cf6", False, None),
    ("aEF", 610, 802, [[0, 0], [0, 24]], "#8b5cf6", False, None),
    ("aFG", 610, 898, [[0, 0], [0, 24]], "#22c55e", False, None),
    ("aGj", 610, 986, [[0, 0], [0, 24]], "#06b6d4", False, None),
    ("afb", 445, 1010, [[0, 0], [0, -112]], "#06b6d4", True, "feedback"),
    ("alv", 800, 142, [[0, 0], [0, 536], [-40, 0]], "#06b6d4", True, "veto"),
    # reflection band wiring
    ("ajr", 610, 1074, [[0, 0], [-435, 102]], "#b45309", False, None),   # journal -> read
    ("arh", 285, 1213, [[0, 0], [35, 0]], "#b45309", False, None),       # read -> heal
    ("ahl", 535, 1213, [[0, 0], [35, 0]], "#b45309", False, None),       # heal -> LLM
    ("alr", 660, 1250, [[0, 0], [-130, 14]], "#8b5cf6", False, None),    # LLM -> report
    ("ahf", 427, 1176, [[0, 0], [0, -278], [183, 0]], "#f59e0b", True, "throttle / pause"),  # heal -> risk
    ("ainf", 235, 110, [[0, 0], [65, -8]], "#0e7490", True, None),       # infra -> wake
]

TEXTS = [
    ("title", 237, -15, "GoldTrader - Autonomous XAUUSD", 26, "#1e1e1e"),
    ("sub", 255, 22, "Two-speed: TradingAgents bias + technical timing", 14, "#757575"),
]


def base(eid, seed):
    return {
        "id": eid, "angle": 0, "strokeColor": "#1e1e1e", "backgroundColor": "transparent",
        "fillStyle": "solid", "strokeWidth": 2, "strokeStyle": "solid", "roughness": 1,
        "opacity": 100, "groupIds": [], "frameId": None, "roundness": None, "seed": seed,
        "version": 1, "versionNonce": seed * 7 % 2147483647, "isDeleted": False,
        "boundElements": [], "updated": 1, "link": None, "locked": False,
    }


def build():
    out = []
    seed = 1000
    for sp in SHAPES:
        eid, x, y, w, h, fill, stroke, label = sp[0], sp[1], sp[2], sp[3], sp[4], sp[5], sp[6], sp[7]
        sw = sp[8] if len(sp) > 8 else 2
        op = sp[9] if len(sp) > 9 else 100
        seed += 1
        el = base(eid, seed)
        el.update({"type": "rectangle", "x": x, "y": y, "width": w, "height": h,
                   "backgroundColor": fill, "strokeColor": stroke, "strokeWidth": sw,
                   "opacity": op, "roundness": {"type": 3}})
        if label:
            tid = eid + "_t"
            el["boundElements"] = [{"type": "text", "id": tid}]
            out.append(el)
            seed += 1
            lines = label.split("\n")
            fs = 16 if len(max(lines, key=len)) <= 24 else 15
            t = base(tid, seed)
            t.update({"type": "text", "x": x + 8, "y": y + h / 2 - len(lines) * fs * 0.6,
                      "width": w - 16, "height": len(lines) * fs * 1.25, "text": label,
                      "fontSize": fs, "fontFamily": 1, "textAlign": "center",
                      "verticalAlign": "middle", "containerId": eid,
                      "originalText": label, "lineHeight": 1.25, "strokeColor": "#1e1e1e"})
            out.append(t)
        else:
            out.append(el)

    for ar in ARROWS:
        eid, x, y, pts, stroke, dashed, label = ar
        seed += 1
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        el = base(eid, seed)
        el.update({"type": "arrow", "x": x, "y": y,
                   "width": max(xs) - min(xs) or 1, "height": max(ys) - min(ys) or 1,
                   "points": pts, "strokeColor": stroke,
                   "strokeStyle": "dashed" if dashed else "solid",
                   "startArrowhead": None, "endArrowhead": "arrow",
                   "lastCommittedPoint": None, "startBinding": None, "endBinding": None})
        if label:
            tid = eid + "_t"
            el["boundElements"] = [{"type": "text", "id": tid}]
            out.append(el)
            seed += 1
            t = base(tid, seed)
            t.update({"type": "text", "x": x, "y": y, "width": 60, "height": 18,
                      "text": label, "fontSize": 14, "fontFamily": 1, "textAlign": "center",
                      "verticalAlign": "middle", "containerId": eid, "originalText": label,
                      "lineHeight": 1.25, "strokeColor": stroke})
            out.append(t)
        else:
            out.append(el)

    for tx in TEXTS:
        eid, x, y, text, fs, color = tx
        seed += 1
        t = base(eid, seed)
        t.update({"type": "text", "x": x, "y": y, "width": len(text) * fs * 0.5,
                  "height": fs * 1.25, "text": text, "fontSize": fs, "fontFamily": 1,
                  "textAlign": "left", "verticalAlign": "top", "containerId": None,
                  "originalText": text, "lineHeight": 1.25, "strokeColor": color})
        out.append(t)
    return out


doc = {
    "type": "excalidraw", "version": 2,
    "source": "goldtrader/scripts/gen_flowchart.py",
    "elements": build(),
    "appState": {"gridSize": None, "viewBackgroundColor": "#ffffff"},
    "files": {},
}
path = ROOT / "system-flow.excalidraw"
path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
print(f"wrote {path}  ({len(doc['elements'])} elements)")
