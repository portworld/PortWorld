// Secondary setup screen that consumes the shared app-scoped wearables manager.
import SwiftUI

struct FutureHardwareSetupView: View {
  @Environment(\.dismiss) private var dismiss
  @ObservedObject private var wearablesRuntimeManager: WearablesRuntimeManager

  init(wearablesRuntimeManager: WearablesRuntimeManager) {
    self.wearablesRuntimeManager = wearablesRuntimeManager
  }

  var body: some View {
    NavigationStack {
      Group {
        switch wearablesRuntimeManager.configurationState {
        case .ready:
          HomeScreenView(wearablesRuntimeManager: wearablesRuntimeManager)

        case .idle, .configuring:
          WearablesInitializationView()

        case .failed:
          RecoverableWearablesInitializationView(
            errorMessage: wearablesRuntimeManager.configurationErrorMessage ?? "Wearables SDK is not initialized yet.",
            diagnostics: wearablesRuntimeManager.configurationDiagnostics,
            isRetrying: wearablesRuntimeManager.configurationState == .configuring,
            onRetry: {
              Task {
                await wearablesRuntimeManager.retryConfiguration()
              }
            }
          )
        }
      }
      .navigationTitle("Glasses Setup")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .cancellationAction) {
          Button("Done") {
            dismiss()
          }
        }
      }
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
}

private struct WearablesInitializationView: View {
  var body: some View {
    VStack(spacing: 16) {
      ProgressView()
        .progressViewStyle(.circular)
      Text("Initializing Wearables SDK")
        .font(.headline)
      Text("Preparing the shared glasses capability layer for this app.")
        .font(.subheadline)
        .foregroundColor(.secondary)
        .multilineTextAlignment(.center)
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .center)
    .padding(24)
  }
}

private struct RecoverableWearablesInitializationView: View {
  let errorMessage: String
  let diagnostics: [String]
  let isRetrying: Bool
  let onRetry: () -> Void

  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
      Text("Wearables SDK Initialization Failed")
        .font(.headline)
      Text(errorMessage)
        .font(.subheadline)
        .multilineTextAlignment(.leading)
        .foregroundColor(.secondary)
      VStack(alignment: .leading, spacing: 8) {
        ForEach(Array(diagnostics.enumerated()), id: \.offset) { _, diagnostic in
          Text("• \(diagnostic)")
            .font(.footnote)
            .foregroundColor(.secondary)
        }
      }
      Button(isRetrying ? "Retrying..." : "Retry initialization") {
        onRetry()
      }
      .disabled(isRetrying)
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .center)
    .padding(24)
  }
}
