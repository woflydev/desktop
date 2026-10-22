#!/usr/bin/env bash
# Average whole-SoC power over a fixed window, for comparing browsers.
# Usage: sudo scripts/measure-power.sh <label> [seconds=120]
#
# Use ONE watch page (not the YouTube home page: hover previews autoplay and
# make the load erratic) and do not touch the mouse during the run.
# Run each scenario with everything else quit, same screen brightness, on
# battery, same video at the same quality, window in the same place. Wait ~30s
# after starting playback so startup work settles before measuring.
set -euo pipefail

label="${1:?label, e.g. zen-gecko | zen-webkit | safari}"
secs="${2:-120}"
interval_ms=5000
samples=$((secs * 1000 / interval_ms))
out="/tmp/power-${label}-$(date +%s).txt"

if [[ $EUID -ne 0 ]]; then
  echo "needs sudo (powermetrics)" >&2
  exit 1
fi

powermetrics --samplers cpu_power,tasks --show-process-energy \
  -i "$interval_ms" -n "$samples" >"$out"

echo "raw: $out"
# Skip the first 2 samples (startup noise, and the first covers time before
# measurement began). Report mean and median so a stall or spike is visible.
cpugpu=$(awk '
  /^Combined Power \(CPU \+ GPU \+ ANE\):/ { n++ }
  /^CPU Power:/ { if (n >= 2) cpu += $3; nc++ }
  /^GPU Power:/ { if (n >= 2) gpu += $3 }
  END { m = n - 2; if (m < 1) m = 1; printf "%.0f %.0f", cpu / m, gpu / m }' "$out")
grep '^Combined Power' "$out" | tail -n +3 | awk '{print $(NF-1)}' | sort -n |
  awk -v label="$label" -v cg="$cpugpu" '
    { v[NR] = $1; sum += $1 }
    END {
      if (!NR) { print "no usable samples; inspect raw file"; exit }
      med = (NR % 2) ? v[(NR+1)/2] : (v[NR/2] + v[NR/2+1]) / 2
      split(cg, a, " ")
      printf "%-12s mean %.0f mW  median %.0f mW  min %.0f  max %.0f  cpu %s  gpu %s  (%d samples)\n",
        label, sum/NR, med, v[1], v[NR], a[1], a[2], NR
    }'

echo "--- energy impact by process (mean per sample, excl. powermetrics itself) ---"
awk '
  /^Name +ID +CPU ms\/s/ { sample++; intask = 1; next }
  /^$/ { intask = 0 }
  intask && sample > 2 && NF >= 9 {
    name = $1; for (i = 2; i <= NF - 8; i++) name = name " " $i
    if (name ~ /^(powermetrics|systemstats|ALL_TASKS)/) next
    imp[name] += $NF
  }
  END { n = sample - 2; if (n < 1) n = 1; for (k in imp) printf "%8.1f  %s\n", imp[k] / n, k }
' "$out" | sort -rn | head -12
