// HomeScreenView.swift
//
// Welcome screen that guides users through the DAT SDK registration process.
// This view is displayed when the app is not yet registered.

import MWDATCore
import SwiftUI

struct HomeScreenView: View {
  @ObservedObject var viewModel: WearablesViewModel
  @Namespace private var onboardingAnimation

  private var isRegistering: Bool {
    viewModel.registrationState == .registering
  }

  private var isRegistered: Bool {
    viewModel.registrationState == .registered
  }

  private var hasDiscoveredDevice: Bool {
    !viewModel.devices.isEmpty
  }

  private var registrationStatusTitle: String {
    if isRegistered { return "Connected" }
    if isRegistering { return "Connecting..." }
    return "Not connected"
  }

  private var registrationStatusSubtitle: String {
    if isRegistered {
      return "You can continue to runtime setup."
    }
    if isRegistering {
      return "Waiting for Meta AI confirmation."
    }
    return "Connect once in Meta AI to unlock live session."
  }

  var body: some View {
    ZStack {
      LinearGradient(
        colors: [
          Color(red: 0.04, green: 0.07, blue: 0.13),
          Color(red: 0.09, green: 0.14, blue: 0.24),
          Color(red: 0.12, green: 0.08, blue: 0.05),
        ],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
      )
      .ignoresSafeArea()

      Circle()
        .fill(Color.appPrimary.opacity(0.2))
        .frame(width: 320, height: 320)
        .blur(radius: 50)
        .offset(x: -120, y: -250)

      Circle()
        .fill(Color(red: 0.35, green: 0.58, blue: 0.95).opacity(0.18))
        .frame(width: 300, height: 300)
        .blur(radius: 55)
        .offset(x: 130, y: -120)

      ScrollView(showsIndicators: false) {
        VStack(alignment: .leading, spacing: 16) {
          VStack(alignment: .leading, spacing: 10) {
            Text("PortWorld")
              .font(.system(.largeTitle, design: .rounded).weight(.bold))
              .foregroundColor(.white)

            Text("Hands-free multimodal assistant for smart glasses")
              .font(.system(.headline, design: .rounded).weight(.medium))
              .foregroundColor(.white.opacity(0.78))
          }
          .padding(.top, 10)

          HomeGlassCard {
            HStack(alignment: .top, spacing: 12) {
              Image(.cameraAccessIcon)
                .resizable()
                .renderingMode(.template)
                .foregroundColor(.white)
                .aspectRatio(contentMode: .fit)
                .frame(width: 46, height: 46)
                .padding(8)
                .background(Color.white.opacity(0.16))
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))

              VStack(alignment: .leading, spacing: 6) {
                Text(registrationStatusTitle)
                  .font(.system(.title3, design: .rounded).weight(.semibold))
                  .foregroundColor(.white)
                Text(registrationStatusSubtitle)
                  .font(.system(.subheadline, design: .rounded).weight(.medium))
                  .foregroundColor(.white.opacity(0.76))
              }

              Spacer(minLength: 0)

              HomeStateBadge(
                text: registrationStatusTitle,
                state: statusBadgeState
              )
            }
          }

          HomeGlassCard {
            VStack(alignment: .leading, spacing: 10) {
              Text("Onboarding progress")
                .font(.system(.headline, design: .rounded).weight(.semibold))
                .foregroundColor(.white)

              ForEach(progressRows) { row in
                HomeProgressRow(row: row)
                  .matchedGeometryEffect(id: row.id, in: onboardingAnimation)
              }
            }
          }

          HomeGlassCard {
            VStack(alignment: .leading, spacing: 10) {
              Text("What you unlock")
                .font(.system(.headline, design: .rounded).weight(.semibold))
                .foregroundColor(.white)

              HomeFeatureRow(
                resource: .smartGlassesIcon,
                title: "First-person video context",
                detail: "Stream visual context from glasses to your assistant pipeline."
              )
              HomeFeatureRow(
                resource: .soundIcon,
                title: "Voice interaction loop",
                detail: "Capture speech and receive generated audio replies in real time."
              )
              HomeFeatureRow(
                resource: .walkingIcon,
                title: "Field-ready workflow",
                detail: "Designed for hands-busy scenarios: support, repair, and tours."
              )
            }
          }
        }
        .padding(.horizontal, 20)
        .padding(.top, 16)
        .padding(.bottom, 140)
      }
    }
    .animation(.spring(response: 0.35, dampingFraction: 0.85), value: viewModel.registrationState)
    .animation(.spring(response: 0.35, dampingFraction: 0.85), value: viewModel.devices.count)
    .safeAreaInset(edge: .bottom) {
      VStack(spacing: 10) {
        Text("You will be redirected to the Meta AI app to confirm access.")
          .font(.system(.caption, design: .rounded).weight(.medium))
          .foregroundColor(.white.opacity(0.7))
          .multilineTextAlignment(.leading)
          .frame(maxWidth: .infinity, alignment: .leading)

        Button {
          viewModel.connectGlasses()
        } label: {
          HStack(spacing: 10) {
            Image(systemName: isRegistering ? "hourglass" : "bolt.horizontal.fill")
            Text(isRegistering ? "Connecting..." : "Connect my glasses")
          }
          .font(.system(.headline, design: .rounded).weight(.semibold))
          .foregroundColor(.white)
          .frame(maxWidth: .infinity)
          .frame(height: 54)
        }
        .buttonStyle(.plain)
        .background(isRegistering ? Color.gray.opacity(0.5) : Color.appPrimary)
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .disabled(isRegistering)
      }
      .padding(.horizontal, 16)
      .padding(.top, 12)
      .padding(.bottom, 12)
      .background(Color.black.opacity(0.45))
      .overlay(alignment: .top) {
        Divider().overlay(Color.white.opacity(0.16))
      }
    }
  }
}

