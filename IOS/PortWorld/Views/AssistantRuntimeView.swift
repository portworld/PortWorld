// Primary runtime screen for the assistant runtime across phone and glasses routes.
import SwiftUI

struct AssistantRuntimeView: View {
  @ObservedObject private var viewModel: AssistantRuntimeViewModel
  private let onOpenFutureHardwareSetup: () -> Void
  @Environment(\.scenePhase) private var scenePhase

  init(
    viewModel: AssistantRuntimeViewModel,
    onOpenFutureHardwareSetup: @escaping () -> Void
  ) {
    self.viewModel = viewModel
    self.onOpenFutureHardwareSetup = onOpenFutureHardwareSetup
  }

  var body: some View {
    let status = viewModel.status

    ZStack {
      LinearGradient(
        colors: [
          Color(red: 0.04, green: 0.07, blue: 0.13),
          Color(red: 0.09, green: 0.14, blue: 0.24),
          Color(red: 0.05, green: 0.08, blue: 0.16),
        ],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
      )
      .ignoresSafeArea()

      ScrollView(showsIndicators: false) {
        VStack(alignment: .leading, spacing: 18) {
          VStack(alignment: .leading, spacing: 8) {
            Text("Assistant Runtime")
              .font(.system(.largeTitle, design: .rounded).weight(.bold))
              .foregroundColor(.white)

            Text("Primary assistant runtime. Phone mode remains stable, and the glasses route now activates through DAT lifecycle with live HFP audio when available, or a labeled phone fallback while developing with the mock device path.")
              .font(.system(.subheadline, design: .rounded).weight(.medium))
              .foregroundColor(.white.opacity(0.78))
          }

          PhoneAssistantPanel(title: "Runtime Route") {
            HStack(spacing: 10) {
              RuntimeRouteButton(
                title: "Phone",
                subtitle: "Live now",
                isSelected: status.selectedRoute == .phone,
                isEnabled: status.canChangeRoute
              ) {
                viewModel.selectRoute(.phone)
              }

              RuntimeRouteButton(
                title: "Glasses",
                subtitle: "DAT + audio aware",
                isSelected: status.selectedRoute == .glasses,
                isEnabled: status.canChangeRoute
              ) {
                viewModel.selectRoute(.glasses)
              }
            }

            HStack(spacing: 8) {
              Circle()
                .fill(glassesReadinessColor(status.glassesReadinessKind))
                .frame(width: 10, height: 10)
              Text(status.glassesReadinessTitle)
                .font(.system(.subheadline, design: .rounded).weight(.semibold))
                .foregroundColor(.white.opacity(0.95))
            }

            Text(status.glassesReadinessDetail)
              .font(.system(.caption, design: .rounded).weight(.medium))
              .foregroundColor(.white.opacity(0.74))

            LabeledContent("Glasses session", value: status.glassesSessionText)
            LabeledContent("Active glasses", value: status.activeGlassesDeviceText)
            LabeledContent("Glasses audio", value: status.glassesAudioModeText)
            LabeledContent("HFP route", value: status.hfpRouteText)
            LabeledContent("Mock workflow", value: status.mockWorkflowText)

            if status.selectedRoute == .phone {
              LabeledContent("Phone vision debug", value: status.debugPhoneVisionModeText)
              Text(status.debugPhoneVisionDetailText)
                .font(.system(.caption, design: .rounded).weight(.medium))
                .foregroundColor(.white.opacity(0.68))
            }

            if status.selectedRoute == .glasses {
              Text(status.glassesDevelopmentDetailText)
                .font(.system(.caption, design: .rounded).weight(.medium))
                .foregroundColor(.white.opacity(0.68))
            }
          }

          PhoneAssistantPanel(title: "Assistant State") {
            LabeledContent("Lifecycle", value: status.assistantRuntimeState.rawValue)
            LabeledContent("Session", value: status.sessionID)
            LabeledContent("Selected route", value: status.selectedRoute.rawValue)
            LabeledContent("Active route", value: status.activeRouteText)
            LabeledContent("Wake phrase", value: status.wakePhraseText)
            LabeledContent("Sleep phrase", value: status.sleepPhraseText)
          }

          PhoneAssistantPanel(title: "Subsystem Status") {
            LabeledContent("Audio mode", value: status.audioModeText)
            LabeledContent("Audio I/O", value: status.audioStatusText)
            LabeledContent("Backend client", value: status.backendStatusText)
            LabeledContent("Transport", value: status.transportStatusText)
            LabeledContent("Uplink", value: status.uplinkStatusText)
            LabeledContent("Playback", value: status.playbackStatusText)
            LabeledContent("Wake detector", value: status.wakeStatusText)
            LabeledContent("Playback route", value: status.playbackRouteText)
            LabeledContent("Vision capture", value: status.visionCaptureStateText)
            LabeledContent("Vision uploads", value: "\(status.visionUploadCount)")
            LabeledContent("Vision failures", value: "\(status.visionUploadFailureCount)")
          }

          PhoneAssistantPanel(title: "Notes") {
            Text(status.infoText.isEmpty ? "No runtime notes." : status.infoText)
              .font(.system(.body, design: .rounded))
              .foregroundColor(.white.opacity(0.82))
            if !status.visionLastErrorText.isEmpty {
              Text(status.visionLastErrorText)
                .font(.system(.footnote, design: .rounded).weight(.semibold))
                .foregroundColor(.orange.opacity(0.92))
            }
            if !status.errorText.isEmpty {
              Text(status.errorText)
                .font(.system(.footnote, design: .rounded).weight(.semibold))
                .foregroundColor(.red.opacity(0.9))
            }
          }
        }
        .padding(.horizontal, 20)
        .padding(.top, 24)
        .padding(.bottom, 160)
      }
    }
    .safeAreaInset(edge: .bottom) {
      VStack(spacing: 10) {
        if status.assistantRuntimeState == .inactive {
          Button {
            Task {
              await viewModel.activateAssistant()
            }
          } label: {
            Text(status.activationButtonTitle)
              .font(.system(.headline, design: .rounded).weight(.semibold))
              .foregroundColor(.white)
              .frame(maxWidth: .infinity)
              .frame(height: 52)
          }
          .buttonStyle(.plain)
          .background(status.canActivateSelectedRoute ? Color.appPrimary : Color.gray.opacity(0.55))
          .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
          .disabled(status.canActivateSelectedRoute == false)
        }

        if status.canDeactivate {
          Button {
            Task {
              await viewModel.deactivateAssistant()
            }
          } label: {
            Text("Deactivate Assistant")
              .font(.system(.headline, design: .rounded).weight(.semibold))
              .foregroundColor(.white)
              .frame(maxWidth: .infinity)
              .frame(height: 52)
          }
          .buttonStyle(.plain)
          .background(Color.red.opacity(0.82))
          .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        }

        if status.canEndConversation {
          Button {
            Task {
              await viewModel.endConversation()
            }
          } label: {
            Text("End Conversation")
              .font(.system(.headline, design: .rounded).weight(.semibold))
              .foregroundColor(.white)
              .frame(maxWidth: .infinity)
              .frame(height: 50)
          }
          .buttonStyle(.plain)
          .background(Color.orange.opacity(0.82))
          .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        }

        Button {
          onOpenFutureHardwareSetup()
        } label: {
          Text("Open Glasses Setup")
            .font(.system(.headline, design: .rounded).weight(.semibold))
            .foregroundColor(.white)
            .frame(maxWidth: .infinity)
            .frame(height: 50)
        }
        .buttonStyle(.plain)
        .background(Color.white.opacity(0.16))
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))

        #if DEBUG
          if status.selectedRoute == .phone {
            Button {
              viewModel.toggleDebugPhoneVisionMode()
            } label: {
              Text(status.debugPhoneVisionToggleTitle)
                .font(.system(.headline, design: .rounded).weight(.semibold))
                .foregroundColor(.white)
                .frame(maxWidth: .infinity)
                .frame(height: 50)
            }
            .buttonStyle(.plain)
            .background(status.canToggleDebugPhoneVision ? Color.white.opacity(0.16) : Color.gray.opacity(0.45))
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            .disabled(status.canToggleDebugPhoneVision == false)
          }
        #endif
      }
      .padding(.horizontal, 16)
      .padding(.top, 12)
      .padding(.bottom, 12)
      .background(Color.black.opacity(0.45))
    }
    .onAppear {
      viewModel.handleScenePhaseChange(scenePhase)
    }
    .onChange(of: scenePhase) { _, newPhase in
      viewModel.handleScenePhaseChange(newPhase)
    }
  }
}

