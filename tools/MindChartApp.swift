// MindChart's Mac app: one window that starts MindChart's local server and shows it, with no Terminal or browser.
// Showing MindChart in its own window (instead of a web browser) also keeps browser extensions, which can read
// everything on a page, away from patient data. Quitting stops the server, which locks the data.
import Cocoa
import WebKit

var signalSources: [DispatchSourceSignal] = []
let python = "/usr/bin/python3"  // Apple's Python: the same on every Mac, and what the tests run on
let preferredPort = 8642

/// Where MindChart's code lives. Installed copies use the standard place; a development build can point elsewhere.
func appHome() -> URL {
    if let custom = Bundle.main.object(forInfoDictionaryKey: "MindChartHome") as? String, !custom.isEmpty {
        return URL(fileURLWithPath: (custom as NSString).expandingTildeInPath)
    }
    return FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/MindChart/App")
}

/// A port nothing else is using: the usual one if it's free (so settings saved by the page carry over), otherwise any.
func pickPort() -> Int {
    func bindable(_ port: Int) -> Int? {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        defer { close(fd) }
        var address = sockaddr_in()
        address.sin_family = sa_family_t(AF_INET)
        address.sin_port = in_port_t(UInt16(port).bigEndian)
        address.sin_addr.s_addr = inet_addr("127.0.0.1")
        var length = socklen_t(MemoryLayout<sockaddr_in>.size)
        let ok = withUnsafeMutablePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(fd, $0, length) == 0 && getsockname(fd, $0, &length) == 0 }
        }
        return ok ? Int(UInt16(bigEndian: address.sin_port)) : nil
    }
    return bindable(preferredPort) ?? bindable(0) ?? preferredPort
}

func loadingPage(_ message: String, error: Bool = false) -> String {
    """
    <html><head><meta name="color-scheme" content="light dark"><style>
    body { margin: 0; height: 100vh; display: grid; place-items: center; font: 15px "Avenir Next", sans-serif;
           background: #f4ecdf; color: #2b2420; }
    @media (prefers-color-scheme: dark) { body { background: #1c1713; color: #f0e7d9; } }
    h1 { font: 500 30px Futura, sans-serif; margin: 0 0 8px; } div { text-align: center; max-width: 460px; padding: 24px; }
    .spin { width: 22px; height: 22px; margin: 18px auto 0; border: 3px solid #d6a02f55; border-top-color: #1f6f6a;
            border-radius: 50%; animation: s .9s linear infinite; } @keyframes s { to { transform: rotate(360deg); } }
    p { color: \(error ? "#b34a2b" : "inherit"); }
    </style></head><body><div><h1>MindChart</h1><p>\(message)</p>\(error ? "" : "<div class='spin'></div>")</div></body></html>
    """
}

