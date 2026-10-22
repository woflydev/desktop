// Shared by the bench pages: posts metrics to the local server, which
// stores them per run. Identical in every engine.
const params = new URLSearchParams(location.search);
const RUN = params.get("run") || "adhoc";

function report(kind, data) {
  const body = JSON.stringify({ run: RUN, kind, t: Date.now(), ua: navigator.userAgent, vis: document.visibilityState, dpr: devicePixelRatio, ...data });
  fetch("/report", { method: "POST", body, keepalive: true }).catch(() => {});
}

// Frame-interval stats from requestAnimationFrame over a window.
function frameStats(intervals) {
  if (!intervals.length) return { frames: 0 };
  const s = [...intervals].sort((a, b) => a - b);
  const med = s[Math.floor(s.length / 2)];
  const long = intervals.filter(d => d > med * 1.5).length;
  const sum = intervals.reduce((a, b) => a + b, 0);
  return {
    frames: intervals.length,
    meanMs: sum / intervals.length,
    medianMs: med,
    p99Ms: s[Math.floor(s.length * 0.99)],
    longFrames: long,
    longFrameRatio: long / intervals.length,
  };
}
