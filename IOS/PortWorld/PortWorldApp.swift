//
//  PortWorldApp.swift
//  PortWorld
//
//  Created by Pierre Haas on 28/02/2026.
//

import Foundation
import MWDATCore
import SwiftUI

@main
struct PortWorldApp: App {
  @State private var sdkInitError: String?
  @State private var wearables: WearablesInterface?
  @State private var wearablesViewModel: WearablesViewModel?

  init() {
    let sdkErrorMessage: String?
    let wearablesInstance: WearablesInterface?
    let wearablesViewModel: WearablesViewModel?

    do {
      try Wearables.configure()
      let sharedWearables = Wearables.shared
      sdkErrorMessage = nil
      wearablesInstance = sharedWearables
      wearablesViewModel = WearablesViewModel(wearables: sharedWearables)
    } catch {
      sdkErrorMessage = error.localizedDescription
      wearablesInstance = nil
      wearablesViewModel = nil
    }

    self._sdkInitError = State(initialValue: sdkErrorMessage)
    self._wearables = State(initialValue: wearablesInstance)
    self._wearablesViewModel = State(initialValue: wearablesViewModel)
  }

  var body: some Scene {
    WindowGroup {
      Group {
        if let wearables, let wearablesViewModel {
          MainAppView(wearables: wearables, viewModel: wearablesViewModel)
            .alert("Error", isPresented: Binding(
              get: { wearablesViewModel.showError },
              set: { wearablesViewModel.showError = $0 }
            )) {
              Button("OK") {
                wearablesViewModel.dismissError()
              }
            } message: {
              Text(wearablesViewModel.errorMessage)
            }
        } else {
          FatalSDKInitializationView(errorMessage: sdkInitError ?? "Unknown initialization error.")
        }
      }
      .alert("Wearables SDK Initialization Failed", isPresented: Binding(
        get: { sdkInitError != nil },
        set: { _ in }
      )) {
        Button("Quit", role: .destructive) {
          exit(0)
        }
      } message: {
        Text(sdkInitError ?? "Unknown initialization error.")
      }
    }
  }
}

private struct FatalSDKInitializationView: View {
  let errorMessage: String

  var body: some View {
    VStack(spacing: 16) {
      Text("Wearables SDK Initialization Failed")
        .font(.headline)
      Text(errorMessage)
        .font(.subheadline)
        .multilineTextAlignment(.center)
        .foregroundColor(.secondary)
      Button("Quit", role: .destructive) {
        exit(0)
      }
    }
    .padding(24)
  }
}
