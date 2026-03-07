// Root shell for the phone-first assistant with secondary access to future hardware setup.
import SwiftUI

struct MainAppView: View {
  @StateObject private var phoneRuntimeViewModel = PhoneAssistantRuntimeViewModel()
  @StateObject private var futureHardwareSetupModel = FutureHardwareSetupModel()
  @State private var isPresentingFutureHardwareSetup = false

  var body: some View {
    PhoneAssistantRuntimeView(
      viewModel: phoneRuntimeViewModel,
      onOpenFutureHardwareSetup: {
        isPresentingFutureHardwareSetup = true
      }
    )
    .sheet(isPresented: $isPresentingFutureHardwareSetup) {
      FutureHardwareSetupView(model: futureHardwareSetupModel)
    }
  }
}
