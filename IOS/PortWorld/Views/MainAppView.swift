// Root shell for the glasses-first assistant experience.
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
          WelcomeShellView { advanceOnboarding { onboardingStore.markWelcomeSeen() } }
        case .features:
          FeatureHighlightsView { advanceOnboarding { onboardingStore.markFeaturesSeen() } }
        case .connectAgents:
          ConnectAgentsIntroView { advanceOnboarding { onboardingStore.markBackendIntroSeen() } }
        case .backendSetup:
          BackendSetupView(appSettingsStore: appSettingsStore) {
            advanceOnboarding { onboardingStore.markBackendValidated() }
          }
        case .metaConnection:
          MetaConnectionView(
            wearablesRuntimeManager: wearablesRuntimeManager,
            onContinue: {
              advanceOnboarding { onboardingStore.markMetaCompleted() }
            },
            onSkip: {
              handleMetaSkip()
            }
          )
        case .profileInterview:
          ProfileInterviewView(
            wearablesRuntimeManager: wearablesRuntimeManager,
            settings: appSettingsStore.settings,
            onContinue: {
              advanceOnboarding { onboardingStore.markProfileCompleted() }
            }
          )
        case .home:
          PostOnboardingShellView(
            appSettingsStore: appSettingsStore,
            wearablesRuntimeManager: wearablesRuntimeManager,
            shouldShowProfileSetupCallToAction: onboardingStore.shouldOfferProfileSetup,
            onOpenMetaSetup: { route = .metaConnection },
            onOpenProfileSetup: { route = .profileInterview }
          )
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
      route = onboardingRoute
    }
  }
}

private extension MainAppView {
  var onboardingRoute: AppRoute {
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

    if onboardingStore.progress.metaCompleted &&
      onboardingStore.progress.profileCompleted == false
    {
      return .profileInterview
    }

    return .home
  }

  func advanceOnboarding(_ mutation: () -> Void) {
    mutation()
    route = onboardingRoute
  }

  func handleMetaSkip() {
    if onboardingRoute == .metaConnection {
      advanceOnboarding { onboardingStore.markMetaSkipped() }
    } else {
      route = .home
    }
  }

  func resolveRoute(
    for configurationState: WearablesRuntimeManager.ConfigurationState
  ) {
    switch configurationState {
    case .idle, .configuring:
      route = .splash
    case .ready, .failed:
      switch route {
      case .metaConnection:
        return
      case .profileInterview where onboardingStore.progress.profileCompleted == false:
        return
      default:
        break
      }
      route = onboardingRoute
    }
  }
}
