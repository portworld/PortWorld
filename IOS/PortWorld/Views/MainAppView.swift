// Root shell for the phone-first assistant with secondary access to future hardware setup.
import SwiftUI

struct MainAppView: View {
  @StateObject private var runtimeViewModel: AssistantRuntimeViewModel
  @ObservedObject private var wearablesRuntimeManager: WearablesRuntimeManager
  @State private var isPresentingFutureHardwareSetup = false

  init(wearablesRuntimeManager: WearablesRuntimeManager) {
    self.wearablesRuntimeManager = wearablesRuntimeManager
    _runtimeViewModel = StateObject(
      wrappedValue: AssistantRuntimeViewModel(wearablesRuntimeManager: wearablesRuntimeManager)
    )
  }

  var body: some View {
    AssistantRuntimeView(
      viewModel: runtimeViewModel,
      onOpenFutureHardwareSetup: {
        isPresentingFutureHardwareSetup = true
      }
    )
    .sheet(isPresented: $isPresentingFutureHardwareSetup) {
      FutureHardwareSetupView(wearablesRuntimeManager: wearablesRuntimeManager)
    }
  }
}
