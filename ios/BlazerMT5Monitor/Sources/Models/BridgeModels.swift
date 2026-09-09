import Foundation

/// One open position, as reported by the EA in its heartbeat.
struct Position: Codable, Identifiable, Hashable {
    let ticket: UInt64
    let type: String // "buy" or "sell"
    let volume: Double
    let openPrice: Double
    let sl: Double
    let tp: Double
    let profit: Double

    var id: UInt64 { ticket }
    var isBuy: Bool { type.lowercased() == "buy" }
}

/// The latest status snapshot pushed by the EA to the bridge.
struct Heartbeat: Codable {
    let accountTag: String
    let login: Int?
    let broker: String?
    let currency: String?
    let symbol: String?
    let magicNumber: Int?
    let balance: Double?
    let equity: Double?
    let margin: Double?
    let freeMargin: Double?
    let tradesToday: Int?
    let dailyLossHit: Bool?
    let remoteTradingEnabled: Bool?
    let positions: [Position]?
    let eaVersion: String?
    let ts: Int?
    /// Server-side receive time, milliseconds since epoch.
    let receivedAt: Double?
}

/// Remote-control flags the app can set and the EA polls.
struct ControlState: Codable {
    var tradingEnabled: Bool
    var flattenAll: Bool
}

struct StatusResponse: Codable {
    let accountTag: String
    let online: Bool
    let lastHeartbeat: Heartbeat?
    let control: ControlState
}

struct AccountSummary: Codable, Identifiable, Hashable {
    let accountTag: String
    let online: Bool
    let lastSeen: Double?
    var id: String { accountTag }
}

struct AccountsResponse: Codable {
    let accounts: [AccountSummary]
}

/// One point in the equity/balance history the bridge keeps per account.
struct HistoryPoint: Codable, Identifiable {
    /// Milliseconds since epoch.
    let ts: Double
    let equity: Double?
    let balance: Double?
    let positionsCount: Int?
    let tradesToday: Int?

    var id: Double { ts }
}

struct HistoryResponse: Codable {
    let accountTag: String
    let history: [HistoryPoint]
}
