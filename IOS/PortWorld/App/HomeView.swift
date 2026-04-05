import SwiftUI

struct HomeView: View {
  let readiness: HomeReadinessState
  let wakePhraseText: String
  let sleepPhraseText: String
  let shouldShowProfileSetupCallToAction: Bool
  let onOpenBackendSettings: () -> Void
  let onOpenGlassesSettings: () -> Void
  let onOpenProfileSetup: () -> Void

  var body: some View {
    PWScreen(title: "Home", titleAlignment: .center, topPadding: PWSpace.md) {
      ScrollView(showsIndicators: false) {
        VStack(alignment: .leading, spacing: PWSpace.section) {
          heroCard(readiness: readiness)
          readinessCard(readiness: readiness)
          if shouldShowProfileSetup(readiness: readiness) {
            profileSetupCard
          }
          phrasesCard
        }
        .padding(.bottom, PWSpace.hero)
      }
    }
  }
}

private extension HomeView {
  func heroCard(readiness: HomeReadinessState) -> some View {
    PWCard(isRaised: true, padding: PWSpace.xl) {
      VStack(alignment: .leading, spacing: PWSpace.md) {
        Text(readiness.assistantSummary)
          .font(.system(.largeTitle, design: .rounded).weight(.bold))
          .foregroundStyle(PWColor.textPrimary)

        Text(readiness.assistantDetail)
          .font(PWTypography.body)
          .foregroundStyle(PWColor.textSecondary)
          .fixedSize(horizontal: false, vertical: true)
      }
    }
  }

  var phrasesCard: some View {
    PWCard {
      VStack(alignment: .leading, spacing: PWSpace.md) {
        Text("Voice commands")
          .font(PWTypography.headline)
          .foregroundStyle(PWColor.textPrimary)

        PhraseRow(
          title: "Start",
          phrase: wakePhraseText,
          systemImage: "waveform"
        )

        PhraseRow(
          title: "Stop",
          phrase: sleepPhraseText,
          systemImage: "stop.circle"
        )
      }
    }
  }

  var profileSetupCard: some View {
    PWCard {
      VStack(alignment: .leading, spacing: PWSpace.md) {
        Text("Finish profile setup")
          .font(PWTypography.headline)
          .foregroundStyle(PWColor.textPrimary)

        Text("Mario still needs to walk you through the voice flow and collect your initial setup details.")
          .font(PWTypography.body)
          .foregroundStyle(PWColor.textSecondary)
          .fixedSize(horizontal: false, vertical: true)

        PWSecondaryButton(title: "Start profile setup") {
          onOpenProfileSetup()
        }
      }
    }
  }

  func readinessCard(readiness: HomeReadinessState) -> some View {
    PWCard {
      VStack(alignment: .leading, spacing: PWSpace.lg) {
        Text("Readiness")
          .font(PWTypography.headline)
          .foregroundStyle(PWColor.textPrimary)

        HomeStatusRowView(state: readiness.backendStatus) { action in
          handleRowAction(action)
        }

        HomeStatusRowView(state: readiness.glassesStatus) { action in
          handleRowAction(action)
        }
      }
    }
  }

  func handleRowAction(_ action: HomeStatusRowState.Action) {
    switch action {
    case .openBackendSettings:
      onOpenBackendSettings()
    case .openGlassesSettings:
      onOpenGlassesSettings()
    }
  }

  func shouldShowProfileSetup(readiness: HomeReadinessState) -> Bool {
    shouldShowProfileSetupCallToAction && readiness.canActivateAssistant
  }
}

private struct PhraseRow: View {
  let title: String
  let phrase: String
  let systemImage: String

  var body: some View {
    HStack(alignment: .top, spacing: PWSpace.md) {
      Image(systemName: systemImage)
        .font(.system(size: 15, weight: .semibold))
        .foregroundStyle(PWColor.textSecondary)
        .frame(width: 20, alignment: .center)

      VStack(alignment: .leading, spacing: PWSpace.xs) {
        Text(title)
          .font(PWTypography.headline)
          .foregroundStyle(PWColor.textPrimary)

        Text("Say \"\(displayPhrase)\"")
          .font(PWTypography.caption)
          .foregroundStyle(PWColor.textSecondary)
      }

      Spacer(minLength: 0)
    }
  }

  private var displayPhrase: String {
    phrase.isEmpty ? "Mario" : phrase
  }
}

private struct HomeStatusRowView: View {
  let state: HomeStatusRowState
  let onTapAction: (HomeStatusRowState.Action) -> Void

  var body: some View {
    HStack(alignment: .top, spacing: PWSpace.md) {
      Image(systemName: state.systemImage)
        .font(.system(size: 15, weight: .semibold))
        .foregroundStyle(state.tone.color)
        .frame(width: 20, alignment: .center)

      VStack(alignment: .leading, spacing: PWSpace.xs) {
        Text(state.title)
          .font(PWTypography.headline)
          .foregroundStyle(PWColor.textPrimary)

        Text(state.label)
          .font(PWTypography.subbody)
          .foregroundStyle(state.tone == .neutral ? PWColor.textPrimary : state.tone.color)

        Text(state.detail)
          .font(PWTypography.caption)
          .foregroundStyle(PWColor.textSecondary)
          .fixedSize(horizontal: false, vertical: true)

        if let action = state.action {
          Button(action.title) {
            onTapAction(action)
          }
          .buttonStyle(.plain)
          .font(PWTypography.subbody)
          .foregroundStyle(PWColor.textSecondary)
          .padding(.top, PWSpace.xs)
        }
      }

      Spacer(minLength: 0)
    }
  }
}
