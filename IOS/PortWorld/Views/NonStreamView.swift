// NonStreamView.swift
//
// Default screen to show getting started tips after app connection
// Initiates runtime activation

import MWDATCore
import SwiftUI

struct NonStreamView: View {
  let viewModel: SessionViewModel
  let store: SessionStateStore
  @ObservedObject var wearablesVM: WearablesViewModel
  @State private var sheetHeight: CGFloat = 300

  var body: some View {
    ZStack {
      BackgroundGradientView()

      VStack(spacing: 0) {
        ScrollView(showsIndicators: false) {
          VStack(spacing: 18) {
            topBar
            heroCard
            connectionCard
            RuntimeStatusPanelView(store: store)
              .padding(.top, 2)
          }
          .padding(.horizontal, 20)
          .padding(.vertical, 18)
        }

        bottomActionBar
      }
    }
    .sheet(isPresented: $wearablesVM.showGettingStartedSheet) {
      if #available(iOS 16.0, *) {
        GettingStartedSheetView(height: $sheetHeight)
          .presentationDetents([.height(sheetHeight)])
          .presentationDragIndicator(.visible)
      } else {
        GettingStartedSheetView(height: $sheetHeight)
      }
    }
    .task {
      await viewModel.preflightWakeAuthorization()
    }
  }

  private var topBar: some View {
    HStack {
      VStack(alignment: .leading, spacing: 4) {
        Text("PortWorld Runtime")
          .font(.system(.title2, design: .rounded).weight(.bold))
          .foregroundColor(.white)

        Text("Assistant setup and backend validation")
          .font(.system(.subheadline, design: .rounded).weight(.medium))
          .foregroundColor(.white.opacity(0.72))
      }

      Spacer()

      Menu {
        Button("Disconnect", role: .destructive) {
          wearablesVM.disconnectGlasses()
        }
        .disabled(wearablesVM.registrationState != .registered)
      } label: {
        Image(systemName: "slider.horizontal.3")
          .font(.system(size: 17, weight: .bold))
          .foregroundColor(.white)
          .frame(width: 42, height: 42)
          .background(Color.white.opacity(0.14))
          .clipShape(Circle())
          .overlay(
            Circle().stroke(Color.white.opacity(0.2), lineWidth: 1)
          )
      }
    }
  }

  private var heroCard: some View {
    VStack(alignment: .leading, spacing: 14) {
      HStack(alignment: .top, spacing: 12) {
        Image(.cameraAccessIcon)
          .resizable()
          .renderingMode(.template)
          .foregroundColor(.white)
          .aspectRatio(contentMode: .fit)
          .frame(width: 52, height: 52)
          .padding(8)
          .background(Color.white.opacity(0.14))
          .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))

        VStack(alignment: .leading, spacing: 4) {
          Text("Activate Assistant")
            .font(.system(.title3, design: .rounded).weight(.semibold))
            .foregroundColor(.white)

          Text("Start session streaming, wake detection hooks, and runtime telemetry in one flow.")
            .font(.system(.subheadline, design: .rounded).weight(.medium))
            .foregroundColor(.white.opacity(0.82))
            .fixedSize(horizontal: false, vertical: true)
        }
      }

      HStack(spacing: 10) {
        StatusChip(
          icon: "antenna.radiowaves.left.and.right",
          label: "Backend",
          value: "Configured"
        )
        StatusChip(
          icon: "waveform.and.mic",
          label: "Wake mode",
          value: store.runtimeWakeEngineText.capitalized
        )
      }
    }
    .padding(16)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(
      LinearGradient(
        colors: [Color(red: 0.18, green: 0.26, blue: 0.42), Color(red: 0.08, green: 0.12, blue: 0.22)],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
      )
    )
    .overlay(
      RoundedRectangle(cornerRadius: 24, style: .continuous)
        .stroke(Color.white.opacity(0.18), lineWidth: 1)
    )
    .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
    .shadow(color: .black.opacity(0.25), radius: 14, x: 0, y: 8)
  }

  private var connectionCard: some View {
    HStack(spacing: 10) {
      Image(systemName: store.hasActiveDevice ? "checkmark.circle.fill" : "hourglass")
        .font(.system(size: 16, weight: .semibold))
        .foregroundColor(store.hasActiveDevice ? Color.green.opacity(0.85) : Color.orange.opacity(0.9))

      Text(store.hasActiveDevice ? "Active device detected. You can launch runtime now." : "No active device detected yet.")
        .font(.system(.subheadline, design: .rounded).weight(.semibold))
        .foregroundColor(.white.opacity(0.9))
        .frame(maxWidth: .infinity, alignment: .leading)
    }
    .padding(.horizontal, 14)
    .padding(.vertical, 12)
    .background(Color.white.opacity(0.11))
    .overlay(
      RoundedRectangle(cornerRadius: 16, style: .continuous)
        .stroke(Color.white.opacity(0.18), lineWidth: 1)
    )
    .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
  }

  private var bottomActionBar: some View {
    VStack(spacing: 10) {
      Button {
        Task {
          await viewModel.activateAssistantRuntime()
        }
      } label: {
        HStack(spacing: 10) {
          Image(systemName: "bolt.fill")
            .font(.system(size: 15, weight: .bold))
          Text(activateButtonTitle)
            .font(.system(.headline, design: .rounded).weight(.semibold))
        }
        .frame(maxWidth: .infinity)
        .frame(height: 54)
      }
      .buttonStyle(.plain)
      .foregroundColor(.white)
      .background(store.canActivateAssistantRuntime ? Color.appPrimary : Color.gray.opacity(0.5))
      .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
      .disabled(!store.canActivateAssistantRuntime)
    }
    .padding(.horizontal, 16)
    .padding(.top, 16)
    .padding(.bottom, 8)
    .background(.ultraThinMaterial)
    .overlay(alignment: .top) {
      Divider()
        .overlay(Color.white.opacity(0.12))
    }
  }

  private var activateButtonTitle: String {
    switch store.assistantRuntimeState {
    case .activating:
      return "Activating..."
    case .failed:
      return "Retry activation"
    default:
      return "Activate assistant"
    }
  }
}

