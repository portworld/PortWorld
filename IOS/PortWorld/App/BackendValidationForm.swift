import SwiftUI

enum BackendValidationField: Hashable {
  case backendURL
  case bearerToken
}

enum BackendValidationForm {
  static func normalized(_ value: String) -> String {
    value.trimmingCharacters(in: .whitespacesAndNewlines)
  }

  static func hasUnsavedChanges(
    backendBaseURL: String,
    bearerToken: String,
    savedSettings: AppSettingsStore.Settings
  ) -> Bool {
    normalized(backendBaseURL) != savedSettings.backendBaseURL ||
      normalized(bearerToken) != savedSettings.bearerToken
  }

  static func backendURLTone(
    backendBaseURL: String,
    bearerToken: String,
    savedSettings: AppSettingsStore.Settings,
    errorMessage: String
  ) -> PWFieldTone {
    if errorMessage.isEmpty == false {
      return .error
    }

    if savedSettings.validationState == .valid &&
      normalized(backendBaseURL) == savedSettings.backendBaseURL &&
      hasUnsavedChanges(
        backendBaseURL: backendBaseURL,
        bearerToken: bearerToken,
        savedSettings: savedSettings
      ) == false
    {
      return .success
    }

    return .normal
  }

  static func backendURLMessage(
    backendBaseURL: String,
    bearerToken: String,
    savedSettings: AppSettingsStore.Settings,
    errorMessage: String,
    unsavedChangesMessage: String?
  ) -> String? {
    if errorMessage.isEmpty == false {
      return errorMessage
    }

    if let unsavedChangesMessage,
      hasUnsavedChanges(
        backendBaseURL: backendBaseURL,
        bearerToken: bearerToken,
        savedSettings: savedSettings
      )
    {
      return unsavedChangesMessage
    }

    if savedSettings.validationState == .valid &&
      normalized(backendBaseURL) == savedSettings.backendBaseURL &&
      hasUnsavedChanges(
        backendBaseURL: backendBaseURL,
        bearerToken: bearerToken,
        savedSettings: savedSettings
      ) == false
    {
      return "Backend connection verified."
    }

    return "Base URL only. PortWorld derives the required endpoints automatically."
  }

  static func validateAndSave(
    backendBaseURL: String,
    bearerToken: String,
    validationClient: BackendValidationClient,
    beforeValidation: (() async -> Void)? = nil,
    saveSettings: (String, String, AppSettingsStore.BackendValidationState) -> Void
  ) async -> String? {
    let trimmedURL = normalized(backendBaseURL)
    let trimmedToken = normalized(bearerToken)

    if let beforeValidation {
      await beforeValidation()
    }

    do {
      try await validationClient.validate(baseURLString: trimmedURL, bearerToken: trimmedToken)
      saveSettings(trimmedURL, trimmedToken, .valid)
      return nil
    } catch let error as BackendValidationClient.ValidationError {
      saveSettings(trimmedURL, trimmedToken, .invalid)
      return error.errorDescription ?? "Validation failed."
    } catch {
      saveSettings(trimmedURL, trimmedToken, .invalid)
      return error.localizedDescription
    }
  }
}

struct BackendValidationFields: View {
  @Binding var backendBaseURL: String
  @Binding var bearerToken: String

  let backendURLMessage: String?
  let backendURLTone: PWFieldTone
  let bearerTokenMessage: String
  let focusedField: FocusState<BackendValidationField?>.Binding
  let onBackendURLSubmit: () -> Void
  let onBearerTokenSubmit: () -> Void

  init(
    backendBaseURL: Binding<String>,
    bearerToken: Binding<String>,
    backendURLMessage: String?,
    backendURLTone: PWFieldTone,
    bearerTokenMessage: String,
    focusedField: FocusState<BackendValidationField?>.Binding,
    onBackendURLSubmit: @escaping () -> Void,
    onBearerTokenSubmit: @escaping () -> Void
  ) {
    _backendBaseURL = backendBaseURL
    _bearerToken = bearerToken
    self.backendURLMessage = backendURLMessage
    self.backendURLTone = backendURLTone
    self.bearerTokenMessage = bearerTokenMessage
    self.focusedField = focusedField
    self.onBackendURLSubmit = onBackendURLSubmit
    self.onBearerTokenSubmit = onBearerTokenSubmit
  }

  var body: some View {
    VStack(alignment: .leading, spacing: PWSpace.lg) {
      PWTextFieldRow(
        label: "Backend URL",
        placeholder: "https://your-backend.example.com",
        text: $backendBaseURL,
        message: backendURLMessage,
        tone: backendURLTone,
        textInputAutocapitalization: .never,
        keyboardType: .URL,
        submitLabel: .next
      )
      .focused(focusedField, equals: .backendURL)
      .onSubmit(onBackendURLSubmit)

      PWTextFieldRow(
        label: "Bearer Token",
        placeholder: "Optional",
        text: $bearerToken,
        message: bearerTokenMessage,
        isSecure: true,
        textInputAutocapitalization: .never,
        submitLabel: .go
      )
      .focused(focusedField, equals: .bearerToken)
      .onSubmit(onBearerTokenSubmit)
    }
  }
}
