import SwiftUI

struct SettingsView: View {
  let settings: AppSettingsStore.Settings
  let readiness: HomeReadinessState
  let isAssistantActive: Bool
  let isGlassesRegistered: Bool
  @Binding var scrollTarget: SettingsScrollTarget?

  let shouldShowProfileSetupCallToAction: Bool
  let onUpdateBackendSettings: (String, String, AppSettingsStore.BackendValidationState) -> Void
  let onStopAssistantIfNeeded: () async -> Void
  let onOpenMetaSetup: () -> Void
  let onOpenProfileSetup: () -> Void
  let onDisconnectGlasses: () -> Void

  @State private var backendBaseURL: String
  @State private var bearerToken: String
  @State private var backendErrorMessage = ""
  @State private var isValidatingBackend = false
  @FocusState private var focusedField: BackendValidationField?

  private let validationClient = BackendValidationClient()

  init(
    settings: AppSettingsStore.Settings,
    readiness: HomeReadinessState,
    isAssistantActive: Bool,
    isGlassesRegistered: Bool,
    scrollTarget: Binding<SettingsScrollTarget?>,
    shouldShowProfileSetupCallToAction: Bool,
    onUpdateBackendSettings: @escaping (String, String, AppSettingsStore.BackendValidationState) -> Void,
    onStopAssistantIfNeeded: @escaping () async -> Void,
    onOpenMetaSetup: @escaping () -> Void,
    onOpenProfileSetup: @escaping () -> Void,
    onDisconnectGlasses: @escaping () -> Void
  ) {
    self.settings = settings
    self.readiness = readiness
    self.isAssistantActive = isAssistantActive
    self.isGlassesRegistered = isGlassesRegistered
    _scrollTarget = scrollTarget
    self.shouldShowProfileSetupCallToAction = shouldShowProfileSetupCallToAction
    self.onUpdateBackendSettings = onUpdateBackendSettings
    self.onStopAssistantIfNeeded = onStopAssistantIfNeeded
    self.onOpenMetaSetup = onOpenMetaSetup
    self.onOpenProfileSetup = onOpenProfileSetup
    self.onDisconnectGlasses = onDisconnectGlasses
    _backendBaseURL = State(initialValue: settings.backendBaseURL)
    _bearerToken = State(initialValue: settings.bearerToken)
  }

  var body: some View {
    PWScreen(title: "Settings", titleAlignment: .center, topPadding: PWSpace.md) {
      ScrollViewReader { proxy in
        ScrollView(showsIndicators: false) {
          VStack(alignment: .leading, spacing: PWSpace.section) {
            SettingsBackendSection(
              readiness: readiness,
              backendBaseURL: $backendBaseURL,
              bearerToken: $bearerToken,
              backendURLMessage: backendURLMessage,
              backendURLTone: backendURLTone,
              focusedField: $focusedField,
              buttonTitle: isValidatingBackend ? "Checking…" : backendButtonTitle,
              isButtonDisabled: isValidatingBackend || BackendValidationForm.normalized(backendBaseURL).isEmpty,
              onBackendURLSubmit: focusBearerToken,
              onBearerTokenSubmit: submitBackendValidation,
              onValidate: submitBackendValidation
            )
            .id(SettingsScrollTarget.backend)

            SettingsGlassesSection(
              readiness: readiness,
              isGlassesRegistered: isGlassesRegistered,
              shouldShowProfileSetupCallToAction: shouldShowProfileSetupCallToAction,
              glassesButtonTitle: glassesButtonTitle,
              onOpenMetaSetup: openMetaSetup,
              onOpenProfileSetup: openProfileSetup,
              onDisconnectGlasses: disconnectGlasses
            )
            .id(SettingsScrollTarget.glasses)

            SettingsHelpSection()
          }
          .padding(.bottom, PWSpace.hero)
        }
        .onChange(of: scrollTarget) { _, newValue in
          guard let newValue else { return }
          withAnimation(.easeOut(duration: 0.24)) {
            proxy.scrollTo(newValue, anchor: .top)
          }
          scrollTarget = nil
        }
      }
    }
  }
}

private extension SettingsView {
  var glassesButtonTitle: String {
    if isGlassesRegistered {
      return "Reconnect Glasses"
    }

    return "Connect Glasses"
  }

  var backendButtonTitle: String {
    hasUnsavedBackendChanges ? "Save & Verify Backend" : "Re-check Backend"
  }

  var hasUnsavedBackendChanges: Bool {
    BackendValidationForm.hasUnsavedChanges(
      backendBaseURL: backendBaseURL,
      bearerToken: bearerToken,
      savedSettings: settings
    )
  }

  var backendURLTone: PWFieldTone {
    BackendValidationForm.backendURLTone(
      backendBaseURL: backendBaseURL,
      bearerToken: bearerToken,
      savedSettings: settings,
      errorMessage: backendErrorMessage
    )
  }

  var backendURLMessage: String? {
    BackendValidationForm.backendURLMessage(
      backendBaseURL: backendBaseURL,
      bearerToken: bearerToken,
      savedSettings: settings,
      errorMessage: backendErrorMessage,
      unsavedChangesMessage: "Save and verify your changes before starting the assistant again."
    )
  }

  func focusBearerToken() {
    focusedField = .bearerToken
  }

  func submitBackendValidation() {
    Task {
      await validateAndSaveBackend()
    }
  }

