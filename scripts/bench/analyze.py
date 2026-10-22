"""Summarises a bench pass: system power per config/workload with 95% CIs,
validity flags per run, page metrics and a per-process power breakdown.

Usage: python3 analyze.py <results/pass-dir> [--baseline zen-dev-lean]
"""

import argparse
import json
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path

# Two-sided 95% t critical values by degrees of freedom.
T95 = {1: 12.71, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228}

GROUPS = [
    ("zen", re.compile(r"^(zen|plugin-container|Zen.*)$")),
    ("safari", re.compile(r"^Safari")),
    ("wk-control", re.compile(r"^wk-control$")),
    ("webkit", re.compile(r"^com\.apple\.WebKit")),
    ("windowserver", re.compile(r"^WindowServer$")),
]


def ci(values):
    n = len(values)
    if n < 2:
        return statistics.mean(values), float("nan")
    m = statistics.mean(values)
    return m, T95.get(n - 1, 1.96) * statistics.stdev(values) / math.sqrt(n)


def welch_p(a, b):
    """Approximate two-sided p for Welch's t-test (normal tail on t; rough
    for tiny n, so the CI table is the primary read-out)."""
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    va, vb = statistics.variance(a) / len(a), statistics.variance(b) / len(b)
    if va + vb == 0:
        return 0.0
    t = (statistics.mean(a) - statistics.mean(b)) / math.sqrt(va + vb)
    return math.erfc(abs(t) / math.sqrt(2))


def top_breakdown(path):
    """Mean POWER per process group over top samples, skipping the first."""
    if not path.exists():
        return {}
    samples, cur = [], None
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("PID"):
            cur = defaultdict(float)
            samples.append(cur)
            continue
        if cur is None:
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            power = float(parts[-1])
        except ValueError:
            continue
        name = " ".join(parts[1:-2])
        for g, rx in GROUPS:
            if rx.search(name):
                cur[g] += power
                break
        else:
            cur["other"] += power
    samples = samples[1:]
    if not samples:
        return {}
    keys = {k for s in samples for k in s}
    return {k: sum(s.get(k, 0) for s in samples) / len(samples) for k in keys}


