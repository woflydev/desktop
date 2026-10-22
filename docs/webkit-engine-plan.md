# WebKit in Zen (macOS): assessment and plan

Status: **proposal, nothing implemented or committed.** Written 2026-10-22 as the follow-up to
[webkit-engine-handoff.md](webkit-engine-handoff.md) (§0 steps 1 and 2).
Scope: Zen on macOS / Apple Silicon. "Gecko path" means all behaviour with the feature pref off.

---

## Part 1. Independent assessment

### 1.1 Short answer

1. **We don't know yet whether the project is worth doing, and the first milestone should find
   out.** No valid Gecko-vs-WebKit-vs-Safari power baseline exists. The one sample (§6 of the handoff) is
   contaminated, and it already hints that integration overhead (WindowServer ≥ WebContent) eats the gain.
2. **The current "underlay + transparent hole" architecture (B) should not be the base for a product.**
   It puts a full-window transparent Gecko layer over the one workload it is meant to speed up (video).
   It also depends on swizzling Gecko's private `ChildView` behaviour, which keeps producing a new bug
   class with every fix (see 1.4).
3. **Some of the power gap is probably in Zen's own chrome and can be fixed without WebKit.** Window
   vibrancy, `backdrop-filter`, endless chrome animations and timers cost power on every page and in
   every hybrid design, because the Gecko chrome keeps running. Gecko also already has hardware
   VP9/AV1/HEVC decode on Apple Silicon (`dom/media/platforms/apple/AppleDecoderModule.cpp:64-71`).
   But it only uses the low-power "detached" video layer in **fullscreen**
   (`gfx/layers/NativeLayerCA.mm:926-950`, `ShouldSpecializeVideo` returns `mRootWindowIsFullscreen`).
   This is a concrete, measurable lever inside Gecko.
4. If measurement shows that a real gap is left after the chrome fixes and that it sits in the
   content engine, the best hybrid design I see is **per-site WebKit for media/streaming hosts, with
   the WebKit view on top and masked (option F below)**, not underneath. Using WebKit for all sites
   is in effect a new browser (option C), and it is out of proportion to the gain for most browsing.

### 1.2 Where Safari's efficiency actually comes from (hypotheses to measure, not facts)

| Source | Does a Zen+WebKit hybrid get it? | Does Gecko already have it / could it? |
|---|---|---|
| Hardware video decode (VideoToolbox) | Yes | **Yes already** (H.264/HEVC/VP9/AV1 HW on Apple Silicon) |
| Low-power detached video compositing (AVSampleBufferDisplayLayer, overlay planes) | Only if nothing translucent composites above the video. **B breaks this**, F might not | Fullscreen only today; windowed is a possible Gecko change |
| JS/layout/style CPU efficiency of WebKit | Yes | No, engine difference (size per workload unknown) |
| Timer coalescing, background-tab throttling, process suspension | Yes for WebKit tabs | Partly (Gecko throttles background tabs; Zen has no auto-discard by default) |
| Opaque, mostly static native chrome | **No**, Zen's Gecko chrome still runs | Partly: vibrancy/blur/animation costs are Zen's choices |
| One WindowServer surface per window with no alpha above content | **No in B** (transparent layer), likely in F | Yes, if Zen avoids translucency over content |

So the hybrid's best case is roughly: *Safari content cost + Zen chrome cost + integration overhead*.
Both Zen chrome cost and integration overhead stay in the hybrid. Phase 0 must measure each term.

### 1.3 Architecture options

Ratings are my estimates before measurement: Eff = expected efficiency gain on video, Risk =
regression risk to existing Zen features, Cost = engineering + maintenance.