private struct RuntimeStatusPanelView: View {
  let store: SessionStateStore
  @State private var showDiagnostics = false

  var body: some View {
    VStack(alignment: .leading, spacing: 12) {
      HStack {
        Text("Runtime Snapshot")
          .font(.system(.headline, design: .rounded).weight(.semibold))
          .foregroundColor(.white)
        Spacer()
        Text(store.runtimeSessionStateText.uppercased())
          .font(.system(.caption2, design: .rounded).weight(.bold))
          .foregroundColor(.white.opacity(0.9))
          .padding(.horizontal, 10)
          .padding(.vertical, 5)
          .background(Color.white.opacity(0.14))
          .clipShape(Capsule())
      }

      RuntimeMetricRow(label: "Wake", value: "\(store.runtimeWakeStateText) (\(store.runtimeWakeCount))")
      RuntimeMetricRow(label: "Query", value: "\(store.runtimeQueryStateText) (\(store.runtimeQueryCount))")
      RuntimeMetricRow(label: "Photo Uploads", value: "\(store.runtimePhotoUploadCount)")
      RuntimeMetricRow(label: "Playback Chunks", value: "\(store.runtimePlaybackChunkCount)")
      RuntimeMetricRow(label: "Video Frames Routed", value: "\(store.runtimeVideoFrameCount)")

      Divider().background(Color.white.opacity(0.2))

      if !store.runtimeErrorText.isEmpty || !store.audioLastError.isEmpty {
        VStack(alignment: .leading, spacing: 4) {
          if !store.runtimeErrorText.isEmpty {
            Text("Runtime Error: \(store.runtimeErrorText)")
          }
          if !store.audioLastError.isEmpty {
            Text("Audio Error: \(store.audioLastError)")
          }
        }
        .font(.system(.caption, design: .rounded).weight(.semibold))
        .foregroundColor(.red.opacity(0.95))
      }

      DisclosureGroup("Advanced telemetry", isExpanded: $showDiagnostics) {
        VStack(alignment: .leading, spacing: 6) {
          RuntimeMetricRow(label: "Backend", value: store.runtimeBackendText)
          RuntimeMetricRow(label: "Session ID", value: store.runtimeSessionIdText)
          RuntimeMetricRow(label: "Query ID", value: store.runtimeQueryIdText)
          RuntimeMetricRow(label: "Wake Runtime", value: store.runtimeWakeRuntimeText)
          RuntimeMetricRow(label: "Speech Auth", value: store.runtimeSpeechAuthorizationText)
          RuntimeMetricRow(label: "Manual Fallback", value: store.runtimeManualWakeFallbackText)
          RuntimeMetricRow(label: "Audio State", value: store.audioStateText)
          RuntimeMetricRow(label: "Audio Stats", value: "chunks \(store.audioChunkCount), bytes \(store.audioByteCount)")
          RuntimeMetricRow(label: "Audio Session Dir", value: store.audioSessionPath)
        }
        .padding(.top, 8)
      }
      .font(.system(.subheadline, design: .rounded).weight(.semibold))
      .foregroundColor(.white.opacity(0.9))
    }
    .padding(16)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(Color.white.opacity(0.1))
    .overlay(
      RoundedRectangle(cornerRadius: 18, style: .continuous)
        .stroke(Color.white.opacity(0.18), lineWidth: 1)
    )
    .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
  }
}

