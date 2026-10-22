// Bench control: one plain window with a WKWebView and no browser chrome,
// to measure the WebKit floor without Safari's UI or Zen's.
// Build: swiftc -O wk-control.swift -o wk-control
// Run:   wk-control <url> <x> <y> <width> <height>   (points, top-left origin)

import Cocoa
import WebKit

struct Options {
  let url: URL
  let x, y, w, h: Double
}

func parseOptions() -> Options {
  let args = CommandLine.arguments
  guard args.count >= 6, let url = URL(string: args[1]),
    let x = Double(args[2]), let y = Double(args[3]),
    let w = Double(args[4]), let h = Double(args[5])
  else {
    FileHandle.standardError.write("usage: wk-control <url> <x> <y> <w> <h>\n".data(using: .utf8)!)
    exit(2)
  }
  return Options(url: url, x: x, y: y, w: w, h: h)
}

final class AppDelegate: NSObject, NSApplicationDelegate {
  let options: Options
  var window: NSWindow!

  init(options: Options) {
    self.options = options
  }

  func applicationDidFinishLaunching(_ note: Notification) {
    let o = options
    let screen = NSScreen.main!.frame
    let frame = NSRect(x: o.x, y: screen.height - o.y - o.h, width: o.w, height: o.h)
    window = NSWindow(
      contentRect: frame, styleMask: [.titled, .closable, .resizable, .miniaturizable],
      backing: .buffered, defer: false)
    window.setFrame(frame, display: true)
    window.title = "wk-control"

    let config = WKWebViewConfiguration()
    config.applicationNameForUserAgent = "Version/26.0 Safari/605.1.15"
    config.mediaTypesRequiringUserActionForPlayback = []
    let view = WKWebView(frame: window.contentView!.bounds, configuration: config)
    view.autoresizingMask = [.width, .height]
    window.contentView!.addSubview(view)
    view.load(URLRequest(url: o.url))

    window.makeKeyAndOrderFront(nil)
    NSApp.activate(ignoringOtherApps: true)
  }

  func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }
}

let app = NSApplication.shared
let delegate = AppDelegate(options: parseOptions())
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
