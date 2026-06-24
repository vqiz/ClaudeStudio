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
