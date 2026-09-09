import SwiftUI

struct HistoryView: View {
    @ObservedObject var settings: AppSettings
    @StateObject private var viewModel: DashboardViewModel

    init(settings: AppSettings) {
        self.settings = settings
        _viewModel = StateObject(wrappedValue: DashboardViewModel(settings: settings))
    }

    var body: some View {
        NavigationStack {
            Group {
                if viewModel.history.isEmpty {
                    ContentUnavailableView(
                        "No History Yet",
                        systemImage: "chart.line.uptrend.xyaxis",
                        description: Text("Equity history appears once the EA has sent a few heartbeats to the bridge.")
                    )
                } else {
                    VStack(spacing: 0) {
                        EquitySparkline(points: viewModel.history.compactMap(\.equity))
                            .frame(height: 160)
                            .padding()

                        List(viewModel.history.reversed()) { point in
                            HStack {
                                Text(Date(timeIntervalSince1970: point.ts / 1000), style: .time)
                                    .font(.caption)
                                Spacer()
                                Text("Equity \(point.equity.map { String(format: "%.2f", $0) } ?? "-")")
                                    .font(.caption)
                                Spacer()
                                Text("Pos \(point.positionsCount.map(String.init) ?? "-")")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .listStyle(.plain)
                    }
                }
            }
            .navigationTitle("Equity History")
            .refreshable { await viewModel.refresh() }
            .task { await viewModel.refresh() }
        }
    }
}