| | Option | Eff | Risk | Cost | Verdict |
|---|---|---|---|---|---|
| A | WKWebView **above** Gecko, unmasked (first prototype) | High | High (in-document overlays drawn under the page) | Low | Only as a building block of F |
| B | WKWebView **under** a transparent Gecko layer with a CSS hole (current) | **Low–Med** (alpha layer above video, vibrancy behind) | High (ChildView swizzles: input, cursor, keys, focus) | High, open-ended | **Stop as the main path**; keep as a measured control in M1 |
| C | Native WebKit-only shell (Orion-style) | Highest | Total (rewrite all of Zen's UI) | Very high | Out of scope; it is a different product |
| D | Chrome as web content in a WKWebView plus sibling tab WKWebViews | High | Total (Zen's chrome is XUL/Gecko-specific) | Very high | Out of scope |
| E | WebKit for **all** http(s), Gecko only for internal pages | Med–High | Very high (every Gecko-content-API consumer: ~180 `browsingContext`, ~150 `selectedBrowser`, actors) | Very high | Not until per-site has proven itself |
| **F** | **WKWebView above Gecko, opaque; CALayer mask + hit-test carve-out wherever Gecko UI has to show over the page** (a WKWebView *subclass*, no Gecko swizzling) | High (video is the top opaque layer when no overlay is open) | Med | Med | **Candidate for M1 spike** |
| G | F, plus moving in-document overlays (urlbar results, glance) into native popup windows | High | Med–High (changes Zen UI internals) | High | Only where the F mask fails |
| H | Snapshot handoff: when a Gecko overlay opens over the page, swap the live view for a `takeSnapshot` image | High | Low–Med (content freezes while overlay is open) | Low | Fallback for rare overlays inside F |
| I | **Don't embed WebKit; fix Zen/Gecko power costs** (chrome translucency, animations, timers, auto tab-unload, windowed detached video in Gecko) | Med (unknown until measured) | **Lowest** | Low–Med | **Do first regardless**; it may be enough |
| J | WKWebView in a child `NSWindow` tracked over the content area | High | High (window-move lag, Spaces/fullscreen/Mission Control edge cases) | Med | Inferior to F |
| K | "Open in WebKit window": a separate lightweight WebKit window for chosen sites, styled like Zen, with no in-tab integration | High for those sites | Very low (Gecko path untouched) | Low | Honest fallback if F fails its gates |

Why F rather than B:
- **No translucent Gecko surface above the video.** When nothing overlaps the page, WindowServer sees
  an opaque WebKit layer on top, which is the precondition for low-power video compositing.
- **Input goes where AppKit sends it.** The first prototype (A) already received clicks and scrolls
  natively. A `WKWebView` subclass can return `nil` from `hitTest:` inside masked rects, which avoids
  swizzling `ChildView` `mouseDown:`/`scrollWheel:`/`resetCursorRects`. The handoff found AppKit not
  consulting `ChildView hitTest:` during dispatch and could not explain it. M1 must reproduce that for
  the *top* view before relying on it.
- **Cursor**: the topmost view's cursor rects win, so WebKit's cursor works without the carve-out.
- **Costs that F still has**: Gecko overlays shown over the page can't blur WebKit pixels (B has the
  same limit), and mask geometry must follow overlay animations. Mask updates are cheap CALayer
  changes and run only while an overlay is visible, unlike B's always-on hole.
- **What doesn't change between A/B/F**: keyboard/first-responder routing, the tab/session/history
  adapter, data stores, permissions, extensions. Those make up most of the work, and Part 2 covers them
  the same way for any embedding option.

### 1.4 Do the `ChildView` workarounds mean B is wrong? Yes.

The prototype swizzles 17 `ChildView` event selectors, plus `hitTest:` and `resetCursorRects`. It also
swizzles `WKWebView keyDown:/keyUp:/flagsChanged:/performKeyEquivalent:` and adds a private
`_wantsKeyDownForEvent:`. Each one fixed a symptom of the same cause: AppKit and Gecko treat
`ChildView` as the window's content, first responder and layout authority (handoff §5, "common root
cause"). `ChildView` in `engine/widget/cocoa/nsCocoaWindow.mm` still has special cases nobody has
handled yet, and each is the next bug in B:
- drag destination (`draggingEntered:` ~L3997)
- `NSTextInputClient`/IME (~L3164)
- Services (`validRequestorForSendType:` ~L4397)
- Quick Look (~L3310)
- the accessibility tree root (~L4667–4790)
- window-drag regions (`mouseDownCanMoveWindow`, `NonDraggableView`, ~L1309–1470)
- swipe/magnify (~L2131, L2166)

Swizzling a private class in a tree that rebases onto Firefox every 4 weeks is a maintenance risk on
its own. F needs at most the keyboard routing, and that can live in a `WKWebView` subclass.

### 1.5 What each option puts at risk in Zen

These risks apply to *every* embedding option and are per WebKit tab:
- **Login state.** WebKit and Gecko have separate cookie jars, so a user signed in to YouTube in Gecko
  is signed out in WebKit.
- **Extensions.** uBlock Origin doesn't run in WebKit tabs. YouTube without an ad blocker is a UX
  regression *and* a power regression, because ads and trackers cost energy.
- **Gecko-only tools.** Password manager, Places history, sync, containers, DevTools, Reader
  view/screenshot actors, boosts and mods (Gecko actors), Glance's content actor, safe browsing and the
  Gecko error pages all need a WebKit adapter or are lost.
- **Memory.** Two engines' process sets run at once (Gecko content, GPU and RDD processes, plus WebKit
  WebContent, GPU and Networking).