  func openMetaSetup() {
    Task {
      await performNavigationAction(onOpenMetaSetup)
    }
  }

  func openProfileSetup() {
    Task {
      await performNavigationAction(onOpenProfileSetup)
    }
  }

  func disconnectGlasses() {
    Task {
      await stopAssistantIfNeeded()
      onDisconnectGlasses()
    }
  }

  func validateAndSaveBackend() async {
    isValidatingBackend = true
    backendErrorMessage = ""
    let validationError = await BackendValidationForm.validateAndSave(
      backendBaseURL: backendBaseURL,
      bearerToken: bearerToken,
      validationClient: validationClient,
      beforeValidation: stopAssistantIfNeeded,
      saveSettings: onUpdateBackendSettings
    )
    backendErrorMessage = validationError ?? ""
    isValidatingBackend = false
  }

  func stopAssistantIfNeeded() async {
    if isAssistantActive {
      await onStopAssistantIfNeeded()
    }
  }

  func performNavigationAction(_ action: @escaping () -> Void) async {
    await stopAssistantIfNeeded()
    await MainActor.run {
      action()
    }
  }
}

private struct SettingsBackendSection: View {
  let readiness: HomeReadinessState
  @Binding var backendBaseURL: String
  @Binding var bearerToken: String
  let backendURLMessage: String?
  let backendURLTone: PWFieldTone
  let focusedField: FocusState<BackendValidationField?>.Binding
  let buttonTitle: String
  let isButtonDisabled: Bool
  let onBackendURLSubmit: () -> Void
  let onBearerTokenSubmit: () -> Void
  let onValidate: () -> Void

  var body: some View {
    PWCard {
      VStack(alignment: .leading, spacing: PWSpace.lg) {
        Text("Backend")
          .font(PWTypography.headline)
          .foregroundStyle(PWColor.textPrimary)

        PWStatusRow(
          title: readiness.backendStatus.title,
          value: readiness.backendStatus.detail,
          tone: readiness.backendStatus.tone,
          systemImage: readiness.backendStatus.systemImage
        )

        BackendValidationFields(
          backendBaseURL: $backendBaseURL,
          bearerToken: $bearerToken,
          backendURLMessage: backendURLMessage,
          backendURLTone: backendURLTone,
          bearerTokenMessage: "Optional. Leave blank if your backend does not require bearer auth. PortWorld stores this token securely in Keychain.",
          focusedField: focusedField,
          onBackendURLSubmit: onBackendURLSubmit,
          onBearerTokenSubmit: onBearerTokenSubmit
        )

        PWSecondaryButton(
          title: buttonTitle,
          isDisabled: isButtonDisabled,
          action: onValidate
        )
      }
    }
  }
}

private struct SettingsGlassesSection: View {
  let readiness: HomeReadinessState
  let isGlassesRegistered: Bool
  let shouldShowProfileSetupCallToAction: Bool
  let glassesButtonTitle: String
  let onOpenMetaSetup: () -> Void
  let onOpenProfileSetup: () -> Void
  let onDisconnectGlasses: () -> Void

  var body: some View {
    PWCard {
      VStack(alignment: .leading, spacing: PWSpace.lg) {
        Text("Glasses")
          .font(PWTypography.headline)
          .foregroundStyle(PWColor.textPrimary)

        PWStatusRow(
          title: readiness.glassesStatus.title,
          value: readiness.glassesStatus.detail,
          tone: readiness.glassesStatus.tone,
          systemImage: readiness.glassesStatus.systemImage
        )

        PWSecondaryButton(title: glassesButtonTitle, action: onOpenMetaSetup)

        if shouldShowProfileSetupCallToAction && readiness.canActivateAssistant {
          PWSecondaryButton(title: "Start profile setup", action: onOpenProfileSetup)
        }

        if isGlassesRegistered {
          PWDestructiveButton(title: "Disconnect Glasses", action: onDisconnectGlasses)
        }
      }
    }
  }
}

private struct SettingsHelpSection: View {
  var body: some View {
    PWCard {
      VStack(alignment: .leading, spacing: PWSpace.lg) {
        Text("Help")
          .font(PWTypography.headline)
          .foregroundStyle(PWColor.textPrimary)

        SettingsHelpBlock(
          title: "Backend unreachable",
          detail: "Confirm the backend URL is correct, the server is running, and your phone can reach it on the current network."
        )

        SettingsHelpBlock(
          title: "Invalid bearer token",
          detail: "Re-enter the bearer token in Backend settings and run the backend check again."
        )

        SettingsHelpBlock(
          title: "Meta connection incomplete",
          detail: "Open the glasses section and reconnect PortWorld through the Meta AI app. You can also finish profile setup here once your glasses are ready."
        )

        SettingsHelpBlock(
          title: "Glasses not nearby",
          detail: "Bring your paired glasses nearby, keep Bluetooth enabled, and try reconnecting."
        )
      }
    }
  }
}

private struct SettingsHelpBlock: View {
  let title: String
  let detail: String

  var body: some View {
    VStack(alignment: .leading, spacing: PWSpace.xs) {
      Text(title)
        .font(PWTypography.headline)
        .foregroundStyle(PWColor.textPrimary)

      Text(detail)
        .font(PWTypography.caption)
        .foregroundStyle(PWColor.textSecondary)
        .fixedSize(horizontal: false, vertical: true)
    }
  }
}
