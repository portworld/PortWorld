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
  @Environment(\.scenePhase) private var scenePhase
  @ObservedObject private var appSettingsStore: AppSettingsStore
  @ObservedObject private var wearablesRuntimeManager: WearablesRuntimeManager
  let shouldShowProfileSetupCallToAction: Bool
  @Binding private var pendingLaunchCommand: AppLaunchCommand?
  let onOpenMetaSetup: () -> Void
  let onOpenProfileSetup: () -> Void

  @StateObject private var viewModel: AssistantRuntimeViewModel
  @State private var selectedTab: AppTab = .home
  @State private var settingsScrollTarget: SettingsScrollTarget?
  @State private var launchCommandAlert: LaunchCommandAlert?

  init(
    appSettingsStore: AppSettingsStore,
    wearablesRuntimeManager: WearablesRuntimeManager,
    shouldShowProfileSetupCallToAction: Bool,
    pendingLaunchCommand: Binding<AppLaunchCommand?>,
    onOpenMetaSetup: @escaping () -> Void,
    onOpenProfileSetup: @escaping () -> Void
  ) {
    self.appSettingsStore = appSettingsStore
    self.wearablesRuntimeManager = wearablesRuntimeManager
    self.shouldShowProfileSetupCallToAction = shouldShowProfileSetupCallToAction
    _pendingLaunchCommand = pendingLaunchCommand
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
    .onAppear {
      handlePendingLaunchCommandIfNeeded()
    }
    .onChange(of: scenePhase) { _, newValue in
      viewModel.handleScenePhaseChange(newValue)
      if newValue == .active {
        handlePendingLaunchCommandIfNeeded()
      }
    }
    .onChange(of: pendingLaunchCommand) { _, _ in
      handlePendingLaunchCommandIfNeeded()
    }
    .alert(item: $launchCommandAlert) { alert in
      Alert(
        title: Text("Session Start Blocked"),
        message: Text(alert.message),
        dismissButton: .default(Text("OK"))
      )
    }
  }
}

private extension PostOnboardingShellView {
  struct LaunchCommandAlert: Identifiable {
    let id = UUID()
    let message: String
  }

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

  func handlePendingLaunchCommandIfNeeded() {
    guard pendingLaunchCommand == .startSession else { return }
    pendingLaunchCommand = nil
    selectedTab = .agent

    guard readiness.canActivateAssistant else {
      launchCommandAlert = LaunchCommandAlert(message: launchCommandBlockedMessage())
      return
    }

    activateAssistant()
  }

  func launchCommandBlockedMessage() -> String {
    if readiness.backendStatus.action == .openBackendSettings {
      return "Siri opened PortWorld, but the assistant could not start because backend setup is not ready. \(readiness.backendStatus.detail)"
    }

    if readiness.glassesStatus.action == .openGlassesSettings || readiness.canActivateAssistant == false {
      return "Siri opened PortWorld, but the assistant could not start because glasses are not ready. \(readiness.glassesStatus.detail)"
    }

    return "Siri opened PortWorld, but the assistant is not ready yet."
  }
}
