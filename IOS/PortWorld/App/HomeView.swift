import SwiftUI

struct HomeView: View {
  @ObservedObject private var viewModel: AssistantRuntimeViewModel
  let settings: AppSettingsStore.Settings
  @ObservedObject private var wearablesRuntimeManager: WearablesRuntimeManager
  let onOpenBackendSetup: () -> Void
  let onOpenMetaSetup: () -> Void

  init(
    viewModel: AssistantRuntimeViewModel,
    settings: AppSettingsStore.Settings,
    wearablesRuntimeManager: WearablesRuntimeManager,
    onOpenBackendSetup: @escaping () -> Void,
    onOpenMetaSetup: @escaping () -> Void
  ) {
    self.viewModel = viewModel
    self.settings = settings
    self.wearablesRuntimeManager = wearablesRuntimeManager
    self.onOpenBackendSetup = onOpenBackendSetup
    self.onOpenMetaSetup = onOpenMetaSetup
  }

  var body: some View {
    let readiness = HomeReadinessState(
      settings: settings,
      runtimeStatus: viewModel.status,
      wearablesRuntimeManager: wearablesRuntimeManager
    )

    PWScreen(topPadding: PWSpace.md) {
      ScrollView(showsIndicators: false) {
        VStack(alignment: .leading, spacing: PWSpace.section) {
          heroCard(readiness: readiness)
          primaryControlCard(readiness: readiness)
          phrasesCard
          readinessCard(readiness: readiness)
        }
        .padding(.bottom, PWSpace.hero)
      }
    }
    .navigationTitle("Home")
    .navigationBarTitleDisplayMode(.inline)
    .onAppear {
      viewModel.selectRoute(.glasses)
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

  func primaryControlCard(readiness: HomeReadinessState) -> some View {
    PWCard {
      VStack(alignment: .leading, spacing: PWSpace.md) {
        Text("Assistant")
          .font(PWTypography.headline)
          .foregroundStyle(PWColor.textPrimary)

        if isDeactivateState {
          PWPrimaryButton(title: "Deactivate Assistant", action: deactivateAssistant)
        } else {
          PWPrimaryButton(
            title: primaryButtonTitle,
            isDisabled: readiness.canActivateAssistant == false || isBusyStopping,
            action: activateAssistant
          )
        }

        if let recoveryAction = readiness.recoveryAction,
           shouldShowRecoveryAction(readiness: readiness)
        {
          PWSecondaryButton(title: recoveryAction.title) {
            handleRecoveryAction(recoveryAction)
          }
        }
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
          phrase: viewModel.status.wakePhraseText,
          systemImage: "waveform"
        )

        PhraseRow(
          title: "Stop",
          phrase: viewModel.status.sleepPhraseText,
          systemImage: "stop.circle"
        )
      }
    }
  }

  func readinessCard(readiness: HomeReadinessState) -> some View {
    PWCard {
      VStack(alignment: .leading, spacing: PWSpace.lg) {
        Text("Readiness")
          .font(PWTypography.headline)
          .foregroundStyle(PWColor.textPrimary)

        HomeStatusRowView(state: readiness.backendStatus)
        HomeStatusRowView(state: readiness.glassesStatus)
      }
    }
  }

  var isDeactivateState: Bool {
    viewModel.status.canDeactivate
  }

  var isBusyStopping: Bool {
    viewModel.status.assistantRuntimeState == .deactivating
  }

  var primaryButtonTitle: String {
    if isBusyStopping {
      return "Stopping…"
    }

    return "Activate Assistant"
  }

  func shouldShowRecoveryAction(readiness: HomeReadinessState) -> Bool {
    readiness.canActivateAssistant == false &&
      viewModel.status.assistantRuntimeState == .inactive
  }

  func activateAssistant() {
    viewModel.selectRoute(.glasses)
    Task {
      await viewModel.activateAssistant()
    }
  }

  func deactivateAssistant() {
    Task {
      await viewModel.deactivateAssistant()
    }
  }

  func handleRecoveryAction(_ action: HomeReadinessState.RecoveryAction) {
    switch action {
    case .openBackendSetup:
      onOpenBackendSetup()
    case .connectGlasses:
      onOpenMetaSetup()
    }
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
      }

      Spacer(minLength: 0)
    }
  }
}