private extension AssistantRuntimeView {
  func glassesReadinessColor(_ kind: GlassesReadinessKind) -> Color {
    switch kind {
    case .neutral:
      return Color.white.opacity(0.75)
    case .success:
      return Color.green.opacity(0.9)
    case .warning:
      return Color.orange.opacity(0.92)
    case .error:
      return Color.red.opacity(0.92)
    }
  }
}

private struct PhoneAssistantPanel<Content: View>: View {
  let title: String
  @ViewBuilder let content: Content

  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      Text(title)
        .font(.system(.headline, design: .rounded).weight(.semibold))
        .foregroundColor(.white)

      content
    }
    .padding(18)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(Color.white.opacity(0.1))
    .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
  }
}

private struct RuntimeRouteButton: View {
  let title: String
  let subtitle: String
  let isSelected: Bool
  let isEnabled: Bool
  let action: () -> Void

  var body: some View {
    Button(action: action) {
      VStack(alignment: .leading, spacing: 4) {
        Text(title)
          .font(.system(.subheadline, design: .rounded).weight(.semibold))
          .foregroundColor(.white)
        Text(subtitle)
          .font(.system(.caption, design: .rounded).weight(.medium))
          .foregroundColor(.white.opacity(0.72))
      }
      .frame(maxWidth: .infinity, alignment: .leading)
      .padding(.vertical, 12)
      .padding(.horizontal, 14)
      .background(isSelected ? Color.appPrimary.opacity(0.9) : Color.white.opacity(0.12))
      .overlay(
        RoundedRectangle(cornerRadius: 14, style: .continuous)
          .stroke(isSelected ? Color.white.opacity(0.36) : Color.white.opacity(0.12), lineWidth: 1)
      )
      .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
    .buttonStyle(.plain)
    .disabled(isEnabled == false)
    .opacity(isEnabled ? 1 : 0.65)
  }
}
