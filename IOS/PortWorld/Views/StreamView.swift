// StreamView.swift
//
// Main UI for video streaming from Meta wearable devices using the DAT SDK.
// This view demonstrates the complete streaming API: video streaming with real-time display, photo capture,
// and error handling.

import MWDATCore
import SwiftUI

struct StreamView: View {
  let viewModel: SessionViewModel
  let store: SessionStateStore

  var body: some View {
    ZStack {
      Color.black
        .edgesIgnoringSafeArea(.all)

      if let videoFrame = store.currentVideoFrame, store.hasReceivedFirstFrame {
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
          StreamRuntimeOverlay(store: store)
          Spacer()
        }
        Spacer()
        ControlsView(viewModel: viewModel, store: store)
      }
      .padding(.all, 24)
    }
    .onDisappear {
      Task {
        if store.canDeactivateAssistantRuntime {
          await viewModel.deactivateAssistantRuntime()
        }
      }
    }
    .sheet(isPresented: Binding(
      get: { store.showPhotoPreview },
      set: { store.showPhotoPreview = $0 }
    )) {
      if let photo = store.capturedPhoto {
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
  let store: SessionStateStore

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      HStack(spacing: 6) {
        Image(systemName: "bolt.fill")
          .foregroundColor(.appPrimary)
        Text("Session: \(store.runtimeSessionStateText)")
      }
      
      HStack(spacing: 6) {
        Image(systemName: "waveform")
          .foregroundColor(.white.opacity(0.7))
        Text("Wake: \(store.runtimeWakeStateText)")
      }
      
      HStack(spacing: 6) {
        Image(systemName: "camera.fill")
          .foregroundColor(.white.opacity(0.7))
        Text("Photo: \(store.runtimePhotoStateText)")
      }

      if !store.runtimeErrorText.isEmpty {
        HStack(alignment: .top, spacing: 6) {
          Image(systemName: "exclamationmark.triangle.fill")
          Text(store.runtimeErrorText)
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
  let viewModel: SessionViewModel
  let store: SessionStateStore

  var body: some View {
    HStack(spacing: 8) {
      CustomButton(
        title: "Deactivate assistant",
        style: .destructive,
        isDisabled: !store.canDeactivateAssistantRuntime
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