- **Version coupling.** WebKit behaviour follows macOS releases, not Zen's.

### 1.6 What has to be measured to decide

All of these come from the M0 harness in Part 2.4:
1. Gap: Safari vs Zen-Gecko-default on video (windowed, fullscreen), idle and scroll.
2. Chrome share: Zen-Gecko-default vs **Zen-Gecko-lean** (vibrancy, acrylic and grey-out off,
   reduced motion) vs **Firefox of the same Gecko version**.
3. WebKit floor: a **bare WKWebView control app** (a generalised `scripts/wk-fullscreen-test.swift`) vs Safari.
4. Integration overhead: Zen-WebKit-B and Zen-WebKit-F vs the control app.
5. Gecko-side lever: Zen-Gecko with windowed detached video (experimental Gecko change), if 1–2 show
   that windowed video is the gap.

**Decision gates** (go/no-go, see the milestones):
- **G0** (after M0): if Zen-Gecko-lean is within 15% of Safari on S1/S2 (defined in 2.4), stop the
  WebKit work, ship option I, and report.
- **G1** (after M1): continue the hybrid only if Zen-WebKit-F beats Zen-Gecko-lean by **≥ 20% system
  power on S1** and does not lose on S3/S4 (2.4). If F fails and B passes, re-assess B with that
  evidence. If both fail, offer K or stop.

---

## Part 2. Plan

### 2.1 Principles

- **Gecko path unchanged.** With `zen.webkit.enabled = false` (the default):
  - no WebKit code runs, no swizzle is installed, no listener or observer is registered, no CSS is
    injected, and no WebKit process starts;
  - nothing under `engine/` is patched for this feature.
- **One IDL design, done up front.** IDL changes cost 10–17 min rebuilds (handoff §3), so the full
  interface (methods plus a listener for callbacks) is designed in M2 and then frozen.
- **Each milestone is separate commits, behind the pref, and reversible** by flipping the pref or
  reverting. No user-data migration: WebKit data stores live in their own directory and can be
  deleted.
- **Per-site, opt-in hosts** (`zen.webkit.hosts`, registered as a real pref, empty by default even when
  enabled, until M8).

### 2.2 UX/UI surface inventory

"Gecko" = behaviour with the feature off (must be unchanged). The second column says how a WebKit tab
handles the surface; M# is the milestone. ⚠ marks known-hard or lossy items.

