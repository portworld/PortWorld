// Keychain-backed storage for assistant credentials used by the assistant runtime.
import Foundation
import Security

enum KeychainCredentialStore {
  private static let service = "com.portworld.ios.credentials"
  private static let account = "api-key"

  enum KeychainError: Error, Equatable {
    case duplicateItem
    case itemNotFound
    case authFailed
    case interactionNotAllowed
    case invalidItemData
    case encodingFailed
    case unexpectedStatus(OSStatus)

    static func from(status: OSStatus) -> KeychainError {
      switch status {
      case errSecDuplicateItem:
        return .duplicateItem
      case errSecItemNotFound:
        return .itemNotFound
      case errSecAuthFailed:
        return .authFailed
      case errSecInteractionNotAllowed:
        return .interactionNotAllowed
      default:
        return .unexpectedStatus(status)
      }
    }
  }

  static func store(apiKey: String) throws {
    guard let data = apiKey.data(using: .utf8) else {
      throw KeychainError.encodingFailed
    }

    let baseQuery: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: account
    ]

    var addQuery = baseQuery
    addQuery[kSecValueData as String] = data

    let addStatus = SecItemAdd(addQuery as CFDictionary, nil)
    switch addStatus {
    case errSecSuccess:
      return
    case errSecDuplicateItem:
      let updateAttributes: [String: Any] = [
        kSecValueData as String: data
      ]
      let updateStatus = SecItemUpdate(baseQuery as CFDictionary, updateAttributes as CFDictionary)
      guard updateStatus == errSecSuccess else {
        throw KeychainError.from(status: updateStatus)
      }
    default:
      throw KeychainError.from(status: addStatus)
    }
  }

  static func retrieve() throws -> String? {
    let query: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: account,
      kSecMatchLimit as String: kSecMatchLimitOne,
      kSecReturnData as String: true
    ]

    var item: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &item)

    switch status {
    case errSecSuccess:
      guard let data = item as? Data,
            let apiKey = String(data: data, encoding: .utf8) else {
        throw KeychainError.invalidItemData
      }
      return apiKey
    case errSecItemNotFound:
      return nil
    default:
      throw KeychainError.from(status: status)
    }
  }

  static func clear() throws {
    let query: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: account
    ]

    let status = SecItemDelete(query as CFDictionary)
    switch status {
    case errSecSuccess, errSecItemNotFound:
      return
    default:
      throw KeychainError.from(status: status)
    }
  }
}
