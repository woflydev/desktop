// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at http://mozilla.org/MPL/2.0/.

/**
 * Efficiency mode: cuts work the user cannot see, without changing what they
 * look at. Behind `zen.efficiency-mode.enabled`; each part has its own pref.
 *
 *  - Tab sleep: unloads background tabs that have not been used for
 *    `zen.efficiency-mode.tab-sleep.timeout-minutes`. Tabs are picked by
 *    Firefox's TabUnloader, which never unloads the selected tab or tabs
 *    playing audio, sharing camera/mic/screen, in picture-in-picture or in
 *    private windows. On top of that, visible tabs (split view, glance), tabs
 *    whose site may show notifications, and pinned tabs/essentials (unless
 *    `include-pinned`) are kept. Pages with unsaved changes are never unloaded.
 *  - Inactive animations: decorative chrome animations pause while Zen is
 *    not the active app (zen-efficiency-mode.css).
 */

import { XPCOMUtils } from "resource://gre/modules/XPCOMUtils.sys.mjs";

const lazy = {};
ChromeUtils.defineESModuleGetters(lazy, {
  TabUnloader: "moz-src:///browser/components/tabbrowser/TabUnloader.sys.mjs",
});

const PREF_ENABLED = "zen.efficiency-mode.enabled";
const PREF_TAB_SLEEP = "zen.efficiency-mode.tab-sleep.enabled";
const PREF_TAB_SLEEP_TIMEOUT = "zen.efficiency-mode.tab-sleep.timeout-minutes";
const PREF_TAB_SLEEP_PINNED = "zen.efficiency-mode.tab-sleep.include-pinned";
const PREF_TAB_SLEEP_INTERVAL =
  "zen.efficiency-mode.tab-sleep.check-interval-seconds";
const PREF_PAUSE_ANIMATIONS = "zen.efficiency-mode.pause-inactive-animations";

XPCOMUtils.defineLazyPreferenceGetter(lazy, "enabled", PREF_ENABLED, false);
XPCOMUtils.defineLazyPreferenceGetter(
  lazy,
  "tabSleepEnabled",
  PREF_TAB_SLEEP,
  true
);
XPCOMUtils.defineLazyPreferenceGetter(
  lazy,
  "tabSleepTimeoutMinutes",
  PREF_TAB_SLEEP_TIMEOUT,
  60
);
XPCOMUtils.defineLazyPreferenceGetter(
  lazy,
  "tabSleepIncludePinned",
  PREF_TAB_SLEEP_PINNED,
  false
);
XPCOMUtils.defineLazyPreferenceGetter(
  lazy,
  "tabSleepIntervalSeconds",
  PREF_TAB_SLEEP_INTERVAL,
  300
);
XPCOMUtils.defineLazyPreferenceGetter(
  lazy,
  "pauseAnimations",
  PREF_PAUSE_ANIMATIONS,
  true
);

class nsZenEfficiencyMode {
  #windows = new Set();
  #tabSleepTimer = null;
  #sleeping = false;
  #initialized = false;

  onWindowReady(aWindow) {
    if (!this.#initialized) {
      this.#initialized = true;
      for (const pref of [
        PREF_ENABLED,
        PREF_TAB_SLEEP,
        PREF_TAB_SLEEP_INTERVAL,
        PREF_PAUSE_ANIMATIONS,
      ]) {
        Services.prefs.addObserver(pref, this);
      }
    }
    this.#windows.add(aWindow);
    aWindow.addEventListener("unload", () => this.#windows.delete(aWindow), {
      once: true,
    });
    this.#update();
  }

  observe(aSubject, aTopic, aData) {
    if (aData === PREF_TAB_SLEEP_INTERVAL) {
      this.#stopTabSleepTimer();
    }
    this.#update();
  }

  #stopTabSleepTimer() {
    this.#tabSleepTimer?.cancel();
    this.#tabSleepTimer = null;
  }

  #update() {
    const animations = lazy.enabled && lazy.pauseAnimations;
    for (const win of this.#windows) {
      win.document.documentElement.toggleAttribute(
        "zen-efficiency-pause-animations",
        animations
      );
    }
    const tabSleep = lazy.enabled && lazy.tabSleepEnabled;
    if (tabSleep && !this.#tabSleepTimer) {
      this.#tabSleepTimer = Cc["@mozilla.org/timer;1"].createInstance(
        Ci.nsITimer
      );
      this.#tabSleepTimer.initWithCallback(
        () => this.sleepInactiveTabs(),
        lazy.tabSleepIntervalSeconds * 1000,
        Ci.nsITimer.TYPE_REPEATING_SLACK_LOW_PRIORITY
      );
    } else if (!tabSleep) {
      this.#stopTabSleepTimer();
    }
  }

  #mayShowNotifications(aTab) {
    const principal = aTab.linkedBrowser?.contentPrincipal;
    if (!principal) {
      return false;
    }
    return (
      Services.perms.testExactPermissionFromPrincipal(
        principal,
        "desktop-notification"
      ) === Services.perms.ALLOW_ACTION
    );
  }

  #shouldKeep(aTab) {
    if (aTab.linkedBrowser?.docShellIsActive) {
      return true;
    }
    if (
      !lazy.tabSleepIncludePinned &&
      (aTab.pinned || aTab.hasAttribute("zen-essential"))
    ) {
      return true;
    }
    return this.#mayShowNotifications(aTab);
  }

  /**
   * Unloads every tab that has been inactive for longer than the timeout and
   * that TabUnloader and the rules above allow.
   *
   * @returns {Promise<number>} How many tabs were unloaded.
   */
  async sleepInactiveTabs() {
    if (this.#sleeping || !lazy.enabled || !lazy.tabSleepEnabled) {
      return 0;
    }
    this.#sleeping = true;
    let unloaded = 0;
    try {
      const timeoutMs = lazy.tabSleepTimeoutMinutes * 60 * 1000;
      const candidates = await lazy.TabUnloader.getSortedTabs(timeoutMs);
      for (const candidate of candidates) {
        const { tab, gBrowser } = candidate;
        if (
          !lazy.TabUnloader.isDiscardable(candidate) ||
          !tab.linkedPanel ||
          this.#shouldKeep(tab)
        ) {
          continue;
        }
        if (gBrowser.discardBrowser(tab)) {
          unloaded++;
        }
      }
    } catch (e) {
      console.error("ZenEfficiencyMode: tab sleep failed", e);
    } finally {
      this.#sleeping = false;
    }
    return unloaded;
  }
}

export const ZenEfficiencyMode = new nsZenEfficiencyMode();
