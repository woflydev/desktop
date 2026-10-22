/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

// Deliberately includes no Gecko headers; see ZenWebKitBridge.h.

#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>
#import <objc/message.h>
#import <objc/runtime.h>

#include "ZenWebKitBridge.h"

static NSMutableDictionary<NSString*, WKWebView*>* Views() {
  static NSMutableDictionary* sViews = [[NSMutableDictionary alloc] init];
  return sViews;
}

static bool sPassThrough = true;
static bool sPageFocused = true;

static NSView* FindChildView(NSView* v) {
  if ([NSStringFromClass([v class]) isEqualToString:@"ChildView"]) {
    return v;
  }
  for (NSView* sub in v.subviews) {
    if (NSView* found = FindChildView(sub)) {
      return found;
    }
  }
  return nil;
}

// Gecko's ChildView fills the window and, being on top, would swallow every
// click meant for the WebKit view beneath it. Return nil from its hit test
// over a visible WebKit view so AppKit falls through to the next sibling.
static void InstallHitTestSwizzle() {
  static dispatch_once_t once;
  dispatch_once(&once, ^{
    Class cls = NSClassFromString(@"ChildView");
    SEL sel = @selector(hitTest:);
    Method m = cls ? class_getInstanceMethod(cls, sel) : nullptr;
    if (!m) {
      return;
    }
    typedef NSView* (*HitTestFn)(id, SEL, NSPoint);
    HitTestFn orig = (HitTestFn)method_getImplementation(m);
    IMP replacement = imp_implementationWithBlock(^NSView*(NSView* self,
                                                           NSPoint p) {
      if (sPassThrough) {
        for (WKWebView* v in Views().allValues) {
          if (!v.hidden && v.superview == self.superview &&
              NSPointInRect(p, v.frame)) {
            return nil;
          }
        }
      }
      return orig(self, sel, p);
    });
    // addMethod fails if the class already has its own hitTest:, in which
    // case replace it; otherwise this avoids touching NSView itself.
    if (!class_addMethod(cls, sel, replacement, method_getTypeEncoding(m))) {
      method_setImplementation(m, replacement);
    }
  });
}

// AppKit does not consult hitTest: when dispatching clicks and scrolls on
// current macOS; it delivers them to Gecko's ChildView regardless. So forward
// pointer events that land on a visible WebKit view directly to it.
static WKWebView* sForwardTarget = nil;  // sticky for a press/drag sequence

static WKWebView* WebKitViewAt(NSView* geckoView, NSEvent* e) {
  if (!sPassThrough) {
    return nil;
  }
  NSPoint p = [geckoView.superview convertPoint:e.locationInWindow fromView:nil];
  for (WKWebView* v in Views().allValues) {
    if (!v.hidden && v.superview == geckoView.superview &&
        NSPointInRect(p, v.frame)) {
      return v;
    }
  }
  return nil;
}

