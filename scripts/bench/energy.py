"""Zen-only energy bench that tolerates the machine being in use.

Variants run at the same time, side by side, so whatever else happens on the
machine affects all of them alike; window positions rotate every round.
For each variant's process tree it measures, from the kernel's per-process
accounting (no sudo):
  - CPU energy (rusage ri_energy_nj), CPU time, wakeups, instructions
  - GPU time (AGXDeviceUserClient AppUsage accumulatedGPUTime)
WindowServer's share (e.g. vibrancy compositing) is not attributable here;
use run.py on an idle machine for that.

  python3 energy.py --variants base,lean --workload video-vp9 --rounds 6
  python3 energy.py --list
"""

import argparse
import ctypes
import json
import math
import os
import re
import shutil
import signal
import statistics
import subprocess
import time
from pathlib import Path

import run as bench

ENERGY_PREFS = {"zen.testing.ignore-window-occlusion": True}

VARIANTS = {
    "base": {},
    "base-copy": {},
    "no-vibrancy": {"zen.widget.macos.window-vibrancy": False},
    "no-greyout": {"zen.view.grey-out-inactive-windows": False},
    "reduced-motion": {"ui.prefersReducedMotion": 1},
    "lean": bench.LEAN_PREFS,
    "fps60": {"layout.frame_rate": 60},
    "efficiency": {"zen.efficiency-mode.enabled": True},
    # Efficiency mode with tab sleep compressed into bench time.
    "efficiency-fast": {
        "zen.efficiency-mode.enabled": True,
        "zen.efficiency-mode.tab-sleep.timeout-minutes": 1,
        "zen.efficiency-mode.tab-sleep.check-interval-seconds": 15,
    },
}

# Workloads that open extra background tabs: name -> (front page, tab page, count).
MULTI_TAB = {
    "busy-tabs": ("idle.html", "busy.html", 16),
    "audio-bg": ("idle.html", "audio.html", 1),
}

SCREEN_W, TOP, GAP = 1728, 40, 8
ROWS_H = 1117 - TOP


class RUsage(ctypes.Structure):
    _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [(n, ctypes.c_uint64) for n in (
        "user_time", "system_time", "pkg_idle_wkups", "interrupt_wkups", "pageins",
        "wired_size", "resident_size", "phys_footprint", "proc_start_abstime",
        "proc_exit_abstime", "child_user_time", "child_system_time", "child_pkg_idle_wkups",
        "child_interrupt_wkups", "child_pageins", "child_elapsed_abstime", "diskio_bytesread",
        "diskio_byteswritten", "cpu_time_qos_default", "cpu_time_qos_maintenance",
        "cpu_time_qos_background", "cpu_time_qos_utility", "cpu_time_qos_legacy",
        "cpu_time_qos_user_initiated", "cpu_time_qos_user_interactive", "billed_system_time",
        "serviced_system_time", "logical_writes", "lifetime_max_phys_footprint", "instructions",
        "cycles", "billed_energy", "serviced_energy", "interval_max_phys_footprint",
        "runnable_time", "flags", "user_ptime", "system_ptime", "pinstructions", "pcycles",
        "energy_nj", "penergy_nj", "secure_time_in_system", "secure_ptime_in_system",
        "neural_footprint", "lifetime_max_neural_footprint", "interval_max_neural_footprint",
    )] + [("reserved", ctypes.c_uint64 * 9)]


_libproc = ctypes.CDLL("/usr/lib/libproc.dylib")


class _Timebase(ctypes.Structure):
    _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]


_tb = _Timebase()
ctypes.CDLL("/usr/lib/libSystem.dylib").mach_timebase_info(ctypes.byref(_tb))
TICK_NS = _tb.numer / _tb.denom


def rusage(pid):
    r = RUsage()
    if _libproc.proc_pid_rusage(pid, 6, ctypes.byref(r)) != 0:
        return None
    return {
        "energy_nj": r.energy_nj,
        "cpu_ns": (r.user_time + r.system_time) * TICK_NS,
        "wakeups": r.interrupt_wkups + r.pkg_idle_wkups,
        "instructions": r.instructions,
        "footprint": r.phys_footprint,
    }


