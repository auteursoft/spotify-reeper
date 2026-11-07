import AppKit
import SwiftUI

/// First-run guide. Reeper uses each person's own (free) Spotify developer app, because
/// Spotify only lets an individual's app serve a handful of allow-listed users.
struct SetupGuideView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var clientID = ""
    @State private var lastFMKey = ""
    @State private var copied = false

    private var clientIDValid: Bool { SpotifyAuthCore.isValidClientID(clientID) }
    private var allDone: Bool { model.isSignedIn && model.hasLastFMKey }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Welcome to Reeper").font(.largeTitle).bold()
                Text("Three one-time steps connect Reeper to your music. They take about five minutes.")
                    .foregroundStyle(.secondary)
            }
            .padding([.horizontal, .top], 24)
            .padding(.bottom, 12)

            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    step(1, "Create your Spotify app", done: clientIDValid) {
                        Text("Reeper talks to Spotify through a free developer app that you own, so your data stays between you and Spotify.")
                        VStack(alignment: .leading, spacing: 4) {
                            Text("1. Open the Spotify Developer Dashboard and log in.")
                            Text("2. Click **Create app**. Any name and description will do.")
                            Text("3. Under **Redirect URIs**, add exactly:")
                            HStack {
                                Text(SpotifyAuthCore.redirectURI)
                                    .font(.system(.body, design: .monospaced))
                                    .textSelection(.enabled)
                                    .padding(.horizontal, 8).padding(.vertical, 3)
                                    .background(RoundedRectangle(cornerRadius: 5).fill(.quaternary))
                                Button(copied ? "Copied" : "Copy") {
                                    NSPasteboard.general.clearContents()
                                    NSPasteboard.general.setString(SpotifyAuthCore.redirectURI, forType: .string)
                                    copied = true
                                }
                            }
                            Text("4. Tick **Web API**, agree to the terms, and click **Save**.")
                        }
                        Button("Open Spotify Developer Dashboard") {
                            model.open("https://developer.spotify.com/dashboard")
                        }
                    }

                    step(2, "Paste your Client ID", done: clientIDValid) {
                        Text("On your app's page, open **Settings** and copy the **Client ID**. Reeper doesn't need the client secret.")
                        HStack {
                            TextField("Client ID", text: $clientID, prompt: Text("32 letters and numbers"))
                                .font(.system(.body, design: .monospaced))
                                .textFieldStyle(.roundedBorder)
                                .onChange(of: clientID) { _, new in
                                    if SpotifyAuthCore.isValidClientID(new) {
                                        model.clientID = new.trimmingCharacters(in: .whitespacesAndNewlines)
                                    }
                                }
                            if !clientID.isEmpty && !clientIDValid {
                                Text("That doesn't look like a Client ID").font(.caption).foregroundStyle(.orange)
                            }
                        }
                    }

                    step(3, "Sign in to Spotify", done: model.isSignedIn) {
                        if let account = model.account {
                            Label("Signed in as \(account.name)", systemImage: "checkmark.circle.fill")
                                .foregroundStyle(.green)
                        } else {
                            HStack {
                                Button("Sign In with Spotify") { Task { await model.signIn() } }
                                    .buttonStyle(.borderedProminent)
                                    .disabled(!clientIDValid || model.isSigningIn)
                                if model.isSigningIn { ProgressView().controlSize(.small) }
                            }
                            Text("Your Spotify app is in Development mode, so it works for your own account. To let someone else use your app, add their Spotify email under **User Management** in the dashboard. Spotify requires the app's owner to have Premium.")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }

                    step(4, "Add a Last.fm API key", done: model.hasLastFMKey) {
                        Text("Reeper reads how people tag songs on Last.fm. Create a free API account (only the name and description matter), then paste the **API key**. You don't need the shared secret.")
                        HStack {
                            TextField("API key", text: $lastFMKey)
                                .font(.system(.body, design: .monospaced))
                                .textFieldStyle(.roundedBorder)
                            Button("Save") { model.setLastFMKey(lastFMKey) }
                                .disabled(lastFMKey.trimmingCharacters(in: .whitespaces).isEmpty)
                        }
                        Button("Create a Last.fm API Account") {
                            model.open("https://www.last.fm/api/account/create")
                        }
                    }
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 12)
            }

            Divider()
            HStack {
                Text("You can change these any time in Settings (⌘,).")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Later") { dismiss() }
                Button("Done") {
                    model.finishedSetup = true
                    dismiss()
                }
                .keyboardShortcut(.defaultAction)
                .disabled(!allDone)
            }
            .padding(16)
        }
        .frame(width: 620, height: 680)
        .onAppear { clientID = model.clientID }
    }

    private func step<Content: View>(_ n: Int, _ title: String, done: Bool,
                                     @ViewBuilder content: () -> Content) -> some View {
        GroupBox {
            HStack(alignment: .top, spacing: 12) {
                ZStack {
                    Circle().fill(done ? Color.green : Color.accentColor).frame(width: 26, height: 26)
                    if done {
                        Image(systemName: "checkmark").font(.caption.bold()).foregroundStyle(.white)
                    } else {
                        Text("\(n)").font(.callout.bold()).foregroundStyle(.white)
                    }
                }
                .accessibilityLabel(done ? "Step \(n), done" : "Step \(n)")
                VStack(alignment: .leading, spacing: 8) {
                    Text(title).font(.headline)
                    content()
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(6)
        }
    }
}
