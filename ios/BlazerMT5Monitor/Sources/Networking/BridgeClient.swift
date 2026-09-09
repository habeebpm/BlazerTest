import Foundation

enum BridgeError: LocalizedError {
    case invalidURL
    case invalidResponse
    case http(Int, String)

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "Invalid bridge URL. Check the address in Settings."
        case .invalidResponse:
            return "The bridge returned an unexpected response."
        case .http(let code, let body):
            if code == 401 { return "Unauthorized - check the API key in Settings." }
            if code == 404 { return "Unknown account tag - has the EA sent a heartbeat yet?" }
            return "Bridge error \(code): \(body.isEmpty ? "no details" : body)"
        }
    }
}

/// Talks to the bridge server's `/api/app/*` endpoints on behalf of this app.
/// The EA never receives requests from this app directly - see App.swift.
struct BridgeClient {
    let baseURL: URL
    let apiKey: String

    private func makeRequest(
        path: String,
        method: String = "GET",
        query: [String: String] = [:],
        body: Data? = nil
    ) throws -> URLRequest {
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else {
            throw BridgeError.invalidURL
        }
        components.path = (components.path as NSString).appendingPathComponent(path)
        if !query.isEmpty {
            components.queryItems = query.map { URLQueryItem(name: $0.key, value: $0.value) }
        }
        guard let url = components.url else { throw BridgeError.invalidURL }

        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue(apiKey, forHTTPHeaderField: "X-Api-Key")
        request.timeoutInterval = 15
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = body
        }
        return request
    }

    private func send(_ request: URLRequest) async throws -> Data {
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw BridgeError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else {
            let bodyText = String(data: data, encoding: .utf8) ?? ""
            throw BridgeError.http(http.statusCode, bodyText)
        }
        return data
    }

    func fetchAccounts() async throws -> [AccountSummary] {
        let request = try makeRequest(path: "/api/app/accounts")
        let data = try await send(request)
        return try JSONDecoder().decode(AccountsResponse.self, from: data).accounts
    }

    func fetchStatus(accountTag: String) async throws -> StatusResponse {
        let request = try makeRequest(path: "/api/app/status", query: ["accountTag": accountTag])
        let data = try await send(request)
        return try JSONDecoder().decode(StatusResponse.self, from: data)
    }

    func fetchHistory(accountTag: String, limit: Int = 200) async throws -> [HistoryPoint] {
        let request = try makeRequest(
            path: "/api/app/history",
            query: ["accountTag": accountTag, "limit": String(limit)]
        )
        let data = try await send(request)
        return try JSONDecoder().decode(HistoryResponse.self, from: data).history
    }

    @discardableResult
    func setControl(accountTag: String, tradingEnabled: Bool? = nil, flattenAll: Bool? = nil) async throws -> ControlState {
        struct Payload: Encodable {
            let accountTag: String
            let tradingEnabled: Bool?
            let flattenAll: Bool?
        }
        struct Response: Decodable {
            let ok: Bool
            let control: ControlState
        }

        let payload = Payload(accountTag: accountTag, tradingEnabled: tradingEnabled, flattenAll: flattenAll)
        let body = try JSONEncoder().encode(payload)
        let request = try makeRequest(path: "/api/app/control", method: "POST", body: body)
        let data = try await send(request)
        return try JSONDecoder().decode(Response.self, from: data).control
    }
}
