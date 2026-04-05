import SwiftUI

struct PWPrimaryButton: View {
  let title: String
  let isDisabled: Bool
  let action: () -> Void

  init(title: String, isDisabled: Bool = false, action: @escaping () -> Void) {
    self.title = title
    self.isDisabled = isDisabled
    self.action = action
  }

  var body: some View {
    PWActionButton(
      title: title,
      isDisabled: isDisabled,
      fillColor: PWColor.primaryButtonFill,
      foregroundColor: PWColor.primaryButtonText,
      borderColor: PWColor.primaryButtonFill,
      action: action
    )
  }
}

struct PWSecondaryButton: View {
  let title: String
  let isDisabled: Bool
  let action: () -> Void

  init(title: String, isDisabled: Bool = false, action: @escaping () -> Void) {
    self.title = title
    self.isDisabled = isDisabled
    self.action = action
  }

  var body: some View {
    PWActionButton(
      title: title,
      isDisabled: isDisabled,
      fillColor: PWColor.secondaryButtonFill,
      foregroundColor: PWColor.secondaryButtonText,
      borderColor: PWColor.border,
      action: action
    )
  }
}

struct PWDestructiveButton: View {
  let title: String
  let isDisabled: Bool
  let action: () -> Void

  init(title: String, isDisabled: Bool = false, action: @escaping () -> Void) {
    self.title = title
    self.isDisabled = isDisabled
    self.action = action
  }

  var body: some View {
    PWActionButton(
      title: title,
      isDisabled: isDisabled,
      fillColor: PWColor.secondaryButtonFill,
      foregroundColor: PWColor.destructive,
      borderColor: PWColor.destructive.opacity(0.55),
      action: action
    )
  }
}

struct PWOnboardingButton: View {
  let title: String
  let isDisabled: Bool
  let action: () -> Void

  init(title: String, isDisabled: Bool = false, action: @escaping () -> Void) {
    self.title = title
    self.isDisabled = isDisabled
    self.action = action
  }

  var body: some View {
    Button(action: action) {
      Text(title)
        .font(.system(.body, design: .rounded).weight(.semibold))
        .foregroundStyle(isDisabled ? PWColor.textTertiary : PWColor.textPrimary)
        .frame(minWidth: 188)
        .padding(.horizontal, 30)
        .frame(height: 54)
    }
    .buttonStyle(PWOnboardingButtonStyle(isDisabled: isDisabled))
    .disabled(isDisabled)
  }
}

private struct PWOnboardingButtonStyle: ButtonStyle {
  let isDisabled: Bool

  func makeBody(configuration: Configuration) -> some View {
    configuration.label
      .background(isDisabled ? PWColor.disabledFill : PWColor.surfaceRaised)
      .overlay(
        Capsule(style: .continuous)
          .stroke(isDisabled ? PWColor.borderSubtle : PWColor.border, lineWidth: 1)
      )
      .clipShape(Capsule(style: .continuous))
      .scaleEffect(configuration.isPressed && isDisabled == false ? 0.985 : 1.0)
      .opacity(configuration.isPressed && isDisabled == false ? 0.92 : 1.0)
      .animation(.easeOut(duration: 0.16), value: configuration.isPressed)
  }
}

private struct PWActionButton: View {
  let title: String
  let isDisabled: Bool
  let fillColor: Color
  let foregroundColor: Color
  let borderColor: Color
  let action: () -> Void

  var body: some View {
    Button(action: action) {
      Text(title)
        .font(PWTypography.headline)
        .foregroundStyle(isDisabled ? PWColor.textTertiary : foregroundColor)
        .frame(maxWidth: .infinity)
        .frame(minHeight: 56)
    }
    .buttonStyle(.plain)
    .background(isDisabled ? PWColor.disabledFill : fillColor)
    .overlay(
      RoundedRectangle(cornerRadius: PWRadius.field, style: .continuous)
        .stroke(isDisabled ? PWColor.borderSubtle : borderColor, lineWidth: 1)
    )
    .clipShape(RoundedRectangle(cornerRadius: PWRadius.field, style: .continuous))
    .disabled(isDisabled)
  }
}