private extension HomeScreenView {
  var statusBadgeState: HomeStateBadge.State {
    if isRegistered { return .success }
    if isRegistering { return .active }
    return .inactive
  }

  var progressRows: [HomeProgressRow.RowData] {
    [
      HomeProgressRow.RowData(
        id: "registration",
        title: "Meta app authorization",
        detail: isRegistered ? "Completed" : (isRegistering ? "In progress..." : "Required"),
        status: isRegistered ? .done : (isRegistering ? .active : .pending)
      ),
      HomeProgressRow.RowData(
        id: "device",
        title: "Device discovery",
        detail: hasDiscoveredDevice ? "\(viewModel.devices.count) device(s) available" : "Waiting for glasses",
        status: hasDiscoveredDevice ? .done : (isRegistered ? .active : .pending)
      ),
      HomeProgressRow.RowData(
        id: "runtime",
        title: "Runtime activation",
        detail: isRegistered ? "Ready on next screen" : "Available after authorization",
        status: isRegistered ? .active : .pending
      ),
    ]
  }
}

private struct HomeGlassCard<Content: View>: View {
  @ViewBuilder var content: Content

  var body: some View {
    content
      .padding(16)
      .frame(maxWidth: .infinity, alignment: .leading)
      .background(
        RoundedRectangle(cornerRadius: 22, style: .continuous)
          .fill(.ultraThinMaterial)
      )
      .overlay(
        RoundedRectangle(cornerRadius: 22, style: .continuous)
          .stroke(Color.white.opacity(0.3), lineWidth: 1)
      )
  }
}

private struct HomeStateBadge: View {
  enum State {
    case success
    case active
    case inactive
  }

  let text: String
  let state: State

  private var icon: String {
    switch state {
    case .success:
      return "checkmark.circle.fill"
    case .active:
      return "hourglass.circle.fill"
    case .inactive:
      return "xmark.circle.fill"
    }
  }

  private var tint: Color {
    switch state {
    case .success:
      return Color.green.opacity(0.88)
    case .active:
      return Color.orange.opacity(0.9)
    case .inactive:
      return Color.white.opacity(0.7)
    }
  }

  var body: some View {
    HStack(spacing: 6) {
      Image(systemName: icon)
      Text(text.uppercased())
        .lineLimit(1)
    }
    .font(.system(.caption2, design: .rounded).weight(.bold))
    .foregroundColor(tint)
    .padding(.horizontal, 10)
    .padding(.vertical, 7)
    .background(Color.white.opacity(0.14))
    .clipShape(Capsule())
  }
}

private struct HomeProgressRow: View {
  struct RowData: Identifiable {
    enum Status {
      case done
      case active
      case pending
    }

    let id: String
    let title: String
    let detail: String
    let status: Status
  }

  let row: RowData

  private var icon: String {
    switch row.status {
    case .done:
      return "checkmark.circle.fill"
    case .active:
      return "circle.lefthalf.filled"
    case .pending:
      return "circle"
    }
  }

  private var tint: Color {
    switch row.status {
    case .done:
      return Color.green.opacity(0.92)
    case .active:
      return Color.orange.opacity(0.94)
    case .pending:
      return Color.white.opacity(0.55)
    }
  }

  var body: some View {
    HStack(alignment: .top, spacing: 10) {
      Image(systemName: icon)
        .font(.system(size: 15, weight: .semibold))
        .foregroundColor(tint)
        .frame(width: 20, alignment: .center)

      VStack(alignment: .leading, spacing: 2) {
        Text(row.title)
          .font(.system(.subheadline, design: .rounded).weight(.semibold))
          .foregroundColor(.white.opacity(0.95))

        Text(row.detail)
          .font(.system(.caption, design: .rounded).weight(.medium))
          .foregroundColor(.white.opacity(0.72))
      }
    }
  }
}

private struct HomeFeatureRow: View {
  let resource: ImageResource
  let title: String
  let detail: String

  var body: some View {
    HStack(alignment: .top, spacing: 12) {
      Image(resource)
        .resizable()
        .renderingMode(.template)
        .foregroundColor(.white.opacity(0.88))
        .aspectRatio(contentMode: .fit)
        .frame(width: 20, height: 20)
        .padding(10)
        .background(Color.white.opacity(0.14))
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))

      VStack(alignment: .leading, spacing: 3) {
        Text(title)
          .font(.system(.subheadline, design: .rounded).weight(.semibold))
          .foregroundColor(.white.opacity(0.95))
        Text(detail)
          .font(.system(.caption, design: .rounded).weight(.medium))
          .foregroundColor(.white.opacity(0.72))
      }
      Spacer()
    }
    .padding(12)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(Color.white.opacity(0.06))
    .overlay(
      RoundedRectangle(cornerRadius: 14, style: .continuous)
        .stroke(Color.white.opacity(0.2), lineWidth: 1)
    )
    .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
  }
}
