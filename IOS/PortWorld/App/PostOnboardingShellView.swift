import MWDATCore
import SwiftUI

private enum AppTab: Hashable {
  case home
  case agent
  case settings
}

enum SettingsScrollTarget: Hashable {
  case backend
  case glasses
}

struct PostOnboardingShellView: View {
  @ObservedObject private var appSettingsStore: AppSettingsStore
  @ObservedObject private var wearablesRuntimeManager: WearablesRuntimeManager
  let shouldShowProfileSetupCallToAction: Bool
  let onOpenMetaSetup: () -> Void
  let onOpenProfileSetup: () -> Void

  @StateObject private var viewModel: AssistantRuntimeViewModel
  @State private var selectedTab: AppTab = .home
  @State private var settingsScrollTarget: SettingsScrollTarget?

  init(
    appSettingsStore: AppSettingsStore,
    wearablesRuntimeManager: WearablesRuntimeManager,
    shouldShowProfileSetupCallToAction: Bool,
    onOpenMetaSetup: @escaping () -> Void,
    onOpenProfileSetup: @escaping () -> Void
  ) {
    self.appSettingsStore = appSettingsStore
    self.wearablesRuntimeManager = wearablesRuntimeManager
    self.shouldShowProfileSetupCallToAction = shouldShowProfileSetupCallToAction
    self.onOpenMetaSetup = onOpenMetaSetup
    self.onOpenProfileSetup = onOpenProfileSetup

    let config = AssistantRuntimeConfig.load(
      backendBaseURLOverride: appSettingsStore.settings.backendBaseURL,
      bearerTokenOverride: appSettingsStore.settings.bearerToken
    )
    _viewModel = StateObject(
      wrappedValue: AssistantRuntimeViewModel(
        wearablesRuntimeManager: wearablesRuntimeManager,
        config: config
      )
    )
  }

  var body: some View {
    TabView(selection: $selectedTab) {
      HomeView(
        readiness: readiness,
        wakePhraseText: viewModel.status.wakePhraseText,
        sleepPhraseText: viewModel.status.sleepPhraseText,
        shouldShowProfileSetupCallToAction: shouldShowProfileSetupCallToAction,
        onOpenBackendSettings: {
          openSettings(.backend)
        },
        onOpenGlassesSettings: {
          openSettings(.glasses)
        },
        onOpenProfileSetup: onOpenProfileSetup
      )
      .tabItem {
        Label("Home", systemImage: "house")
      }
      .tag(AppTab.home)

      AgentView(
        readiness: readiness,
        runtimeStatus: viewModel.status,
        onActivateAssistant: activateAssistant,
        onDeactivateAssistant: deactivateAssistant
      )
      .tabItem {
        Label("Agent", systemImage: "sparkles")
      }
      .tag(AppTab.agent)

      SettingsView(
        settings: appSettingsStore.settings,
        readiness: readiness,
        isAssistantActive: viewModel.status.canDeactivate,
        isGlassesRegistered: wearablesRuntimeManager.registrationState == .registered,
        scrollTarget: $settingsScrollTarget,
        shouldShowProfileSetupCallToAction: shouldShowProfileSetupCallToAction,
        onUpdateBackendSettings: updateBackendSettings,
        onStopAssistantIfNeeded: stopAssistantIfNeeded,
        onOpenMetaSetup: onOpenMetaSetup,
        onOpenProfileSetup: onOpenProfileSetup,
        onDisconnectGlasses: disconnectGlasses
      )
      .tabItem {
        Label("Settings", systemImage: "gearshape")
      }
      .tag(AppTab.settings)
    }
    .tint(PWColor.textPrimary)
    .toolbarBackground(PWColor.background, for: .tabBar)
    .toolbarBackground(.visible, for: .tabBar)
  }
}

private extension PostOnboardingShellView {
  var readiness: HomeReadinessState {
    HomeReadinessState(
      settings: appSettingsStore.settings,
      runtimeStatus: viewModel.status,
      wearablesRuntimeManager: wearablesRuntimeManager
    )
  }

  func openSettings(_ target: SettingsScrollTarget) {
    settingsScrollTarget = target
    selectedTab = .settings
  }

  func activateAssistant() {
    Task {
      await viewModel.activateAssistant()
    }
  }

  func deactivateAssistant() {
    Task {
      await viewModel.deactivateAssistant()
    }
  }

  func updateBackendSettings(
    backendBaseURL: String,
    bearerToken: String,
    validationState: AppSettingsStore.BackendValidationState
  ) {
    appSettingsStore.updateBackendSettings(
      backendBaseURL: backendBaseURL,
      bearerToken: bearerToken,
      validationState: validationState
    )
  }

  func stopAssistantIfNeeded() async {
    guard viewModel.status.canDeactivate else { return }
    await viewModel.deactivateAssistant()
  }

  func disconnectGlasses() {
    wearablesRuntimeManager.disconnectGlasses()
  }
}
