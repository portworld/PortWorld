import SwiftUI

struct PWScreen<Content: View>: View {
  let title: String?
  let horizontalPadding: CGFloat
  let topPadding: CGFloat
  private let content: Content

  init(
    title: String? = nil,
    horizontalPadding: CGFloat = PWSpace.screen,
    topPadding: CGFloat = PWSpace.xl,
    @ViewBuilder content: () -> Content
  ) {
    self.title = title
    self.horizontalPadding = horizontalPadding
    self.topPadding = topPadding
    self.content = content()
  }

  var body: some View {
    ZStack {
      PWColor.background
        .ignoresSafeArea()

      VStack(alignment: .leading, spacing: PWSpace.section) {
        if let title {
          Text(title)
            .font(PWTypography.title)
            .foregroundStyle(PWColor.textPrimary)
        }

        content
      }
      .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
      .padding(.horizontal, horizontalPadding)
      .padding(.top, topPadding)
    }
  }
}

struct PWCard<Content: View>: View {
  let isRaised: Bool
  let padding: CGFloat
  private let content: Content

  init(
    isRaised: Bool = false,
    padding: CGFloat = PWSpace.lg,
    @ViewBuilder content: () -> Content
  ) {
    self.isRaised = isRaised
    self.padding = padding
    self.content = content()
  }

  var body: some View {
    content
      .padding(padding)
      .frame(maxWidth: .infinity, alignment: .leading)
      .background(isRaised ? PWColor.surfaceRaised : PWColor.surface)
      .overlay(
        RoundedRectangle(cornerRadius: PWRadius.card, style: .continuous)
          .stroke(isRaised ? PWColor.borderStrong : PWColor.border, lineWidth: 1)
      )
      .clipShape(RoundedRectangle(cornerRadius: PWRadius.card, style: .continuous))
  }
}
