import MWDATCore
import SwiftUI

struct SettingsView: View {
  @ObservedObject var appSettingsStore: AppSettingsStore
  @ObservedObject var viewModel: AssistantRuntimeViewModel
  @ObservedObject var wearablesRuntimeManager: WearablesRuntimeManager
  @Binding var scrollTarget: SettingsScrollTarget?

  let onOpenMetaSetup: () -> Void
  let onOpenWakePractice: () -> Void
  let onOpenProfileInterview: () -> Void

  @State private var backendBaseURL: String
  @State private var bearerToken: String
  @State private var backendErrorMessage = ""
  @State private var isValidatingBackend = false
  @FocusState private var focusedField: Field?

  private let validationClient = BackendValidationClient()

  init(
    appSettingsStore: AppSettingsStore,
    viewModel: AssistantRuntimeViewModel,
    wearablesRuntimeManager: WearablesRuntimeManager,
    scrollTarget: Binding<SettingsScrollTarget?>,
    onOpenMetaSetup: @escaping () -> Void,
    onOpenWakePractice: @escaping () -> Void,
    onOpenProfileInterview: @escaping () -> Void
  ) {
    self.appSettingsStore = appSettingsStore
    self.viewModel = viewModel
    self.wearablesRuntimeManager = wearablesRuntimeManager
    _scrollTarget = scrollTarget
    self.onOpenMetaSetup = onOpenMetaSetup
    self.onOpenWakePractice = onOpenWakePractice
    self.onOpenProfileInterview = onOpenProfileInterview
    _backendBaseURL = State(initialValue: appSettingsStore.settings.backendBaseURL)
    _bearerToken = State(initialValue: appSettingsStore.settings.bearerToken)
  }

