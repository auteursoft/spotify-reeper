// Tests for SpotifyAuthCore (PKCE, URLs, token parsing). Foundation only.
// SHA-256 comes from the system's `shasum`/`sha256sum` here, so the test runs on Linux too;
// the app uses CryptoKit.

import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

var failures = 0
func check(_ ok: Bool, _ what: String) {
    print((ok ? "PASS " : "FAIL ") + what)
    if !ok { failures += 1 }
}

func sha256(_ data: Data) -> Data {
    let tool = FileManager.default.fileExists(atPath: "/usr/bin/sha256sum") ? "/usr/bin/sha256sum" : "/usr/bin/shasum"
    let p = Process()
    p.executableURL = URL(fileURLWithPath: tool)
    p.arguments = tool.hasSuffix("shasum") ? ["-a", "256"] : []
    let inPipe = Pipe(), outPipe = Pipe()
    p.standardInput = inPipe
    p.standardOutput = outPipe
    try! p.run()
    inPipe.fileHandleForWriting.write(data)
    try! inPipe.fileHandleForWriting.close()
    let hex = String(decoding: outPipe.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self).prefix(64)
    p.waitUntilExit()
    var bytes = [UInt8]()
    var i = hex.startIndex
    while i < hex.endIndex {
        let j = hex.index(i, offsetBy: 2)
        bytes.append(UInt8(hex[i..<j], radix: 16)!)
        i = j
    }
    return Data(bytes)
}

// RFC 7636 Appendix B test vector.
check(SpotifyAuthCore.challenge(for: "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk", sha256: sha256)
      == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM", "PKCE S256 challenge matches RFC 7636")

let v = SpotifyAuthCore.makeVerifier()
let allowed = Set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
check(v.count == 64 && v.allSatisfy { allowed.contains($0) }, "verifier is 64 unreserved characters")
check(SpotifyAuthCore.makeVerifier() != v, "verifiers are random")

check(SpotifyAuthCore.isValidClientID("492c6795e8984791b62c2646c5d54560"), "valid client id")
check(!SpotifyAuthCore.isValidClientID("492c6795e8984791b62c2646c5d5456"), "short client id rejected")
check(!SpotifyAuthCore.isValidClientID("YOUR CLIENT ID"), "placeholder rejected")
check(SpotifyAuthCore.isValidClientID(" 492c6795e8984791b62c2646c5d54560\n"), "whitespace around client id ignored")

let url = SpotifyAuthCore.authorizationURL(clientID: "abc", challenge: "ch", state: "st")
let q = Dictionary(uniqueKeysWithValues: (URLComponents(url: url, resolvingAgainstBaseURL: false)!.queryItems ?? [])
    .map { ($0.name, $0.value ?? "") })
check(url.host == "accounts.spotify.com" && url.path == "/authorize", "authorize endpoint")
check(q["response_type"] == "code" && q["code_challenge_method"] == "S256" && q["code_challenge"] == "ch",
      "PKCE parameters")
check(q["redirect_uri"] == "reeper-auth://callback" && q["state"] == "st", "redirect and state")
check(q["scope"]?.contains("playlist-modify-private") == true && q["scope"]?.contains("user-library-read") == true,
      "scopes")
check(url.absoluteString.contains("redirect_uri=reeper-auth://callback") || url.absoluteString.contains("reeper-auth"),
      "redirect URI in the URL")

do {
    let code = try SpotifyAuthCore.code(from: URL(string: "reeper-auth://callback?code=XYZ&state=st")!, expectedState: "st")
    check(code == "XYZ", "code read from callback")
} catch { check(false, "callback parse: \(error)") }
do {
    _ = try SpotifyAuthCore.code(from: URL(string: "reeper-auth://callback?code=XYZ&state=evil")!, expectedState: "st")
    check(false, "state mismatch should throw")
} catch let e as SpotifyAuthCore.CallbackError { check(e == .stateMismatch, "state mismatch rejected") } catch {}
do {
    _ = try SpotifyAuthCore.code(from: URL(string: "reeper-auth://callback?error=access_denied&state=st")!, expectedState: "st")
    check(false, "denied should throw")
} catch let e as SpotifyAuthCore.CallbackError {
    check(e.errorDescription == "Spotify sign-in was cancelled.", "user cancel has a friendly message")
} catch {}

let req = SpotifyAuthCore.tokenRequest(code: "c d", verifier: "v~1", clientID: "cid")
let body = String(data: req.httpBody ?? Data(), encoding: .utf8) ?? ""
check(req.httpMethod == "POST" && req.url == SpotifyAuthCore.tokenURL, "token request endpoint")
check(body.contains("grant_type=authorization_code") && body.contains("code=c%20d")
      && body.contains("code_verifier=v~1") && body.contains("client_id=cid")
      && body.contains("redirect_uri=reeper-auth%3A%2F%2Fcallback"), "token request form body is encoded")
check(!body.contains("client_secret"), "no client secret is sent")

let now = Date(timeIntervalSince1970: 1000)
do {
    let t = try SpotifyAuthCore.tokens(from: Data(#"{"access_token":"A","refresh_token":"R","expires_in":3600}"#.utf8),
                                       status: 200, now: now)
    check(t == SpotifyTokens(accessToken: "A", refreshToken: "R", expiresAt: 4600), "token reply parsed")
} catch { check(false, "token parse: \(error)") }
do {
    _ = try SpotifyAuthCore.tokens(from: Data(#"{"error":"invalid_grant","error_description":"Invalid authorization code"}"#.utf8),
                                   status: 400, now: now)
    check(false, "error reply should throw")
} catch {
    check(error.localizedDescription.contains("Invalid authorization code"), "token error explained")
}

print(failures == 0 ? "\nALL PASSED" : "\n\(failures) FAILED")
exit(failures == 0 ? 0 : 1)
