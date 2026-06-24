import SwiftUI
import WebKit

/// Eingebettete Browser-Vorschau (F359): ein echter `WKWebView`, der eine lokale Dev-Server-URL
/// lädt und sie regelmäßig neu lädt (Live-Reload), sodass Änderungen am Dev-Server automatisch in
/// der Vorschau erscheinen. Der Reload umgeht den Cache, um geänderte Inhalte zu übernehmen.
struct WebPreview: NSViewRepresentable {
    let url: URL
    /// Reload-Intervall in Sekunden (Live-Reload eines Dev-Servers).
    var reloadInterval: TimeInterval = 1.0

    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        let webView = WKWebView(frame: .zero, configuration: config)
        webView.load(noCacheRequest())
        context.coordinator.start(webView: webView, interval: reloadInterval) { [self] in
            noCacheRequest()
        }
        return webView
    }

    func updateNSView(_ nsView: WKWebView, context: Context) {}

    static func dismantleNSView(_ nsView: WKWebView, coordinator: Coordinator) {
        coordinator.stop()
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    private func noCacheRequest() -> URLRequest {
        var req = URLRequest(url: url)
        req.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        return req
    }

    final class Coordinator {
        private var timer: Timer?

        func start(webView: WKWebView, interval: TimeInterval, request: @escaping () -> URLRequest) {
            timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak webView] _ in
                webView?.load(request())
            }
        }

        func stop() {
            timer?.invalidate(); timer = nil
        }
    }
}

/// Inline-Datei-Vorschau (F057): rendert eine echte Datei je nach Typ — Bilder und SVG direkt im
/// `WKWebView`, Markdown nach einer minimalen HTML-Konvertierung (Überschriften, Listen, fett).
/// So lassen sich Bilder, SVG und Markdown „direkt aus der echten Datei" als Vorschau anzeigen.
struct FilePreview: NSViewRepresentable {
    let fileURL: URL

    func makeNSView(context: Context) -> WKWebView {
        let webView = WKWebView()
        render(into: webView)
        return webView
    }

    func updateNSView(_ nsView: WKWebView, context: Context) { render(into: nsView) }

    private func render(into webView: WKWebView) {
        let dir = fileURL.deletingLastPathComponent()
        switch fileURL.pathExtension.lowercased() {
        case "md", "markdown":
            let md = (try? String(contentsOf: fileURL, encoding: .utf8)) ?? ""
            let html = "<html><head><meta charset='utf-8'></head>"
                + "<body style='font-family:-apple-system,sans-serif;margin:24px;font-size:22px'>"
                + Self.markdownToHTML(md) + "</body></html>"
            webView.loadHTMLString(html, baseURL: dir)
        default:
            // Bilder + SVG werden vom WKWebView direkt aus der Datei gerendert.
            webView.loadFileURL(fileURL, allowingReadAccessTo: dir)
        }
    }

    /// Minimale Markdown→HTML-Konvertierung (Überschriften, Listenpunkte, **fett**, Absätze).
    static func markdownToHTML(_ md: String) -> String {
        func esc(_ s: Substring) -> String {
            s.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
        }
        func bold(_ s: String) -> String {
            var parts = s.components(separatedBy: "**"); guard parts.count >= 3 else { return s }
            var out = ""
            for (i, p) in parts.enumerated() {
                out += p
                if i < parts.count - 1 { out += (i % 2 == 0) ? "<b>" : "</b>" }
            }
            return out
        }
        var html = ""
        for raw in md.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.hasPrefix("## ") { html += "<h2>\(bold(esc(raw.dropFirst(3))))</h2>" }
            else if line.hasPrefix("# ") { html += "<h1>\(bold(esc(raw.dropFirst(2))))</h1>" }
            else if line.hasPrefix("- ") { html += "<li>\(bold(esc(raw.dropFirst(2))))</li>" }
            else if line.isEmpty { html += "" }
            else { html += "<p>\(bold(esc(raw[...])))</p>" }
        }
        return html
    }
}
