import SwiftUI

enum PWOnboardingScaffoldStyle {
  case centeredHero
  case leadingContent
}

enum PWScreenTitleAlignment {
  case leading
  case center
}

struct PWScreen<Content: View>: View {
  let title: String?
  let titleAlignment: PWScreenTitleAlignment
  let horizontalPadding: CGFloat
  let topPadding: CGFloat
  private let content: Content

  init(
    title: String? = nil,
    titleAlignment: PWScreenTitleAlignment = .leading,
    horizontalPadding: CGFloat = PWSpace.screen,
    topPadding: CGFloat = PWSpace.xl,
    @ViewBuilder content: () -> Content
  ) {
    self.title = title
    self.titleAlignment = titleAlignment
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
            .multilineTextAlignment(titleAlignment == .center ? .center : .leading)
            .frame(
              maxWidth: .infinity,
              alignment: titleAlignment == .center ? .center : .leading
            )
        }

        content
      }
      .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
      .padding(.horizontal, horizontalPadding)
      .padding(.top, topPadding)
    }
  }
}

struct PWOnboardingScaffold<Content: View, Footer: View>: View {
  let style: PWOnboardingScaffoldStyle
  let title: String
  let subtitle: String?
  let horizontalPadding: CGFloat
  let topPadding: CGFloat
  let heroSpacing: CGFloat
  private let content: Content
  private let footer: Footer

  init(
    style: PWOnboardingScaffoldStyle,
    title: String,
    subtitle: String? = nil,
    horizontalPadding: CGFloat = PWSpace.screen,
    topPadding: CGFloat = 28,
    heroSpacing: CGFloat = PWSpace.section,
    @ViewBuilder content: () -> Content,
    @ViewBuilder footer: () -> Footer
  ) {
    self.style = style
    self.title = title
    self.subtitle = subtitle
    self.horizontalPadding = horizontalPadding
    self.topPadding = topPadding
    self.heroSpacing = heroSpacing
    self.content = content()
    self.footer = footer()
  }

  var body: some View {
    ZStack {
      PWColor.background
        .ignoresSafeArea()

      switch style {
      case .centeredHero:
        centeredHeroLayout
      case .leadingContent:
        leadingContentLayout
      }
    }
    .safeAreaInset(edge: .bottom) {
      HStack {
        Spacer(minLength: 0)
        footer
        Spacer(minLength: 0)
      }
      .padding(.horizontal, horizontalPadding)
      .padding(.top, PWSpace.lg)
      .padding(.bottom, PWSpace.xl)
    }
  }

  private var centeredHeroLayout: some View {
    VStack(spacing: heroSpacing) {
      Spacer(minLength: 0)

      heroBlock(multilineAlignment: .center)
        .frame(maxWidth: 332)

      content

      Spacer(minLength: 0)
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .padding(.horizontal, horizontalPadding)
    .padding(.top, topPadding)
  }

  private var leadingContentLayout: some View {
    ScrollView(showsIndicators: false) {
      VStack(alignment: .leading, spacing: heroSpacing) {
        heroBlock(multilineAlignment: .leading)
        content
      }
      .frame(maxWidth: .infinity, alignment: .topLeading)
      .padding(.horizontal, horizontalPadding)
      .padding(.top, topPadding)
      .padding(.bottom, 120)
    }
  }

  private func heroBlock(multilineAlignment: TextAlignment) -> some View {
    VStack(alignment: multilineAlignment == .center ? .center : .leading, spacing: PWSpace.md) {
      Text(title)
        .font(PWTypography.display)
        .foregroundStyle(PWColor.textPrimary)
        .multilineTextAlignment(multilineAlignment)

      if let subtitle, subtitle.isEmpty == false {
        Text(subtitle)
          .font(PWTypography.body)
          .foregroundStyle(PWColor.textSecondary)
          .multilineTextAlignment(multilineAlignment)
          .fixedSize(horizontal: false, vertical: true)
      }
    }
    .frame(maxWidth: .infinity, alignment: multilineAlignment == .center ? .center : .leading)
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
