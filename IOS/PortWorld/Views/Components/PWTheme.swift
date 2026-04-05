import SwiftUI

enum PWColor {
  static let background = Color(red: 10.0 / 255.0, green: 10.0 / 255.0, blue: 10.0 / 255.0)
  static let surface = Color(red: 20.0 / 255.0, green: 20.0 / 255.0, blue: 20.0 / 255.0)
  static let surfaceRaised = Color(red: 26.0 / 255.0, green: 26.0 / 255.0, blue: 26.0 / 255.0)
  static let input = Color(red: 17.0 / 255.0, green: 17.0 / 255.0, blue: 17.0 / 255.0)
  static let disabledFill = Color(red: 30.0 / 255.0, green: 30.0 / 255.0, blue: 30.0 / 255.0)

  static let border = Color(red: 42.0 / 255.0, green: 42.0 / 255.0, blue: 42.0 / 255.0)
  static let borderStrong = Color(red: 74.0 / 255.0, green: 74.0 / 255.0, blue: 74.0 / 255.0)
  static let borderSubtle = Color(red: 32.0 / 255.0, green: 32.0 / 255.0, blue: 32.0 / 255.0)

  static let textPrimary = Color(red: 245.0 / 255.0, green: 245.0 / 255.0, blue: 245.0 / 255.0)
  static let textSecondary = Color(red: 179.0 / 255.0, green: 179.0 / 255.0, blue: 179.0 / 255.0)
  static let textTertiary = Color(red: 122.0 / 255.0, green: 122.0 / 255.0, blue: 122.0 / 255.0)

  static let primaryButtonFill = Color(red: 242.0 / 255.0, green: 242.0 / 255.0, blue: 242.0 / 255.0)
  static let primaryButtonText = Color(red: 10.0 / 255.0, green: 10.0 / 255.0, blue: 10.0 / 255.0)
  static let secondaryButtonFill = surfaceRaised
  static let secondaryButtonText = textPrimary

  static let destructive = Color(uiColor: .systemRed)
  static let success = Color(red: 138.0 / 255.0, green: 150.0 / 255.0, blue: 140.0 / 255.0)
  static let warning = Color(red: 142.0 / 255.0, green: 142.0 / 255.0, blue: 147.0 / 255.0)
  static let error = Color(uiColor: .systemRed)
}

enum PWTypography {
  static let display = Font.system(.largeTitle, design: .rounded).weight(.bold)
  static let title = Font.system(.title2, design: .rounded).weight(.semibold)
  static let headline = Font.system(.headline, design: .rounded).weight(.semibold)
  static let body = Font.system(.body, design: .rounded)
  static let subbody = Font.system(.subheadline, design: .rounded)
  static let caption = Font.system(.caption, design: .rounded).weight(.medium)
}

enum PWSpace {
  static let xs: CGFloat = 4
  static let sm: CGFloat = 8
  static let md: CGFloat = 12
  static let lg: CGFloat = 16
  static let xl: CGFloat = 20
  static let section: CGFloat = 24
  static let hero: CGFloat = 32
  static let screen: CGFloat = 20
}

enum PWRadius {
  static let chip: CGFloat = 10
  static let field: CGFloat = 14
  static let card: CGFloat = 18
  static let panel: CGFloat = 24
}

enum PWStatusTone {
  case neutral
  case success
  case warning
  case error

  var color: Color {
    switch self {
    case .neutral:
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
