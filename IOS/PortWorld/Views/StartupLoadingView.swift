// Branded first-launch loading view shown while shared app capabilities initialize.
import SwiftUI

struct StartupLoadingView: View {
  @ScaledMetric(relativeTo: .largeTitle) private var artworkSize = 224

  var body: some View {
    ZStack {
      Color.black
        .ignoresSafeArea()

      Image("StartupMark")
        .resizable()
        .aspectRatio(contentMode: .fit)
        .frame(width: artworkSize, height: artworkSize)
        .accessibilityHidden(true)
    }
    .statusBarHidden(true)
    .accessibilityElement(children: .ignore)
    .accessibilityLabel("PortWorld is starting")
  }
}
