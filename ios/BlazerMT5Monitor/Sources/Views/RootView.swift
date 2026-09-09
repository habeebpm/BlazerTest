import SwiftUI

struct RootView: View {
    @EnvironmentObject private var settings: AppSettings

    var body: some View {
        TabView {
            DashboardView(settings: settings)
                .tabItem { Label("Dashboard", systemImage: "speedometer") }

            HistoryView(settings: settings)
                .tabItem { Label("History", systemImage: "chart.line.uptrend.xyaxis") }

            SettingsView()
                .tabItem { Label("Settings", systemImage: "gearshape") }
        }
    }
}
