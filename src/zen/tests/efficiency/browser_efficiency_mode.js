/* Any copyright is dedicated to the Public Domain.
   https://creativecommons.org/publicdomain/zero/1.0/ */

"use strict";

const { ZenEfficiencyMode } = ChromeUtils.importESModule(
  "resource:///modules/zen/efficiency/ZenEfficiencyMode.sys.mjs"
);

const TEST_URL = "https://example.com/";
const LONG_AGO = Date.now() - 2 * 60 * 60 * 1000;

async function addStaleTab(url = TEST_URL) {
  const tab = BrowserTestUtils.addTab(gBrowser, url);
  await BrowserTestUtils.browserLoaded(tab.linkedBrowser);
  tab.updateLastAccessed(LONG_AGO);
  return tab;
}

add_task(async function test_off_by_default() {
  Assert.ok(
    !Services.prefs.getBoolPref("zen.efficiency-mode.enabled"),
    "efficiency mode is off by default"
  );
  Assert.ok(
    !document.documentElement.hasAttribute("zen-efficiency-pause-animations"),
    "animations are not paused while off"
  );

  const tab = await addStaleTab();
  Assert.equal(
    await ZenEfficiencyMode.sleepInactiveTabs(),
    0,
    "nothing sleeps while off"
  );
  Assert.ok(tab.linkedPanel, "stale tab stays loaded while off");
  BrowserTestUtils.removeTab(tab);
});

add_task(async function test_pause_animations_attribute() {
  await SpecialPowers.pushPrefEnv({
    set: [["zen.efficiency-mode.enabled", true]],
  });
  Assert.ok(
    document.documentElement.hasAttribute("zen-efficiency-pause-animations"),
    "animations pause attribute set when enabled"
  );

  await SpecialPowers.pushPrefEnv({
    set: [["zen.efficiency-mode.pause-inactive-animations", false]],
  });
  Assert.ok(
    !document.documentElement.hasAttribute("zen-efficiency-pause-animations"),
    "sub-pref turns the attribute off"
  );
  await SpecialPowers.popPrefEnv();
  await SpecialPowers.popPrefEnv();
  Assert.ok(
    !document.documentElement.hasAttribute("zen-efficiency-pause-animations"),
    "attribute removed when efficiency mode is turned off again"
  );
});

add_task(async function test_tab_sleep() {
  await SpecialPowers.pushPrefEnv({
    set: [
      ["zen.efficiency-mode.enabled", true],
      ["zen.efficiency-mode.tab-sleep.timeout-minutes", 30],
    ],
  });

  const stale = await addStaleTab();
  const fresh = await addStaleTab();
  fresh.updateLastAccessed();
  const pinned = await addStaleTab();
  gBrowser.pinTab(pinned);
  pinned.updateLastAccessed(LONG_AGO);

  const notifying = await addStaleTab("https://example.org/");
  const principal = notifying.linkedBrowser.contentPrincipal;
  Services.perms.addFromPrincipal(
    principal,
    "desktop-notification",
    Services.perms.ALLOW_ACTION
  );

  await ZenEfficiencyMode.sleepInactiveTabs();

  Assert.ok(
    !stale.linkedPanel,
    "tab unused for longer than the timeout is unloaded"
  );
  Assert.ok(fresh.linkedPanel, "recently used tab stays loaded");
  Assert.ok(pinned.linkedPanel, "pinned tab stays loaded by default");
  Assert.ok(
    notifying.linkedPanel,
    "tab allowed to show notifications stays loaded"
  );
  Assert.ok(gBrowser.selectedTab.linkedPanel, "selected tab stays loaded");

  await SpecialPowers.pushPrefEnv({
    set: [["zen.efficiency-mode.tab-sleep.include-pinned", true]],
  });
  await ZenEfficiencyMode.sleepInactiveTabs();
  Assert.ok(
    !pinned.linkedPanel,
    "pinned tab unloads when include-pinned is set"
  );
  await SpecialPowers.popPrefEnv();

  Services.perms.removeFromPrincipal(principal, "desktop-notification");
  gBrowser.unpinTab(pinned);
  for (const tab of [stale, fresh, pinned, notifying]) {
    BrowserTestUtils.removeTab(tab);
  }
  await SpecialPowers.popPrefEnv();
});
