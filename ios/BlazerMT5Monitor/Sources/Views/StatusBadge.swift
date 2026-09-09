import SwiftUI

struct StatusBadge: View {
    let online: Bool

    var body: some View {
        HStack(spacing: 4) {
            Circle()
                .fill(online ? Color.green : Color.red)
                .frame(width: 8, height: 8)
            Text(online ? "Online" : "Offline")
                .font(.subheadline)
                .foregroundStyle(online ? .green : .red)
        }
    }
}
