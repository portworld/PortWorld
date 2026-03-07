// Primary runtime screen for the shipping phone-only assistant flow.
import SwiftUI

struct PhoneAssistantRuntimeView: View {
  @ObservedObject private var viewModel: PhoneAssistantRuntimeViewModel
  private let onOpenFutureHardwareSetup: () -> Void
  @Environment(\.scenePhase) private var scenePhase

  init(
    viewModel: PhoneAssistantRuntimeViewModel,
    onOpenFutureHardwareSetup: @escaping () -> Void
  ) {
    self.viewModel = viewModel
    self.onOpenFutureHardwareSetup = onOpenFutureHardwareSetup
  }

  var body: some View {
    let store = viewModel.store

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
            Text("Phone-Only Assistant")
              .font(.system(.largeTitle, design: .rounded).weight(.bold))
              .foregroundColor(.white)

            Text("Primary assistant runtime. Phone mic, speaker, wake, and backend conversation run without the legacy DAT session stack.")
              .font(.system(.subheadline, design: .rounded).weight(.medium))
              .foregroundColor(.white.opacity(0.78))
          }

          PhoneAssistantPanel(title: "Assistant State") {
            LabeledContent("Lifecycle", value: store.assistantRuntimeState.rawValue)
            LabeledContent("Session", value: store.sessionID)
            LabeledContent("Wake phrase", value: store.wakePhraseText)
            LabeledContent("Sleep phrase", value: store.sleepPhraseText)
          }

          PhoneAssistantPanel(title: "Subsystem Status") {
            LabeledContent("Phone audio", value: store.audioStatusText)
            LabeledContent("Backend client", value: store.backendStatusText)
            LabeledContent("Transport", value: store.transportStatusText)
            LabeledContent("Uplink", value: store.uplinkStatusText)
            LabeledContent("Playback", value: store.playbackStatusText)
            LabeledContent("Wake detector", value: store.wakeStatusText)
            LabeledContent("Playback route", value: store.playbackRouteText)
          }

          PhoneAssistantPanel(title: "Notes") {
            Text(store.infoText.isEmpty ? "No runtime notes." : store.infoText)
              .font(.system(.body, design: .rounded))
              .foregroundColor(.white.opacity(0.82))
            if !store.errorText.isEmpty {
              Text(store.errorText)
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
        if store.canActivate {
          Button {
            Task {
              await viewModel.activateAssistant()
            }
          } label: {
            Text("Activate Assistant")
              .font(.system(.headline, design: .rounded).weight(.semibold))
              .foregroundColor(.white)
              .frame(maxWidth: .infinity)
              .frame(height: 52)
          }
          .buttonStyle(.plain)
          .background(Color.appPrimary)
          .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        }

        if store.canDeactivate {
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

        if store.canEndConversation {
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