def gpu_times():
    """pid -> accumulated GPU ns, over all of its Metal user clients."""
    out = bench.sh(["ioreg", "-r", "-c", "AGXDeviceUserClient", "-l"], check=False).stdout
    times = {}
    for entry in re.split(r"\n(?=\+-o )", out):
        m = re.search(r'"IOUserClientCreator" = "pid (\d+),', entry)
        if not m:
            continue
        pid = int(m.group(1))
        total = sum(int(v) for v in re.findall(r'"accumulatedGPUTime"=(\d+)', entry))
        times[pid] = times.get(pid, 0) + total
    return times


def descendants(root):
    out = bench.sh(["ps", "-axo", "pid=,ppid="]).stdout
    children = {}
    for line in out.split("\n"):
        parts = line.split()
        if len(parts) == 2:
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    seen, stack = [], [root]
    while stack:
        p = stack.pop()
        seen.append(p)
        stack += children.get(p, [])
    return seen


class Tree:
    """Tracks counters for a process tree; processes that appear later count
    from zero, ones that exit keep their last sample."""

    KEYS = ("energy_nj", "cpu_ns", "wakeups", "instructions", "gpu_ns")

    def __init__(self, root):
        self.root, self.first, self.last = root, {}, {}

    def sample(self, gpu, baseline=False):
        for pid in descendants(self.root):
            r = rusage(pid)
            if r is None:
                continue
            r["gpu_ns"] = gpu.get(pid, 0)
            if pid not in self.first:
                self.first[pid] = r if baseline else {k: 0 for k in r}
            self.last[pid] = r

    def totals(self):
        return {k: sum(self.last[p][k] - self.first[p][k] for p in self.last) for k in self.KEYS}

    def footprint(self):
        return sum(self.last[p]["footprint"] for p in self.last)


