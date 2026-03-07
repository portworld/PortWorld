// Root shell for the phone-first assistant with secondary access to future hardware setup.
import SwiftUI

struct MainAppView: View {
  @StateObject private var phoneRuntimeViewModel = PhoneAssistantRuntimeViewModel()
  @ObservedObject private var wearablesRuntimeManager: WearablesRuntimeManager
  @State private var isPresentingFutureHardwareSetup = false

  init(wearablesRuntimeManager: WearablesRuntimeManager) {
    self.wearablesRuntimeManager = wearablesRuntimeManager
  }

  var body: some View {
    PhoneAssistantRuntimeView(
      viewModel: phoneRuntimeViewModel,
      onOpenFutureHardwareSetup: {
        isPresentingFutureHardwareSetup = true
      }
    )
    .sheet(isPresented: $isPresentingFutureHardwareSetup) {
      FutureHardwareSetupView(wearablesRuntimeManager: wearablesRuntimeManager)
    }
  }
}
