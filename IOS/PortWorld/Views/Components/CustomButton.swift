// CustomButton.swift
//
// Reusable button component used throughout the CameraAccess app for consistent styling.

import SwiftUI

struct CustomButton: View {
  let title: String
  let style: ButtonStyle
  let isDisabled: Bool
  let expandsHorizontally: Bool
  let minHeight: CGFloat
  let cornerRadius: CGFloat
  let horizontalPadding: CGFloat?
  let action: () -> Void

  enum ButtonStyle: Equatable {
    case primary, destructive

    var backgroundColor: Color {
      switch self {
      case .primary:
        return PWColor.primaryButtonFill
      case .destructive:
        return PWColor.secondaryButtonFill
      }
    }

    var foregroundColor: Color {
      switch self {
      case .primary:
        return PWColor.primaryButtonText
      case .destructive:
        return PWColor.destructive
      }
    }
  }

  init(
    title: String,
    style: ButtonStyle,
    isDisabled: Bool,
    expandsHorizontally: Bool = true,
    minHeight: CGFloat = 56,
    cornerRadius: CGFloat = 30,
    horizontalPadding: CGFloat? = nil,
    action: @escaping () -> Void
  ) {
    self.title = title
    self.style = style
    self.isDisabled = isDisabled
    self.expandsHorizontally = expandsHorizontally
    self.minHeight = minHeight
    self.cornerRadius = cornerRadius
    self.horizontalPadding = horizontalPadding
    self.action = action
  }

  var body: some View {
    Button(action: action) {
      Text(title)
        .font(PWTypography.headline)
        .foregroundColor(style.foregroundColor)
        .frame(maxWidth: expandsHorizontally ? .infinity : nil)
        .frame(minHeight: minHeight)
    }
    .padding(.horizontal, horizontalPadding ?? 0)
    .background(style.backgroundColor)
    .overlay(
      RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
        .stroke(style == .destructive ? PWColor.destructive.opacity(0.55) : style.backgroundColor, lineWidth: 1)
    )
    .clipShape(RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
    .disabled(isDisabled)
    .opacity(isDisabled ? 0.6 : 1.0)
  }
}