| Surface | WebKit-tab handling | M# |
|---|---|---|
| **Load routing** | Route *before* Gecko starts the load: hook URL-open paths (`URILoadingHelper`/`loadURI` + link clicks via a parent-side decision), not `onStateChange` + `browser.stop()`, which leaks a Gecko request with Gecko cookies. Navigations inside WebKit to non-matching hosts: `decidePolicyForNavigationAction` → hand back to Gecko in the same tab | M2 |
| Tab model / Zen adapter | Each WebKit tab keeps its Gecko `<browser>` lazy/blank. A `ZenWebKitTabAdapter` exposes `currentURI`, title, `canGoBack/Forward`, security state and loading state to the ~180 `currentURI`/`browsingContext` call sites. Spike: reuse Firefox's lazy-browser (pending tab) mechanism vs per-instance getters ⚠ | M3 |
| Tab strip, spaces, essentials, pinned, folders, live folders | Work off the `<tab>`, so they work once the adapter is in place. Essentials/pinned "reset to pinned URL" uses the adapter | M3 |
| Tab title, favicon | KVO `title`; favicon from `<link rel=icon>` via a `WKUserScript` → `WKScriptMessageHandler`, fetched through the WebKit data store and fed to `gBrowser.setIcon` | M3 |
| Loading/progress indicator, busy state | KVO `estimatedProgress`, `isLoading` | M3 |
| Audio indicator / mute | `WKMediaPlaybackState`, `requestMediaPlaybackState`; mute via `_setPageMuted:` SPI or a user script ⚠ | M6 |
| Urlbar URL, lock/identity, cert info | KVO `URL`, `hasOnlySecureContent`, `serverTrust` → identity panel adapter. Mixed content and cert details are partly lossy ⚠ | M3 |
| Shield / tracking protection UI | Shows WebKit content-blocking state (see Extensions) | M5 |
| Search, page actions, star/bookmark | Bookmarks via adapter URI. Page actions that need content actors are hidden for WebKit tabs | M3 |
| Back/forward buttons, history menu, swipe | `goBack/goForward`, `backForwardList`; `allowsBackForwardNavigationGestures` replaces Gecko's swipe on WebKit tabs | M3 |
| Reload/stop, hard reload | `reload`, `reloadFromOrigin`, `stopLoading` routed from `BrowserCommands` via the adapter | M3 |
| History/Places, frecency, sync | `PlacesUtils.history.insert` on committed navigations (`didCommitNavigation`); visits are typed by transition | M3 |
| Session restore | `interactionState` (macOS 12+) stored in the Zen session store per tab; restored lazily on tab select | M3 |
| Tab discard / unload / sleep | Destroy the view and keep `interactionState`, the same as Gecko unloading | M3 |
| Find in page | Zen findbar → `findString:withConfiguration:`. Match count via `_countStringMatches` SPI or a user script ⚠ | M6 |
| Zoom | `pageZoom` per-site, synced with Gecko's site-zoom store and urlbar zoom badge | M6 |
| Print / Save as PDF | `printOperationWithPrintInfo:`, `createPDFWithConfiguration:` | M6 |
| Reader mode | Hidden for WebKit tabs (lossy) ⚠, or later: extract via user script into a Gecko reader tab | later |
| Screenshots, tab previews, tab hover cards, Glance previews | `takeSnapshotWithConfiguration:` | M6 |
| Element fullscreen | Native WebKit fullscreen (exit animation matches Safari, handoff §5a). Zen's window-fullscreen state kept in sync | M6 |
| PiP | WebKit's native PiP (no Gecko PiP toggle on WebKit tabs) | M6 |
| Media controls (Zen media module uses Gecko MediaController) | Adapter for `ZenMediaController`: play/pause via `pauseAllMediaPlayback`/user script, metadata via a Media Session user script ⚠. macOS Now Playing comes from WebKit natively; avoid duplicates | M6 |
| Glance (alt-click / overlay) | Glance on a WebKit page: the overlay is in-document Gecko → F mask region. Glance *of* a WebKit host: opens a WebKit view in the glance frame. Glance link-click detection uses a Gecko actor → user-script equivalent | M7 |
| Split view (multiple visible views) | One `WKWebView` per split pane, frames from each pane's container. Mixed Gecko/WebKit panes supported | M7 |
| Compact mode sidebar/toolbar slide-over | F mask follows the flyout rect per frame while it animates (mask only, no WKWebView resize). Background opaque themed (no blur over WebKit) ⚠ visual difference | M7 |
| Sidebar collapse/expand, resize, vertical↔horizontal tabs, single toolbar, right side | Frame sync. To avoid async relayout lag, during Zen `motion` animations move/mask the view and resize at the end; measure against Gecko for smoothness | M2/M7 |
| Window resize, fullscreen, inactive greying, tint | Frame sync; greying/tint applied by Gecko chrome only (page not tinted, like Safari) | M2 |
| Vibrancy / window material | Behind the opaque WebKit view it's invisible. M0 decides whether to keep it on at all for WebKit tabs | M1 |
| Light/dark, theme colour | `underPageBackgroundColor`; `themeColor` KVO feeds Zen's site-colour features if any | M6 |
| Drag & drop: tabs over page, links/images out of page, files into page | Out of page: WebKit native. Tab drags over the page: the Zen DnD module needs the page area as a drop target → mask the drop zones or forward `draggingEntered:` from the subclass ⚠ | M7 |
| Context menus | `WKUIDelegate` `willOpenMenu`/`contextMenu` hooks → append Zen items (open in glance/split/new tab) | M6 |
| Downloads | `WKDownload` (macOS 11.3+) → Gecko `Downloads` API so the Zen downloads UI and the library show it | M6 |
| Permission prompts (camera, mic, location, notifications, screen) | `requestMediaCapturePermissionForOrigin` → Gecko `PopupNotifications` + `SitePermissions` store. Notifications: user script `Notification` shim → Gecko alerts ⚠ | M6 |
| JS dialogs (alert/confirm/prompt), beforeunload, file picker, HTTP auth, client certs | `WKUIDelegate` runJavaScript*/`runOpenPanel` → Gecko `Prompter` (tab-modal); `didReceiveAuthenticationChallenge` → Gecko auth prompt | M6 |
| window.open / target=_blank / popups | `createWebViewWithConfiguration` → new Zen tab bound to the returned view; popup blocking via Gecko prefs | M6 |
| Passwords / autofill / passkeys | WebKit's AutoFill does not use Gecko's store. Phase 1: none (documented). Phase 2: user-script bridge into `LoginManager` (security review required) ⚠. Passkeys: system via WebKit (needs entitlement check) | later |
| Cookies, login state | Opt-in one-time copy of Gecko cookies for the routed hosts into `WKHTTPCookieStore` when a host is enabled (user-confirmed) ⚠ | M4 |
| Spaces/containers/space-specific data | `WKWebsiteDataStore dataStoreForIdentifier:` (macOS 14+) keyed by container/space UUID | M4 |
| Private windows | `nonPersistentDataStore` per private window | M4 |
| Clear data / forget site / sanitize | Sanitizer hooks call `removeDataOfTypes` on the relevant stores | M4 |
| Extensions / content blocking | Minimum: a `WKContentRuleList` built from an EasyList-class list, so the WebKit tab is no worse at ad blocking (power!). Optional: `WKWebExtensionController` (macOS 15.4+, present in SDK 27.0) for Safari-format extensions. Gecko WebExtensions do **not** apply ⚠ | M5 |
| Boosts, zap, mods (Gecko actors / page CSS) | Boost CSS → `WKUserStyleSheet`/`WKUserScript`; zap needs a user-script port ⚠ | M7 |
| DevTools / view-source | Safari Web Inspector (`inspectable = YES`); Gecko DevTools menu items open it. view-source: route to a Gecko tab | M6 |
| Keyboard shortcuts | Subclass `performKeyEquivalent:`/`keyDown:`: keys marked `reserved` in Gecko's `<keyset>`, plus Zen's own `kbs` shortcuts, go to Gecko first (exported to native as a set, not hard-coded). Everything else goes to the page first; anything the page doesn't handle bubbles via `nextResponder` = ChildView | M2 |
| Focus (urlbar ↔ page) | Explicit focus owner (as in the prototype) but driven by Gecko focus events plus the subclass's `becomeFirstResponder`; Tab/Shift-Tab past the page edge moves into chrome | M2 |
| Text selection, IME, dictation, Services, Writing Tools, Look Up | Native in WKWebView while it's first responder; verify each on the checklist | M8 |
| Cursor | Native (top view) in F | M1 |
| Accessibility | WKWebView exposes its own AX subtree. It must be reachable from the window's AX tree, whose root is Gecko's `ChildView` (~L4667): inject the WKWebView AX element as a child of the Gecko content-area AX node ⚠ needs VoiceOver test | M8 |
| HiDPI, chrome zoom, CSS px ↔ points | Convert with `window.devicePixelRatio` / `backingScaleFactor` (prototype assumes 1 CSS px = 1 pt, which breaks with `layout.css.devPixelsPerPx` or chrome zoom) | M2 |
| Error pages, cert errors, HTTPS-only, safe browsing | `didFailProvisionalNavigation` → Gecko-style error page shown in the Gecko browser area (route back to Gecko); HTTPS-only by upgrading in `decidePolicy`; safe browsing via WebKit's built-in (Apple/Google), documented as different | M6 |
| User agent | Derive from the installed Safari version (prototype hard-codes `Version/18.0`) | M2 |
| Telemetry/crash | WebContent crash → `webViewWebContentProcessDidTerminate` → Zen "tab crashed" UI with reload | M6 |

