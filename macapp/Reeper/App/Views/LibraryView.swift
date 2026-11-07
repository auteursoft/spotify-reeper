import SwiftUI

struct LibraryView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text(model.audioSupported
                     ? "Reeper listens to a 30-second preview of each song in your library and reads how people tag it on Last.fm. That's what lets it tell which songs fit a mood. The first analysis takes about a second per song; everything is saved, so it only happens once per song, and you can stop and continue any time."
                     : "Reeper reads how people tag each song in your library on Last.fm. That's what lets it tell which songs fit a mood. Everything is saved, so each song is only looked up once, and you can stop and continue any time.")
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                HStack(spacing: 16) {
                    if model.audioSupported {
                        StatCard(title: "Songs analyzed for sound", value: model.libraryStatus?.audioAnalyzed,
                                 systemImage: "waveform")
                    }
                    StatCard(title: "Songs with Last.fm tags", value: model.libraryStatus?.tagged,
                             systemImage: "tag")
                }

                if model.audioSupported && model.useAudio && model.libraryStatus?.modelsPresent == false {
                    GroupBox {
                        HStack {
                            Label("The audio models aren't installed.", systemImage: "exclamationmark.triangle.fill")
                                .foregroundStyle(.orange)
                            Spacer()
                            Button("Download Models") { model.downloadModels() }
                                .disabled(model.activity != nil)
                        }
                        .padding(4)
                    }
                }

                HStack {
                    Button {
                        model.analyzeLibrary()
                    } label: {
                        Label("Analyze Library", systemImage: "waveform.badge.magnifyingglass")
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .disabled(model.activity != nil || !model.isSignedIn)
                    Button("Re-read Playlists from Spotify") { model.analyzeLibrary(refresh: true) }
                        .disabled(model.activity != nil || !model.isSignedIn)
                        .help("Use after adding songs to your playlists in Spotify")
                }
                if !model.isSignedIn {
                    Text("Sign in to Spotify to analyze your library.").foregroundStyle(.secondary)
                }

                if let a = model.lastAnalysis {
                    GroupBox("Last analysis") {
                        VStack(alignment: .leading, spacing: 6) {
                            LabeledContent("Songs in your library", value: "\(a.tracks)")
                            LabeledContent("Analyzed for sound", value: "\(a.withAudio)")
                            LabeledContent("With Last.fm tags", value: "\(a.withTags)")
                            ForEach(a.audioStatus.sorted(by: { $0.key < $1.key }), id: \.key) { status, count in
                                LabeledContent {
                                    Text("\(count)")
                                } label: {
                                    AudioStatusLabel(status: status)
                                }
                            }
                        }
                        .padding(4)
                    }
                    Text("“Not on Deezer” and “No preview” songs are judged by their tags alone.")
                        .font(.caption).foregroundStyle(.secondary)
                }

                GroupBox("What Reeper includes") {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Playlists you own or collaborate on" + (model.includeLiked ? ", and your Liked Songs." : "."))
                        Text("Spotify doesn't let apps read the songs in other people's playlists.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(4)
                }
            }
            .padding(20)
            .frame(maxWidth: 720, alignment: .leading)
        }
        .navigationTitle("Library Analysis")
        .task { await model.refreshLibraryStatus() }
    }
}

struct StatCard: View {
    let title: String
    let value: Int?
    let systemImage: String

    var body: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 4) {
                Label(title, systemImage: systemImage).foregroundStyle(.secondary)
                Text(value.map { $0.formatted() } ?? "—")
                    .font(.system(size: 28, weight: .semibold, design: .rounded))
                    .monospacedDigit()
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(6)
        }
        .accessibilityElement(children: .combine)
    }
}
