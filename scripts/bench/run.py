"""Unattended power/smoothness bench for Zen vs Safari vs a bare WKWebView.

Primary metric: whole-system power from the battery gauge
(AppleSmartBattery PowerTelemetryData: AccumulatedSystemLoad /
SystemLoadAccumulatorCount, published about once a minute as a 1 Hz average).
No sudo needed. Per-process breakdown comes from `top`'s POWER column.

  python3 run.py prepare                 # build control app, pages, profiles, warm-up
  python3 run.py run --pass p1 [--reps 3] [--configs a,b] [--workloads x,y]
  python3 run.py smoke                   # short runs of every config, to check plumbing

Raw data goes to <bench>/results/<pass>/, one directory per run.
"""

import argparse
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = Path(os.environ.get("ZEN_BENCH_DIR", HERE.parents[2] / "bench"))
WWW = BENCH / "www"
APPS = BENCH / "apps"
PROFILES = BENCH / "profiles"
RESULTS = BENCH / "results"
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"

# Window geometry in points, top-left origin.
WIN_X, WIN_Y, WIN_W, WIN_H = 120, 80, 1440, 900

WORKLOADS = {
    "video-h264": "video.html?codec=h264",
    "video-vp9": "video.html?codec=vp9",
    "video-av1": "video.html?codec=av1",
    "idle": "idle.html",
    "scroll": "scroll.html",
}

COMMON_PREFS = {
    "zen.welcome-screen.seen": True,
    "browser.shell.checkDefaultBrowser": False,
    "browser.aboutwelcome.enabled": False,
    "browser.sessionstore.resume_from_crash": False,
    "browser.startup.page": 0,
    "browser.tabs.warnOnClose": False,
    "browser.warnOnQuit": False,
    "datareporting.policy.dataSubmissionEnabled": False,
    "toolkit.telemetry.enabled": False,
    "app.update.auto": False,
    "media.autoplay.default": 0,
}

LEAN_PREFS = {
    "zen.widget.macos.window-vibrancy": False,
    "zen.view.grey-out-inactive-windows": False,
    "zen.theme.acrylic-urlbar": False,
    "zen.theme.acrylic-sidebar": False,
    "ui.prefersReducedMotion": 1,
}

ZEN_CONFIGS = {
    "zen-release": ("/Applications/Zen.app/Contents/MacOS/zen", {}),
    "zen-dev": (str(APPS / "ZenGecko.app/Contents/MacOS/zen"), {}),
    "zen-dev-lean": (str(APPS / "ZenGecko.app/Contents/MacOS/zen"), LEAN_PREFS),
    "zen-webkit-b": (
        str(APPS / "ZenWebKitB.app/Contents/MacOS/zen"),
        {"zen.webkit.hosts": "127.0.0.1"},
    ),
}
ALL_CONFIGS = ["desktop", "safari", "wk-control", *ZEN_CONFIGS]

# Process names that belong to each config, for the top breakdown.
PROC_GROUPS = {
    "zen": re.compile(r"^(zen|plugin-container|Zen.*)$"),
    "safari": re.compile(r"^Safari$"),
    "wk-control": re.compile(r"^wk-control$"),
    "webkit": re.compile(r"^com\.apple\.WebKit\."),
    "windowserver": re.compile(r"^WindowServer$"),
}


def log(*args):
    print(time.strftime("%H:%M:%S"), *args, flush=True)


def sh(cmd, check=True, **kw):
    return subprocess.run(cmd, check=check, capture_output=True, text=True, **kw)


# --- system state ---------------------------------------------------------


def battery():
    out = sh(["ioreg", "-rn", "AppleSmartBattery"]).stdout

    def num(key):
        m = re.search(rf'"{key}"\s*=\s*(\d+)', out)
        return int(m.group(1)) if m else None

    def tele(key):
        m = re.search(rf'"{key}"=(\d+)', out)
        return int(m.group(1)) if m else None

    amps = num("InstantAmperage")
    if amps is not None and amps >= 2**63:
        amps -= 2**64
    return {
        "t": time.time(),
        "external": '"ExternalConnected" = Yes' in out,
        "percent": num("CurrentCapacity"),
        "voltage_mv": num("Voltage"),
        "amperage_ma": amps,
        "temperature": num("Temperature"),
        "system_load_mw": tele("SystemLoad"),
        "acc_load": tele("AccumulatedSystemLoad"),
        "acc_count": tele("SystemLoadAccumulatorCount"),
    }


def brightness_millinits():
    out = sh(["ioreg", "-rc", "AppleARMBacklight"], check=False).stdout
    m = re.search(r'"BrightnessMilliNits"=\{[^}]*"value"=(\d+)', out)
    return int(m.group(1)) if m else None