### 2.3 Regression strategy

Goal: with the pref off nothing changes. With the pref on, nothing changes for non-matching hosts.

1. **Gating**
   - New `prefs/zen/webkit.yaml`: `zen.webkit.enabled` (bool, default false, `mirror: always`) and
     `zen.webkit.hosts` (string, default "").
   - `ZenWebKit*.js` returns before registering anything when the pref is off, and listens for the
     pref to turn on or off at runtime with full teardown.
   - Swizzles/subclass installation happen only on first view creation (already true in the
     prototype; keep it that way and assert it in tests).
   - Optional build flag `--enable-zen-webkit` so release builds can exclude the module until M8.
2. **Footprint audit**: the only edits outside `src/zen/webkit/` are the existing three include lines
   and a pref file. Each commit's diff is checked for edits to Gecko-path files. In F there are no
   `ChildView` swizzles.
3. **Baseline the test suites before any change.** On base commit `541ee5519` plus the current
   uncommitted prototype *reverted* (stash it), run `npm test` and the perf tests in
   `src/zen/tests/performance/` (vsync, opaque region, tab switch/open/close, startup) twice to tell
   pre-existing failures and flakes apart. Store the results in `docs/webkit-baselines/tests-<date>.txt`.
4. **After every milestone**:
   - full `npm test` with the pref off and on (empty hosts), plus the perf tests;
   - any new failure blocks the milestone; an equal failure count is not enough, the failure set must
     match;
   - `npm run lint`.
