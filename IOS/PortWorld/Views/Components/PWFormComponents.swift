import SwiftUI

enum PWFieldTone {
  case normal
  case success
  case warning
  case error

  var borderColor: Color {
    switch self {
    case .normal:
      return PWColor.border
    case .success:
      return PWColor.success
    case .warning:
      return PWColor.warning
    case .error:
      return PWColor.error
    }
  }

  var messageColor: Color {
    switch self {
    case .normal:
      return PWColor.textSecondary
    case .success:
      return PWColor.success
    case .warning:
      return PWColor.warning
    case .error:
      return PWColor.error
    }
  }
}

struct PWTextFieldRow: View {
  let label: String
  let placeholder: String
  @Binding var text: String
  let message: String?
  let tone: PWFieldTone
  let isSecure: Bool
  let textInputAutocapitalization: TextInputAutocapitalization
  let keyboardType: UIKeyboardType
  let submitLabel: SubmitLabel

  init(
    label: String,
    placeholder: String,
    text: Binding<String>,
    message: String? = nil,
    tone: PWFieldTone = .normal,
    isSecure: Bool = false,
    textInputAutocapitalization: TextInputAutocapitalization = .never,
    keyboardType: UIKeyboardType = .default,
    submitLabel: SubmitLabel = .done
  ) {
    self.label = label
    self.placeholder = placeholder
    _text = text
    self.message = message
    self.tone = tone
    self.isSecure = isSecure
    self.textInputAutocapitalization = textInputAutocapitalization
    self.keyboardType = keyboardType
    self.submitLabel = submitLabel
  }

  var body: some View {
    VStack(alignment: .leading, spacing: PWSpace.sm) {
      Text(label)
        .font(PWTypography.headline)
        .foregroundColor(PWColor.textPrimary)

      Group {
        if isSecure {
          SecureField("", text: $text, prompt: placeholderView)
        } else {
          TextField("", text: $text, prompt: placeholderView)
            .textInputAutocapitalization(textInputAutocapitalization)
            .keyboardType(keyboardType)
        }
      }
      .submitLabel(submitLabel)
      .font(PWTypography.body)
      .foregroundColor(PWColor.textPrimary)
      .padding(.horizontal, PWSpace.lg)
      .frame(minHeight: 52)
      .background(PWColor.input)
      .overlay(
        RoundedRectangle(cornerRadius: PWRadius.field, style: .continuous)
          .stroke(tone.borderColor, lineWidth: 1)
      )
      .clipShape(RoundedRectangle(cornerRadius: PWRadius.field, style: .continuous))

      if let message, !message.isEmpty {
        Text(message)
          .font(PWTypography.caption)
          .foregroundColor(tone.messageColor)
      }
    }
  }

  private var placeholderView: Text {
    Text(placeholder)
      .font(PWTypography.body)
      .foregroundColor(PWColor.textTertiary)
  }
}

struct PWStatusRow: View {
  let title: String
  let value: String
  let tone: PWStatusTone
  let systemImage: String?

  init(
    title: String,
    value: String,
    tone: PWStatusTone = .neutral,
    systemImage: String? = nil
  ) {
    self.title = title
    self.value = value
    self.tone = tone
    self.systemImage = systemImage
  }

  var body: some View {
    HStack(alignment: .top, spacing: PWSpace.md) {
      if let systemImage {
        Image(systemName: systemImage)
          .font(.system(size: 15, weight: .semibold))
          .foregroundColor(tone.color)
          .frame(width: 20, alignment: .center)
      }

      VStack(alignment: .leading, spacing: PWSpace.xs) {
        Text(title)
          .font(PWTypography.headline)
          .foregroundColor(PWColor.textPrimary)

        Text(value)
          .font(PWTypography.caption)
          .foregroundColor(tone == .neutral ? PWColor.textSecondary : tone.color)
          .fixedSize(horizontal: false, vertical: true)
      }

      Spacer(minLength: 0)
    }
    .frame(maxWidth: .infinity, alignment: .leading)
  }
}

struct PWBottomActionBar<Content: View>: View {
  private let content: Content

  init(@ViewBuilder content: () -> Content) {
    self.content = content()
  }

  var body: some View {
    VStack(alignment: .leading, spacing: PWSpace.sm) {
      content
    }
    .padding(.horizontal, PWSpace.lg)
    .padding(.top, PWSpace.md)
    .padding(.bottom, PWSpace.md)
    .background(PWColor.background)
    .overlay(alignment: .top) {
      Rectangle()
        .fill(PWColor.borderSubtle)
        .frame(height: 1)
    }
  }
}