static void InstallEventForwarders() {
  static dispatch_once_t once;
  dispatch_once(&once, ^{
    Class cls = NSClassFromString(@"ChildView");
    if (!cls) {
      return;
    }
    struct Fwd {
      const char* name;
      int kind;  // 0 = pointer (hit-tested), 1 = press (sets sticky), 2 = drag/up (uses sticky)
    };
    static const Fwd kEvents[] = {
        {"mouseDown:", 1},        {"rightMouseDown:", 1},
        {"otherMouseDown:", 1},   {"mouseDragged:", 2},
        {"rightMouseDragged:", 2}, {"otherMouseDragged:", 2},
        {"mouseUp:", 2},          {"rightMouseUp:", 2},
        {"otherMouseUp:", 2},     {"scrollWheel:", 0},
        {"magnifyWithEvent:", 0}, {"smartMagnifyWithEvent:", 0},
        {"rotateWithEvent:", 0},  {"swipeWithEvent:", 0},
        {"beginGestureWithEvent:", 0}, {"endGestureWithEvent:", 0},
        {"pressureChangeWithEvent:", 0},
    };
    for (const Fwd& f : kEvents) {
      SEL sel = sel_registerName(f.name);
      Method m = class_getInstanceMethod(cls, sel);
      if (!m) {
        continue;
      }
      typedef void (*EventFn)(id, SEL, NSEvent*);
      EventFn orig = (EventFn)method_getImplementation(m);
      int kind = f.kind;
      IMP replacement = imp_implementationWithBlock(^void(NSView* self, NSEvent* e) {
        WKWebView* target = nil;
        if (kind == 2) {
          target = sForwardTarget;
        } else {
          target = WebKitViewAt(self, e);
          if (kind == 1) {
            sForwardTarget = target;
          }
        }
        if (target && target.superview && [target respondsToSelector:sel]) {
          bool isPress = kind == 1;
          bool isRelease = kind == 2 && (e.type == NSEventTypeLeftMouseUp ||
                                         e.type == NSEventTypeRightMouseUp ||
                                         e.type == NSEventTypeOtherMouseUp);
          if (isPress || isRelease) {
            orig(self, sel, e);
          }
          ((EventFn)objc_msgSend)(target, sel, e);
          if (isPress) {
            sPageFocused = true;
            [target.window makeFirstResponder:target];
          }
          if (kind == 2 && (e.type == NSEventTypeLeftMouseUp ||
                            e.type == NSEventTypeRightMouseUp ||
                            e.type == NSEventTypeOtherMouseUp)) {
            sForwardTarget = nil;
          }
          return;
        }
        orig(self, sel, e);
      });
      if (!class_addMethod(cls, sel, replacement, method_getTypeEncoding(m))) {
        method_setImplementation(m, replacement);
      }
    }
  });
}

static NSArray<NSValue*>* SubtractRect(NSArray<NSValue*>* rects, NSRect hole) {
  NSMutableArray<NSValue*>* out = [NSMutableArray array];
  for (NSValue* value in rects) {
    NSRect r = value.rectValue;
    NSRect i = NSIntersectionRect(r, hole);
    if (NSIsEmptyRect(i)) {
      [out addObject:value];
      continue;
    }
    NSRect parts[4] = {
        NSMakeRect(r.origin.x, r.origin.y, r.size.width, i.origin.y - r.origin.y),
        NSMakeRect(r.origin.x, NSMaxY(i), r.size.width, NSMaxY(r) - NSMaxY(i)),
        NSMakeRect(r.origin.x, i.origin.y, i.origin.x - r.origin.x, i.size.height),
        NSMakeRect(NSMaxX(i), i.origin.y, NSMaxX(r) - NSMaxX(i), i.size.height),
    };
    for (NSRect part : parts) {
      if (!NSIsEmptyRect(part)) {
        [out addObject:[NSValue valueWithRect:part]];
      }
    }
  }
  return out;
}

// ChildView covers the window with a cursor rect for Gecko's own cursor, which
// would hide the cursor WebKit sets. Leave the WebKit area out of it.
static void InstallCursorRectOverride() {
  static dispatch_once_t once;
  dispatch_once(&once, ^{
    Class cls = NSClassFromString(@"ChildView");
    SEL sel = @selector(resetCursorRects);
    Method m = cls ? class_getInstanceMethod(cls, sel) : nullptr;
    if (!m) {
      return;
    }
    typedef void (*Fn)(id, SEL);
    Fn orig = (Fn)method_getImplementation(m);
    IMP replacement = imp_implementationWithBlock(^void(NSView* self) {
      NSArray<NSValue*>* rects = @[ [NSValue valueWithRect:self.bounds] ];
      bool carved = false;
      if (sPassThrough) {
        for (WKWebView* v in Views().allValues) {
          if (!v.hidden && v.superview == self.superview) {
            rects = SubtractRect(
                rects, [self convertRect:v.frame fromView:self.superview]);
            carved = true;
          }
        }
      }
      NSCursor* cursor = [NSClassFromString(@"MOZDynamicCursor")
          performSelector:NSSelectorFromString(@"sharedInstance")];
      if (!carved || !cursor) {
        orig(self, sel);
        return;
      }
      [self discardCursorRects];
      for (NSValue* r in rects) {
        [self addCursorRect:r.rectValue cursor:cursor];
      }
    });
    if (!class_addMethod(cls, sel, replacement, method_getTypeEncoding(m))) {
      method_setImplementation(m, replacement);
    }
  });
}