5. **New tests** in `src/zen/tests/webkit/`, registered in `src/zen/tests/manifest.toml`:
   - pref off: no `zen-webkit-active` attribute, no style element, `gZenWebKit` inert, the service is
     not instantiated, and loading a would-be host stays in Gecko;
   - pref on with a `localhost` host (served by the test harness): route, title/URL sync, back/forward,
     close, tab switch, session store round trip, private-window store isolation.
   - Native side: a small gtest (`npm run test:gtest`) for frame conversion and key-reservation logic.
6. **Manual parity checklist**, run by you before and after each milestone with the pref off, and with
   the pref on for both a non-matching and a matching site. Each step records pass/fail:
   - tabs: open, close, switch, drag, pin, essentials, folders, spaces switch;
   - urlbar: type, results, breakout, Cmd+L/Cmd+T/Cmd+W, Ctrl+Tab;
   - layout: sidebar collapse/expand/resize, compact mode both bars, vertical↔horizontal, split view
     (2 and 3 panes), glance open/close;
   - page interaction: find, zoom, print, context menu, downloads, a permission prompt, a JS alert,
     fullscreen video in and out, PiP;
   - window: resize, fullscreen, inactive window greying, light/dark switch;
   - session: session restore after quit, private window;
   - input: VoiceOver reads the page, IME input (Japanese), Services menu, Look Up;
   - drag and drop: link out, file in.