def page_metrics(records):
    out = {}
    video = [r for r in records if r["kind"] == "video"]
    if video:
        total = sum(r["total"] for r in video)
        dropped = sum(r["dropped"] for r in video)
        out["video_frames"] = total
        out["dropped_ratio"] = dropped / total if total else None
        out["video_size"] = f"{video[-1].get('w')}x{video[-1].get('h')}"
    scroll = [r for r in records if r["kind"] == "scroll" and r.get("frames")]
    if scroll:
        frames = sum(r["frames"] for r in scroll)
        out["scroll_fps"] = 1000 / (sum(r["meanMs"] * r["frames"] for r in scroll) / frames)
        out["long_frame_ratio"] = sum(r["longFrames"] for r in scroll) / frames
    out["hidden_reports"] = sum(1 for r in records if r.get("vis") != "visible")
    out["errors"] = [r.get("error") for r in records if r["kind"] == "error"]
    out["ua"] = records[-1]["ua"] if records else None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pass_dir", type=Path)
    ap.add_argument("--baseline", default="zen-dev-lean")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    pages = defaultdict(list)
    pages_file = args.pass_dir / "pages.jsonl"
    if pages_file.exists():
        for line in pages_file.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            pages[r["run"]].append(r)

    runs = []
    for d in sorted(args.pass_dir.iterdir()):
        meta_file = d / "meta.json"
        if not meta_file.exists():
            continue
        m = json.loads(meta_file.read_text())
        s = m["summary"]
        flags = []
        if s["external_seen"] or m["battery_start"]["external"]:
            flags.append("external-power")
        if m.get("hid_idle_end_s") is not None and m["hid_idle_end_s"] < m["duration_s"]:
            flags.append("user-input")
        if m.get("brightness_mnits") != m.get("brightness_end_mnits"):
            flags.append("brightness-changed")
        if m.get("low_power_mode"):
            flags.append("low-power-mode")
        if not s["mean_mw"] or s["counts"] < 120:
            flags.append("too-few-gauge-samples")
        if "No thermal warning" not in m.get("thermal_end", "No thermal warning"):
            flags.append("thermal")
        pm = page_metrics(pages.get(d.name, []))
        if m["workload"] != "none" and not pages.get(d.name):
            flags.append("no-page-reports")
        if pm.get("hidden_reports"):
            flags.append("page-hidden")
        if pm.get("errors"):
            flags.append("page-error")
        runs.append({
            "dir": d.name, "config": m["config"], "workload": m["workload"],
            "mean_mw": s["mean_mw"], "intervals": s["intervals_mw"],
            "battery": m["battery_start"]["percent"], "brightness": m.get("brightness_mnits"),
            "flags": flags, "page": pm, "procs": top_breakdown(d / "top.txt"),
            "extra": {k: m[k] for k in m if k.startswith("safari_")},
        })

    print(f"# {args.pass_dir.name}: {len(runs)} runs\n")
    print("## Runs")
    for r in runs:
        mw = f"{r['mean_mw']:.0f}" if r["mean_mw"] else "-"
        print(f"{r['dir']:<40} {mw:>6} mW  batt {r['battery']}%  {','.join(r['flags']) or 'ok'} {r['extra'] or ''}")

    valid = [r for r in runs if not r["flags"]]
    by = defaultdict(list)
    for r in valid:
        by[(r["workload"], r["config"])].append(r)

    print("\n## System power (valid runs only), mean ± 95% CI")
    desktop = [r["mean_mw"] for r in valid if r["config"] == "desktop"]
    if desktop:
        m, h = ci(desktop)
        print(f"desktop floor (no browser): {m:.0f} ± {h:.0f} mW (n={len(desktop)})")
    workloads = sorted({w for w, _ in by if w != "none"})
    summary = {}
    for w in workloads:
        print(f"\n### {w}")
        base = [r["mean_mw"] for r in by.get((w, args.baseline), [])]
        rows = []
        for (ww, c), rs in by.items():
            if ww != w:
                continue
            vals = [r["mean_mw"] for r in rs]
            m, h = ci(vals)
            rows.append((m, c, h, vals, rs))
        for m, c, h, vals, rs in sorted(rows):
            rel = ""
            if base and c != args.baseline:
                bm = statistics.mean(base)
                rel = f"  {100 * (m / bm - 1):+.1f}% vs {args.baseline} (p≈{welch_p(vals, base):.3f})"
            pm = [r["page"] for r in rs]
            extra = ""
            if any("dropped_ratio" in p for p in pm):
                dr = [p["dropped_ratio"] for p in pm if p.get("dropped_ratio") is not None]
                extra += f"  dropped {100 * statistics.mean(dr):.2f}%" if dr else ""
            if any("scroll_fps" in p for p in pm):
                extra += f"  {statistics.mean(p['scroll_fps'] for p in pm):.0f} fps"
                extra += f" long {100 * statistics.mean(p['long_frame_ratio'] for p in pm):.2f}%"
            procs = defaultdict(list)
            for r in rs:
                for k, v in r["procs"].items():
                    procs[k].append(v)
            ptxt = " ".join(f"{k}={statistics.mean(v):.0f}" for k, v in sorted(procs.items(), key=lambda kv: -statistics.mean(kv[1]))[:5])
            print(f"{c:<14} {m:7.0f} ± {h:5.0f} mW  n={len(vals)}{rel}{extra}")
            print(f"{'':<14} top POWER: {ptxt}")
            summary[f"{w}/{c}"] = {"mean_mw": m, "ci95_mw": h, "n": len(vals), "runs": vals}
    if args.json:
        args.json.write_text(json.dumps({"summary": summary, "runs": runs}, indent=1))


if __name__ == "__main__":
    main()
