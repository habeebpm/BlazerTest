import Foundation

@MainActor
final class DashboardViewModel: ObservableObject {
    @Published var status: StatusResponse?
    @Published var history: [HistoryPoint] = []
    @Published var errorMessage: String?
    @Published var isLoading = false
    @Published var isSendingCommand = false

    private let settings: AppSettings
    private var refreshTask: Task<Void, Never>?

    init(settings: AppSettings) {
        self.settings = settings
    }

    /// Polls the bridge on a fixed interval until `stopAutoRefresh()` is called
    /// or this object is deallocated.
    func startAutoRefresh(interval: TimeInterval = 10) {
        stopAutoRefresh()
        refreshTask = Task { [weak self] in
            while let self, !Task.isCancelled {
                await self.refresh()
                try? await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000))
            }
        }
    }

    func stopAutoRefresh() {
        refreshTask?.cancel()
        refreshTask = nil
    }

    func refresh() async {
        guard let client = settings.makeClient(), settings.isConfigured else {
            errorMessage = "Configure the bridge URL, API key, and account tag in Settings."
            return
        }

        isLoading = true
        defer { isLoading = false }

        do {
            async let statusResult = client.fetchStatus(accountTag: settings.accountTag)
            async let historyResult = client.fetchHistory(accountTag: settings.accountTag)
            let (statusValue, historyValue) = try await (statusResult, historyResult)
            status = statusValue
            history = historyValue
            errorMessage = nil
        } catch {
            errorMessage = friendlyMessage(for: error)
        }
    }

    func setTradingEnabled(_ enabled: Bool) async {
        guard let client = settings.makeClient() else { return }
        isSendingCommand = true
        defer { isSendingCommand = false }
        do {
            _ = try await client.setControl(accountTag: settings.accountTag, tradingEnabled: enabled)
            await refresh()
        } catch {
            errorMessage = friendlyMessage(for: error)
        }
    }

    func flattenAll() async {
        guard let client = settings.makeClient() else { return }
        isSendingCommand = true
        defer { isSendingCommand = false }
        do {
            _ = try await client.setControl(accountTag: settings.accountTag, flattenAll: true)
            await refresh()
        } catch {
            errorMessage = friendlyMessage(for: error)
        }
    }

    private func friendlyMessage(for error: Error) -> String {
        (error as? BridgeError)?.errorDescription ?? error.localizedDescription
    }
}
