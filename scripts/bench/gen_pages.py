"""Writes the generated bench pages (idle, scroll) into a www directory.

Usage: python3 gen_pages.py <www-dir>
"""

import random
import sys
from pathlib import Path

WORDS = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor "
    "incididunt ut labore et dolore magna aliqua enim ad minim veniam quis nostrud "
    "exercitation ullamco laboris nisi aliquip ex ea commodo consequat duis aute irure "
    "in reprehenderit voluptate velit esse cillum fugiat nulla pariatur excepteur sint "
    "occaecat cupidatat non proident"
).split()

STYLE = """<style>
body { margin: 0; font: 17px/1.6 Georgia, serif; color: #222; background: #fafafa; }
main { max-width: 720px; margin: 0 auto; padding: 40px 24px; }
h1, h2 { font-family: -apple-system, Helvetica, sans-serif; }
.fig { height: 280px; border-radius: 8px; margin: 24px 0; }
</style>"""

SCROLL_SCRIPT = """<script>
// Time-based scroll at a fixed speed so every engine covers the same distance
// per second whatever its frame rate; bounces at the ends.
const SPEED = 600; // CSS px per second
let dir = 1, prev = null, intervals = [];
function step(now) {
  if (prev !== null) {
    const dt = now - prev;
    intervals.push(dt);
    const max = document.documentElement.scrollHeight - innerHeight;
    let y = scrollY + dir * SPEED * dt / 1000;
    if (y >= max) { y = max; dir = -1; } else if (y <= 0) { y = 0; dir = 1; }
    scrollTo(0, y);
  }
  prev = now;
  requestAnimationFrame(step);
}
requestAnimationFrame(step);
setInterval(() => { report("scroll", frameStats(intervals)); intervals = []; }, 30000);
</script>"""

IDLE_SCRIPT = """<script>setInterval(() => report("idle", {}), 60000);</script>"""


def paragraph(rng):
    return " ".join(rng.choice(WORDS) for _ in range(rng.randint(60, 120))).capitalize() + "."


def article(rng, count):
    out = []
    for i in range(count):
        if i % 6 == 0:
            out.append(f"<h2>Section {i // 6 + 1}</h2>")
        if i % 9 == 4:
            hue = (i * 37) % 360
            out.append(
                f'<div class="fig" style="background:linear-gradient(135deg,'
                f'hsl({hue} 60% 55%),hsl({(hue + 60) % 360} 60% 35%))"></div>'
            )
        out.append(f"<p>{paragraph(rng)}</p>")
    return "\n".join(out)


def page(title, heading, body, script):
    return (
        f'<!doctype html><meta charset="utf-8"><title>{title}</title>{STYLE}\n'
        f"<main><h1>{heading}</h1>{body}</main>\n"
        f'<script src="bench.js"></script>\n{script}\n'
    )


def main():
    www = Path(sys.argv[1])
    rng = random.Random(42)
    (www / "idle.html").write_text(page("bench idle", "Static article", article(rng, 30), IDLE_SCRIPT))
    (www / "scroll.html").write_text(
        page("bench scroll", "Scrolling article", article(rng, 400), SCROLL_SCRIPT)
    )


if __name__ == "__main__":
    main()
