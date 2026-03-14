import SwiftUI

struct HomeHostView: View {
  let wearablesRuntimeManager: WearablesRuntimeManager
  let settings: AppSettingsStore.Settings
  let onOpenBackendSetup: () -> Void
  let onOpenMetaSetup: () -> Void

  @StateObject private var viewModel: AssistantRuntimeViewModel

  init(
    wearablesRuntimeManager: WearablesRuntimeManager,
    settings: AppSettingsStore.Settings,
    onOpenBackendSetup: @escaping () -> Void,
    onOpenMetaSetup: @escaping () -> Void
  ) {
    self.wearablesRuntimeManager = wearablesRuntimeManager
    self.settings = settings
    self.onOpenBackendSetup = onOpenBackendSetup
    self.onOpenMetaSetup = onOpenMetaSetup

    let config = AssistantRuntimeConfig.load(
      backendBaseURLOverride: settings.backendBaseURL,
      bearerTokenOverride: settings.bearerToken
    )
    _viewModel = StateObject(
      wrappedValue: AssistantRuntimeViewModel(
        wearablesRuntimeManager: wearablesRuntimeManager,
        config: config
      )
    )
  }

  var body: some View {
    HomeView(
      viewModel: viewModel,
      settings: settings,
      wearablesRuntimeManager: wearablesRuntimeManager,
      onOpenBackendSetup: onOpenBackendSetup,
      onOpenMetaSetup: onOpenMetaSetup
    )
  }
}
