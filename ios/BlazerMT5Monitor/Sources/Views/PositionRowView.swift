import SwiftUI

struct PositionRowView: View {
    let position: Position

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(position.type.uppercased())
                    .font(.caption)
                    .bold()
                    .foregroundStyle(position.isBuy ? .green : .red)
                Text(String(format: "%.2f lots @ %.2f", position.volume, position.openPrice))
                    .font(.subheadline)
                Text(String(format: "SL %.2f   TP %.2f", position.sl, position.tp))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Text(String(format: "%.2f", position.profit))
                .font(.body.bold())
                .foregroundStyle(position.profit >= 0 ? .green : .red)
        }
        .padding(.vertical, 2)
    }
}
