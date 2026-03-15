// Root shell for the phone-first assistant with secondary access to future hardware setup.
import SwiftUI

struct MainAppView: View {
  @StateObject private var appSettingsStore = AppSettingsStore()
  @StateObject private var onboardingStore = OnboardingStore()
  @ObservedObject private var wearablesRuntimeManager: WearablesRuntimeManager
  @State private var route: AppRoute = .splash

  init(wearablesRuntimeManager: WearablesRuntimeManager) {
    self.wearablesRuntimeManager = wearablesRuntimeManager
  }

  var body: some View {
    NavigationStack {
      ZStack {
        switch route {
        case .splash:
          Color.clear
        case .welcome:
          WelcomeShellView {
            onboardingStore.markWelcomeSeen()
            route = nextOnboardingRoute()
          }
        case .features:
          FeatureHighlightsView {
            onboardingStore.markFeaturesSeen()
            route = nextOnboardingRoute()
          }
        case .connectAgents:
          ConnectAgentsIntroView {
            onboardingStore.markBackendIntroSeen()
            route = nextOnboardingRoute()
          }
        case .backendSetup:
          BackendSetupView(appSettingsStore: appSettingsStore) {
            onboardingStore.markBackendValidated()
            route = nextOnboardingRoute()
          }
        case .metaConnection:
          MetaConnectionView(
            wearablesRuntimeManager: wearablesRuntimeManager,
            onContinue: {
              onboardingStore.markMetaCompleted()
              route = nextOnboardingRoute()
            },
            onSkip: {
              onboardingStore.markMetaSkipped()
              route = nextOnboardingRoute()
            }
          )
        case .wakePractice:
          WakePracticeView(
            wearablesRuntimeManager: wearablesRuntimeManager,
            settings: appSettingsStore.settings,
            onContinue: {
              onboardingStore.markWakePracticeCompleted()
              route = nextOnboardingRoute()
            }
          )
        case .profileInterview:
          ProfileInterviewView(
            wearablesRuntimeManager: wearablesRuntimeManager,
            settings: appSettingsStore.settings,
            onContinue: {
              onboardingStore.markProfileCompleted()
              route = nextOnboardingRoute()
            }
          )
        case .home:
          PostOnboardingShellView(
            appSettingsStore: appSettingsStore,
            wearablesRuntimeManager: wearablesRuntimeManager,
            onOpenMetaSetup: {
              route = .metaConnection
            },
            onOpenWakePractice: {
              route = .wakePractice
            },
            onOpenProfileInterview: {
              route = .profileInterview
            }
          )
          .id(runtimeHostIdentity)
        }

        if route == .splash {
          StartupLoadingView()
            .transition(.opacity)
        }
      }
    }
    .animation(.easeOut(duration: 0.24), value: route)
    .onAppear {
      resolveRoute(for: wearablesRuntimeManager.configurationState)
    }
    .onChange(of: wearablesRuntimeManager.configurationState) { _, newValue in
      resolveRoute(for: newValue)
    }
    .onChange(of: onboardingStore.progress) { _, _ in
      guard route != .splash else { return }
      route = nextOnboardingRoute()
    }
  }
}

private extension MainAppView {
  var runtimeHostIdentity: String {
    [
      appSettingsStore.settings.backendBaseURL,
      appSettingsStore.settings.bearerToken,
      appSettingsStore.settings.validationState.rawValue,
    ].joined(separator: "|")
  }

  func nextOnboardingRoute() -> AppRoute {
    if onboardingStore.progress.welcomeSeen == false {
      return .welcome
    }

    if onboardingStore.progress.featuresSeen == false {
      return .features
    }

    if onboardingStore.progress.backendIntroSeen == false {
      return .connectAgents
    }

    if onboardingStore.progress.backendValidated == false {
      return .backendSetup
    }

    if onboardingStore.progress.metaCompleted == false &&
      onboardingStore.progress.metaSkipped == false
    {
      return .metaConnection
    }

    if onboardingStore.progress.wakePracticeCompleted == false {
      return .wakePractice
    }

    if onboardingStore.progress.profileCompleted == false {
      return .profileInterview
    }

    return .home
  }

  func resolveRoute(
    for configurationState: WearablesRuntimeManager.ConfigurationState
  ) {
    switch configurationState {
    case .idle, .configuring:
      route = .splash
    case .ready, .failed:
      if onboardingStore.progress.profileCompleted == false {
        switch route {
        case .profileInterview:
          return
        default:
          break
        }
      }
      route = nextOnboardingRoute()
    }
  }
}
