import AppIntents

struct StartPortWorldSessionIntent: AppIntent {
  static var title: LocalizedStringResource = "Start PortWorld Session"
  static var description = IntentDescription(
    "Launch PortWorld and start an assistant session through your connected glasses."
  )
  static var openAppWhenRun = true

  @MainActor
  func perform() async throws -> some IntentResult & ProvidesDialog {
    AppLaunchCommandStore.enqueue(.startSession)
    let dialog = AppLaunchCommandStore.startSessionIntentDialog()
    return .result(dialog: IntentDialog(stringLiteral: dialog))
  }
}

struct PortWorldAppShortcutsProvider: AppShortcutsProvider {
  static var appShortcuts: [AppShortcut] {
    AppShortcut(
      intent: StartPortWorldSessionIntent(),
      phrases: [
        "Start \(.applicationName) session",
        "Start assistant in \(.applicationName)",
        "Launch \(.applicationName) assistant session",
      ],
      shortTitle: "Start Session",
      systemImageName: "sparkles"
    )
  }
}