  var body: some View {
    let readiness = HomeReadinessState(
      settings: appSettingsStore.settings,
      runtimeStatus: viewModel.status,
      wearablesRuntimeManager: wearablesRuntimeManager
    )

    PWScreen(title: "Settings", titleAlignment: .center, topPadding: PWSpace.md) {
      ScrollViewReader { proxy in
        ScrollView(showsIndicators: false) {
          VStack(alignment: .leading, spacing: PWSpace.section) {
            backendSection(readiness: readiness)
              .id(SettingsScrollTarget.backend)
            phoneVisionSection
              .id(SettingsScrollTarget.phoneVision)
            glassesSection(readiness: readiness)
              .id(SettingsScrollTarget.glasses)
            practiceSection
            helpSection
              .id(SettingsScrollTarget.help)
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
  enum Field {
    case backendURL
    case bearerToken
  }

  func backendSection(readiness: HomeReadinessState) -> some View {
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
        .focused($focusedField, equals: .backendURL)
        .onSubmit {
          focusedField = .bearerToken
        }

        PWTextFieldRow(
          label: "Bearer Token",
          placeholder: "Optional",
          text: $bearerToken,
          message: "Optional. Leave blank if your backend does not require bearer auth. PortWorld stores this token securely in Keychain.",
          isSecure: true,
          textInputAutocapitalization: .never,
          submitLabel: .go
        )
        .focused($focusedField, equals: .bearerToken)
        .onSubmit {
          Task {
            await validateAndSaveBackend()
          }
        }

        PWSecondaryButton(
          title: isValidatingBackend ? "Checking…" : backendButtonTitle,
          isDisabled: isValidatingBackend || normalized(backendBaseURL).isEmpty
        ) {
          Task {
            await validateAndSaveBackend()
          }
        }
      }
    }
  }

  func glassesSection(readiness: HomeReadinessState) -> some View {
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

        PWSecondaryButton(title: glassesButtonTitle) {
          Task {
            await performNavigationAction(onOpenMetaSetup)
          }
        }

        if wearablesRuntimeManager.registrationState == .registered {
          PWDestructiveButton(title: "Disconnect Glasses") {
            Task {
              await stopAssistantIfNeeded()
              wearablesRuntimeManager.disconnectGlasses()
            }
          }
        }
      }
    }
  }

  var phoneVisionSection: some View {
    PWCard {
      VStack(alignment: .leading, spacing: PWSpace.lg) {
        Text("Phone Vision")
          .font(PWTypography.headline)
          .foregroundStyle(PWColor.textPrimary)

        PWStatusRow(
          title: phoneVisionTitle,
          value: phoneVisionDetail,
          tone: phoneVisionTone,
          systemImage: phoneVisionSystemImage
        )

        if viewModel.status.phoneVisionUploadCount > 0 || viewModel.status.phoneVisionUploadFailureCount > 0 {
          PWStatusRow(
            title: "Uploads",
            value: "success=\(viewModel.status.phoneVisionUploadCount) failed=\(viewModel.status.phoneVisionUploadFailureCount)",
            tone: viewModel.status.phoneVisionUploadFailureCount > 0 ? .warning : .neutral,
            systemImage: "camera.aperture"
          )
        }

        if viewModel.status.phoneVisionLastErrorText.isEmpty == false {
          PWStatusRow(
            title: viewModel.status.phoneVisionHasAnalysisWarning ? "Analysis warning" : "Last error",
            value: viewModel.status.phoneVisionLastErrorText,
            tone: viewModel.status.phoneVisionHasAnalysisWarning ? .warning : .error,
            systemImage: "exclamationmark.triangle"
          )
        }

        PWSecondaryButton(
          title: viewModel.status.phoneVisionToggleTitle,
          isDisabled: viewModel.status.canTogglePhoneVision == false
        ) {
          viewModel.setPhoneVisionEnabled(viewModel.status.phoneVisionModeText != "enabled")
        }
      }
    }
  }

  var practiceSection: some View {
    PWCard {
      VStack(alignment: .leading, spacing: PWSpace.lg) {
        Text("Practice & Profile")
          .font(PWTypography.headline)
          .foregroundStyle(PWColor.textPrimary)

        PWSecondaryButton(title: "Replay Wake Practice") {
          Task {
            await performNavigationAction(onOpenWakePractice)
          }
        }

        PWSecondaryButton(title: "Replay Profile Onboarding") {
          Task {
            await performNavigationAction(onOpenProfileInterview)
          }
        }
      }
    }
  }

  var helpSection: some View {
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
          detail: "Open the glasses section and reconnect PortWorld through the Meta AI app."
        )

        SettingsHelpBlock(
          title: "Glasses not nearby",
          detail: "Bring your paired glasses nearby, keep Bluetooth enabled, and try reconnecting."
        )

        SettingsHelpBlock(
          title: "Speech recognition denied",
          detail: "Allow microphone and speech recognition access in iPhone Settings before replaying wake practice."
        )
      }
    }
  }

  var glassesButtonTitle: String {
    if wearablesRuntimeManager.registrationState == .registered {
      return "Reconnect Glasses"
    }

    return "Connect Glasses"
  }

  var backendButtonTitle: String {
    hasUnsavedBackendChanges ? "Save & Verify Backend" : "Re-check Backend"
  }

  var phoneVisionTitle: String {
    "Phone vision is \(viewModel.status.phoneVisionModeText)"
  }

  var phoneVisionDetail: String {
    let base = viewModel.status.phoneVisionDetailText
    let captureState = viewModel.status.phoneVisionCaptureStateText
    if captureState == "inactive" {
      return base
    }
    return "\(base)\nCurrent state: \(captureState)."
  }

  var phoneVisionTone: PWStatusTone {
    if viewModel.status.phoneVisionHasAnalysisWarning {
      return .warning
    }
    if viewModel.status.phoneVisionLastErrorText.isEmpty == false {
      return .error
    }
    if viewModel.status.phoneVisionModeText == "enabled" {
      return .success
    }
    return .neutral
  }

  var phoneVisionSystemImage: String {
    viewModel.status.phoneVisionModeText == "enabled" ? "camera.fill" : "camera"
  }

  var hasUnsavedBackendChanges: Bool {
    normalized(backendBaseURL) != appSettingsStore.settings.backendBaseURL ||
      normalized(bearerToken) != appSettingsStore.settings.bearerToken
  }

  var backendURLTone: PWFieldTone {
    if backendErrorMessage.isEmpty == false {
      return .error
    }

    if appSettingsStore.settings.validationState == .valid &&
      normalized(backendBaseURL) == appSettingsStore.settings.backendBaseURL &&
      hasUnsavedBackendChanges == false
    {
      return .success
    }

    return .normal
  }

  var backendURLMessage: String? {
    if backendErrorMessage.isEmpty == false {
      return backendErrorMessage
    }

    if hasUnsavedBackendChanges {
      return "Save and verify your changes before starting the assistant again."
    }

    if appSettingsStore.settings.validationState == .valid {
      return "Backend connection verified."
    }

    return "Base URL only. PortWorld derives the required endpoints automatically."
  }

  func validateAndSaveBackend() async {
    let trimmedURL = normalized(backendBaseURL)
    let trimmedToken = normalized(bearerToken)

    isValidatingBackend = true
    backendErrorMessage = ""

    await stopAssistantIfNeeded()

    do {
      try await validationClient.validate(baseURLString: trimmedURL, bearerToken: trimmedToken)
      appSettingsStore.updateBackendSettings(
        backendBaseURL: trimmedURL,
        bearerToken: trimmedToken,
        validationState: .valid
      )
      isValidatingBackend = false
    } catch let error as BackendValidationClient.ValidationError {
      appSettingsStore.updateBackendSettings(
        backendBaseURL: trimmedURL,
        bearerToken: trimmedToken,
        validationState: .invalid
      )
      backendErrorMessage = error.errorDescription ?? "Validation failed."
      isValidatingBackend = false
    } catch {
      appSettingsStore.updateBackendSettings(
        backendBaseURL: trimmedURL,
        bearerToken: trimmedToken,
        validationState: .invalid
      )
      backendErrorMessage = "Validation failed."
      isValidatingBackend = false
    }
  }

  func performNavigationAction(_ action: @escaping () -> Void) async {
    await stopAssistantIfNeeded()
    await MainActor.run {
      action()
    }
  }

  func stopAssistantIfNeeded() async {
    guard viewModel.status.canDeactivate else { return }
    await viewModel.deactivateAssistant()
  }

  func normalized(_ value: String) -> String {
    value.trimmingCharacters(in: .whitespacesAndNewlines)
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
