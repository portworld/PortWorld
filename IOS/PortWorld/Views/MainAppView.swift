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
    ZStack {
      switch route {
      case .splash:
        Color.clear
      case .welcome:
        OnboardingIntroStepView(
          title: "Welcome to PortWorld",
          subtitle: "Your hands-free assistant for Meta smart glasses.",
          buttonTitle: "Continue"
        ) {
          advanceOnboarding { onboardingStore.markWelcomeSeen() }
        }
      case .features:
        FeatureHighlightsView { advanceOnboarding { onboardingStore.markFeaturesSeen() } }
      case .connectAgents:
        OnboardingIntroStepView(
          title: "Connect your agents",
          subtitle: "PortWorld runs against your own backend. Next, you’ll add its URL and optional bearer token.",
          buttonTitle: "Set up backend"
        ) {
          advanceOnboarding { onboardingStore.markBackendIntroSeen() }
        }
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
      case .wakePractice:
        WakePracticeView(
          wearablesRuntimeManager: wearablesRuntimeManager,
          settings: appSettingsStore.settings,
          onContinue: {
            advanceOnboarding { onboardingStore.markWakePracticeCompleted() }
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
    .animation(.easeOut(duration: 0.24), value: route)
    .onAppear { refreshRoute() }
    .onChange(of: wearablesRuntimeManager.configurationState) { _, newValue in
      refreshRoute(configurationState: newValue)
    }
    .onChange(of: onboardingStore.progress) { _, _ in
      refreshRoute()
    }
  }
}

private extension MainAppView {
  func advanceOnboarding(_ mutation: () -> Void) {
    mutation()
    refreshRoute(preservingInProgressStep: false)
  }

  func handleMetaSkip() {
    if MainAppRouteResolver.onboardingRoute(for: onboardingStore.progress) == .metaConnection {
      advanceOnboarding { onboardingStore.markMetaSkipped() }
    } else {
      route = .home
    }
  }

  func refreshRoute(
    configurationState: WearablesRuntimeManager.ConfigurationState? = nil,
    preservingInProgressStep: Bool = true
  ) {
    route = MainAppRouteResolver.route(
      configurationState: configurationState ?? wearablesRuntimeManager.configurationState,
      progress: onboardingStore.progress,
      currentRoute: route,
      preservingInProgressStep: preservingInProgressStep
    )
  }
}

private enum MainAppRouteResolver {
  static func route(
    configurationState: WearablesRuntimeManager.ConfigurationState,
    progress: OnboardingStore.Progress,
    currentRoute: AppRoute,
    preservingInProgressStep: Bool
  ) -> AppRoute {
    switch configurationState {
    case .idle, .configuring:
      return .splash
    case .ready, .failed:
      if preservingInProgressStep && shouldPreserve(route: currentRoute, progress: progress) {
        return currentRoute
      }
      return onboardingRoute(for: progress)
    }
  }

  static func onboardingRoute(for progress: OnboardingStore.Progress) -> AppRoute {
    if progress.welcomeSeen == false {
      return .welcome
    }

    if progress.featuresSeen == false {
      return .features
    }

    if progress.backendIntroSeen == false {
      return .connectAgents
    }

    if progress.backendValidated == false {
      return .backendSetup
    }

    if progress.metaCompleted == false && progress.metaSkipped == false {
      return .metaConnection
    }

    if progress.metaCompleted && progress.wakePracticeCompleted == false {
      return .wakePractice
    }

    if progress.metaCompleted && progress.profileCompleted == false {
      return .profileInterview
    }

    return .home
  }

  private static func shouldPreserve(route: AppRoute, progress: OnboardingStore.Progress) -> Bool {
    switch route {
    case .metaConnection:
      return progress.metaCompleted == false && progress.metaSkipped == false
    case .wakePractice:
      return progress.metaCompleted && progress.wakePracticeCompleted == false
    case .profileInterview:
      return progress.metaCompleted && progress.profileCompleted == false
    case .splash, .welcome, .features, .connectAgents, .backendSetup, .home:
      return false
    }
  }
}
