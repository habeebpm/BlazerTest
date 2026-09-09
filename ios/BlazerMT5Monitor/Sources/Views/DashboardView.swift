import SwiftUI

struct DashboardView: View {
    @ObservedObject var settings: AppSettings
    @StateObject private var viewModel: DashboardViewModel

    init(settings: AppSettings) {
        self.settings = settings
        _viewModel = StateObject(wrappedValue: DashboardViewModel(settings: settings))
    }

    var body: some View {
        NavigationStack {
            List {
                if !settings.isConfigured {
                    Section {
                        Label(
                            "Set the bridge URL, API key, and account tag in Settings to start monitoring.",
                            systemImage: "exclamationmark.triangle"
                        )
                        .foregroundStyle(.orange)
                    }
                } else if let status = viewModel.status {
                    accountSection(status)
                    controlSection(status)
                    positionsSection(status)
                } else if viewModel.isLoading {
                    Section {
                        HStack {
                            Spacer()
                            ProgressView("Loading...")
                            Spacer()
                        }
                    }
                }

                if let error = viewModel.errorMessage {
                    Section {
                        Text(error)
                            .font(.footnote)
                            .foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle("EA Monitor")
            .refreshable { await viewModel.refresh() }
            .task { viewModel.startAutoRefresh() }
            .onDisappear { viewModel.stopAutoRefresh() }
        }
    }

    @ViewBuilder
    private func accountSection(_ status: StatusResponse) -> some View {
        Section("Account") {
            HStack {
                Text("Status")
                Spacer()
                StatusBadge(online: status.online)
            }
            if let hb = status.lastHeartbeat {
                row("Broker", hb.broker ?? "-")
                row("Symbol", hb.symbol ?? "-")
                row("Balance", currency(hb.balance))
                row("Equity", currency(hb.equity))
                row("Trades Today", hb.tradesToday.map(String.init) ?? "-")
                if hb.dailyLossHit == true {
                    Label("Daily loss limit hit — new entries paused by the EA", systemImage: "exclamationmark.octagon.fill")
                        .foregroundStyle(.red)
                        .font(.footnote)
                }
            } else {
                Text("No heartbeat received yet from this account.")
                    .foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    private func controlSection(_ status: StatusResponse) -> some View {
        Section("Remote Control") {
            Toggle(isOn: Binding(
                get: { status.control.tradingEnabled },
                set: { newValue in
                    Task { await viewModel.setTradingEnabled(newValue) }
                }
            )) {
                Text("Trading Enabled")
            }
            .disabled(viewModel.isSendingCommand)

            Button(role: .destructive) {
                Task { await viewModel.flattenAll() }
            } label: {
                Label("Flatten All Positions Now", systemImage: "xmark.octagon")
            }
            .disabled(viewModel.isSendingCommand)

            Text("Trading Enabled only pauses new entries — the EA keeps managing (trailing/breakeven) any open positions itself. Flatten closes everything immediately.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private func positionsSection(_ status: StatusResponse) -> some View {
        Section("Open Positions") {
            let positions = status.lastHeartbeat?.positions ?? []
            if positions.isEmpty {
                Text("No open positions.")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(positions) { position in
                    PositionRowView(position: position)
                }
            }
        }
    }

    private func row(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label)
            Spacer()
            Text(value).foregroundStyle(.secondary)
        }
    }

    private func currency(_ value: Double?) -> String {
        guard let value else { return "-" }
        return String(format: "%.2f", value)
    }
}
