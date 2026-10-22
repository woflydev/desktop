# Handoff: WebKit (Safari engine) inside Zen — battery/efficiency effort

Status: **prototype only, uncommitted, not daily-drivable.** Written 2026-10-22.
Repo: `/Users/woffles/Desktop/zen-battery/desktop` (fork of zen-browser/desktop, branch `dev`, base commit `541ee5519`).
Platform: macOS (Apple Silicon), Gecko-based Zen; engine tree in `engine/`.

---

## 0. Instructions to the incoming agent (read first)

You are taking over with fresh context. Do these in order and do not skip ahead to code.

1. **Independent assessment first.** Before reading the prototype as "the plan", form your
   own view of how to bring Safari's native engine (WebKit via `WKWebView`) into Zen to gain
   battery life and smoothness. Cover at least: feasibility, architecture options (see §7 for
   the ones already considered, but generate your own), what Zen/Gecko features each option
   puts at risk, expected efficiency gain vs. cost, and what you'd need to measure to decide.
   Treat the prototype's architecture (WebKit view *under* a transparent Gecko window) as one
   candidate, **not** a decision. Be willing to conclude it is the wrong approach.
2. **Then write a comprehensive plan before implementing anything.** The plan must cover:
   - every UX/UI surface that touches page content (see §8 checklist) and how each is handled;
   - a regression strategy whose goal is that **all existing functionality stays unchanged**
     for anything not explicitly moved to WebKit: feature flag/pref defaulting to off, Gecko
     path untouched, existing test suites (`npm test`, `src/zen/tests/`) still green, and a
     before/after manual parity checklist;
   - a measurement plan with valid baselines (see §6) and explicit pass/fail thresholds for
     power, so "significantly improved power efficiency" is demonstrated, not assumed;
   - milestones ordered so each one is shippable behind the flag and reversible.
3. **Read §5 (status table) and the "common root cause" paragraph.** Several fixes are built but not yet
   confirmed on screen; re-verify them with the user before building on top. Decide, as part of your assessment,
   whether the many `ChildView`-special-case workarounds in the bridge indicate that this architecture is the right
   one to continue with.
4. Ask the user (woffles) before anything outward-facing or hard to reverse (commits, pushes,
   publishing). Nothing has been committed. The user tests visually; you cannot (see §9).

---

## 1. Goal

Make Zen on macOS as battery-efficient and smooth as Safari by rendering web content with the
system WebKit, while keeping Zen's UI (Gecko/XUL chrome: vertical tabs, spaces, split view,
glance, compact mode, boosts, etc.) intact. No functional regressions for what Zen does today.

## 2. What was done

### 2.1 Static battery review of Zen (before any prototype)
Ranked likely costs in the Gecko chrome (not measured, from code reading):
1. Window vibrancy: `zen.widget.macos.window-vibrancy` default true
   ([prefs/zen/macos.yaml](../prefs/zen/macos.yaml)); whole window wrapped in an
   `NSVisualEffectView` ([nsCocoaWindow-mm.patch](../src/widget/cocoa/nsCocoaWindow-mm.patch));
   `zen.view.grey-out-inactive-windows` forces the blur to stay active when off
   ([VibrancyManager-mm.patch](../src/widget/cocoa/VibrancyManager-mm.patch)).
2. CSS `backdrop-filter`/blur: compact sidebar `blur(42px)`
   ([sidebar.inc.css:281](../src/zen/compact-mode/sidebar.inc.css)), omnibox
   ([zen-omnibox.css:291](../src/zen/common/styles/zen-omnibox.css)), others in
   `zen-single-components.css`, `vertical-tabs.css`, boosts. The sidebar one is gated by
   `zen.theme.acrylic-sidebar` / `prefers-reduced-transparency` — check its default in
   [prefs/zen/theme.yaml](../prefs/zen/theme.yaml).
3. Infinite CSS animations in chrome (text-gradient 2s/5s, progress pulses, media marquee).
4. Global `NSEvent` mouse monitors for compact mode
   ([ZenMouseTrackerCocoa.mm](../src/zen/compact-mode/ZenMouseTrackerCocoa.mm)).
