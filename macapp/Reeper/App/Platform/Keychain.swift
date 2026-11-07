import Foundation
import Security

/// Small wrapper over the macOS Keychain for Reeper's secrets: Spotify tokens and the
/// Last.fm API key. Items are this-device-only and readable only by Reeper.
enum Keychain {
    private static let service = "com.auteursoft.reeper"

    static func set(_ data: Data, for account: String) {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: service,
                                    kSecAttrAccount as String: account]
        let update: [String: Any] = [kSecValueData as String: data]
        let status = SecItemUpdate(query as CFDictionary, update as CFDictionary)
        if status == errSecItemNotFound {
            var add = query
            add[kSecValueData as String] = data
            add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            SecItemAdd(add as CFDictionary, nil)
        }
    }

    static func data(for account: String) -> Data? {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: service,
                                    kSecAttrAccount as String: account,
                                    kSecReturnData as String: true,
                                    kSecMatchLimit as String: kSecMatchLimitOne]
        var out: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &out) == errSecSuccess else { return nil }
        return out as? Data
    }

    static func remove(_ account: String) {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: service,
                                    kSecAttrAccount as String: account]
        SecItemDelete(query as CFDictionary)
    }

    // Typed helpers

    static var spotifyTokens: SpotifyTokens? {
        get { data(for: "spotify-tokens").flatMap { try? JSONDecoder().decode(SpotifyTokens.self, from: $0) } }
        set {
            if let newValue, let data = try? JSONEncoder().encode(newValue) { set(data, for: "spotify-tokens") }
            else { remove("spotify-tokens") }
        }
    }

    static var lastFMKey: String? {
        get { data(for: "lastfm-api-key").map { String(decoding: $0, as: UTF8.self) } }
        set {
            let trimmed = newValue?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            if trimmed.isEmpty { remove("lastfm-api-key") } else { set(Data(trimmed.utf8), for: "lastfm-api-key") }
        }
    }
}