private struct RuntimeMetricRow: View {
  let label: String
  let value: String

  var body: some View {
    HStack(alignment: .firstTextBaseline, spacing: 10) {
      Text(label)
        .font(.system(.caption, design: .rounded).weight(.semibold))
        .foregroundColor(.white.opacity(0.72))

      Text(value)
        .font(.system(.caption, design: .rounded).weight(.bold))
        .foregroundColor(.white)
        .lineLimit(2)
        .frame(maxWidth: .infinity, alignment: .trailing)
    }
  }
}

private struct StatusChip: View {
  let icon: String
  let label: String
  let value: String

  var body: some View {
    HStack(spacing: 8) {
      Image(systemName: icon)
        .font(.system(size: 12, weight: .semibold))
        .foregroundColor(.white.opacity(0.85))

      VStack(alignment: .leading, spacing: 1) {
        Text(label)
          .font(.system(.caption2, design: .rounded).weight(.semibold))
          .foregroundColor(.white.opacity(0.66))
        Text(value)
          .font(.system(.caption, design: .rounded).weight(.bold))
          .foregroundColor(.white)
      }
    }
    .padding(.horizontal, 10)
    .padding(.vertical, 8)
    .background(Color.white.opacity(0.12))
    .clipShape(Capsule())
  }
}

private struct BackgroundGradientView: View {
  var body: some View {
    ZStack {
      LinearGradient(
        colors: [Color(red: 0.09, green: 0.12, blue: 0.2), Color(red: 0.03, green: 0.04, blue: 0.1), Color(red: 0.14, green: 0.08, blue: 0.03)],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
      )
      .ignoresSafeArea()

      Circle()
        .fill(Color.appPrimary.opacity(0.24))
        .frame(width: 320, height: 320)
        .blur(radius: 60)
        .offset(x: -130, y: -280)

      Circle()
        .fill(Color(red: 0.19, green: 0.49, blue: 0.9).opacity(0.22))
        .frame(width: 280, height: 280)
        .blur(radius: 70)
        .offset(x: 160, y: -120)
    }
  }
}

struct GettingStartedSheetView: View {
  @Environment(\.dismiss) var dismiss
  @Binding var height: CGFloat

  var body: some View {
    VStack(spacing: 24) {
      Text("Getting started")
        .font(.system(size: 18, weight: .semibold))
        .foregroundColor(.primary)

      VStack(spacing: 12) {
        TipRowView(
          resource: .videoIcon,
          text: "First, Microphone Access needs permission to use your glasses microphone.",
          iconColor: .primary,
          titleColor: .primary,
          textColor: .primary
        )
        TipRowView(
          resource: .tapIcon,
          text: "Capture photos by tapping the camera button.",
          iconColor: .primary,
          titleColor: .primary,
          textColor: .primary
        )
        TipRowView(
          resource: .smartGlassesIcon,
          text: "The capture LED lets others know when you're capturing content or going live.",
          iconColor: .primary,
          titleColor: .primary,
          textColor: .primary
        )
      }
      .padding(.bottom, 16)

      CustomButton(
        title: "Continue",
        style: .primary,
        isDisabled: false
      ) {
        dismiss()
      }
    }
    .padding(.all, 24)
    .background(
      GeometryReader { geo -> Color in
        DispatchQueue.main.async {
          height = geo.size.height
        }
        return Color.clear
      }
    )
  }
}
