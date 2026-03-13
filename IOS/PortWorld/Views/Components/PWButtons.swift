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
