import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// Spotify sign-in with Authorization Code + PKCE: the flow for apps that can't keep a
/// client secret. Only the user's own Client ID is needed (see the setup guide).
public enum SpotifyAuthCore {
    public static let redirectURI = "reeper-auth://callback"
    public static let callbackScheme = "reeper-auth"
    public static let authorizeURL = URL(string: "https://accounts.spotify.com/authorize")!
    public static let tokenURL = URL(string: "https://accounts.spotify.com/api/token")!
    /// Same permissions as the command-line cleaner.
    public static let scopes = [
        "playlist-read-private", "playlist-read-collaborative", "playlist-modify-private",
        "playlist-modify-public", "user-library-modify", "user-library-read",
    ]

    /// A Spotify Client ID is 32 hexadecimal characters.
    public static func isValidClientID(_ id: String) -> Bool {
        let trimmed = id.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.count == 32 && trimmed.allSatisfy { $0.isHexDigit }
    }

    /// A random code verifier: 64 characters from the unreserved set (RFC 7636 §4.1).
    public static func makeVerifier<G: RandomNumberGenerator>(using rng: inout G) -> String {
        let chars = Array("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
        return String((0..<64).map { _ in chars[Int(rng.next() % UInt64(chars.count))] })
    }

    public static func makeVerifier() -> String {
        var rng = SystemRandomNumberGenerator()
        return makeVerifier(using: &rng)
    }

    /// The S256 code challenge: base64url(SHA-256(verifier)), without padding.
    public static func challenge(for verifier: String, sha256: (Data) -> Data) -> String {
        base64URL(sha256(Data(verifier.utf8)))
    }

    public static func base64URL(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    public static func authorizationURL(clientID: String, challenge: String, state: String) -> URL {
        var c = URLComponents(url: authorizeURL, resolvingAgainstBaseURL: false)!
        c.queryItems = [
            URLQueryItem(name: "client_id", value: clientID),
            URLQueryItem(name: "response_type", value: "code"),
            URLQueryItem(name: "redirect_uri", value: redirectURI),
            URLQueryItem(name: "code_challenge_method", value: "S256"),
            URLQueryItem(name: "code_challenge", value: challenge),
            URLQueryItem(name: "state", value: state),
            URLQueryItem(name: "scope", value: scopes.joined(separator: " ")),
        ]
        return c.url!
    }

    public enum CallbackError: Error, LocalizedError, Equatable {
        case denied(String)
        case stateMismatch
        case missingCode

        public var errorDescription: String? {
            switch self {
            case .denied(let reason):
                if reason == "access_denied" { return "Spotify sign-in was cancelled." }
                if reason.contains("redirect") || reason.contains("INVALID_CLIENT") {
                    return "Spotify rejected the sign-in (\(reason)). Check that your Spotify app's "
                        + "Redirect URIs include \(SpotifyAuthCore.redirectURI) exactly, and that "
                        + "the Client ID in Settings is correct."
                }
                return "Spotify sign-in failed: \(reason)"
            case .stateMismatch: return "Spotify sign-in failed a security check. Please try again."
            case .missingCode: return "Spotify didn't return a sign-in code. Please try again."
            }
        }
    }

    /// Reads the authorization code from the callback URL, checking `state`.
    public static func code(from callback: URL, expectedState: String) throws -> String {
        let items = URLComponents(url: callback, resolvingAgainstBaseURL: false)?.queryItems ?? []
        func value(_ name: String) -> String? { items.first { $0.name == name }?.value }
        if let error = value("error") { throw CallbackError.denied(error) }
        guard value("state") == expectedState else { throw CallbackError.stateMismatch }
        guard let code = value("code"), !code.isEmpty else { throw CallbackError.missingCode }
        return code
    }

    public static func form(_ fields: [(String, String)]) -> Data {
        var allowed = CharacterSet.alphanumerics
        allowed.insert(charactersIn: "-._~")
        return Data(fields.map { k, v in
            "\(k)=\(v.addingPercentEncoding(withAllowedCharacters: allowed) ?? v)"
        }.joined(separator: "&").utf8)
    }

    /// The request that trades the authorization code for tokens.
    public static func tokenRequest(code: String, verifier: String, clientID: String) -> URLRequest {
        var r = URLRequest(url: tokenURL)
        r.httpMethod = "POST"
        r.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        r.httpBody = form([("grant_type", "authorization_code"), ("code", code),
                           ("redirect_uri", redirectURI), ("client_id", clientID),
                           ("code_verifier", verifier)])
        return r
    }

    private struct TokenReply: Decodable {
        let access_token: String
        let refresh_token: String?
        let expires_in: Double?
        let error: String?
        let error_description: String?
    }

    public struct TokenError: Error, LocalizedError {
        public let message: String
        public var errorDescription: String? { message }
    }

    /// Parses Spotify's token reply.
    public static func tokens(from data: Data, status: Int, now: Date = Date()) throws -> SpotifyTokens {
        if status >= 400 {
            let detail = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            let reason = (detail?["error_description"] as? String) ?? (detail?["error"] as? String) ?? "HTTP \(status)"
            throw TokenError(message: "Spotify sign-in failed: \(reason)")
        }
        guard let reply = try? JSONDecoder().decode(TokenReply.self, from: data) else {
            throw TokenError(message: "Spotify sign-in failed: unreadable reply.")
        }
        return SpotifyTokens(accessToken: reply.access_token, refreshToken: reply.refresh_token,
                             expiresAt: now.timeIntervalSince1970 + (reply.expires_in ?? 3600))
    }
}