7. **Kill switch**: if a WebKit view crashes or misbehaves, the tab can be reopened in Gecko
   ("Open in Gecko" item on the tab context menu), and the pref can be disabled at runtime without a
   restart.

### 2.4 Power and smoothness measurement plan

**What the current script measures, and its gaps.** `scripts/measure-power.sh` reports
`powermetrics` CPU+GPU+ANE power. That is SoC only: it excludes the display, DRAM and Wi-Fi, so it is
not battery drain. It also lumps processes by name, but `WebKit.WebContent`/`GPU`/`Networking` have the
same names under Safari and under Zen, so they can't be attributed to the right browser.

Upgrades (M0):
- Sample whole-system power from `ioreg -rn AppleSmartBattery` (battery voltage × amperage, plus the
  system power telemetry fields where present) every 5 s. This is the primary metric. `powermetrics`
  stays as the breakdown.
- Add `--show-process-coalition` so all of a browser's helper processes are counted under the app that
  owns them.
- Record metadata per run: config label, build hash, macOS build, battery %, brightness, thermal state
  (`pmset -g therm`), Low Power Mode, display.
- Write CSV per run to `docs/webkit-baselines/` (or outside the repo), plus a summary script that
  computes the mean, a 95% CI and Welch's t-test per pair.
- Smoothness:
  - dropped/decoded frame counts from `HTMLVideoElement.getVideoPlaybackQuality()`, collected by the
    test page itself (same JS in every engine);
  - scroll and animation hitches from `xctrace record --template 'Animation Hitches'` per process;
  - sidebar-animation lag recorded by you on a 120 Hz screen recording if needed.

