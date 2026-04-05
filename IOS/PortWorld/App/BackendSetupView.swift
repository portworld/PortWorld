import SwiftUI

struct BackendSetupView: View {
  @ObservedObject var appSettingsStore: AppSettingsStore

  let onValidationSuccess: () -> Void

  @State private var backendBaseURL: String
  @State private var bearerToken: String
  @State private var errorMessage = ""
  @State private var isValidating = false
  @FocusState private var focusedField: BackendValidationField?

  private let validationClient = BackendValidationClient()

  init(
    appSettingsStore: AppSettingsStore,
    onValidationSuccess: @escaping () -> Void
  ) {
    self.appSettingsStore = appSettingsStore
    self.onValidationSuccess = onValidationSuccess
    _backendBaseURL = State(initialValue: appSettingsStore.settings.backendBaseURL)
    _bearerToken = State(initialValue: appSettingsStore.settings.bearerToken)
  }

  var body: some View {
    PWOnboardingScaffold(
      style: .leadingContent,
      title: "Add your backend",
      subtitle: "Use your self-hosted PortWorld URL. Add a bearer token only if your deployment requires it.",
      content: {
        BackendValidationFields(
          backendBaseURL: $backendBaseURL,
          bearerToken: $bearerToken,
          backendURLMessage: backendURLMessage,
          backendURLTone: backendURLTone,
          bearerTokenMessage: "Optional. Leave blank if your backend does not require bearer auth.",
          focusedField: $focusedField,
          onBackendURLSubmit: {
            focusedField = .bearerToken
          },
          onBearerTokenSubmit: {
            Task { await validateAndContinue() }
          }
        )

        if statusMessage.isEmpty == false {
          PWStatusRow(
            title: statusTitle,
            value: statusMessage,
            tone: statusTone,
            systemImage: statusSymbol
          )
          .padding(.top, PWSpace.sm)
        }
      },
      footer: {
        PWOnboardingButton(
          title: isValidating ? "Checking..." : "Verify backend",
          isDisabled: isContinueDisabled,
          action: {
            Task { await validateAndContinue() }
          }
        )
      }
    )
  }
}

private extension BackendSetupView {
  var isContinueDisabled: Bool {
    isValidating || BackendValidationForm.normalized(backendBaseURL).isEmpty
  }

  var backendURLTone: PWFieldTone {
    BackendValidationForm.backendURLTone(
      backendBaseURL: backendBaseURL,
      bearerToken: bearerToken,
      savedSettings: appSettingsStore.settings,
      errorMessage: errorMessage
    )
  }

  var backendURLMessage: String? {
    BackendValidationForm.backendURLMessage(
      backendBaseURL: backendBaseURL,
      bearerToken: bearerToken,
      savedSettings: appSettingsStore.settings,
      errorMessage: errorMessage,
      unsavedChangesMessage: nil
    )
  }

  var statusTitle: String {
    if isValidating {
      return "Checking backend"
    }

    switch appSettingsStore.settings.validationState {
    case .valid:
      return "Backend ready"
    case .invalid:
      return "Validation failed"
    case .unknown:
      return ""
    }
  }

  var statusMessage: String {
    if isValidating {
      return "Checking connectivity and deployment readiness."
    }

    if errorMessage.isEmpty == false {
      return errorMessage
    }

    switch appSettingsStore.settings.validationState {
    case .valid:
      return "The backend is reachable and ready for onboarding."
    case .invalid, .unknown:
      return ""
    }
  }

  var statusTone: PWStatusTone {
    if isValidating { return .neutral }
    if errorMessage.isEmpty == false { return .error }
    return appSettingsStore.settings.validationState == .valid ? .success : .neutral
  }

  var statusSymbol: String? {
    if isValidating { return "network" }
    if errorMessage.isEmpty == false { return "exclamationmark.triangle" }
    return appSettingsStore.settings.validationState == .valid ? "checkmark.circle" : nil
  }

  func validateAndContinue() async {
    isValidating = true
    errorMessage = ""

    let validationError = await BackendValidationForm.validateAndSave(
      backendBaseURL: backendBaseURL,
      bearerToken: bearerToken,
      validationClient: validationClient,
      saveSettings: { backendBaseURL, bearerToken, validationState in
        appSettingsStore.updateBackendSettings(
          backendBaseURL: backendBaseURL,
          bearerToken: bearerToken,
          validationState: validationState
        )
      }
    )

    isValidating = false
    errorMessage = validationError ?? ""

    if validationError == nil {
      onValidationSuccess()
    }
  }
}
