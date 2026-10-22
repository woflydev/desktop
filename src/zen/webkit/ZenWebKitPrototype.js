// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at http://mozilla.org/MPL/2.0/.

// EXPERIMENTAL: render selected sites in a native WKWebView laid over the tab
// content area. Hosts come from the `zen.webkit.hosts` pref (comma separated,
// suffix match). Console helpers: gZenWebKit.open(url), gZenWebKit.close().

// prettier-ignore
// eslint-disable-next-line no-lone-blocks
{
  const HOSTS_PREF = "zen.webkit.hosts";
  const DEFAULT_HOSTS = "youtube.com,netflix.com,twitch.tv";

  let native = null;
  try {
    native = Cc["@mozilla.org/zen/webkit-view;1"].getService(Ci.nsIZenWebKitView);
  } catch (e) {
    // Not available on this platform/build.
  }

  const gZenWebKit = {
    _counter: 0,

    get available() {
      return !!native;
    },

    _hosts() {
      return Services.prefs
        .getStringPref(HOSTS_PREF, DEFAULT_HOSTS)
        .split(",")
        .map(h => h.trim())
        .filter(Boolean);
    },

    _matches(uri) {
      if (!uri || !/^https?$/.test(uri.scheme)) {
        return false;
      }
      return this._hosts().some(h => uri.host === h || uri.host.endsWith("." + h));
    },

    _injectStyle() {
      const style = document.createElement("style");
      style.id = "zen-webkit-style";
      // The WebKit view sits *under* Gecko's, so everything between it and
      // the window must be transparent over the page area.
      style.textContent = `
        :root[zen-webkit-active] #zen-main-app-wrapper,
        :root[zen-webkit-active] #tabbrowser-tabbox,
        :root[zen-webkit-active] #tabbrowser-tabpanels .browserSidebarContainer,
        :root[zen-webkit-active] #tabbrowser-tabpanels .browserContainer,
        :root[zen-webkit-active] #tabbrowser-tabpanels .browserStack {
          background: transparent !important;
        }
        :root[zen-webkit-active][zen-compact-mode] .zen-toolbar-background {
          background: light-dark(rgb(235, 235, 235), rgb(27, 27, 27)) !important;
          backdrop-filter: none !important;
        }
        :root[zen-webkit-active] #tabbrowser-tabpanels .deck-selected browser[type="content"] {
          visibility: hidden !important;
        }
      `;
      document.documentElement.appendChild(style);
    },

    init() {
      if (!native) {
        return;
      }
      this._injectStyle();
      gBrowser.addTabsProgressListener(this);
      gBrowser.tabContainer.addEventListener("TabSelect", () => this._onSelect());
      gBrowser.tabContainer.addEventListener("TabClose", e => this._destroy(e.target));
      window.addEventListener("resize", () => this._sync());
      // Sidebar slide / compact mode animations move the container without
      // resizing it, so also resync when a transition ends.
      document.addEventListener("transitionend", () => this._sync(), true);
      this._resizeObserver = new ResizeObserver(() => this._sync());
      const rootObserver = new MutationObserver(() => this._burst());
      rootObserver.observe(document.documentElement, {
        attributes: true,
        attributeFilter: [
          "zen-sidebar-expanded",
          "zen-compact-mode",
          "zen-compact-animating",
          "zen-right-side",
          "zen-single-toolbar",
        ],
      });
      document.addEventListener("transitionrun", () => this._burst(), true);

      // Decide whether Gecko UI drawn over the page needs the clicks.
      const update = () => this._updatePassThrough();
      const isEditable = el =>
        !!el?.matches?.("input, textarea, [contenteditable='true']");
      document.addEventListener("focusin", e => {
        if (isEditable(e.target)) {
          this._setPageFocus(false);
        }
        update();
      });
      document.addEventListener("focusout", () => {
        setTimeout(() => {
          if (!isEditable(document.activeElement)) {
            this._setPageFocus(true);
          }
          update();
        }, 0);
      });
      for (const ev of ["popupshown", "popuphidden"]) {
        document.addEventListener(ev, update, true);
      }
      const mo = new MutationObserver(update);
      mo.observe(gBrowser.tabpanels, {
        attributes: true,
        subtree: true,
        attributeFilter: ["class", "has-toolbar-hovered"],
      });
      for (const id of ["navigator-toolbox", "zen-appcontent-navbar-wrapper"]) {
        const el = document.getElementById(id);
        if (el) {
          mo.observe(el, {
            attributes: true,
            attributeFilter: ["zen-has-hover", "zen-has-implicit-hover"],
          });
        }
      }
    },

    // Returns why Gecko UI needs the clicks, or "" if the page should get them.
    _setPageFocus(focused) {
      try {
        native.setPageFocused(focused && !!gBrowser.selectedTab._zenWebKit);
      } catch (e) {}
    },

    _geckoWantsInput() {
      // Note: plain urlbar focus is not enough. It can linger after a load
      // (the hidden Gecko page cannot take focus) and would starve WebKit.
      if (gURLBar.view?.isOpen) {
        return "urlbar-view-open";
      }
      if (gURLBar.hasAttribute("breakout-extend")) {
        return "urlbar-breakout";
      }
      if (document.querySelector(".zen-glance-overlay")) {
        return "glance";
      }
      if (document.querySelector("panel[panelopen]")) {
        return "panel-open";
      }
      // Compact mode slides the sidebar/toolbar over the page on hover.
      if (
        document.documentElement.hasAttribute("zen-compact-mode") &&
        document.querySelector(
          "[zen-has-hover], #tabbrowser-tabpanels[has-toolbar-hovered]"
        )
      ) {
        return "compact-hover";
      }
      return "";
    },

    _updatePassThrough() {
      if (!native) {
        return;
      }
      try {
        const reason = this._geckoWantsInput();
        native.setPassThrough(!reason);
      } catch (e) {}
    },

    // nsIWebProgressListener, via gBrowser.addTabsProgressListener.
    onStateChange(browser, webProgress, request, flags) {
      const wpl = Ci.nsIWebProgressListener;
      if (
        !webProgress.isTopLevel ||
        !(flags & wpl.STATE_START) ||
        !(flags & wpl.STATE_IS_DOCUMENT) ||
        !(request instanceof Ci.nsIChannel)
      ) {
        return;
      }
      const tab = gBrowser.getTabForBrowser(browser);
      if (!tab) {
        return;
      }
      if (this._matches(request.URI)) {
        const url = request.URI.spec;
        browser.stop();
        this._destroy(tab);
        this._create(tab, url);
      } else if (tab._zenWebKit) {
        this._destroy(tab);
      }
    },

    _create(tab, url) {
      const id = "zwk-" + ++this._counter;
      native.create(window, id, url);
      tab._zenWebKit = id;
      tab.setAttribute("zen-webkit", "true");
      // The load came from the urlbar; hand focus to the page.
      gURLBar.view?.close?.();
      gURLBar.blur();
      gBrowser.setTabLabel?.(tab, new URL(url).hostname);
      this._onSelect();
    },

    _destroy(tab) {
      if (tab._zenWebKit) {
        try {
          native.destroy(tab._zenWebKit);
        } catch (e) {}
        tab._zenWebKit = null;
        tab.removeAttribute("zen-webkit");
        if (tab == gBrowser.selectedTab) {
          document.documentElement.removeAttribute("zen-webkit-active");
          this._clearHole();
        }
      }
    },

    _observeContainers() {
      this._resizeObserver.disconnect();
      const selected = gBrowser.selectedBrowser?.closest(".browserSidebarContainer");
      for (const el of [gBrowser.tabpanels, gBrowser.tabbox, selected]) {
        if (el) {
          this._resizeObserver.observe(el);
        }
      }
    },

    // Zen animates layout with JS, so keep the native view in step with the
    // page container on every frame for a while after a layout change.
    _burst(ms = 800) {
      this._burstUntil = performance.now() + ms;
      if (this._bursting) {
        return;
      }
      this._bursting = true;
      const tick = () => {
        this._sync();
        if (performance.now() < this._burstUntil) {
          requestAnimationFrame(tick);
        } else {
          this._bursting = false;
        }
      };
      requestAnimationFrame(tick);
    },

    _onSelect() {
      this._observeContainers();
      for (const tab of gBrowser.tabs) {
        if (tab._zenWebKit) {
          native.setVisible(tab._zenWebKit, tab == gBrowser.selectedTab);
        }
      }
      const active = !!gBrowser.selectedTab._zenWebKit;
      document.documentElement.toggleAttribute("zen-webkit-active", active);
      if (!active) {
        this._clearHole();
      }
      this._sync();
      this._updatePassThrough();
    },

    _holeTargets() {
      return ["zen-browser-background"]
        .map(id => document.getElementById(id))
        .filter(Boolean);
    },

    _clearHole() {
      this._lastSyncKey = null;
      for (const el of this._holeTargets()) {
        el.style.removeProperty("clip-path");
      }
    },

    // Cut a rounded-rect hole out of Zen's background layers so the WebKit
    // view underneath shows through; Gecko UI still draws over the hole.
    _cutHole(r, radius) {
      for (const el of this._holeTargets()) {
        const b = el.getBoundingClientRect();
        const x = r.left - b.left;
        const y = r.top - b.top;
        const w = r.width;
        const h = r.height;
        const k = Math.min(radius, w / 2, h / 2);
        const hole =
          `M${x + k} ${y}H${x + w - k}A${k} ${k} 0 0 1 ${x + w} ${y + k}` +
          `V${y + h - k}A${k} ${k} 0 0 1 ${x + w - k} ${y + h}` +
          `H${x + k}A${k} ${k} 0 0 1 ${x} ${y + h - k}` +
          `V${y + k}A${k} ${k} 0 0 1 ${x + k} ${y}Z`;
        const outer = `M0 0H${b.width}V${b.height}H0Z`;
        el.style.clipPath = `path(evenodd, "${outer}${hole}")`;
      }
    },

    _sync() {
      const tab = gBrowser.selectedTab;
      if (!tab?._zenWebKit) {
        return;
      }
      const container =
        gBrowser.selectedBrowser.closest(".browserSidebarContainer") ||
        gBrowser.getBrowserContainer();
      const r = container.getBoundingClientRect();
      const radius = parseFloat(getComputedStyle(container).borderTopLeftRadius) || 0;
      const key = `${tab._zenWebKit}:${r.left},${r.top},${r.width},${r.height},${radius}`;
      if (key === this._lastSyncKey) {
        return;
      }
      this._lastSyncKey = key;
      try {
        // Corners are rounded by the hole in Gecko's rendering, not natively.
        native.setFrame(tab._zenWebKit, r.left, r.top, r.width, r.height, 0);
        this._cutHole(r, radius);
      } catch (e) {}
    },

    open(url) {
      const tab = gBrowser.selectedTab;
      this._destroy(tab);
      this._create(tab, url);
    },

    close() {
      this._destroy(gBrowser.selectedTab);
    },
  };

  window.gZenWebKit = gZenWebKit;
  const startupObserver = {
    observe(subject) {
      if (subject !== window) {
        return;
      }
      Services.obs.removeObserver(startupObserver, "browser-delayed-startup-finished");
      gZenWebKit.init();
    },
  };
  Services.obs.addObserver(startupObserver, "browser-delayed-startup-finished");
}
