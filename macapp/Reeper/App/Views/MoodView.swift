import SwiftUI
import UniformTypeIdentifiers

struct MoodView: View {
    let mood: Mood
    enum Tab: String, CaseIterable, Identifiable {
        case seeds = "Seeds"
        case insights = "What It Learned"
        case build = "Build Playlist"
        var id: String { rawValue }
    }
    @SceneStorage("moodTab") private var tab: Tab = .seeds

    var body: some View {
        Group {
            switch tab {
            case .seeds: SeedsTab(mood: mood)
            case .insights: InsightsTab(mood: mood)
            case .build: BuildTab(mood: mood)
            }
        }
        .navigationTitle(mood.name)
        .toolbar {
            ToolbarItem(placement: .principal) {
                Picker("View", selection: $tab) {
                    ForEach(Tab.allCases) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .fixedSize()
            }
        }
    }
}

// MARK: - Seeds

struct SeedsTab: View {
    @Environment(AppModel.self) private var model
    let mood: Mood
    @State private var selectedSeeds = Set<Seed.ID>()
    @State private var selectedPlaylists = Set<String>()
    @State private var addingSong = false
    @State private var addingPlaylist = false
    @State private var dropTargeted = false

    private var seedCount: Int { mood.seeds.count }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text("Seeds are songs that nail this mood. Reeper learns what they share, in sound and in how people tag them, compared with the rest of your library. Use at least 5; 15 to 30 that really share the feel work best.")
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                GroupBox {
                    VStack(alignment: .leading) {
                        HStack {
                            Text("Songs").font(.headline)
                            Text("\(seedCount)").foregroundStyle(.secondary).monospacedDigit()
                            Spacer()
                            Button("Add Song…", systemImage: "plus") { addingSong = true }
                            Button("Remove", systemImage: "minus") { removeSeeds(selectedSeeds) }
                                .disabled(selectedSeeds.isEmpty)
                                .labelStyle(.iconOnly)
                                .help("Remove the selected songs (⌫)")
                        }
                        Table(mood.seeds, selection: $selectedSeeds) {
                            TableColumn("Title") { s in Text(s.displayTitle) }
                            TableColumn("Artist") { s in Text(s.displayArtist).foregroundStyle(.secondary) }
                        }
                        .frame(height: 240)
                        .onDeleteCommand { removeSeeds(selectedSeeds) }
                        .overlay {
                            if mood.seeds.isEmpty {
                                Text("Add songs, paste Spotify links, or drag songs here from the Spotify app.")
                                    .foregroundStyle(.secondary)
                                    .padding()
                            }
                        }
                    }
                    .padding(4)
                }

                GroupBox {
                    VStack(alignment: .leading) {
                        HStack {
                            Text("Playlists").font(.headline)
                            Text("every song in them counts as a seed").foregroundStyle(.secondary)
                            Spacer()
                            Button("Add Playlist…", systemImage: "plus") { addingPlaylist = true }
                            Button("Remove", systemImage: "minus") { removePlaylists(selectedPlaylists) }
                                .disabled(selectedPlaylists.isEmpty)
                                .labelStyle(.iconOnly)
                                .help("Remove the selected playlists")
                        }
                        List(mood.seedPlaylists, id: \.self, selection: $selectedPlaylists) { entry in
                            Label(playlistLabel(entry), systemImage: "music.note.list")
                        }
                        .frame(height: 100)
                        .onDeleteCommand { removePlaylists(selectedPlaylists) }
                        .overlay {
                            if mood.seedPlaylists.isEmpty {
                                Text("Optional").foregroundStyle(.secondary)
                            }
                        }
                    }
                    .padding(4)
                }

                HStack {
                    Button("Check Seeds") { model.checkSeeds(mood.name) }
                        .disabled(mood.seeds.isEmpty && mood.seedPlaylists.isEmpty)
                        .help("Find each seed on Spotify, fetch its tags, and analyze its sound")
                    if let check = model.seedChecks[mood.name] {
                        Text(checkSummary(check)).foregroundStyle(.secondary)
                    }
                }