5. Media controller 1s `setInterval` ([ZenMediaController.sys.mjs:273](../src/zen/media/ZenMediaController.sys.mjs)).
6. `browser.sessionstore.interval`, no automatic background-tab discarding, grain overlay layer.
Already good: ThinLTO + PGO, `-mcpu=apple-m1`, `gfx.webrender.compositor` pinned on for macOS.
Also: existing perf tests in `src/zen/tests/performance/` (vsync, opaque-region, etc.).

### 2.2 Prototype: WebKit tabs for selected hosts
Behavior: when a tab starts loading a host in pref `zen.webkit.hosts` (comma list, suffix
match, default `youtube.com,netflix.com,twitch.tv`), Gecko's load is stopped and a native
`WKWebView` loads the URL instead. Console helpers: `gZenWebKit.open(url)`, `gZenWebKit.close()`.

Files (all new unless noted):
- `src/zen/webkit/nsIZenWebKitView.idl` — XPCOM interface: `create/setFrame/setVisible/setPassThrough/setPageFocused/destroy/getUrl/getTitle`.
- `src/zen/webkit/nsZenWebKitView.{h,mm}` — XPCOM impl. **Includes Gecko headers only; no WebKit.**
- `src/zen/webkit/ZenWebKitBridge.{h,mm}` — plain-C bridge. **Includes WebKit, no Gecko headers.**
- `src/zen/webkit/components.conf`, `moz.build`, `jar.inc.mn` — registration/build.
- `src/zen/webkit/ZenWebKitPrototype.js` — chrome-side logic (progress listener, tab events, hole CSS, passthrough policy).
- Edited: `src/zen/moz.build` (adds `webkit` dir, cocoa only), `src/browser/base/content/zen-assets.jar.inc.mn`
  (include), `src/zen/common/ZenPreloadedScripts.js` (loadSubScript).
- `scripts/measure-power.sh` — `powermetrics` wrapper (see §6).
- `engine/zen/webkit/*` — manual symlinks into `src/` (needed because `engine/zen/<dir>` holds per-file symlinks; `npm run import` would also create them).

### 2.3 Architecture of the prototype ("underlay + hole")
- `WKWebView` is added to the window as a sibling **directly beneath** Gecko's `ChildView`
  (found by class name `ChildView`, same superview = the `ZenWindowMaterialView` wrapper).
- Gecko is made transparent over the page area: `:root[zen-webkit-active]` CSS makes
  `#zen-main-app-wrapper`, `#tabbrowser-tabbox`, `.browserSidebarContainer/.browserContainer/.browserStack`
  transparent, hides the selected Gecko `<browser>` (`visibility:hidden`), and a rounded-rect
  `clip-path: path(evenodd, …)` is cut out of `#zen-browser-background` and `#zen-toolbar-background`
  following `.browserSidebarContainer`'s rect/radius. Result: Gecko UI (urlbar, glance…) draws over WebKit.
