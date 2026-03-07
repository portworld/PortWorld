// App entry point for the shipping iPhone-first assistant experience.
import SwiftUI

@main
struct PortWorldApp: App {
  @StateObject private var wearablesRuntimeManager = WearablesRuntimeManager()

  var body: some Scene {
    WindowGroup {
      MainAppView(wearablesRuntimeManager: wearablesRuntimeManager)
        .task {
          await wearablesRuntimeManager.startIfNeeded()
        }
        .onOpenURL { url in
          Task {
            await wearablesRuntimeManager.handleIncomingURL(url)
          }
        }
    }
  }
}