                if let check = model.seedChecks[mood.name] {
                    SeedCheckTable(check: check)
                }
            }
            .padding(20)
        }
        .overlay {
            if dropTargeted {
                RoundedRectangle(cornerRadius: 10).stroke(Color.accentColor, lineWidth: 3).padding(4)
                    .allowsHitTesting(false)
            }
        }
        .dropDestination(for: String.self) { items, _ in
            addLinks(items.joined(separator: "\n"))
            return true
        } isTargeted: { dropTargeted = $0 }
        .onPasteCommand(of: [.plainText, .url]) { providers in
            for p in providers {
                _ = p.loadObject(ofClass: NSString.self) { s, _ in
                    if let s = s as? String { Task { @MainActor in addLinks(s) } }
                }
            }
        }
        .sheet(isPresented: $addingSong) { AddSongSheet(mood: mood) }
        .sheet(isPresented: $addingPlaylist) { AddPlaylistSheet(mood: mood) }
    }

    private func playlistLabel(_ entry: String) -> String {
        if entry.contains("open.spotify.com/playlist/") || entry.hasPrefix("spotify:playlist:") {
            if let id = entry.split(separator: "/").last?.split(separator: "?").first,
               let pl = model.playlists.first(where: { $0.id == String(id) }) {
                return pl.name
            }
            return "Playlist link: \(entry)"
        }
        return entry
    }

    private func checkSummary(_ c: SeedCheck) -> String {
        let audio = c.seeds.filter { $0.audioStatus == "ok" }.count
        let tagged = c.seeds.filter { !$0.tags.isEmpty }.count
        return "\(c.seeds.count) songs: \(audio) with audio, \(tagged) with tags"
    }

    private func removeSeeds(_ ids: Set<Seed.ID>) {
        guard !ids.isEmpty else { return }
        var m = mood
        m.seeds.removeAll { ids.contains($0.id) }
        selectedSeeds = []
        Task { await model.saveMood(m, previousName: mood.name) }
    }

    private func removePlaylists(_ entries: Set<String>) {
        guard !entries.isEmpty else { return }
        var m = mood
        m.seedPlaylists.removeAll { entries.contains($0) }
        selectedPlaylists = []
        Task { await model.saveMood(m, previousName: mood.name) }
    }

    /// Adds Spotify track and playlist links found in text (pasted or dropped).
    private func addLinks(_ text: String) {
        var m = mood
        var added = 0
        for raw in text.split(whereSeparator: { $0.isNewline || $0 == " " }) {
            let s = String(raw)
            if s.contains("open.spotify.com/track/") || s.hasPrefix("spotify:track:") {
                let seed = Seed.link(s)
                if !m.seeds.contains(seed) { m.seeds.append(seed); added += 1 }
            } else if s.contains("open.spotify.com/playlist/") || s.hasPrefix("spotify:playlist:") {
                if !m.seedPlaylists.contains(s) { m.seedPlaylists.append(s); added += 1 }
            }
        }
        if added > 0 { Task { await model.saveMood(m, previousName: mood.name) } }
    }
}

struct SeedCheckTable: View {
    let check: SeedCheck

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Check results").font(.headline)
            Table(check.seeds) {
                TableColumn("Song") { s in Text(s.title) }
                TableColumn("Artist") { s in Text(s.artist).foregroundStyle(.secondary) }
                TableColumn("On Spotify") { s in
                    Image(systemName: s.onSpotify ? "checkmark.circle.fill" : "xmark.circle")
                        .foregroundStyle(s.onSpotify ? .green : .orange)
                        .accessibilityLabel(s.onSpotify ? "Found on Spotify" : "Not found on Spotify")
                }
                .width(80)
                TableColumn("Tags") { s in
                    Text(s.tags.isEmpty ? "none" : s.tags.joined(separator: ", ")
                         + (s.tagSource == "artist" ? " (artist)" : ""))
                        .foregroundStyle(s.tags.isEmpty ? .secondary : .primary)
                        .help(s.tagSource == "artist" ? "No tags for this song; using its artist's tags" : "")
                }
                TableColumn("Sound") { s in AudioStatusLabel(status: s.audioStatus) }
                    .width(110)
            }
            .frame(height: 280)
            ForEach(check.problems, id: \.self) { p in
                Label(p, systemImage: "info.circle").font(.callout).foregroundStyle(.secondary)
            }
        }
    }
}

struct AudioStatusLabel: View {
    let status: String

