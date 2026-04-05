import MWDATCore
import SwiftUI

struct MetaConnectionView: View {
  @ObservedObject var wearablesRuntimeManager: WearablesRuntimeManager
  @State private var hasAutoRequestedMetaPermission = false

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

  private var isRequestingDiscoveryPermission: Bool {
    wearablesRuntimeManager.discoveryPermissionState == .requesting
  }

  private var hasGrantedDiscoveryPermission: Bool {
    wearablesRuntimeManager.hasSatisfiedDiscoveryPermission
  }

  private var isReadyToContinue: Bool {
    wearablesRuntimeManager.configurationState == .ready &&
      isRegistered &&
      hasGrantedDiscoveryPermission
  }

  var body: some View {
    PWOnboardingScaffold(
      style: .leadingContent,
      title: "Connect your Meta glasses",
      subtitle: "Pair PortWorld with the Meta ecosystem so the assistant can follow your glasses workflow. If you are not ready now, you can finish this later from Home or Settings.",
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

          Button(deferButtonTitle) {
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
      await maybeAutoRequestMetaPermission()
    }
    .onChange(of: wearablesRuntimeManager.registrationState) { _, _ in
      Task {
        await maybeAutoRequestMetaPermission()
      }
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
        title: "Meta camera access",
        value: cameraAccessStatusDetail,
        tone: cameraAccessStatusTone,
        systemImage: cameraAccessStatusSymbol
      )

      PWStatusRow(
        title: "Glasses discovery",
        value: discoveryStatusDetail,
        tone: discoveryStatusTone,
        systemImage: discoveryStatusSymbol
      )

      PWStatusRow(
        title: "Glasses audio",
        value: audioRouteStatusDetail,
        tone: audioRouteStatusTone,
        systemImage: audioRouteStatusSymbol
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
    if shouldRequestCameraAccess { return cameraAccessButtonTitle }
    if isRegistering { return "Connecting..." }
    if isInitializing { return "Preparing..." }
    return "Connect my glasses"
  }

  var deferButtonTitle: String {
    if isReadyToContinue {
      return "Finish setup later from the app"
    }

    return "Set up later from Home or Settings"
  }

  var primaryButtonDisabled: Bool {
    isInitializing || isRegistering || isRequestingDiscoveryPermission
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

    if shouldRequestCameraAccess {
      Task {
        await wearablesRuntimeManager.requestDiscoveryPermissionFromMetaOnboarding()
      }
      return
    }

    wearablesRuntimeManager.connectGlasses()
  }

  func maybeAutoRequestMetaPermission() async {
    guard hasAutoRequestedMetaPermission == false else { return }
    guard wearablesRuntimeManager.configurationState == .ready else { return }
    guard isRegistered else { return }

    switch wearablesRuntimeManager.discoveryPermissionState {
    case .unknown, .needsApproval, .failed:
      hasAutoRequestedMetaPermission = true
      await wearablesRuntimeManager.requestDiscoveryPermissionFromMetaOnboarding()
    case .requesting, .granted:
      return
    }
  }

  var shouldRequestCameraAccess: Bool {
    guard wearablesRuntimeManager.configurationState == .ready else { return false }
    guard isRegistered else { return false }
    switch wearablesRuntimeManager.discoveryPermissionState {
    case .unknown, .needsApproval, .failed:
      return true
    case .requesting, .granted:
      return false
    }
  }

  var cameraAccessButtonTitle: String {
    switch wearablesRuntimeManager.discoveryPermissionState {
    case .failed:
      return "Retry camera access"
    case .unknown, .needsApproval:
      return "Grant camera access"
    case .requesting:
      return "Opening Meta AI..."
    case .granted:
      return "Continue"
    }
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

  var cameraAccessStatusDetail: String {
    switch wearablesRuntimeManager.discoveryPermissionState {
    case .unknown:
      if isRegistered {
        return "PortWorld still needs Meta camera access so the glasses can appear here."
      }
      return "Camera access starts after Meta authorization completes."
    case .requesting:
      return "Confirm camera access in the Meta AI app now."
    case .granted:
      return "Meta camera access is granted."
    case .needsApproval:
      return "Open the Meta AI app and approve camera access for PortWorld."
    case .failed(let message):
      return message
    }
  }

  var cameraAccessStatusTone: PWStatusTone {
    switch wearablesRuntimeManager.discoveryPermissionState {
    case .granted:
      return .success
    case .requesting:
      return .neutral
    case .unknown, .needsApproval:
      return isRegistered ? .warning : .neutral
    case .failed:
      return .error
    }
  }

  var cameraAccessStatusSymbol: String {
    switch wearablesRuntimeManager.discoveryPermissionState {
    case .granted:
      return "checkmark.circle"
    case .requesting:
      return "camera.viewfinder"
    case .unknown, .needsApproval:
      return "camera"
    case .failed:
      return "xmark.octagon"
    }
  }

  var discoveryStatusDetail: String {
    if hasDiscoveredDevice {
      let suffix = wearablesRuntimeManager.devices.count == 1 ? "device" : "devices"
      return "\(wearablesRuntimeManager.devices.count) compatible \(suffix) available."
    }
    if hasGrantedDiscoveryPermission == false {
      return "Glasses discovery begins after Meta camera access is approved."
    }
    if isRegistered {
      return "Keep your paired glasses nearby so they appear here."
    }
    return "Discovery begins after Meta authorization completes."
  }

  var discoveryStatusTone: PWStatusTone {
    if hasDiscoveredDevice { return .success }
    return hasGrantedDiscoveryPermission ? .warning : .neutral
  }

  var discoveryStatusSymbol: String {
    if hasDiscoveredDevice { return "eyeglasses" }
    return isRegistered ? "antenna.radiowaves.left.and.right" : "dot.radiowaves.left.and.right"
  }

  var audioRouteStatusDetail: String {
    switch wearablesRuntimeManager.hfpRouteAvailability {
    case .active:
      return "Your glasses audio route is active now."
    case .selectable:
      return "Your glasses audio route is available and PortWorld can request it when onboarding starts."
    case .unknown:
      return "PortWorld will request the glasses audio route when onboarding starts."
    case .unavailable:
      return "Connect the glasses audio route before starting the onboarding interview."
    }
  }

  var audioRouteStatusTone: PWStatusTone {
    switch wearablesRuntimeManager.hfpRouteAvailability {
    case .active, .selectable:
      return .success
    case .unknown:
      return .neutral
    case .unavailable:
      return .warning
    }
  }

  var audioRouteStatusSymbol: String {
    switch wearablesRuntimeManager.hfpRouteAvailability {
    case .active, .selectable:
      return "checkmark.circle"
    case .unknown:
      return "dot.radiowaves.left.and.right"
    case .unavailable:
      return "waveform.badge.exclamationmark"
    }
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
