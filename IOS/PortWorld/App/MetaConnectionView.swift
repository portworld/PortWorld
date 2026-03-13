import MWDATCore
import SwiftUI

struct MetaConnectionView: View {
  @ObservedObject var wearablesRuntimeManager: WearablesRuntimeManager

  let onContinue: () -> Void
  let onSkip: () -> Void

  private var isInitializing: Bool {
    wearablesRuntimeManager.configurationState == .idle ||
      wearablesRuntimeManager.configurationState == .configuring
  }

  private var isInitializationFailed: Bool {
    wearablesRuntimeManager.configurationState == .failed
  }

  private var isRegistering: Bool {
    wearablesRuntimeManager.registrationState == .registering
  }

  private var isRegistered: Bool {
    wearablesRuntimeManager.registrationState == .registered
  }

  private var hasDiscoveredDevice: Bool {
    wearablesRuntimeManager.devices.isEmpty == false
  }

  private var isReadyToContinue: Bool {
    wearablesRuntimeManager.configurationState == .ready &&
      isRegistered &&
      hasDiscoveredDevice &&
      wearablesRuntimeManager.activeCompatibilityMessage == nil
  }

  var body: some View {
    PWOnboardingScaffold(
      style: .leadingContent,
      title: "Connect your Meta glasses",
      subtitle: "Pair PortWorld with the Meta ecosystem so the assistant can follow your glasses workflow.",
      content: {
        VStack(alignment: .leading, spacing: PWSpace.section) {
          prerequisiteBlock
          statusBlock

          if let compatibilityMessage = wearablesRuntimeManager.activeCompatibilityMessage {
            PWStatusRow(
              title: "Compatibility",
              value: compatibilityMessage,
              tone: .warning,
              systemImage: "exclamationmark.triangle"
            )
          }

          if isInitializationFailed {
            failureBlock
          }
        }
      },
      footer: {
        VStack(spacing: PWSpace.md) {
          PWOnboardingButton(
            title: primaryButtonTitle,
            isDisabled: primaryButtonDisabled,
            action: primaryAction
          )

          Button(isReadyToContinue ? "Continue later" : "Set up later") {
            onSkip()
          }
          .buttonStyle(.plain)
          .font(PWTypography.subbody)
          .foregroundStyle(PWColor.textSecondary)
        }
      }
    )
    .task {
      await wearablesRuntimeManager.startIfNeeded()
    }
    .alert("Error", isPresented: Binding(
      get: { wearablesRuntimeManager.showError },
      set: { wearablesRuntimeManager.showError = $0 }
    )) {
      Button("OK") {
        wearablesRuntimeManager.dismissError()
      }
    } message: {
      Text(wearablesRuntimeManager.errorMessage)
    }
  }
}

private extension MetaConnectionView {
  var prerequisiteBlock: some View {
    VStack(alignment: .leading, spacing: PWSpace.md) {
      Text("Before you start")
        .font(PWTypography.headline)
        .foregroundStyle(PWColor.textPrimary)

      MetaBulletRow(text: "Install the Meta AI app on this iPhone.")
      MetaBulletRow(text: "Pair your Ray-Ban Meta glasses in the Meta app first.")
      MetaBulletRow(text: "Keep Bluetooth enabled and your glasses nearby.")
    }
  }

  var statusBlock: some View {
    VStack(alignment: .leading, spacing: PWSpace.lg) {
      Text("Connection status")
        .font(PWTypography.headline)
        .foregroundStyle(PWColor.textPrimary)

      PWStatusRow(
        title: "Wearables SDK",
        value: sdkStatusDetail,
        tone: sdkStatusTone,
        systemImage: sdkStatusSymbol
      )

      PWStatusRow(
        title: "Meta authorization",
        value: authorizationStatusDetail,
        tone: authorizationStatusTone,
        systemImage: authorizationStatusSymbol
      )

      PWStatusRow(
        title: "Glasses discovery",
        value: discoveryStatusDetail,
        tone: discoveryStatusTone,
        systemImage: discoveryStatusSymbol
      )
    }
  }