def layout(n, slot):
    cols = 2 if n > 1 else 1
    rows = math.ceil(n / cols)
    w = (SCREEN_W - GAP * (cols + 1)) // cols
    h = min(900, (ROWS_H - GAP * (rows + 1)) // rows)
    return GAP + (slot % cols) * (w + GAP), TOP + (slot // cols) * (h + GAP), w, h


def launch(variant, slot, n, url, binary):
    profile = bench.PROFILES / f"energy-{variant}.run"
    if profile.exists():
        shutil.rmtree(profile)
    shutil.copytree(bench.PROFILES / "zen-dev.warm", profile, symlinks=True)
    for name in ("zen-sessions.jsonlz4", "zen-sessions-backup", "sessionstore-backups",
                 "sessionCheckpoints.json", "lock", ".parentlock"):
        p = profile / name
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    x, y, w, h = layout(n, slot)
    saved = (bench.WIN_X, bench.WIN_Y, bench.WIN_W, bench.WIN_H)
    bench.WIN_X, bench.WIN_Y, bench.WIN_W, bench.WIN_H = x, y, w, h
    bench.write_user_js(profile, {**ENERGY_PREFS, **VARIANTS[variant]})
    bench.WIN_X, bench.WIN_Y, bench.WIN_W, bench.WIN_H = saved
    # `open -g` launches without taking focus from whatever the user is doing.
    app = Path(binary).parents[2]
    urls = url if isinstance(url, list) else [url]
    bench.sh(["open", "-n", "-g", "-a", str(app), "--args", "-no-remote", "-profile",
              str(profile), *urls])
    for _ in range(50):
        r = bench.sh(["pgrep", "-f", f"MacOS/zen -no-remote -profile {profile}"], check=False)
        if r.stdout.strip():
            return Pid(int(r.stdout.split()[0])), profile
        time.sleep(0.2)
    raise RuntimeError(f"{variant} did not start")


class Pid:
    def __init__(self, pid):
        self.pid = pid

    def wait(self, timeout):
        end = time.time() + timeout
        while time.time() < end:
            if bench.sh(["kill", "-0", str(self.pid)], check=False).returncode:
                return
            time.sleep(0.5)
        raise subprocess.TimeoutExpired(str(self.pid), timeout)


def stop(proc, profile):
    try:
        os.kill(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        bench.sh(["pkill", "-9", "-f", str(profile)], check=False)
    bench.sh(["pkill", "-f", str(profile)], check=False)


def ci95(values):
    if len(values) < 2:
        return float("nan")
    t = {1: 12.71, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306,
         9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179}.get(len(values) - 1, 2.0)
    return t * statistics.stdev(values) / math.sqrt(len(values))


def report(rounds, variants, base):
    print(f"\n{'variant':<16}{'CPU energy mW':>15}{'CPU %':>8}{'GPU %':>8}{'wakeups/s':>11}"
          f"{'Δenergy vs ' + base:>22}")
    per = {v: [r["variants"][v] for r in rounds if v in r["variants"]] for v in variants}
    for v in variants:
        rs = per[v]
        if not rs:
            continue
        mw = [r["energy_nj"] / 1e6 / r["seconds"] for r in rs]
        cpu = [100 * r["cpu_ns"] / 1e9 / r["seconds"] for r in rs]
        gpu = [100 * r["gpu_ns"] / 1e9 / r["seconds"] for r in rs]
        wk = [r["wakeups"] / r["seconds"] for r in rs]
        rel = ""
        if v != base and per.get(base):
            diffs = [100 * (a["energy_nj"] / b["energy_nj"] - 1) for a, b in zip(rs, per[base])]
            rel = f"{statistics.mean(diffs):+.1f}% ± {ci95(diffs):.1f}"
        print(f"{v:<16}{statistics.mean(mw):9.0f} ±{ci95(mw):4.0f}{statistics.mean(cpu):8.1f}"
              f"{statistics.mean(gpu):8.1f}{statistics.mean(wk):11.0f}{rel:>22}")
    print(f"(n={len(rounds)} paired rounds; ± is the 95% CI)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", type=lambda s: s.split(","), default=["base", "lean"])
    ap.add_argument("--workload", default="video-vp9", choices=[*bench.WORKLOADS, *MULTI_TAB])
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--warmup", type=int, default=45)
    ap.add_argument("--duration", type=int, default=180)
    ap.add_argument("--binary", default=str(bench.APPS / "ZenGecko.app/Contents/MacOS/zen"))
    ap.add_argument("--name")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--report", type=Path, help="only print the report for a results file")
    args = ap.parse_args()
    if args.list:
        for k, v in VARIANTS.items():
            print(f"{k:<16}{v}")
        return
    if args.report:
        data = json.loads(args.report.read_text())
        report(data["rounds"], data["variants"], data["variants"][0])
        return

    name = args.name or f"{args.workload}-{'-'.join(args.variants)}-{time.strftime('%m%d-%H%M')}"
    out = bench.RESULTS / "energy" / name
    out.mkdir(parents=True, exist_ok=True)
    server = bench.start_server(out)
    rounds = []
    try:
        for rnd in range(args.rounds):
            order = args.variants[rnd % len(args.variants):] + args.variants[:rnd % len(args.variants)]
            bench.log(f"round {rnd + 1}/{args.rounds}: slots {order}")
            insts = {}
            for slot, v in enumerate(order):
                if args.workload in MULTI_TAB:
                    front, page, count = MULTI_TAB[args.workload]
                    url = [f"{bench.BASE}/{front}?run={rnd:02d}-{v}"] + [
                        f"{bench.BASE}/{page}?run={rnd:02d}-{v}&tab={i}" for i in range(count)]
                else:
                    url = f"{bench.BASE}/{bench.WORKLOADS[args.workload]}"
                    url += ("&" if "?" in url else "?") + f"run={rnd:02d}-{v}"
                insts[v] = launch(v, slot, len(order), url, args.binary)
            time.sleep(args.warmup)
            trees = {v: Tree(p.pid) for v, (p, _) in insts.items()}
            gpu = gpu_times()
            for t in trees.values():
                t.sample(gpu, baseline=True)
            start = time.time()
            while time.time() - start < args.duration:
                time.sleep(5)
                gpu = gpu_times()
                for t in trees.values():
                    t.sample(gpu)
            seconds = time.time() - start
            result = {"round": rnd, "order": order, "seconds": seconds, "variants": {}}
            for v, t in trees.items():
                result["variants"][v] = {**t.totals(), "seconds": seconds,
                                         "footprint": t.footprint(), "procs": len(t.last)}
            for v, (p, prof) in insts.items():
                stop(p, prof)
            rounds.append(result)
            (out / "rounds.json").write_text(json.dumps(
                {"variants": args.variants, "workload": args.workload, "rounds": rounds}, indent=1))
            line = "  ".join(f"{v} {r['energy_nj'] / 1e6 / seconds:.0f} mW"
                             for v, r in result["variants"].items())
            bench.log("   ", line)
            time.sleep(5)
    finally:
        server.terminate()
    report(rounds, args.variants, args.variants[0])
    print(f"results: {out}")


if __name__ == "__main__":
    main()