def hid_idle_s():
    out = sh(["ioreg", "-c", "IOHIDSystem", "-d", "4"], check=False).stdout
    m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out)
    return int(m.group(1)) / 1e9 if m else None


def thermal():
    return sh(["pmset", "-g", "therm"], check=False).stdout.strip()


def low_power_mode():
    out = sh(["pmset", "-g"], check=False).stdout
    m = re.search(r"lowpowermode\s+(\d)", out)
    return bool(m and m.group(1) == "1")


def other_browsers():
    """Browser processes not started by the bench (would contaminate runs)."""
    out = sh(["ps", "-axo", "pid=,comm="]).stdout
    found = []
    for line in out.splitlines():
        if str(APPS) in line:
            continue
        if re.search(r"/Zen\.app/Contents/MacOS/zen$|/Safari\.app/Contents/MacOS/Safari$|"
                     r"Google Chrome\.app/Contents/MacOS/Google Chrome$|/firefox$", line.strip()):
            found.append(line.strip())
    return found


# --- config launch/quit -----------------------------------------------------


def write_user_js(profile, prefs):
    lines = []
    for k, v in {**COMMON_PREFS, **prefs}.items():
        lines.append(f"user_pref({json.dumps(k)}, {json.dumps(v)});")
    (profile / "user.js").write_text("\n".join(lines) + "\n")
    xul = {
        "chrome://browser/content/browser.xhtml": {
            "main-window": {
                "screenX": str(WIN_X), "screenY": str(WIN_Y),
                "width": str(WIN_W), "height": str(WIN_H), "sizemode": "normal",
            }
        }
    }
    (profile / "xulstore.json").write_text(json.dumps(xul))


def osa(script):
    return sh(["osascript", "-e", script], check=False)