class AppDelegate: NSObject, NSApplicationDelegate, WKUIDelegate, WKNavigationDelegate, WKDownloadDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var server: Process?
    var port = preferredPort
    var quitting = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 820),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "MindChart"
        window.minSize = NSSize(width: 900, height: 600)
        window.tabbingMode = .disallowed
        window.center()
        window.setFrameAutosaveName("MindChart")
        web = WKWebView(frame: .zero, configuration: WKWebViewConfiguration())
        web.uiDelegate = self
        web.navigationDelegate = self
        window.contentView = web
        show("Starting…")
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        DispatchQueue.global(qos: .userInitiated).async { self.startServer() }
    }

    func show(_ message: String, error: Bool = false) {
        DispatchQueue.main.async { self.web.loadHTMLString(loadingPage(message, error: error), baseURL: nil) }
    }

    func run(_ program: String, _ arguments: [String], in folder: URL? = nil, output: FileHandle? = nil) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: program)
        process.arguments = arguments
        process.currentDirectoryURL = folder
        process.standardOutput = output ?? FileHandle.nullDevice
        process.standardError = output ?? FileHandle.nullDevice
        try? process.run()
        process.waitUntilExit()
    }

    func startServer() {
        let home = appHome()
        guard FileManager.default.fileExists(atPath: home.appendingPathComponent("server.py").path) else {
            return show("MindChart's files weren't found in \(home.path). Please reinstall MindChart.", error: true)
        }
        run("/usr/bin/open", ["-g", "-a", "Ollama"])  // Ollama runs the AI model; start it quietly if needed
        // The updater's messages go in the log too, so a problem with updating can be found
        let logs = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/MindChart")
        try? FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
        let outputFile = logs.appendingPathComponent("app-output.log")
        FileManager.default.createFile(atPath: outputFile.path, contents: nil)
        let output = try? FileHandle(forWritingTo: outputFile)
        show("Checking for updates…")
        run(python, ["updater.py"], in: home, output: output)

        show("Starting…")
        port = pickPort()

        let process = Process()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = ["server.py"]
        process.currentDirectoryURL = home
        var environment = ProcessInfo.processInfo.environment
        environment["MINDCHART_PORT"] = String(port)
        environment["MINDCHART_APP"] = "1"  // the server watches for the app going away and locks the data
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process.environment = environment
        process.standardOutput = output ?? FileHandle.nullDevice
        process.standardError = output ?? FileHandle.nullDevice
        process.terminationHandler = { _ in
            if !self.quitting {
                self.show("MindChart stopped unexpectedly. Its error log is in your Library/Logs/MindChart folder; " +
                          "please send it along with what you were doing. Quit and reopen MindChart to continue.", error: true)
            }
        }
        do { try process.run() } catch { return show("MindChart couldn't start: \(error.localizedDescription)", error: true) }
        server = process

        for _ in 0..<120 where !quitting {  // wait up to a minute for it to answer
            if serverAnswers() {
                DispatchQueue.main.async { self.web.load(URLRequest(url: URL(string: "http://localhost:\(self.port)/")!)) }
                return
            }
            usleep(500_000)
        }
        show("MindChart is taking too long to start. Quit and reopen it, and if that doesn't help, send the error log.", error: true)
    }

    func serverAnswers() -> Bool {
        var request = URLRequest(url: URL(string: "http://localhost:\(port)/api/session")!, timeoutInterval: 2)
        request.setValue("1", forHTTPHeaderField: "X-MindChart")
        let done = DispatchSemaphore(value: 0)
        var ok = false
        URLSession.shared.dataTask(with: request) { _, response, _ in
            ok = (response as? HTTPURLResponse)?.statusCode == 200
            done.signal()
        }.resume()
        _ = done.wait(timeout: .now() + 3)
        return ok
    }

    // Quitting stops the server, which locks the data on the way out.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        quitting = true
        guard let server = server, server.isRunning else { return }
        server.terminate()
        let deadline = Date().addingTimeInterval(8)
        while server.isRunning && Date() < deadline { usleep(100_000) }
    }

    // MARK: Things a browser normally does: choosing files, dialogs, links and saving downloads.

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = false
        panel.beginSheetModal(for: window) { completionHandler($0 == .OK ? panel.urls : nil) }
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        alert.beginSheetModal(for: window) { completionHandler($0 == .alertFirstButtonReturn) }
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if action.shouldPerformDownload { return decisionHandler(.download) }
        if let url = action.request.url, ["http", "https"].contains(url.scheme ?? ""), url.host != "localhost" {
            NSWorkspace.shared.open(url)  // links to other websites open in the normal browser
            return decisionHandler(.cancel)
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        decisionHandler(response.canShowMIMEType ? .allow : .download)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                  completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = suggestedFilename
        panel.directoryURL = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
        panel.beginSheetModal(for: window) { choice in
            guard choice == .OK, let url = panel.url else { return completionHandler(nil) }
            try? FileManager.default.removeItem(at: url)  // the person already agreed to replace it
            completionHandler(url)
        }
    }

    // MARK: Menus, so the usual keyboard shortcuts (copy, paste, undo, quit…) work.

    func buildMenu() {
        let menu = NSMenu()
        func add(_ title: String, _ items: [(String, Selector?, String)]) {
            let item = NSMenuItem()
            let submenu = NSMenu(title: title)
            for (name, action, key) in items {
                submenu.addItem(name == "-" ? .separator() : NSMenuItem(title: name, action: action, keyEquivalent: key))
            }
            item.submenu = submenu
            menu.addItem(item)
        }
        add("MindChart", [("About MindChart", #selector(NSApplication.orderFrontStandardAboutPanel(_:)), ""), ("-", nil, ""),
                          ("Hide MindChart", #selector(NSApplication.hide(_:)), "h"), ("-", nil, ""),
                          ("Quit MindChart", #selector(NSApplication.terminate(_:)), "q")])
        add("Edit", [("Undo", Selector(("undo:")), "z"), ("Redo", Selector(("redo:")), "Z"), ("-", nil, ""),
                     ("Cut", #selector(NSText.cut(_:)), "x"), ("Copy", #selector(NSText.copy(_:)), "c"),
                     ("Paste", #selector(NSText.paste(_:)), "v"), ("Select All", #selector(NSText.selectAll(_:)), "a")])
        add("View", [("Reload", #selector(reload), "r")])
        add("Window", [("Minimize", #selector(NSWindow.performMiniaturize(_:)), "m"),
                       ("Close", #selector(NSWindow.performClose(_:)), "w")])
        NSApp.mainMenu = menu
    }

    @objc func reload() { web.reload() }
}

let app = NSApplication.shared
let delegate = AppDelegate()
// Being told to stop (by logging out, a shutdown or "kill") is handled like ⌘Q, so the server stops and locks the data.
for stopSignal in [SIGTERM, SIGHUP, SIGINT] {
    signal(stopSignal, SIG_IGN)
    let source = DispatchSource.makeSignalSource(signal: stopSignal, queue: .main)
    source.setEventHandler { NSApp.terminate(nil) }
    source.resume()
    signalSources.append(source)
}
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
