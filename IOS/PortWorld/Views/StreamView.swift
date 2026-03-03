// StreamView.swift
//
// Main UI for video streaming from Meta wearable devices using the DAT SDK.
// This view demonstrates the complete streaming API: video streaming with real-time display, photo capture,
// and error handling.

import MWDATCore
import SwiftUI

struct StreamView: View {
  @ObservedObject var viewModel: StreamSessionViewModel
  @ObservedObject var wearablesVM: WearablesViewModel

  var body: some View {
    ZStack {
      Color.black
        .edgesIgnoringSafeArea(.all)

      if let videoFrame = viewModel.currentVideoFrame, viewModel.hasReceivedFirstFrame {
        GeometryReader { geometry in
          Image(uiImage: videoFrame)
            .resizable()
            .aspectRatio(contentMode: .fill)
            .frame(width: geometry.size.width, height: geometry.size.height)
            .clipped()
        }
        .edgesIgnoringSafeArea(.all)
      } else {
        ProgressView()
          .scaleEffect(1.5)
          .foregroundColor(.white)
      }

      VStack {
        HStack {
          StreamRuntimeOverlay(viewModel: viewModel)
          Spacer()
        }
        Spacer()
        ControlsView(viewModel: viewModel)
      }
      .padding(.all, 24)
    }
    .onDisappear {
      Task {
        if viewModel.canDeactivateAssistantRuntime {
          await viewModel.deactivateAssistantRuntime()
        }
      }
    }
    .sheet(isPresented: $viewModel.showPhotoPreview) {
      if let photo = viewModel.capturedPhoto {
        PhotoPreviewView(
          photo: photo,
          onDismiss: {
            viewModel.dismissPhotoPreview()
          }
        )
      }
    }
  }
}

private struct StreamRuntimeOverlay: View {
  @ObservedObject var viewModel: StreamSessionViewModel

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      HStack(spacing: 6) {
        Image(systemName: "bolt.fill")
          .foregroundColor(.appPrimary)
        Text("Session: \(viewModel.runtimeSessionStateText)")
      }
      
      HStack(spacing: 6) {
        Image(systemName: "waveform")
          .foregroundColor(.white.opacity(0.7))
        Text("Wake: \(viewModel.runtimeWakeStateText)")
      }
      
      HStack(spacing: 6) {
        Image(systemName: "camera.fill")
          .foregroundColor(.white.opacity(0.7))
        Text("Photo: \(viewModel.runtimePhotoStateText)")
      }

      if !viewModel.runtimeErrorText.isEmpty {
        HStack(alignment: .top, spacing: 6) {
          Image(systemName: "exclamationmark.triangle.fill")
          Text(viewModel.runtimeErrorText)
        }
        .foregroundColor(.red.opacity(0.9))
      }
    }
    .font(.system(.caption, design: .rounded).weight(.semibold))
    .foregroundColor(.white)
    .padding(14)
    .background(.ultraThinMaterial)
    .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
  }
}

struct ControlsView: View {
  @ObservedObject var viewModel: StreamSessionViewModel

  var body: some View {
    HStack(spacing: 8) {
      CustomButton(
        title: "Deactivate assistant",
        style: .destructive,
        isDisabled: !viewModel.canDeactivateAssistantRuntime
      ) {
        Task {
          await viewModel.deactivateAssistantRuntime()
        }
      }

      CircleButton(icon: "camera.fill", text: nil) {
        viewModel.capturePhoto()
      }

      CircleButton(icon: "waveform", text: nil) {
        viewModel.triggerWakeForTesting()
      }
    }
  }
}