    var body: some View {
        switch status {
        case "ok": Label("Analyzed", systemImage: "waveform").foregroundStyle(.green)
        case "no_match": Label("Not on Deezer", systemImage: "questionmark.circle").foregroundStyle(.secondary)
        case "no_preview": Label("No preview", systemImage: "speaker.slash").foregroundStyle(.secondary)
        case "off": Label("Off", systemImage: "waveform.slash").foregroundStyle(.secondary)
        default: Label("Failed", systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
        }
    }
}

// MARK: - Add song / playlist

struct AddSongSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let mood: Mood
    @State private var query = ""
    @State private var results: [TrackInfo] = []
    @State private var selection = Set<TrackInfo.ID>()
    @State private var searching = false

    var body: some View {
        VStack(spacing: 0) {
            TextField("Song, artist, or Spotify link", text: $query)
                .textFieldStyle(.roundedBorder)
                .padding()
            List(results, selection: $selection) { t in
                VStack(alignment: .leading) {
                    Text(t.title)
                    Text([t.artist, t.album].compactMap { $0 }.joined(separator: " — "))
                        .font(.caption).foregroundStyle(.secondary)
                }
                .tag(t.id)
            }
            .overlay {
                if searching { ProgressView() }
                else if results.isEmpty && !query.isEmpty { Text("No songs found").foregroundStyle(.secondary) }
            }
        }
        .frame(width: 480, height: 420)
        .navigationTitle("Add Songs to “\(mood.name)”")
        .toolbar {
            ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
            ToolbarItem(placement: .confirmationAction) {
                Button(selection.count > 1 ? "Add \(selection.count) Songs" : "Add Song") { add() }
                    .disabled(selection.isEmpty)
            }
        }
        .task(id: query) {
            try? await Task.sleep(for: .milliseconds(300))
            guard !Task.isCancelled else { return }
            searching = true
            results = await model.searchTracks(query)
            searching = false
        }
    }

    private func add() {
        var m = mood
        for t in results where selection.contains(t.id) {
            let seed = Seed.song(artist: t.artist, title: t.title, uri: t.uri)
            if !m.seeds.contains(seed) { m.seeds.append(seed) }
        }
        Task {
            if await model.saveMood(m, previousName: mood.name) { dismiss() }
        }
    }
}

