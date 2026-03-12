// Branded first-launch loading view shown while shared app capabilities initialize.
import SwiftUI

struct StartupLoadingView: View {
  @ScaledMetric(relativeTo: .largeTitle) private var artworkSize = 224

  var body: some View {
    ZStack {
      Color.black
        .ignoresSafeArea()

      VStack(spacing: 20) {
        Image("StartupMark")
          .resizable()
          .aspectRatio(contentMode: .fit)
          .frame(width: artworkSize, height: artworkSize)
          .accessibilityHidden(true)

        Text("PortWorld")
          .font(.system(.title, design: .rounded).weight(.bold))
          .tracking(0.8)
          .foregroundStyle(.white)
      }
      .padding(24)
      .accessibilityElement(children: .combine)
      .accessibilityLabel("PortWorld is starting")
    }
    .statusBarHidden(true)
  }
}