**Workloads.** Local and deterministic: pages served from `python3 -m http.server` on localhost, so
there are no ads, recommendations or network variance:
- **S1** windowed 1080p60 video, one file each in H.264, VP9 and AV1 (looping `<video>`), 10 min;
- **S2** the same, element fullscreen;
- **S3** static article page, idle, 10 min (measures chrome idle cost);
- **S4** scripted auto-scroll of a long page at a fixed speed (identical JS), 5 min, power + hitches;
- **S5** real-world check, run *after* the local ones: one fixed YouTube watch URL at fixed quality,
  logged-out, no mouse movement (with uBO in Gecko and content blocking in WebKit, else note "no
  blocker");
- **S6** background load: 20 tabs open (fixed list of local pages), foreground S3.

**Configurations:**
1. Safari
2. bare WKWebView control app
3. Firefox release on the same Gecko version
4. Zen-Gecko default
5. Zen-Gecko lean (vibrancy, acrylic and grey-out off, reduced motion)
6. Zen-WebKit-B (prototype)
7. Zen-WebKit-F (M1 spike)
8. optional: Zen-Gecko with windowed detached video

**Protocol:**
- MacBook on battery, between 80% and 40%, Low Power Mode off.
- Built-in display only, auto-brightness, True Tone and Night Shift off, fixed brightness level.
- Volume muted (audio path is the same in all configs).
- Every other app quit. Spotlight and Time Machine paused; wait until `mds`/`backupd` are idle.
- Same window size and position (set by script).
- 60 s warm-up after playback starts, then the measurement window. Hands off input.
- **≥ 3 runs per config, interleaved in shuffled order** (not A-A-A then B-B-B) to cancel battery-level
  and thermal drift; a 2 min cool-down between runs.

**Pass/fail thresholds** (system power, the battery-derived metric):
- "Significantly improved" means Zen-WebKit-F ≤ 0.80 × Zen-Gecko-lean on S1 (every codec) and S2, and
  the 95% CIs don't overlap.
- No regression: S3 and S4 ≤ 1.05 × Zen-Gecko-lean; S6 no worse than Zen-Gecko-lean (memory reported
  too).
- Smoothness: dropped-frame ratio ≤ Gecko's; hitch time ratio ≤ Gecko's on S4; you rate the sidebar
  animation as no worse than on Gecko pages.
- The G0/G1 gates in 1.6 use these numbers. Results are reported as tables with CIs, never single
  runs.

I can't run `sudo powermetrics` or verify things visually myself. You run the harness with one command
per config, and I analyse the CSVs.

### 2.5 Milestones

Each milestone is reversible, gated by the pref, and ends with the regression and parity checks in 2.3.

| M | Content | Exit criteria |
|---|---|---|
| **M0** | Measurement harness (script upgrade, local test pages, control app, summary script). Run baselines for configs 1–6. In parallel: the low-risk Gecko-side power fixes from the handoff §2.1 (option I), each behind its own pref, measured individually | **G0** decision with data |
| **M1** | Spike F in a throwaway branch (WKWebView subclass on top, mask + hit-test carve-out, natural cursor). Measure F vs B on S1–S4. Verify where AppKit dispatches events for the top view | **G1** decision; choose F, B or K |
| **M2** | Productise the foundation: register the prefs; cleanly split the prototype into bridge, subclass and adapter; the single IDL design (methods + `nsIZenWebKitViewListener` callbacks for navigation, title, progress, security, media, fullscreen, permission, dialog, download, crash); pre-load routing; frame sync with CSS→points conversion; keyboard reservation from `<keyset>`; focus ownership; UA; trim comments per `engine/AGENTS.md` | Test suites unchanged; parity checklist pass with pref off/on |
| **M3** | Tab adapter: title, favicon, URL/identity, progress, back/forward/reload/stop, Places history, session restore, unload | Adapter tests green; YouTube usable as a daily tab |
| **M4** | Data: per-container/space stores (macOS 14+), private windows, sanitize, opt-in cookie copy | Isolation tests green |
| **M5** | Content blocking via `WKContentRuleList`; evaluate `WKWebExtensionController` | S5 measured with a blocker |
| **M6** | Page features: find, zoom, print/PDF, context menu, downloads, permissions, dialogs, popups, fullscreen/PiP/media controls, errors, crash UI, inspector, snapshots | Each surface on the checklist passes |
| **M7** | Zen features over WebKit: glance, split view, compact mode, layout animations, DnD, boosts | Checklist pass; smoothness thresholds |
| **M8** | Accessibility, IME, Services, Writing Tools; dogfood period; final power validation (full 2.4 matrix); then decide default host list | All 2.4 thresholds met; you sign off |

### 2.6 Things to confirm with you before work starts

1. **Approve starting with M0 (measurement plus Gecko-side fixes) before any further WebKit code?**
   This rules out continuing the underlay prototype for now.
2. The FIXED-UNVERIFIED items in handoff §5 (3, 6, 7, 8, 9, 11) only matter if B survives G1, so I
   propose not re-verifying them now. OK?
3. Minimum macOS for the WebKit feature: 14 (per-space data stores) or 15.4 (Web Extension API)?
4. Is it acceptable that WebKit tabs have no Gecko password manager or Gecko extensions in v1, given
   content blocking in M5?
5. How should the prototype files be kept: stash them on a local branch (not pushed) so the
   working tree returns to the Gecko baseline for M0's test baselines?
