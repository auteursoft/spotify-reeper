import AppKit
import AuthenticationServices
import CryptoKit
import Foundation

struct SignInCancelled: Error {}

/// Signs in to Spotify in the system's secure web sheet (ASWebAuthenticationSession), using
/// Authorization Code + PKCE. No client secret exists in the app.
@MainActor
final class SpotifySignIn: NSObject, ASWebAuthenticationPresentationContextProviding {
    private var session: ASWebAuthenticationSession?

    func signIn(clientID: String) async throws -> SpotifyTokens {
        let verifier = SpotifyAuthCore.makeVerifier()
        let challenge = SpotifyAuthCore.challenge(for: verifier) { Data(SHA256.hash(data: $0)) }
        let state = UUID().uuidString
        let url = SpotifyAuthCore.authorizationURL(clientID: clientID, challenge: challenge, state: state)

        let callback: URL = try await withCheckedThrowingContinuation { continuation in
            let session = ASWebAuthenticationSession(
                url: url, callbackURLScheme: SpotifyAuthCore.callbackScheme
            ) { callbackURL, error in
                if let callbackURL {
                    continuation.resume(returning: callbackURL)
                } else if let error = error as? ASWebAuthenticationSessionError, error.code == .canceledLogin {
                    continuation.resume(throwing: SignInCancelled())
                } else {
                    continuation.resume(throwing: error ?? SignInCancelled())
                }
            }
            session.presentationContextProvider = self
            // Share cookies with Safari so people already logged in to Spotify needn't type a password.
            session.prefersEphemeralWebBrowserSession = false
            self.session = session
            if !session.start() {
                continuation.resume(throwing: SpotifyAuthCore.TokenError(message: "Couldn't open the Spotify sign-in window."))
            }
        }
        session = nil

        let code = try SpotifyAuthCore.code(from: callback, expectedState: state)
        let request = SpotifyAuthCore.tokenRequest(code: code, verifier: verifier, clientID: clientID)
        let (data, response) = try await URLSession.shared.data(for: request)
        return try SpotifyAuthCore.tokens(from: data, status: (response as? HTTPURLResponse)?.statusCode ?? 0)
    }

    nonisolated func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        MainActor.assumeIsolated {
            NSApp.keyWindow ?? NSApp.mainWindow ?? NSApp.windows.first ?? ASPresentationAnchor()
        }
    }
}
