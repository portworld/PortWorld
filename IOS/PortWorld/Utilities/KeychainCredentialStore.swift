// Keychain-backed storage for assistant credentials used by the assistant runtime.
import Foundation
import Security

enum KeychainCredentialStore {
  private static let service = "com.portworld.ios.credentials"
  private static let apiKeyAccount = "api-key"
  private static let bearerTokenAccount = "bearer-token"

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
    try store(value: apiKey, account: apiKeyAccount)
  }

  static func storeBearerToken(_ bearerToken: String) throws {
    try store(value: bearerToken, account: bearerTokenAccount)
  }

  static func retrieve() throws -> String? {
    try retrieveValue(account: apiKeyAccount)
  }

  static func retrieveBearerToken() throws -> String? {
    try retrieveValue(account: bearerTokenAccount)
  }

  static func clearBearerToken() throws {
    try clearValue(account: bearerTokenAccount)
  }

  static func clear() throws {
    try clearValue(account: apiKeyAccount)
  }

  private static func store(value: String, account: String) throws {
    guard let data = value.data(using: .utf8) else {
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

  private static func retrieveValue(account: String) throws -> String? {
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
            let value = String(data: data, encoding: .utf8) else {
        throw KeychainError.invalidItemData
      }
      return value
    case errSecItemNotFound:
      return nil
    default:
      throw KeychainError.from(status: status)
    }
  }

  private static func clearValue(account: String) throws {
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