- Frame sync: `ResizeObserver` on the browser container + window `resize` + capture-phase `transitionend`.
- **Input** (important, see §4): Gecko's `ChildView` is on top and receives all clicks/scrolls.
  `ZenWebKitBridge.mm` swizzles `ChildView` mouse/gesture handlers (`mouseDown:`, `mouseUp:`,
  `mouseDragged:`, right/other variants, `scrollWheel:`, `magnifyWithEvent:`, `smartMagnifyWithEvent:`,
  `rotateWithEvent:`, `swipeWithEvent:`, begin/endGesture, `pressureChangeWithEvent:`) to forward the event
  to the `WKWebView` when the pointer is over a visible WebKit view and passthrough is on.
  A sticky target is kept for a press→drag→release sequence. `ChildView hitTest:` is also swizzled to return
  nil over the view (this only affects Gecko's own hover tracker — see §4).
- Press/release are delivered to **both** Gecko (original handler: rolls up popups, blurs the urlbar, lets chrome
  see a real click) and then WebKit; drags, scrolls and gestures go to WebKit only.
- **Cursor**: `ChildView -resetCursorRects` is swizzled to carve the WebKit frame(s) out of Gecko's full-window
  cursor rect (cursor rects are invalidated whenever a view moves/shows/hides or passthrough flips). WebKit then
  drives the cursor itself (`WebPageProxy::setCursor` → `-[NSCursor set]`) and re-asserts it after AppKit resets.
- **Keyboard**: `WKWebView keyDown:/keyUp:/flagsChanged:` are swizzled. Key events with Cmd or Ctrl held are routed
  to Gecko's `ChildView` (so Zen/Firefox shortcuts like Cmd+L, Cmd+T, Ctrl+Tab work), except Cmd+C/X/V/A/Z/Y which
  stay with the page; `flagsChanged:` goes to both; a matching `keyUp` follows its `keyDown`. Plain typing goes to
  the page.
- **Focus ownership** (`setPageFocused`, new IDL method): the page owns the keyboard by default. JS flips it to
  Gecko when any editable chrome element gets `focusin` (urlbar, find bar, …) and back on `focusout`; native moves
  `firstResponder` between `WKWebView` and `ChildView` accordingly. Showing a WebKit tab only takes first
  responder if the page owns the keyboard.
- **Ctrl+Tab / tab-switching keys**: two separate AppKit behaviours had to be handled.
  1. Gecko's `ChildView` implements the private `-_wantsKeyDownForEvent:` returning YES (source comment: "this fixes
     ctrl-tab"); without it AppKit consumes Ctrl+Tab for the key-view loop and sends no `keyDown:`. `WKWebView` gets the
     same method.
  2. With the `WKWebView` as first responder, WebKit's `-performKeyEquivalent:` claims Ctrl-only key equivalents
     (apparently to handle them asynchronously) and `keyDown:` is never delivered, so the `keyDown:` router never saw
     Ctrl+Tab. `-performKeyEquivalent:` is therefore swizzled: Ctrl (no Cmd) + Tab / PageUp / PageDown is sent
     straight to `ChildView keyDown:` and consumed. Verified in-process with synthetic events (single press switches
     tab with the WebKit view focused; it switched nothing before). Other Ctrl combos are left to the page on purpose
     (emacs-style text editing). Cmd combos are not intercepted here so native menu items (Cmd+Q/H/M) keep working.
  Any other key AppKit/WebKit swallow for a non-Gecko first responder is a candidate for the same class of bug.
- **Element fullscreen**: `WKPreferences elementFullscreenEnabled`/`fullScreenEnabled` set via KVC. While
  `WKWebView.fullscreenState` (macOS 13+) is not `NotInFullscreen`, `setFrame` only records the desired frame (WebKit
  moves the view into its own fullscreen window; resetting its frame fought the animation and clamped the size).
  A KVO observer on `fullscreenState` re-inserts the view beneath `ChildView` and restores the frame on exit.
- **Layout tracking**: the script now observes `#tabbrowser-tabpanels`, `#tabbrowser-tabbox` and the *selected tab's*
  `.browserSidebarContainer` (re-observed on every `TabSelect`; the original code observed only the container of the
  tab selected at startup, so other tabs only synced at the end of animations). A short per-frame sync "burst"
  (~800 ms of `requestAnimationFrame`) is triggered by root attribute changes (`zen-sidebar-expanded`,
  `zen-compact-mode`, `zen-compact-animating`, `zen-right-side`, `zen-single-toolbar`) and `transitionrun`. Zen
  animates layout with its JS `motion` library, so there are no CSS transition events to rely on. Syncs are skipped
  when the rect/radius did not change.
- **Compact-mode sidebar flyout** (`:root[zen-webkit-active][zen-compact-mode] .zen-toolbar-background`): given an
  opaque themed background with the backdrop filter off (Gecko cannot blur WebKit's pixels). The hole `clip-path`
  is applied only to `#zen-browser-background` (it used to be applied to `#zen-toolbar-background` as well, which
  cut the sidebar's own background when it slid over the page).
- Passthrough policy (JS `_geckoWantsInput`): Gecko gets input while urlbar results view is open, urlbar
  breakout, glance overlay, an open `panel[panelopen]`, or (compact mode only) sidebar/toolbar hover.
  Plain urlbar focus is deliberately *not* a trigger (it lingered and starved WebKit).
- Tab model: `tab._zenWebKit` holds a view id; one native view per WebKit tab; shown/hidden on `TabSelect`;
  destroyed on `TabClose` or when the tab navigates to a non-matching host.
- UA: `applicationNameForUserAgent = "Version/18.0 Safari/605.1.15"`; `isInspectable = YES` (Safari Web Inspector).

## 3. Build/run notes (hard-won)

- Real objdir: `engine/obj-aarch64-apple-darwin`. (`…27.2.0` is an empty stub.) App binary:
  `engine/obj-aarch64-apple-darwin/dist/Nightly.app/Contents/MacOS/zen`.
- Full/incremental C++: `cd engine && ./mach build`. First build after adding the module took ~12 min
  (reconfigure + stale `zen/*`/widget + single-threaded `rustc gkrust` ~5+ min). **Any IDL change regenerates
  headers and costs 10-17 min** (the last one took 17); a `.mm`-only change is ~2-3 min (relink). Don't churn the IDL.
- JS/CSS-only changes: chrome files are *copied*, not linked → run `./mach build faster` then relaunch.
- Run with a throwaway profile (does not touch the user's data):
  `./zen -profile <dir> about:blank`, then `./zen -profile <dir> "<url>"` to open a URL in the running instance.
- Debugging recipe that worked: temporary env-gated `NSLog` hooks in the bridge (swizzle a method, log
  receiver/caller via `[NSThread callStackSymbols]`), plus synthetic events through `[NSApp sendEvent:]` /
  `[window sendEvent:]`, and a local page served with `python3 -m http.server` with `user_pref("zen.webkit.hosts",
  "localhost")` in the scratch profile's `user.js`. All such probes have been removed from the tree; re-add as needed.
  Caution: test instances run on the user's screen and the user's real input mixes with synthetic input.
- `npm run build` = `surfer build`; tests: `npm test` (`scripts/run_tests.py`), gtests `npm run test:gtest`.
- The tokensave index available in this environment points at a different repo (`/Users/woffles/Desktop/eldoom`);
  it is not usable here. Use grep/Read. In zsh, quote globs (`--include='*.mjs'`).

### Pitfalls already hit
- WebKit's ObjC header declares `@class JSContext`, which **collides with Gecko's `struct JSContext`**. No file may
  include both → the bridge split. Keep it that way.
- `OS_LIBS += ["-framework WebKit"]` must be one string (two strings become `-lWebKit`).
- In the bridge, a local named `id` shadows the ObjC type; use `NSObject*` in blocks.
- `request.cancel()` on the progress-listener request throws `NS_ERROR_NOT_IMPLEMENTED` (remote stand-in);
  use `browser.stop()`.
- The preloaded script runs before `gBrowserInit.delayedStartupPromise` exists; init from the
  `browser-delayed-startup-finished` observer notification instead.
- `Services`/`Cc`/`Ci` are available in the script because it is `loadSubScript`ed into window scope.

## 4. Key technical findings

- **AppKit does not call `-hitTest:` on `ChildView` when dispatching clicks/scrolls on this macOS** (Darwin 27.2.0).
  Synthetic events sent with `[window sendEvent:]` reached `ChildView` even though `hitTest:` (called manually)
  resolved to `WKWebView`, and a trace showed `hitTest:` was never invoked during dispatch. Gecko only uses
  `hitTest:` in `ChildViewMouseTracker::ViewForEvent` (hover). Hence the event-forwarding swizzles.
  Hover appeared to work only because WebKit's tracking areas ignore stacking order.
- On a real click AppKit itself calls `makeFirstResponder:` with the WebKit view (it uses the underlying view for
  focus) while still delivering the `mouseDown:` to `ChildView`; hence the explicit forwarding plus Gecko delivery.
- **Cursor mechanism**: `ChildView -resetCursorRects` adds a rect over its whole bounds owned by `MOZDynamicCursor`;
  the topmost rect owner wins, so Gecko's arrow covered WebKit's cursor. Carving the WebKit frame out of that
  rect fixed it (verified by the user: hand cursor shows, survives clicks; link drags show WebKit's drag preview
  and the system drag cursor, which is expected). A log of every `-[NSCursor set]` showed WebKit setting hand/arrow
  and AppKit resetting to arrow on `cursorUpdate:`/tracking events/`becomeKeyWindow`.
- **Shortcut mechanism** (measured by sending real `NSEvent` key events through `-[NSApp sendEvent:]`): with the
  `WKWebView` as first responder, Cmd+L was delivered to the page (`keyDown 'l'`) and the urlbar did not focus, so
  all Zen keyboard shortcuts were dead whenever the page had keyboard focus. After key routing, Cmd+L reached
  `ChildView`, focus ownership moved to Gecko, and a following plain key went to `ChildView`.
- Gecko windows are `movableByWindowBackground = YES` and `ChildView` returns YES from
  `mouseDownCanMoveWindow` (see [nsCocoaWindow.mm](../engine/widget/cocoa/nsCocoaWindow.mm) ~L1300–1470, ~L5449).
- Layer order in the window (top→bottom): `NSTitlebarContainerView` / `ZenWindowMaterialView` →
  `ChildView` (with `ViewRegionContainerView`s, `PixelHostingView`) → `WKWebView`(s) → system `NSVisualEffectView`.
- `ChildView` lives in `engine/widget/cocoa/nsCocoaWindow.mm` in this tree (no separate `nsChildView.mm`).
- Zen's page-area backgrounds are dedicated elements: `#zen-browser-background`, `#zen-toolbar-background`
  (class `zen-browser-generic-background`) plus `#zen-main-app-wrapper`'s own background; `#browser`, `body`,
  `#tabbrowser-tabpanels` are already transparent.
- Dependence of Zen UI on Gecko content APIs (rough grep counts over `src/zen` + `src/browser`):
  `browsingContext` ~180, `.currentURI`/`contentPrincipal` ~180, `selectedBrowser` ~150, `docShell` ~90,
  `contentWindow/contentDocument` ~75, `JSWindowActor/getActor` ~30, `messageManager` ~30.
  Actors in use: boosts, glance, window-drag, mods marketplace (`src/zen/*/actors`).

## 5. Problems reported by the user on dev builds, and their status

Legend: VERIFIED = user-confirmed or measured; FIXED-UNVERIFIED = fix built and smoke-tested (starts, WebKit
process spawns, no script errors) but not yet seen working on screen; OPEN.

| # | Report | Status |
|---|---|---|
| 1 | Cursor never changed over WebKit content | VERIFIED fixed (cursor-rect carve-out, §2.3) |
| 2 | Clicks/scroll/selection/input on YouTube | VERIFIED working (event forwarding, §2.3) |
| 3 | Shortcuts dead while page focused (Cmd+L etc.) | Root cause measured; routing + focus ownership FIXED-UNVERIFIED by user; Cmd+L routing verified with synthetic key events |
| 4 | Side-tab-bar switching weirdness | Not reproduced by user on the latest build ("no problems there") |
| 5 | YouTube "browser doesn't support full screen" | Enabled; user confirmed the button works |
| 6 | Fullscreen: video snaps to a smaller size, not filling the screen | FIXED-UNVERIFIED (frame sync no longer fights WebKit's fullscreen window) |
| 7 | Exiting fullscreen: jumpy animation, video edges clipping | FIXED-UNVERIFIED (same cause + view re-inserted beneath `ChildView` and frame restored via KVO) |
| 8 | Compact-mode sidebar flyout transparent (only text visible) | Changed to translucent after first fix; now opaque themed background — FIXED-UNVERIFIED |
| 9 | Sidebar collapse/expand: sidebar vanishes (sometimes clipping over page) then the page snaps to full width, vs. smooth on Gecko pages | FIXED-UNVERIFIED (observation bug + per-frame sync burst); may still lag by a frame because AppKit view frames and Gecko's compositor are not synchronized and resizing a `WKWebView` re-lays the page out asynchronously — if so this is architectural |
| 10 | Ctrl+Tab does nothing on WebKit pages | `_wantsKeyDownForEvent:` added; user then reported it works |
| 11 | Ctrl+Tab needed two presses, then (after the first-responder fix) did not work at all until the sidebar was clicked | Root causes in §2.3 (first responder reset to the window on hide; WebKit claiming Ctrl-only key equivalents). Both FIXED; single-press switching verified with synthetic events, FIXED-UNVERIFIED by the user |
| 12 | Fullscreen EXIT: content shrinks to the size of the whole window, then the video "pops" to its in-page spot | RESOLVED, NOT A BUG: reproduced identically in a plain inset `WKWebView` test app and in Safari itself (§5a); it is WebKit's native behaviour |
| 13 | Sidebar collapse/expand fluid, flyout fixed | VERIFIED by user |

**Common root cause of 3, 9, 10 (and likely more):** AppKit/Gecko keyboard, mouse and layout machinery assumes
Gecko's `ChildView` is the window's content, first responder and layout authority. A sibling `WKWebView` is a
second authority that Gecko does not know about, so every place Gecko or AppKit special-cases `ChildView` (key
equivalents, key-view loop, cursor rects, tracking areas, hit testing, first responder, drag/drop destinations,
accessibility, text input/IME, services/dictation, window dragging) is a potential regression. Treat finding
these systematically (grep `widget/cocoa/nsCocoaWindow.mm` for `ChildView` special cases) as a planning task,
not a bug hunt.

Note: `zen.webkit.hosts` is not a registered pref (so it is absent from `about:config`); it is read with a built-in
default (`youtube.com,netflix.com,twitch.tv`). Create it as a String pref in `about:config` or via `user.js` to
change it.

Launching a built binary directly: from `engine/obj-aarch64-apple-darwin/dist/Nightly.app/Contents/MacOS` the
previous agent ran `./zen -profile <ABSOLUTE path> about:blank` successfully, then `./zen -profile <same path> "<url>"`
to open a URL in the running instance. The user reported they could not launch it that way and the cause was not
established (the error text was not captured). Likely suspects: a relative profile path, another instance of the
same build already running (needs `-no-remote` or the second call just talks to it), or running from the wrong
directory. Ask for the exact error before assuming.

### 5a. Fullscreen investigation notes (so the next agent does not repeat dead ends)

- State machine measured via KVO/sampling of `WKWebView.fullscreenState` (macOS 13+): `0` not, `1` entering, `2` in,
  `3` exiting. On entry WebKit moves the view from `ZenWindowMaterialView` into a `WebCoreFullScreenWindow` (frame
  1728x1085 on the test display) within ~50-100 ms; `-[WKFullScreenWindowController _replaceView:with:]` removes the
  view from the Zen window, which resets the window's first responder to the window itself. On exit the state stays `3`
  for ~500 ms while the fullscreen window keeps its frame (the visible animation is not a window-frame animation we
  could sample), then the view is back in `ZenWindowMaterialView` at exactly the frame the bridge recorded.
- So the bridge's restored frame is correct; the odd exit animation ("sides crop then snap") must come from the
  animation target WebKit computes (placeholder/initial rect) or from compositing of the transparent Gecko layer above
  the animating window. Not diagnosed. Ideas: sample the window server list (`CGWindowListCopyWindowInfo`, safe
  during transitions) at 16 ms intervals during exit; compare `_initialFrame` assumptions (screen coords) with the
  hole position; test with the Gecko layer hidden during exit; consider implementing pseudo-fullscreen yourself
  (expand the `WKWebView` over the Zen window, hide chrome) instead of WebKit's window swap.
- **CONTROL EXPERIMENT RESULT (user-run, 2026-10-22):** `/tmp/wk-fullscreen-test` (plain window + inset `WKWebView`,
  no Gecko, no Zen code) shows the same exit: the fullscreen content shrinks to the *window's* size (resize the window
  and the shrink target resizes with it), then pops to the video's own spot in the page. The entry animation zooms
  from the video's spot and looks fine. The app stays open through fullscreen and exits cleanly (Mission Control
  shows "click to exit fullscreen"). Conclusion: the exit animation is not an integration bug. The user also tested Safari
  itself and it shows the same pop, so this is WebKit's native behaviour and Safari-parity needs no fix. Only if we
  wanted to improve on Safari: use SPI, or implement fullscreen ourselves (e.g. shim
  `Element.requestFullscreen` with a user script, expand the element/view to the window, and toggle chrome).
  Also note: the earlier script-triggered fullscreen that terminated the Zen test instance (status 6) did not happen
  to the control app with a real click, which supports (but does not prove) that it was a harness artifact.
- **User description of the exit glitch (superseded by the result above):** the fullscreen content shrinks, with both sides cropping smoothly, until it
  matches the whole web-view frame (the Zen pane), and only then does the video jump back to its in-page viewport.
  That suggests WebKit animates to the *web view's frame* instead of the element's rect, and the page then re-lays
  out when fullscreen ends. This may be stock `WKWebView` behaviour for an embedder, not something this integration
  causes. **Control experiment:** `scripts/wk-fullscreen-test.swift` (build with
  `swiftc -O scripts/wk-fullscreen-test.swift -o /tmp/wk-fullscreen-test`, run `/tmp/wk-fullscreen-test`) is a plain
  window with a `WKWebView` inset at the same geometry as the Zen pane, loading a YouTube video with element
  fullscreen enabled. If its exit looks the same, the cause is WebKit's behaviour for a view smaller than the window
  and the options are to accept it or to implement fullscreen ourselves (resize the view over the window, hide
  chrome, drive the page via `WKUIDelegate`/fullscreen delegate hooks); if it looks right, the cause is in the
  integration (hole/transparent Gecko layer, frame handling, placeholder geometry).
- **Harness caveat:** triggering fullscreen from a probe (`evaluateJavaScript` of `requestFullscreen()`) made the test
  instance exit with status 6 about 50 ms after entry, with no signal handler, `NSException` handler or system log
  output. The user's real-click fullscreen worked on earlier builds and the process survived one early probe run, so
  this may be a harness artifact, but it was NOT ruled out as a product bug. Reproduce with a real click on a real
  site before trusting either conclusion. `lldb` cannot be used to launch the build (the process exits inside `dyld`
  at startup under the debugger).
- Process hygiene: the user normally has `/Applications/Zen.app` running. Never `pkill -f zen`; kill test instances by
  their unique profile path (`pkill -f "<profile dir>"`).
- Console noise seen on dev builds and unrelated to this work: `Failed to load resource:///actors/ZenGlanceChild.sys.mjs`
  (and `ZenBoostsChild`), `sandbox_extension_issue_file_to_process failed … plugin-container.app`,
  `ZenSessionManager … Saving Zen session data` debug lines.

## 6. Power measurement

`scripts/measure-power.sh <label> [seconds]` (needs `sudo`): runs `powermetrics` (cpu_power + tasks), skips the
first 2 samples, prints mean/median/min/max combined mW and energy-impact by process name (excluding
`powermetrics`/`systemstats`). Protocol: same video, same quality, on battery, other apps quit, same brightness,
wait ~30s after playback starts, **do not touch the mouse** (YouTube home-page hover previews autoplay and
contaminated the first run). Compare Zen+Gecko (clear `zen.webkit.hosts`) vs Zen+WebKit vs Safari.

**Only data so far is invalid for comparison** (hover autoplay): zen-webkit mean 736 mW / median 762 mW / min 78 /
max 1582 over 16 samples; by-process mean energy impact: WindowServer ~102, WebKit.WebContent ~94,
WebKit.GPU ~23, zen ~18. Observation worth testing: WindowServer ≥ the WebContent process — consistent with
the cost of a full-window transparent Gecko layer + vibrancy compositing over the video (hypothesis).
No Gecko or Safari baseline has been collected. **Whether this prototype saves battery is therefore unproven**: expected savings come from WebKit rendering/decoding the page, but the Zen process still runs the Gecko UI, and the full-window transparent Gecko layer above the video plus window vibrancy adds WindowServer compositing work that a plain Safari window does not have. Measure before claiming any gain. `Second underflow occurred` lines are `powermetrics` noise.

## 7. Architecture options (starting list — generate your own)

A. **Overlay above Gecko** (first prototype, worked for scroll/click): simplest, but Gecko overlays (urlbar,
   glance, split view, compact sidebar) draw *behind* the page. Mitigations: hide/snapshot the view when overlays
   open, or move overlay UI into native child windows.
B. **Underlay + transparent hole** (current): overlays work, but a full-window transparent Gecko layer is
   composited above video (may erode the energy win and prevents some direct-scanout style optimizations);
   needs event forwarding (above), cursor/keyboard/focus plumbing, CSS hole maintenance for every theme/state.
C. **Native shell, WebKit-only** (Orion-style): best battery, loses Zen's Gecko UI code reuse; effectively a new browser.
D. **Chrome as web-tech inside a WKWebView with sibling WKWebViews for tabs**: variant of C.
E. Hybrid per-site engine (what the prototype does) vs. WebKit-for-all-http(s) with Gecko only for `about:`/`chrome:`/Zen pages.
Extensions: Orion implements its own WebExtension runtime over WKWebView; Apple's `WKWebExtensionController`
exists on recent macOS (**verify availability/limits against the SDK before relying on it**). Blocking `webRequest`
(MV2 uBlock Origin) isn't available in WebKit; expect declarativeNetRequest / `WKContentRuleList`-style blocking.

## 8. UX/UI surfaces to cover in the plan (non-exhaustive checklist)

Tab strip/spaces/essentials/pinned/folders; tab titles, favicons, loading/audio indicators; urlbar (URL, lock/security
state, shield, search, page actions); back/forward + swipe gestures; reload/stop; find-in-page; zoom; print/PDF;
reader mode; screenshots/tab previews; fullscreen/PiP/media controls (Zen media module uses Gecko MediaController);
glance, split view (multiple views), compact mode (sidebar/toolbar slide-over), vertical↔horizontal tab bar switching,
sidebar resize, window resize/fullscreen/inactive greying; drag-and-drop of tabs/links/files over the page; context menus;
downloads; permissions prompts (camera/mic/location/notifications); passwords/autofill; cookies/containers/space-specific
data (`WKWebsiteDataStore(forIdentifier:)` needs macOS 14+); private windows (non-persistent store); history/Places
sync and session restore (`WKWebView.interactionState`); tab discard/sleep; DevTools/view-source; boosts/zap/mods/
page-injected features (currently Gecko actors); extensions; keyboard shortcuts incl. Zen's own (focus/first-responder
routing between Gecko and WebKit); accessibility; cursor; text selection; IME; HiDPI/zoom/CSS-px↔points mapping;
vibrancy/window-material/transparency interplay; light/dark; error pages/cert errors/HTTPS-only; safe browsing.

## 9. Constraints of the previous agent's environment (so you can plan around them)

- Screen capture and accessibility (synthetic OS-level clicks) were **denied**, so visual verification was
  impossible; the user tested manually. In-process verification worked: `NSLog` from the bridge, `dump()` from
  chrome JS, and sending `NSEvent`s through `[window sendEvent:]` to trace routing.
- Launching the app puts a real window on the user's screen; kill test instances (`pkill -f "zen -profile"`).
- Nothing is committed. Git status at handoff (also now modified: nothing outside this list; the new IDL method
  `setPageFocused` is in `src/zen/webkit/`): modified `src/browser/base/content/zen-assets.jar.inc.mn`,
  `src/zen/common/ZenPreloadedScripts.js`, `src/zen/moz.build`; untracked `scripts/measure-power.sh`,
  `src/zen/webkit/`, `docs/webkit-engine-handoff.md` (plus the manual `engine/zen/webkit` symlinks).
- `engine/AGENTS.md` (Mozilla tree rules) applies when editing under `engine/`: keep code comments to a strict
  minimum (the prototype's comments are more verbose than that and should be trimmed), no emoji, `./mach format`
  for formatting, and do not pipe slow `./mach` commands through `head/tail/grep` in a way that forces reruns.
- Commit/PR attribution lines required by the environment: commits end with
  `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`; PR bodies end with
  `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

## 10. Known limitations of the prototype (so you don't rediscover them)

Chrome commands that act on the selected browser (reload, find, zoom, print, back/forward) target the hidden Gecko
tab; link-drag previews use WebKit's native drag; Gecko sees a click on the page area as a click on the transparent
chrome beneath it (side effects possible); the hole is only recomputed on resize/transition events.
Tab title/URL/favicon do not follow in-page navigation; no history/Places entries; no back/forward UI wiring; no
session restore; no find/zoom/print; no downloads/permissions routing; extensions/boosts don't apply; multiple
WebKit tabs share no state; glance/split-view/compact-mode over WebKit untested; window tint changes while a
WebKit tab is selected (`#zen-main-app-wrapper` forced transparent); passthrough is heuristic (attribute/DOM
checks), so some Gecko overlays may not receive clicks; first click after closing the urlbar may be consumed.