static void InvalidateGeckoCursorRects(NSWindow* window) {
  if (NSView* gecko = FindChildView(window.contentView)) {
    [window invalidateCursorRectsForView:gecko];
  }
}

static bool KeyBelongsToGecko(NSEvent* e) {
  NSEventModifierFlags mods = e.modifierFlags;
  if (!(mods & (NSEventModifierFlagCommand | NSEventModifierFlagControl))) {
    return false;
  }
  if ((mods & NSEventModifierFlagCommand) && e.type != NSEventTypeFlagsChanged) {
    NSString* c = e.charactersIgnoringModifiers.lowercaseString;
    for (NSString* pageKey in @[ @"c", @"x", @"v", @"a", @"z", @"y" ]) {
      if ([c isEqualToString:pageKey]) {
        return false;
      }
    }
  }
  return true;
}

// WebKit has first responder while the page is focused, so Zen's own
// shortcuts (Cmd+L, Ctrl+Tab, ...) would never reach Gecko. Route those key
// events to Gecko instead, keeping edit commands and plain typing for the page.
static void InstallKeyRouting() {
  static dispatch_once_t once;
  dispatch_once(&once, ^{
    Class cls = NSClassFromString(@"WKWebView");
    static NSMutableSet<NSNumber*>* sGeckoKeys = [[NSMutableSet alloc] init];
    // Without this AppKit consumes Ctrl+Tab for its key-view loop; Gecko's
    // ChildView does the same to receive it.
    class_addMethod(cls, NSSelectorFromString(@"_wantsKeyDownForEvent:"),
                    imp_implementationWithBlock(^BOOL(NSView*, NSEvent*) {
                      return YES;
                    }),
                    "c@:@");

    SEL equivSel = @selector(performKeyEquivalent:);
    Method equivMethod = cls ? class_getInstanceMethod(cls, equivSel) : nullptr;
    if (equivMethod) {
      typedef BOOL (*EquivFn)(NSObject*, SEL, NSEvent*);
      EquivFn equivOrig = (EquivFn)method_getImplementation(equivMethod);
      // WebKit claims other key equivalents without ever delivering keyDown:,
      // so tab-switching keys have to be taken before it sees them.
      IMP equivReplacement =
          imp_implementationWithBlock(^BOOL(NSView* self, NSEvent* e) {
            NSEventModifierFlags mods =
                e.modifierFlags & NSEventModifierFlagDeviceIndependentFlagsMask;
            bool ctrlOnly = (mods & NSEventModifierFlagControl) &&
                            !(mods & NSEventModifierFlagCommand);
            if (e.type == NSEventTypeKeyDown && ctrlOnly &&
                (e.keyCode == 48 || e.keyCode == 116 || e.keyCode == 121)) {
              NSView* gecko = FindChildView(self.window.contentView);
              if (gecko) {
                [sGeckoKeys addObject:@(e.keyCode)];
                ((void (*)(id, SEL, NSEvent*))objc_msgSend)(
                    gecko, @selector(keyDown:), e);
                return YES;
              }
            }
            return equivOrig(self, equivSel, e);
          });
      if (!class_addMethod(cls, equivSel, equivReplacement,
                           method_getTypeEncoding(equivMethod))) {
        method_setImplementation(equivMethod, equivReplacement);
      }
    }
    for (NSString* name in @[ @"keyDown:", @"keyUp:", @"flagsChanged:" ]) {
      SEL sel = NSSelectorFromString(name);
      Method m = cls ? class_getInstanceMethod(cls, sel) : nullptr;
      if (!m) {
        continue;
      }
      typedef void (*EventFn)(NSObject*, SEL, NSEvent*);
      EventFn orig = (EventFn)method_getImplementation(m);
      IMP replacement = imp_implementationWithBlock(^void(NSView* self, NSEvent* e) {
        NSView* gecko = FindChildView(self.window.contentView);
        if (gecko) {
          NSNumber* code = @(e.keyCode);
          if (e.type == NSEventTypeFlagsChanged) {
            ((EventFn)objc_msgSend)(gecko, sel, e);
          } else if (e.type == NSEventTypeKeyUp && [sGeckoKeys containsObject:code]) {
            [sGeckoKeys removeObject:code];
            ((EventFn)objc_msgSend)(gecko, sel, e);
            return;
          } else if (e.type == NSEventTypeKeyDown && KeyBelongsToGecko(e)) {
            [sGeckoKeys addObject:code];
            ((EventFn)objc_msgSend)(gecko, sel, e);
            return;
          }
        }
        orig(self, sel, e);
      });
      if (!class_addMethod(cls, sel, replacement, method_getTypeEncoding(m))) {
        method_setImplementation(m, replacement);
      }
    }
  });
}