struct AddPlaylistSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let mood: Mood
    @State private var filter = ""
    @State private var link = ""
    @State private var selection = Set<String>()
    @State private var checking = false

    private var mine: [PlaylistInfo] {
        model.playlists.filter { $0.mine && (filter.isEmpty || $0.name.localizedCaseInsensitiveContains(filter)) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Choose your playlists, or paste a link to a playlist you own or collaborate on. Spotify doesn't let apps read other people's playlists; copy their songs into a playlist of your own first.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            TextField("Filter your playlists", text: $filter)
                .textFieldStyle(.roundedBorder)
            List(selection: $selection) {
                ForEach(mine) { pl in
                    Text(pl.name).tag(pl.url ?? pl.id)
                }
            }
            HStack {
                TextField("Or paste a playlist link", text: $link)
                    .textFieldStyle(.roundedBorder)
                if checking { ProgressView().controlSize(.small) }
            }
        }
        .padding()
        .frame(width: 460, height: 460)
        .navigationTitle("Add Playlists to “\(mood.name)”")
        .toolbar {
            ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
            ToolbarItem(placement: .confirmationAction) {
                Button("Add") { Task { await add() } }
                    .disabled(selection.isEmpty && link.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
    }

    private func add() async {
        var m = mood
        for entry in selection where !m.seedPlaylists.contains(entry) { m.seedPlaylists.append(entry) }
        let trimmed = link.trimmingCharacters(in: .whitespaces)
        if !trimmed.isEmpty {
            checking = true
            let found = await model.lookupPlaylist(trimmed)
            checking = false
            guard let found else { return }
            if !m.seedPlaylists.contains(found.link) { m.seedPlaylists.append(found.link) }
        }
        if await model.saveMood(m, previousName: mood.name) { dismiss() }
    }
}

// MARK: - Insights

struct InsightsTab: View {
    @Environment(AppModel.self) private var model
    let mood: Mood

    private var seedTotal: Int { mood.seeds.count }

    var body: some View {
        if let r = model.trainResults[mood.name] {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    VerdictBanner(result: r)
                    GroupBox("More of") { ChipFlow(items: r.summary.moreOf, tint: .green) }
                    GroupBox("Less of") { ChipFlow(items: r.summary.lessOf, tint: .red) }
                    if r.summary.soundWeight > 0 {
                        Text("Beyond these named qualities, the overall sound of a track also counts.")
                            .font(.callout).foregroundStyle(.secondary)
                    }
                    Text("Learned from \(r.summary.seedsUsed) seed songs, compared with \(r.summary.comparedWith) other tracks in your library.")
                        .font(.callout).foregroundStyle(.secondary)
                    if !r.check.isEmpty {
                        GroupBox("Check: each seed left out, then ranked against your library") {
                            Table(r.check) {
                                TableColumn("Seed") { c in Text(c.title) }
                                TableColumn("Artist") { c in Text(c.artist).foregroundStyle(.secondary) }
                                TableColumn("Ranked") { c in
                                    HStack {
                                        ProgressView(value: c.percentile / 100).frame(width: 80)
                                        Text("top \(max(1, Int((100 - c.percentile).rounded())))%")
                                            .monospacedDigit()
                                    }
                                    .accessibilityElement(children: .combine)
                                }
                            }
                            .frame(height: 260)
                        }
                        Text("If much of your library fits this mood, even a perfect model can't put every seed in the top 5%: other songs that fit also rank high.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Button("Learn Again") { model.train(mood.name) }
                        .disabled(model.activity != nil)
                }
                .padding(20)
                .frame(maxWidth: 760, alignment: .leading)
            }
        } else {
            ContentUnavailableView {
                Label("See what “\(mood.name)” means to Reeper", systemImage: "brain")
            } description: {
                Text(seedTotal < 5 && mood.seedPlaylists.isEmpty
                     ? "Add at least 5 seed songs first."
                     : "Reeper learns the mood from your seeds and checks how well it learned it. The first time, it analyzes your library, which can take a while; it's saved for next time.")
            } actions: {
                Button("Learn This Mood") { model.train(mood.name) }
                    .buttonStyle(.borderedProminent)
                    .disabled((seedTotal < 5 && mood.seedPlaylists.isEmpty) || model.activity != nil)
            }
        }
    }
}

struct VerdictBanner: View {
    let result: TrainResult

    var body: some View {
        let (icon, color): (String, Color) = switch result.verdict {
        case "good": ("checkmark.seal.fill", .green)
        case "fair": ("exclamationmark.circle.fill", .yellow)
        default: ("exclamationmark.triangle.fill", .orange)
        }
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon).font(.title).foregroundStyle(color)
            VStack(alignment: .leading, spacing: 4) {
                Text(headline).font(.title3).bold()
                if let text = result.verdictText {
                    Text(text.prefix(1).uppercased() + text.dropFirst()).foregroundStyle(.secondary)
                }
            }
        }
        .accessibilityElement(children: .combine)
    }

    private var headline: String {
        guard let m = result.medianPercentile else { return "Learned" }
        return "Seeds typically rank in the top \(max(1, Int((100 - m).rounded())))% of your library"
    }
}

struct ChipFlow: View {
    let items: [String]
    let tint: Color

    var body: some View {
        if items.isEmpty {
            Text("Nothing stands out").foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading)
        } else {
            FlowLayout(spacing: 6) {
                ForEach(items, id: \.self) { item in
                    Text(item)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 4)
                        .background(Capsule().fill(tint.opacity(0.15)))
                        .overlay(Capsule().stroke(tint.opacity(0.35)))
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(4)
        }
    }
}

/// Lays out children left to right, wrapping onto new rows.
struct FlowLayout: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, row: CGFloat = 0, widest: CGFloat = 0
        for s in subviews {
            let size = s.sizeThatFits(.unspecified)
            if x > 0 && x + size.width > maxWidth {
                x = 0
                y += row + spacing
                row = 0
            }
            x += size.width + spacing
            row = max(row, size.height)
            widest = max(widest, x - spacing)
        }
        return CGSize(width: min(widest, maxWidth), height: y + row)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, row: CGFloat = 0
        for s in subviews {
            let size = s.sizeThatFits(.unspecified)
            if x > bounds.minX && x + size.width > bounds.maxX {
                x = bounds.minX
                y += row + spacing
                row = 0
            }
            s.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing
            row = max(row, size.height)
        }
    }
}
