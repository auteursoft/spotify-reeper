import SwiftUI

struct BuildTab: View {
    @Environment(AppModel.self) private var model
    let mood: Mood

    @State private var excluded = Set<Pick.ID>()
    @State private var name = ""
    @State private var creating = false
    @State private var created: CreatedPlaylist?
    @State private var showAdvanced = false

    private var options: Binding<BuildOptions> {
        Binding(get: { model.buildOptions[mood.name] ?? BuildOptions() },
                set: { model.buildOptions[mood.name] = $0 })
    }

    private var result: BuildResult? { model.buildResults[mood.name] }
    private var included: [Pick] { result?.picks.filter { !excluded.contains($0.id) } ?? [] }

    var body: some View {
        HStack(spacing: 0) {
            optionsPane
                .frame(width: 290)
            Divider()
            resultsPane
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .onChange(of: result?.picks.map(\.id)) { _, _ in
            // A new preview: everything included again, ready to create.
            excluded = []
            created = nil
            if let result { name = result.defaultName }
        }
        .onAppear { if name.isEmpty { name = result?.defaultName ?? "" } }
    }

    // MARK: Options

    private var optionsPane: some View {
        Form {
            Section {
                Stepper(value: options.size, in: 5...200, step: 5) {
                    LabeledContent("Length", value: "\(options.wrappedValue.size) songs")
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text("Mix")
                    Slider(value: options.horizon, in: 0...1, step: 0.05) {
                        Text("Mix")
                    } minimumValueLabel: {
                        Image(systemName: "house").accessibilityLabel("Your library")
                    } maximumValueLabel: {
                        Image(systemName: "sparkle.magnifyingglass").accessibilityLabel("New to you")
                    }
                    .labelsHidden()
                    Text(mixDescription).font(.caption).foregroundStyle(.secondary)
                }
            }
            Section {
                DisclosureGroup("Advanced", isExpanded: $showAdvanced) {
                    Stepper(value: options.maxPerArtist, in: 1...20) {
                        LabeledContent("Songs per artist", value: "at most \(options.wrappedValue.maxPerArtist)")
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        LabeledContent("Minimum fit", value: "better than \(Int((options.wrappedValue.minFit * 100).rounded()))%")
                        Slider(value: options.minFit, in: 0...0.95, step: 0.05).labelsHidden()
                        Text("Leaves out songs that fit the mood worse than this share of your library.").font(.caption).foregroundStyle(.secondary)
                    }
                    Stepper(value: options.depth, in: 50...1000, step: 50) {
                        LabeledContent("Discovery depth", value: "\(options.wrappedValue.depth) songs")
                    }
                    Text("How many songs from outside your library to consider. More finds more, but the first run takes longer.")
                        .font(.caption).foregroundStyle(.secondary)
                    Button("Restore Defaults") { options.wrappedValue = BuildOptions() }
                }
            }
            Section {
                Button {
                    model.build(mood.name)
                } label: {
                    Label(result == nil ? "Preview Playlist" : "Preview Again", systemImage: "wand.and.stars")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .keyboardShortcut(.return, modifiers: .command)
                .disabled(model.activity != nil || (mood.seeds.count < 5 && mood.seedPlaylists.isEmpty))
                Text("Nothing is created in Spotify until you choose Create.").font(.caption).foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
    }

    private var mixDescription: String {
        let h = options.wrappedValue.horizon
        if h == 0 { return "Only songs already in your library" }
        if h == 1 { return "Only songs new to you" }
        return "About \(Int((h * 100).rounded()))% new to you"
    }

    // MARK: Results

    @ViewBuilder
    private var resultsPane: some View {
        if let result {
            VStack(spacing: 0) {
                Table(result.picks) {
                    TableColumn("") { p in
                        Toggle("Include", isOn: Binding(get: { !excluded.contains(p.id) },
                                                        set: { on in if on { excluded.remove(p.id) } else { excluded.insert(p.id) } }))
                            .labelsHidden()
                            .accessibilityLabel("Include \(p.title)")
                    }
                    .width(24)
                    TableColumn("Title") { p in
                        HStack(spacing: 6) {
                            Text(p.title)
                            if p.new {
                                Text("NEW").font(.caption2).bold()
                                    .padding(.horizontal, 5).padding(.vertical, 1)
                                    .background(Capsule().fill(Color.accentColor.opacity(0.18)))
                                    .help("Not in your library")
                            }
                        }
                        .opacity(excluded.contains(p.id) ? 0.4 : 1)
                    }
                    .width(min: 160, ideal: 240)
                    TableColumn("Artist") { p in Text(p.artist).foregroundStyle(.secondary) }
                        .width(min: 100, ideal: 160)
                    TableColumn("Fit") { p in
                        HStack(spacing: 6) {
                            ProgressView(value: p.fit).frame(width: 50)
                            Text(String(format: "%.1f", p.fit * 100)).monospacedDigit().foregroundStyle(.secondary)
                        }
                        .help("Fits this mood better than \(String(format: "%.1f", p.fit * 100))% of your library")
                        .accessibilityElement(children: .ignore)
                        .accessibilityLabel("Fits better than \(String(format: "%.1f", p.fit * 100)) percent of your library")
                    }
                    .width(90)
                    TableColumn("Why") { p in
                        Text(p.why.joined(separator: " · ")).foregroundStyle(.secondary).help(p.why.joined(separator: "\n"))
                    }
                    .width(min: 160, ideal: 260)
                    TableColumn("Found via") { p in Text(p.via ?? "").foregroundStyle(.secondary) }
                        .width(min: 100, ideal: 150)
                }
                Divider()
                createBar(result)
            }
        } else {
            ContentUnavailableView {
                Label("Preview a “\(mood.name)” playlist", systemImage: "wand.and.stars")
            } description: {
                Text(mood.seeds.count < 5 && mood.seedPlaylists.isEmpty
                     ? "Add at least 5 seed songs first."
                     : "Choose the length and mix, then Preview. You can untick songs before creating the playlist.")
            }
        }
    }

    private func createBar(_ result: BuildResult) -> some View {
        HStack(spacing: 10) {
            if let created {
                Label("Created in Spotify", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                Button("Open in Spotify") { model.open(created.url) }
                Spacer()
            } else {
                Text("\(included.count) songs, \(included.filter(\.new).count) new to you")
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
                Spacer()
                TextField("Playlist name", text: $name)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 300)
                if creating { ProgressView().controlSize(.small) }
                Button("Create in Spotify") { Task { await create(result) } }
                    .buttonStyle(.borderedProminent)
                    .disabled(creating || included.isEmpty || name.trimmingCharacters(in: .whitespaces).isEmpty)
                    .help("Creates a private playlist in your Spotify library")
            }
        }
        .padding(10)
    }

    private func create(_ result: BuildResult) async {
        creating = true
        defer { creating = false }
        created = await model.createPlaylist(name: name.trimmingCharacters(in: .whitespaces),
                                             picks: included, mood: mood.name)
    }
}
