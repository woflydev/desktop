/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

// Plain C interface to the WebKit-side code. WebKit's Obj-C headers declare
// @class JSContext, which collides with Gecko's JSContext, so no file may
// include both. Keep this header free of either.

#ifndef ZenWebKitBridge_h_
#define ZenWebKitBridge_h_

#ifdef __cplusplus
extern "C" {
#endif

// `nswindow` is an NSWindow*. Returns false on failure.
bool ZenWK_Create(void* nswindow, const char* id, const char* url);
bool ZenWK_SetFrame(const char* id, double x, double y, double w, double h,
                    double radius);
bool ZenWK_SetVisible(const char* id, bool visible);
void ZenWK_Destroy(const char* id);
// While true, clicks over a visible WebKit view fall through Gecko to it.
void ZenWK_SetPassThrough(bool passThrough);
// Moves keyboard focus between the WebKit page and Gecko UI.
void ZenWK_SetPageFocused(bool focused);
// Writes a UTF-8, NUL-terminated string into `out`. Returns false if no view.
bool ZenWK_GetUrl(const char* id, char* out, unsigned long outLen);
bool ZenWK_GetTitle(const char* id, char* out, unsigned long outLen);

#ifdef __cplusplus
}
#endif

#endif