  var failureBlock: some View {
    VStack(alignment: .leading, spacing: PWSpace.sm) {
      Text("Initialization details")
        .font(PWTypography.headline)
        .foregroundStyle(PWColor.textPrimary)

      Text(wearablesRuntimeManager.configurationErrorMessage ?? "Wearables initialization failed.")
        .font(PWTypography.subbody)
        .foregroundStyle(PWColor.textSecondary)

      if wearablesRuntimeManager.configurationDiagnostics.isEmpty == false {
        VStack(alignment: .leading, spacing: PWSpace.sm) {
          ForEach(Array(wearablesRuntimeManager.configurationDiagnostics.enumerated()), id: \.offset) { _, diagnostic in
            Text("• \(diagnostic)")
              .font(PWTypography.caption)
              .foregroundStyle(PWColor.textSecondary)
          }
        }
      }
    }
  }

  var primaryButtonTitle: String {
    if isInitializationFailed { return "Retry initialization" }
    if isReadyToContinue { return "Continue" }
    if isRegistering { return "Connecting..." }
    if isInitializing { return "Preparing..." }
    return "Connect my glasses"
  }

  var primaryButtonDisabled: Bool {
    isInitializing || isRegistering
  }

  func primaryAction() {
    if isInitializationFailed {
      Task { await wearablesRuntimeManager.retryConfiguration() }
      return
    }

    if isReadyToContinue {
      onContinue()
      return
    }

    wearablesRuntimeManager.connectGlasses()
  }

  var sdkStatusDetail: String {
    switch wearablesRuntimeManager.configurationState {
    case .idle, .configuring:
      return "Preparing shared Meta wearables support for the app."
    case .failed:
      return wearablesRuntimeManager.configurationErrorMessage ?? "Initialization failed."
    case .ready:
      return "Ready for Meta registration and device discovery."
    }
  }

  var sdkStatusTone: PWStatusTone {
    switch wearablesRuntimeManager.configurationState {
    case .idle, .configuring:
      return .neutral
    case .failed:
      return .error
    case .ready:
      return .success
    }
  }

  var sdkStatusSymbol: String {
    switch wearablesRuntimeManager.configurationState {
    case .idle, .configuring:
      return "gearshape"
    case .failed:
      return "xmark.octagon"
    case .ready:
      return "checkmark.circle"
    }
  }

  var authorizationStatusDetail: String {
    if isRegistering {
      return "Waiting for confirmation from the Meta app."
    }
    if isRegistered {
      return "Meta registration is complete."
    }
    return "PortWorld has not been authorized in the Meta app yet."
  }

  var authorizationStatusTone: PWStatusTone {
    if isRegistering { return .neutral }
    return isRegistered ? .success : .warning
  }

  var authorizationStatusSymbol: String {
    if isRegistering { return "hourglass" }
    return isRegistered ? "checkmark.circle" : "iphone.gen3.radiowaves.left.and.right"
  }

  var discoveryStatusDetail: String {
    if hasDiscoveredDevice {
      let suffix = wearablesRuntimeManager.devices.count == 1 ? "device" : "devices"
      return "\(wearablesRuntimeManager.devices.count) compatible \(suffix) available."
    }
    if isRegistered {
      return "Waiting for your glasses to appear nearby."
    }
    return "Discovery begins after Meta authorization completes."
  }

  var discoveryStatusTone: PWStatusTone {
    if hasDiscoveredDevice { return .success }
    return isRegistered ? .warning : .neutral
  }

  var discoveryStatusSymbol: String {
    if hasDiscoveredDevice { return "eyeglasses" }
    return isRegistered ? "antenna.radiowaves.left.and.right" : "dot.radiowaves.left.and.right"
  }
}

private struct MetaBulletRow: View {
  let text: String

  var body: some View {
    HStack(alignment: .top, spacing: PWSpace.md) {
      Circle()
        .fill(PWColor.borderStrong)
        .frame(width: 6, height: 6)
        .padding(.top, 7)

      Text(text)
        .font(PWTypography.body)
        .foregroundStyle(PWColor.textSecondary)
        .fixedSize(horizontal: false, vertical: true)

      Spacer(minLength: 0)
    }
  }
}