class Instance:
    def __init__(self, config, url):
        self.config, self.url, self.proc = config, url, None

    def start(self):
        c = self.config
        if c == "desktop":
            return
        if c == "safari":
            sh(["open", "-a", "Safari", self.url])
            time.sleep(4)
            osa(f'tell application "Safari" to set bounds of front window to '
                f"{{{WIN_X}, {WIN_Y}, {WIN_X + WIN_W}, {WIN_Y + WIN_H}}}")
            return
        if c == "wk-control":
            self.proc = subprocess.Popen(
                [str(APPS / "wk-control"), self.url, str(WIN_X), str(WIN_Y), str(WIN_W), str(WIN_H)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return
        binary, _ = ZEN_CONFIGS[c]
        run_profile = PROFILES / f"{c}.run"
        if run_profile.exists():
            shutil.rmtree(run_profile)
        shutil.copytree(PROFILES / f"{c}.warm", run_profile, symlinks=True)
        # Instances are stopped with a signal, so drop session state to make
        # every launch open only the bench URL.
        for name in ("zen-sessions.jsonlz4", "zen-sessions-backup", "sessionstore-backups",
                     "sessionstore.jsonlz4", "sessionCheckpoints.json", "lock", ".parentlock"):
            path = run_profile / name
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists() or path.is_symlink():
                path.unlink()
        self.proc = subprocess.Popen(
            [binary, "-no-remote", "-profile", str(run_profile), self.url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    def describe(self):
        if self.config == "safari":
            r = osa('tell application "Safari" to get {count of windows, count of tabs of front window}')
            return {"safari_windows_tabs": r.stdout.strip()}
        return {}

    def stop(self):
        c = self.config
        if c == "desktop":
            return
        if c == "safari":
            osa('tell application "Safari" to quit')
            for _ in range(30):
                if sh(["pgrep", "-x", "Safari"], check=False).returncode:
                    return
                time.sleep(1)
            sh(["pkill", "-x", "Safari"], check=False)
            return
        if self.proc:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.proc.wait(timeout=10)
        if c in ZEN_CONFIGS:
            sh(["pkill", "-f", str(PROFILES / f"{c}.run")], check=False)
            time.sleep(2)


# --- measurement --------------------------------------------------------------


def measure(run_dir, config, workload, warmup, duration):
    url = f"{BASE}/{WORKLOADS[workload]}" if workload in WORKLOADS else "about:blank"
    url += ("&" if "?" in url else "?") + f"run={run_dir.name}"
    meta = {
        "config": config, "workload": workload, "url": url,
        "warmup_s": warmup, "duration_s": duration,
        "brightness_mnits": brightness_millinits(), "thermal_start": thermal(),
        "low_power_mode": low_power_mode(), "battery_start": battery(),
    }
    inst = Instance(config, url)
    inst.start()
    time.sleep(warmup)
    meta.update(inst.describe())

    top_out = open(run_dir / "top.txt", "w")
    samples = max(2, duration // 10)
    top = subprocess.Popen(
        ["top", "-l", str(samples + 1), "-s", "10", "-o", "power", "-n", "40",
         "-stats", "pid,command,cpu,power"], stdout=top_out, stderr=subprocess.DEVNULL)

    start = time.time()
    meta["measure_start"] = start
    batt = []
    last_count = None
    while time.time() - start < duration:
        b = battery()
        if b["acc_count"] != last_count:
            batt.append(b)
            last_count = b["acc_count"]
        time.sleep(2)
    end = time.time()
    meta["measure_end"] = end
    meta["hid_idle_end_s"] = hid_idle_s()
    top.wait(timeout=60)
    top_out.close()

    inst.stop()
    meta["battery_end"] = battery()
    meta["thermal_end"] = thermal()
    meta["brightness_end_mnits"] = brightness_millinits()
    (run_dir / "battery.jsonl").write_text("\n".join(json.dumps(b) for b in batt) + "\n")
    meta["summary"] = summarize_battery(batt, start)
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=1))
    return meta


def summarize_battery(batt, start):
    """Average power over the gauge's accumulator intervals that lie fully
    inside the measurement window (the first published update can cover time
    before it started)."""
    # batt[0] is the state at the window start; the interval ending at
    # batt[1] began before the window, so full intervals start at batt[1].
    intervals = []
    for prev, cur in zip(batt[1:], batt[2:]):
        dc = cur["acc_count"] - prev["acc_count"]
        if dc > 0:
            intervals.append((cur["acc_load"] - prev["acc_load"]) / dc)
    tot_c = batt[-1]["acc_count"] - batt[1]["acc_count"] if len(batt) > 2 else 0
    tot_l = batt[-1]["acc_load"] - batt[1]["acc_load"] if len(batt) > 2 else 0
    return {
        "intervals_mw": intervals,
        "mean_mw": tot_l / tot_c if tot_c else None,
        "counts": tot_c,
        "external_seen": any(b["external"] for b in batt),
    }


# --- commands -------------------------------------------------------------------


def start_server(results_dir):
    return subprocess.Popen(
        [sys.executable, str(HERE / "server.py"), str(WWW), str(results_dir), str(PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cmd_prepare(args):
    WWW.mkdir(parents=True, exist_ok=True)
    for f in (HERE / "pages").iterdir():
        shutil.copy(f, WWW / f.name)
    sh([sys.executable, str(HERE / "gen_pages.py"), str(WWW)])
    for name in ("bbb-h264.mp4", "bbb-vp9.webm", "bbb-av1.webm"):
        if not (WWW / "media" / name).exists():
            log("missing media", name)
    APPS.mkdir(parents=True, exist_ok=True)
    sh(["swiftc", "-O", str(HERE / "wk-control.swift"), "-o", str(APPS / "wk-control")])
    log("built wk-control")

    PROFILES.mkdir(parents=True, exist_ok=True)
    server = start_server(RESULTS / "prepare")
    try:
        for c, (binary, prefs) in ZEN_CONFIGS.items():
            if args.configs and c not in args.configs:
                continue
            if not Path(binary).exists():
                log("skip", c, "(no binary)", binary)
                continue
            warm = PROFILES / f"{c}.warm"
            if warm.exists():
                shutil.rmtree(warm)
            warm.mkdir()
            write_user_js(warm, prefs)
            # Two launches: the first does one-time profile creation work.
            for i in range(2):
                log("warming", c, i)
                p = subprocess.Popen(
                    [binary, "-no-remote", "-profile", str(warm), f"{BASE}/idle.html?run=warm"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                time.sleep(args.warm_s)
                os.killpg(p.pid, signal.SIGTERM)
                try:
                    p.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
                sh(["pkill", "-f", str(warm)], check=False)
                time.sleep(3)
            for lock in ("lock", ".parentlock"):
                (warm / lock).unlink(missing_ok=True)
    finally:
        server.terminate()
    log("prepared")


def preflight(strict):
    problems = []
    b = battery()
    if b["external"]:
        problems.append("on external power")
    if low_power_mode():
        problems.append("Low Power Mode is on")
    others = other_browsers()
    if others:
        problems.append("other browsers running: " + "; ".join(others))
    if problems:
        log("PREFLIGHT:", *problems)
        if strict:
            sys.exit(1)
    return problems


def wait_ready(args, first):
    """Block until the machine is fit to measure: on battery, no other
    browsers, and nobody has touched it for a while."""
    announced = None
    while True:
        b = battery()
        reasons = []
        if b["external"]:
            reasons.append("on external power")
        if first and b["percent"] is not None and b["percent"] < args.start_battery:
            reasons.append(f"battery {b['percent']}% < {args.start_battery}% to start")
        others = other_browsers()
        if others:
            reasons.append("other browsers running: " + "; ".join(others))
        idle = hid_idle_s() or 0
        if idle < args.idle_before:
            reasons.append(f"user input {idle:.0f} s ago")
        if not reasons:
            return
        msg = ", ".join(r.split(" ago")[0] if r.startswith("user input") else r for r in reasons)
        if msg != announced:
            log("waiting:", ", ".join(reasons))
            announced = msg
        time.sleep(15)


def cmd_run(args, smoke=False):
    configs = args.configs or ALL_CONFIGS
    workloads = args.workloads or ["video-vp9", "idle"]
    out = RESULTS / args.pass_name
    out.mkdir(parents=True, exist_ok=True)
    if smoke:
        preflight(strict=False)

    plan = []
    rng = random.Random(args.seed)
    for rep in range(args.reps):
        block = []
        for c in configs:
            for w in (["none"] if c == "desktop" else workloads):
                block.append((c, w, rep))
        rng.shuffle(block)
        plan += block
    per_run = args.warmup + args.duration + args.cooldown + 25
    log(f"{len(plan)} runs, about {len(plan) * per_run / 3600:.1f} h; results in {out}")
    (out / "plan.json").write_text(json.dumps(plan))

    done = {(m["config"], m["workload"], m.get("rep")) for m in
            (json.loads(f.read_text()) for f in out.glob("*/meta.json")) if not m.get("disturbed")}
    queue = [(c, w, rep, 0) for c, w, rep in plan if (c, w, rep) not in done]
    seq = len(list(out.glob("*/meta.json")))

    server = start_server(out)
    caffeinate = subprocess.Popen(["caffeinate", "-d", "-i", "-m"])
    first = True
    try:
        while queue:
            c, w, rep, tries = queue.pop(0)
            if not smoke:
                wait_ready(args, first)
            first = False
            b = battery()
            if b["percent"] is not None and b["percent"] < args.min_battery and not smoke:
                log(f"battery {b['percent']}% < {args.min_battery}%; stopping (re-run to resume)")
                break
            run_dir = out / f"{seq:03d}-{c}-{w}-r{rep}"
            seq += 1
            run_dir.mkdir(exist_ok=True)
            log(f"[{len(plan) - len(queue)}/{len(plan)}] {c} {w} rep{rep} (battery {b['percent']}%)")
            meta = measure(run_dir, c, w, args.warmup, args.duration)
            meta["rep"] = rep
            idle = meta["hid_idle_end_s"] or 0
            meta["disturbed"] = (idle < args.warmup + args.duration
                                 or meta["summary"]["external_seen"]) and not smoke
            (run_dir / "meta.json").write_text(json.dumps(meta, indent=1))
            s = meta["summary"]
            mean = f"{s['mean_mw']:.0f} mW" if s["mean_mw"] else "n/a"
            log(f"    -> {mean} over {s['counts']} s, hid idle {idle:.0f} s"
                + (" DISTURBED" if meta["disturbed"] else ""))
            if meta["disturbed"] and tries < 2:
                queue.append((c, w, rep, tries + 1))
            time.sleep(args.cooldown)
    finally:
        caffeinate.terminate()
        server.terminate()
    log("done")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--configs", type=lambda s: s.split(","))
    p.add_argument("--warm-s", type=int, default=60)
    for name in ("run", "smoke"):
        r = sub.add_parser(name)
        r.add_argument("--pass", dest="pass_name", default="smoke" if name == "smoke" else "p1")
        r.add_argument("--configs", type=lambda s: s.split(","))
        r.add_argument("--workloads", type=lambda s: s.split(","))
        r.add_argument("--reps", type=int, default=1 if name == "smoke" else 3)
        r.add_argument("--warmup", type=int, default=20 if name == "smoke" else 75)
        r.add_argument("--duration", type=int, default=70 if name == "smoke" else 300)
        r.add_argument("--cooldown", type=int, default=5 if name == "smoke" else 45)
        r.add_argument("--min-battery", type=int, default=15)
        r.add_argument("--seed", type=int, default=1)
        r.add_argument("--start-battery", type=int, default=95)
        r.add_argument("--idle-before", type=int, default=180)
    args = ap.parse_args()
    if args.cmd == "prepare":
        cmd_prepare(args)
    else:
        cmd_run(args, smoke=args.cmd == "smoke")


if __name__ == "__main__":
    main()
