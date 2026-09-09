import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var settings: AppSettings

    var body: some View {
        NavigationStack {
            Form {
                Section("Bridge Server") {
                    TextField("https://your-bridge.example.com", text: $settings.bridgeURLString)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()

                    SecureField("App API key", text: $settings.apiKey)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()

                    TextField("Account tag", text: $settings.accountTag)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }

                Section {
                    Text("Account tag must match InpBridgeAccountTag on the EA (or its MT5 account login number, if that input is left blank). The API key here is the bridge's APP_API_KEY, not the EA's key.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section("About") {
                    HStack {
                        Text("App")
                        Spacer()
                        Text("BlazerTest MT5 Monitor").foregroundStyle(.secondary)
                    }
                    Text("This app monitors and remotely pauses/resumes/flattens the XAUUSD_Confluence_EA running in an MT5 terminal. It does not place trades itself and cannot run the EA on-device — see the repo README for the full architecture.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Settings")
        }
    }
}
