import SwiftUI

/// Lightweight self-drawn line chart (no external chart library needed).
struct EquitySparkline: View {
    let points: [Double]

    var body: some View {
        GeometryReader { geo in
            let minValue = points.min() ?? 0
            let maxValue = points.max() ?? 1
            let range = max(maxValue - minValue, 0.0001)

            Path { path in
                for (index, value) in points.enumerated() {
                    let x: CGFloat = points.count > 1
                        ? geo.size.width * CGFloat(index) / CGFloat(points.count - 1)
                        : geo.size.width / 2
                    let normalized = (value - minValue) / range
                    let y = geo.size.height * (1 - CGFloat(normalized))
                    if index == 0 {
                        path.move(to: CGPoint(x: x, y: y))
                    } else {
                        path.addLine(to: CGPoint(x: x, y: y))
                    }
                }
            }
            .stroke(Color.accentColor, style: StrokeStyle(lineWidth: 2, lineJoin: .round))
        }
    }
}
