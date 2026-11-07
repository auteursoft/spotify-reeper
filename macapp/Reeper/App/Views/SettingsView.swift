import SwiftUI

struct SettingsView: View {
    var body: some View {
        TabView {
            AccountsSettings()
                .tabItem { Label("Accounts", systemImage: "person.crop.circle") }
            AnalysisSettings()
                .tabItem { Label("Analysis", systemImage: "waveform") }
        }
        .frame(width: 540)
    }
}

struct AccountsSettings: View {
    @Environment(AppModel.self) private var model
    @State private var clientID = ""
    @State private var lastFMKey = ""
    @State private var confirmSignOut = false

    var body: some View {
        Form {
            Section("Spotify") {
                TextField("Client ID", text: $clientID, prompt: Text("From your app in the Spotify Developer Dashboard"))
                    .font(.system(.body, design: .monospaced))
                    .onSubmit(saveClientID)
                if !clientID.isEmpty && !SpotifyAuthCore.isValidClientID(clientID) {
                    Text("A Client ID is 32 letters and numbers.").font(.caption).foregroundStyle(.orange)
                }
                LabeledContent("Redirect URI") {
                    Text(SpotifyAuthCore.redirectURI).font(.system(.body, design: .monospaced)).textSelection(.enabled)
                }
                LabeledContent("Account") {
                    if let account = model.account {
                        HStack {
                            Text(account.name)
                            Button("Sign Out") { confirmSignOut = true }
                        }
                    } else if model.isSignedIn {
                        Text("Signed in")
                    } else {
                        Button("Sign In with Spotify") {
                            saveClientID()
                            Task { await model.signIn() }
                        }
                        .disabled(!SpotifyAuthCore.isValidClientID(clientID) || model.isSigningIn)
                    }
                }
            }
            Section {
                SecureField("API key", text: $lastFMKey, prompt: Text(model.hasLastFMKey ? "Saved in your Keychain" : "Paste your API key"))
                    .onSubmit(saveLastFM)
                HStack {
                    Button("Save Key", action: saveLastFM)
                        .disabled(lastFMKey.trimmingCharacters(in: .whitespaces).isEmpty)
                    Spacer()
                    Link("Your Last.fm API accounts", destination: URL(string: "https://www.last.fm/api/accounts")!)
                }
            } header: {
                Text("Last.fm")
            } footer: {
                Text("Keys and sign-in tokens are stored in your Mac's Keychain.").font(.caption).foregroundStyle(.secondary)
            }
            Section {
                Button("Open Setup Guide") { model.showSetupGuide = true }
            }
        }
        .formStyle(.grouped)
        .onAppear { clientID = model.clientID }
        .confirmationDialog("Sign out of Spotify?", isPresented: $confirmSignOut) {
            Button("Sign Out", role: .destructive) { model.signOut() }
        } message: {
            Text("Your moods and analysis stay on this Mac.")
        }
    }

    private func saveClientID() {
        let trimmed = clientID.trimmingCharacters(in: .whitespacesAndNewlines)
        guard SpotifyAuthCore.isValidClientID(trimmed), trimmed != model.clientID else { return }
        model.clientID = trimmed
        model.signOut()  // tokens belong to the old Client ID
    }

    private func saveLastFM() {
        model.setLastFMKey(lastFMKey)
        lastFMKey = ""
    }
}

struct AnalysisSettings: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        Form {
            Section {
                if model.audioSupported {
                    Toggle("Analyze how songs sound", isOn: $model.useAudio)
                    Text("Listens to 30-second previews with on-device audio models. Turn off to judge songs by Last.fm tags only (faster, less accurate).")
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    Text("This edition of Reeper judges songs by their Last.fm tags.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Toggle("Include Liked Songs in my library", isOn: $model.includeLiked)
            }
            if model.audioSupported {
                Section("Audio models") {
                    LabeledContent("Status") {
                        Text(model.libraryStatus?.modelsPresent == true ? "Installed" : "Not installed")
                    }
                    if model.libraryStatus?.modelsPresent != true {
                        Button("Download Models") { model.downloadModels() }
                            .disabled(model.activity != nil)
                    }
                }
            }
            Section {
                LabeledContent("Data folder") {
                    Button("Show in Finder") { model.revealDataFolder() }
                }
                Text("Your moods and the analysis of each song are kept here, on this Mac only.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .task { await model.refreshLibraryStatus() }
    }
}

struct AcknowledgementsView: View {
    /// The tags-only (App Store) edition doesn't include Essentia or its models.
    private var includesEssentia: Bool {
        Bundle.main.resourceURL.map {
            FileManager.default.fileExists(atPath: $0.appendingPathComponent("python/.reeper-audio").path)
        } ?? false
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text("Acknowledgements").font(.title2).bold()
                if includesEssentia {
                    credit("Essentia audio models",
                           "Music Technology Group, Universitat Pompeu Fabra. Discogs-EffNet and classification heads, licensed CC BY-NC-SA 4.0.",
                           "https://essentia.upf.edu/models.html")
                    credit("Essentia",
                           "Music Technology Group, Universitat Pompeu Fabra, licensed AGPL-3.0. Reeper's complete source code is available under the same terms.",
                           "https://github.com/auteursoft/spotify-reeper")
                }
                credit("Last.fm", "Song and artist tags and similar music, from the Last.fm API.", "https://www.last.fm/api")
                credit("Deezer", "30-second song previews, from the Deezer API.", "https://developers.deezer.com")
                credit("Spotify", "Your playlists and library, via the Spotify Web API. Spotify is a trademark of Spotify AB; Reeper is not affiliated with or endorsed by Spotify.",
                       "https://developer.spotify.com")
                credit("Open-source software", "Python, TensorFlow, NumPy, SciPy, scikit-learn, spotipy, requests and their dependencies, under their respective licenses.", nil)
            }
            .padding(24)
        }
        .frame(width: 520, height: 460)
    }

    private func credit(_ title: String, _ text: String, _ link: String?) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.headline)
            Text(text).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if let link, let url = URL(string: link) { Link(link, destination: url).font(.caption) }
        }
    }
}
