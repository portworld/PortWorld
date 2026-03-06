// StreamSessionView.swift

import Combine
import MWDATCore
import SwiftUI

@MainActor
private final class SessionViewModelLifetime: ObservableObject {
  @Published private(set) var viewModel: SessionViewModel?

  func ensureInitialized(
    wearables: WearablesInterface,
    store: SessionStateStore,
    preferSpeakerOutput: Bool
  ) {
    guard viewModel == nil else { return }
    viewModel = SessionViewModel(
      wearables: wearables,
      store: store,
      preferSpeakerOutput: preferSpeakerOutput
    )
  }
}

struct StreamSessionView: View {
  let wearables: WearablesInterface
  @ObservedObject private var wearablesViewModel: WearablesViewModel
  @StateObject private var sessionViewModelLifetime = SessionViewModelLifetime()
  @State private var store = SessionStateStore()
  @Environment(\.scenePhase) private var scenePhase

  init(wearables: WearablesInterface, wearablesVM: WearablesViewModel) {
    self.wearables = wearables
    self.wearablesViewModel = wearablesVM
  }

  var body: some View {
    let preferSpeakerOutput = wearablesViewModel.isMockModeEnabled

    ZStack {
      if let viewModel = sessionViewModelLifetime.viewModel {
        NonStreamView(viewModel: viewModel, store: store, wearablesVM: wearablesViewModel)
      } else {
        ProgressView()
      }
    }
    .alert("Error", isPresented: Binding(
      get: { store.showError },
      set: { store.showError = $0 }
    )) {
      Button("OK") {
        sessionViewModelLifetime.viewModel?.dismissError()
      }
    } message: {
      Text(store.errorMessage)
    }
    .onAppear {
      sessionViewModelLifetime.ensureInitialized(
        wearables: wearables,
        store: store,
        preferSpeakerOutput: preferSpeakerOutput
      )
      sessionViewModelLifetime.viewModel?.handleScenePhaseChange(scenePhase)
    }
    .onChange(of: scenePhase) { _, newPhase in
      sessionViewModelLifetime.ensureInitialized(
        wearables: wearables,
        store: store,
        preferSpeakerOutput: preferSpeakerOutput
      )
      sessionViewModelLifetime.viewModel?.handleScenePhaseChange(newPhase)
    }
  }
}
