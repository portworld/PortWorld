import SwiftUI

struct OnboardingIntroStepView: View {
  let title: String
  let subtitle: String
  let buttonTitle: String
  let onContinue: () -> Void

  var body: some View {
    PWOnboardingScaffold(
      style: .centeredHero,
      title: title,
      subtitle: subtitle,
      content: {
        EmptyView()
      },
      footer: {
        PWOnboardingButton(title: buttonTitle, action: onContinue)
      }
    )
  }
}
