import Foundation
import Combine

/// User-configurable connection settings: bridge URL + account tag live in
/// UserDefaults, the API key lives in the Keychain.
final class AppSettings: ObservableObject {
    @Published var bridgeURLString: String {
        didSet { UserDefaults.standard.set(bridgeURLString, forKey: Keys.bridgeURL) }
    }
    @Published var accountTag: String {
        didSet { UserDefaults.standard.set(accountTag, forKey: Keys.accountTag) }
    }
    @Published var apiKey: String {
        didSet { KeychainStore.set(apiKey, forKey: Keys.apiKey) }
    }

    private enum Keys {
        static let bridgeURL = "bridgeURLString"
        static let accountTag = "accountTag"
        static let apiKey = "appApiKey"
    }

    init() {
        bridgeURLString = UserDefaults.standard.string(forKey: Keys.bridgeURL) ?? ""
        accountTag = UserDefaults.standard.string(forKey: Keys.accountTag) ?? ""
        apiKey = KeychainStore.get(Keys.apiKey) ?? ""
    }

    var bridgeURL: URL? {
        guard !bridgeURLString.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
        return URL(string: bridgeURLString)
    }

    var isConfigured: Bool {
        bridgeURL != nil && !apiKey.isEmpty && !accountTag.trimmingCharacters(in: .whitespaces).isEmpty
    }

    func makeClient() -> BridgeClient? {
        guard let url = bridgeURL, !apiKey.isEmpty else { return nil }
        return BridgeClient(baseURL: url, apiKey: apiKey)
    }
}
