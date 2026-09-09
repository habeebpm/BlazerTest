import SwiftUI

/// BlazerTest MT5 EA Monitor
///
/// This app does NOT run the MQL5 EA on-device — MetaTrader's algo-trading
/// engine only exists inside the MetaTrader terminal (desktop, or MT5's own
/// mobile app for manual trading), and Apple's platform has no way to host
/// or execute third-party MQL5 code. Instead this app is a remote
/// monitor/control panel: the EA (running in an MT5 terminal on a VPS or
/// desktop, see /MQL5) keeps trading fully autonomously and pushes status to
/// a small bridge server (see /bridge); this app polls that same bridge to
/// show live account/position state and to send remote commands (pause /
/// resume new entries, flatten all positions).
@main
struct BlazerMT5MonitorApp: App {
    @StateObject private var settings = AppSettings()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(settings)
        }
    }
}