static WKWebView* ViewFor(const char* id) {
  return Views()[[NSString stringWithUTF8String:id]];
}

static bool CopyOut(NSString* str, char* out, unsigned long outLen) {
  if (!outLen) {
    return false;
  }
  const char* utf8 = (str ?: @"").UTF8String;
  strlcpy(out, utf8, outLen);
  return true;
}

static NSMutableDictionary<NSString*, NSValue*>* DesiredFrames() {
  static NSMutableDictionary* sFrames = [[NSMutableDictionary alloc] init];
  return sFrames;
}

static bool InWebKitFullscreen(WKWebView* view) {
  if (@available(macOS 13.0, *)) {
    return view.fullscreenState != WKFullscreenStateNotInFullscreen;
  }
  return false;
}

// After WebKit's fullscreen window hands the view back it may be on top of
// Gecko's layer and at the fullscreen frame; put it back where it belongs.
static void RestoreAfterFullscreen(WKWebView* view) {
  NSString* key = [Views() allKeysForObject:view].firstObject;
  NSView* gecko = view.window ? FindChildView(view.window.contentView) : nil;
  if (!key || !gecko || !gecko.superview) {
    return;
  }
  [view removeFromSuperview];
  [gecko.superview addSubview:view positioned:NSWindowBelow relativeTo:gecko];
  if (NSValue* frame = DesiredFrames()[key]) {
    view.frame = frame.rectValue;
  }
  [view.window invalidateCursorRectsForView:gecko];
  [view.window makeFirstResponder:(sPageFocused && !view.hidden) ? (NSView*)view : gecko];
}

@interface ZenWKFullscreenObserver : NSObject
@end

@implementation ZenWKFullscreenObserver
- (void)observeValueForKeyPath:(NSString*)keyPath
                      ofObject:(id)object
                        change:(NSDictionary*)change
                       context:(void*)context {
  WKWebView* view = (WKWebView*)object;
  if (!InWebKitFullscreen(view)) {
    dispatch_async(dispatch_get_main_queue(), ^{
      RestoreAfterFullscreen(view);
    });
  }
}
@end

static ZenWKFullscreenObserver* FullscreenObserver() {
  static ZenWKFullscreenObserver* sObserver = [[ZenWKFullscreenObserver alloc] init];
  return sObserver;
}

bool ZenWK_Create(void* nswindow, const char* id, const char* url) {
  NSWindow* window = (NSWindow*)nswindow;
  if (!window || !window.contentView) {
    return false;
  }
  NSString* key = [NSString stringWithUTF8String:id];
  if (Views()[key]) {
    return true;
  }

  WKWebViewConfiguration* config = [[WKWebViewConfiguration alloc] init];
  // Present as Safari so sites do not serve app-webview fallbacks.
  config.applicationNameForUserAgent = @"Version/18.0 Safari/605.1.15";
  for (NSString* fullscreenKey in @[ @"elementFullscreenEnabled", @"fullScreenEnabled" ]) {
    @try {
      [config.preferences setValue:@YES forKey:fullscreenKey];
    } @catch (NSException*) {
    }
  }
  WKWebView* view = [[WKWebView alloc] initWithFrame:NSMakeRect(0, 0, 1, 1)
                                       configuration:config];
  [config release];
  if (@available(macOS 13.3, *)) {
    view.inspectable = YES;
  }
  view.allowsBackForwardNavigationGestures = YES;
  view.wantsLayer = YES;
  view.layer.masksToBounds = YES;
  // Sit directly beneath Gecko's view so chrome (urlbar, glance, ...) can
  // draw over the page through a transparent hole in Gecko's rendering.
  NSView* gecko = FindChildView(window.contentView);
  if (gecko && gecko.superview) {
    [gecko.superview addSubview:view
                     positioned:NSWindowBelow
                     relativeTo:gecko];
    InstallHitTestSwizzle();
    InstallEventForwarders();
    InstallKeyRouting();
    InstallCursorRectOverride();
  } else {
    [window.contentView addSubview:view
                        positioned:NSWindowAbove
                        relativeTo:nil];
  }

  NSURL* nsurl = [NSURL URLWithString:[NSString stringWithUTF8String:url]];
  if (nsurl) {
    [view loadRequest:[NSURLRequest requestWithURL:nsurl]];
  }
  Views()[key] = view;
  if (@available(macOS 13.0, *)) {
    [view addObserver:FullscreenObserver()
           forKeyPath:@"fullscreenState"
              options:NSKeyValueObservingOptionNew
              context:nullptr];
  }
  [view release];
  return true;
}

