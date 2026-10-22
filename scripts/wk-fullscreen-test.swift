import Cocoa
import WebKit

// Plain WKWebView inset inside a window, mirroring the Zen layout, to see how
// stock WebKit element fullscreen enters and exits. Build:
//   swiftc -O scripts/wk-fullscreen-test.swift -o /tmp/wk-fullscreen-test
// Run: /tmp/wk-fullscreen-test [url]
final class AppDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        let appItem = NSMenuItem()
        menu.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        NSApp.mainMenu = menu

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1280, height: 975),
            styleMask: [.titled, .closable, .resizable, .miniaturizable],
            backing: .buffered, defer: false)
        window.title = "WKWebView fullscreen test"
        window.center()

        let config = WKWebViewConfiguration()
        config.applicationNameForUserAgent = "Version/18.0 Safari/605.1.15"
        config.preferences.setValue(true, forKey: "elementFullscreenEnabled")

        let frame = NSRect(x: 230, y: 8, width: 1042, height: 959 - 28)
        let web = WKWebView(frame: frame, configuration: config)
        web.autoresizingMask = [.width, .height]
        window.contentView?.addSubview(web)

        let urlString = CommandLine.arguments.count > 1
            ? CommandLine.arguments[1]
            : "https://www.youtube.com/watch?v=jNQXAC9IVRw"
        web.load(URLRequest(url: URL(string: urlString)!))

        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = AppDelegate()
app.delegate = delegate
app.run()
