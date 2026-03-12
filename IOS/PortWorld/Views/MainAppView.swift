// Root shell for the phone-first assistant with secondary access to future hardware setup.
import SwiftUI

struct MainAppView: View {
  @StateObject private var runtimeViewModel: AssistantRuntimeViewModel
  @ObservedObject private var wearablesRuntimeManager: WearablesRuntimeManager
  @State private var isPresentingFutureHardwareSetup = false
  @State private var showsStartupLoadingView = true

  init(wearablesRuntimeManager: WearablesRuntimeManager) {
    self.wearablesRuntimeManager = wearablesRuntimeManager
    _runtimeViewModel = StateObject(
      wrappedValue: AssistantRuntimeViewModel(wearablesRuntimeManager: wearablesRuntimeManager)
    )
  }

  var body: some View {
    ZStack {
      AssistantRuntimeView(
        viewModel: runtimeViewModel,
        onOpenFutureHardwareSetup: {
          isPresentingFutureHardwareSetup = true
        }
      )

      if showsStartupLoadingView {
        StartupLoadingView()
          .transition(.opacity)
      }
    }
    .animation(.easeOut(duration: 0.24), value: showsStartupLoadingView)
    .sheet(isPresented: $isPresentingFutureHardwareSetup) {
      FutureHardwareSetupView(wearablesRuntimeManager: wearablesRuntimeManager)
    }
    .onAppear {
      updateStartupLoadingVisibility(for: wearablesRuntimeManager.configurationState)
    }
    .onChange(of: wearablesRuntimeManager.configurationState) { _, newValue in
      updateStartupLoadingVisibility(for: newValue)
    }
  }
}

private extension MainAppView {
  func updateStartupLoadingVisibility(
    for configurationState: WearablesRuntimeManager.ConfigurationState
  ) {
    switch configurationState {
    case .idle, .configuring:
      break
    case .ready, .failed:
      showsStartupLoadingView = false
    }
  }
}