bool ZenWK_SetFrame(const char* id, double x, double y, double w, double h,
                    double radius) {
  WKWebView* view = ViewFor(id);
  if (!view || !view.superview) {
    return false;
  }
  NSView* parent = view.superview;
  CGFloat ny = parent.isFlipped ? y : parent.bounds.size.height - y - h;
  NSRect frame = NSMakeRect(x, ny, w, h);
  DesiredFrames()[[NSString stringWithUTF8String:id]] = [NSValue valueWithRect:frame];
  if (InWebKitFullscreen(view)) {
    return true;
  }
  bool moved = !NSEqualRects(view.frame, frame);
  view.frame = frame;
  view.layer.cornerRadius = radius;
  if (moved) {
    InvalidateGeckoCursorRects(view.window);
  }
  return true;
}

bool ZenWK_SetVisible(const char* id, bool visible) {
  WKWebView* view = ViewFor(id);
  if (!view) {
    return false;
  }
  if (!visible && !view.hidden) {
    NSResponder* responder = view.window.firstResponder;
    NSView* gecko = FindChildView(view.window.contentView);
    if (gecko && [responder isKindOfClass:[NSView class]] &&
        [(NSView*)responder isDescendantOf:view]) {
      [view.window makeFirstResponder:gecko];
    }
  }
  view.hidden = !visible;
  InvalidateGeckoCursorRects(view.window);
  if (visible && sPageFocused) {
    [view.window makeFirstResponder:view];
  }
  return true;
}

void ZenWK_Destroy(const char* id) {
  NSString* key = [NSString stringWithUTF8String:id];
  WKWebView* view = Views()[key];
  if (view) {
    NSWindow* window = view.window;
    if (@available(macOS 13.0, *)) {
      [view removeObserver:FullscreenObserver() forKeyPath:@"fullscreenState"];
    }
    [DesiredFrames() removeObjectForKey:key];
    [view stopLoading];
    [view removeFromSuperview];
    if (window) {
      InvalidateGeckoCursorRects(window);
    }
    [Views() removeObjectForKey:key];
  }
}

bool ZenWK_GetUrl(const char* id, char* out, unsigned long outLen) {
  WKWebView* view = ViewFor(id);
  return view && CopyOut(view.URL.absoluteString, out, outLen);
}

bool ZenWK_GetTitle(const char* id, char* out, unsigned long outLen) {
  WKWebView* view = ViewFor(id);
  return view && CopyOut(view.title, out, outLen);
}

void ZenWK_SetPassThrough(bool passThrough) {
  if (sPassThrough == passThrough) {
    return;
  }
  sPassThrough = passThrough;
  for (WKWebView* v in Views().allValues) {
    if (v.window) {
      InvalidateGeckoCursorRects(v.window);
    }
  }
}

void ZenWK_SetPageFocused(bool focused) {
  sPageFocused = focused;
  for (WKWebView* v in Views().allValues) {
    NSWindow* window = v.window;
    if (!window) {
      continue;
    }
    if (focused && !v.hidden) {
      [window makeFirstResponder:v];
    } else if (!focused) {
      if (NSView* gecko = FindChildView(window.contentView)) {
        [window makeFirstResponder:gecko];
      }
    }
  }
}
