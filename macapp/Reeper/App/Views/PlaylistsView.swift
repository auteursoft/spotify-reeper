import AppKit
import SwiftUI

extension PlaylistInfo {
    var trackCount: Int { tracks ?? 0 }
    var ownerLabel: String { mine ? "You" : (ownerName ?? ownerId ?? "") }
    var visibilityLabel: String { visibility.capitalized }
}

struct PlaylistsView: View {
    @Environment(AppModel.self) private var model
    @State private var selection = Set<PlaylistInfo.ID>()
    @State private var sortOrder = [KeyPathComparator(\PlaylistInfo.name,
                                                      comparator: String.StandardComparator.localizedStandard)]
    @State private var confirmDelete: Set<String>?
    @State private var merging: Set<String>?
    @State private var renaming: Set<String>?
    @State private var report: OperationReport?
    @State private var showFilters = false

    private var sorted: [PlaylistInfo] { model.playlists.sorted(using: sortOrder) }
    private var selected: [PlaylistInfo] { model.playlists.filter { selection.contains($0.id) } }

    var body: some View {
        @Bindable var model = model
        VStack(spacing: 0) {
            if let error = model.playlistFilterError {
                Label(error, systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.orange)
                    .font(.callout)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal)
                    .padding(.vertical, 6)
                    .background(.orange.opacity(0.1))
                    .accessibilityLabel("Filter problem: \(error)")
            }
            table
            Divider()
            statusBar
        }
        .navigationTitle("Playlists")
        .navigationSubtitle(model.account.map { "Signed in as \($0.name)" } ?? "")
        .searchable(text: $model.playlistFilter.pattern, placement: .toolbar,
                    prompt: Text(searchPrompt))
        .toolbar { toolbar }
        .task(id: model.playlistFilter) {
            // Debounce typing; changing the filter cancels the previous reload.
            try? await Task.sleep(for: .milliseconds(180))
            guard !Task.isCancelled else { return }
            await model.reloadPlaylists()
        }
        .onDeleteCommand { if !selection.isEmpty { confirmDelete = selection } }
        .confirmationDialog(deleteTitle, isPresented: Binding(
            get: { confirmDelete != nil }, set: { if !$0 { confirmDelete = nil } }),
                            titleVisibility: .visible, presenting: confirmDelete) { ids in
            Button("Remove \(ids.count == 1 ? "Playlist" : "\(ids.count) Playlists")", role: .destructive) {
                Task { await delete(ids) }
            }
        } message: { ids in
            Text(deleteMessage(ids))
        }
        .sheet(item: Binding(get: { merging.map(IDSet.init) }, set: { merging = $0?.ids })) { set in
            MergeSheet(sources: model.playlists.filter { set.ids.contains($0.id) }) { result in
                report = .move(result)
                selection = []
            }
        }
        .sheet(item: Binding(get: { renaming.map(IDSet.init) }, set: { renaming = $0?.ids })) { set in
            RenameSheet(targets: model.playlists.filter { set.ids.contains($0.id) }) { result in
                report = .rename(result)
            }
        }
        .sheet(item: $report) { r in OperationReportView(report: r) }
    }

    private var searchPrompt: String {
        switch model.playlistFilter.mode {
        case .substring: return "Filter playlists"
        case .glob: return "Wildcard, e.g. *2019*"
        case .regex: return "Regular expression"
        }
    }

    // MARK: Table

    private var table: some View {
        Table(sorted, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Name", value: \.name, comparator: String.StandardComparator.localizedStandard) { pl in
                Text(pl.name).help(pl.name)
            }
            .width(min: 200, ideal: 340)
            TableColumn("Owner", value: \.ownerLabel, comparator: String.StandardComparator.localizedStandard) { pl in
                Text(pl.ownerLabel).foregroundStyle(pl.mine ? .primary : .secondary)
            }
            .width(min: 80, ideal: 140)
            TableColumn("Visibility", value: \.visibility) { pl in
                Label(pl.visibilityLabel, systemImage: visibilityIcon(pl.visibility))
                    .labelStyle(.titleAndIcon)
                    .foregroundStyle(.secondary)
            }
            .width(min: 90, ideal: 120)
            TableColumn("Tracks", value: \.trackCount) { pl in
                Text(pl.tracks.map(String.init) ?? "—").monospacedDigit()
                    .frame(maxWidth: .infinity, alignment: .trailing)
            }
            .width(min: 50, ideal: 70)
        }
        .contextMenu(forSelectionType: PlaylistInfo.ID.self) { ids in
            if !ids.isEmpty { actions(for: ids) }
        } primaryAction: { ids in
            if ids.count == 1, let pl = model.playlists.first(where: { ids.contains($0.id) }) { model.open(pl.url) }
        }
        .overlay {
            if model.playlists.isEmpty && !model.isLoadingPlaylists {
                if model.playlistFilter.pattern.isEmpty && model.playlistTotal == 0 {
                    ContentUnavailableView("No playlists", systemImage: "music.note.list",
                                           description: Text("Your Spotify library has no playlists yet."))
                } else {
                    ContentUnavailableView.search(text: model.playlistFilter.pattern)
                }
            }
        }
    }

    @ViewBuilder
    private func actions(for ids: Set<String>) -> some View {
        let pls = model.playlists.filter { ids.contains($0.id) }
        if pls.count == 1, let pl = pls.first {
            Button("Open in Spotify") { model.open(pl.url) }
            Button("Copy Link") {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(pl.url ?? "", forType: .string)
            }
            Divider()
        }
        Button("Merge into Playlist…") { merging = ids }
        Button("Rename…") { renaming = ids }
            .disabled(!pls.contains(where: \.mine))
        Divider()
        Button("Remove from Library…", role: .destructive) { confirmDelete = ids }
    }

    // MARK: Toolbar

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        @Bindable var model = model
        ToolbarItem(placement: .navigation) {
            Picker("Match", selection: $model.playlistFilter.mode) {
                Text("Contains").tag(PlaylistFilter.Mode.substring)
                Text("Wildcard").tag(PlaylistFilter.Mode.glob)
                Text("Regex").tag(PlaylistFilter.Mode.regex)
            }
            .pickerStyle(.menu)
            .help("How the filter text matches playlist names")
        }
        ToolbarItem {
            Button {
                showFilters.toggle()
            } label: {
                Label("Filters", systemImage: activeFilterCount > 0
                      ? "line.3.horizontal.decrease.circle.fill" : "line.3.horizontal.decrease.circle")
            }
            .help("Owner, visibility and exclusion filters")
            .popover(isPresented: $showFilters, arrowEdge: .bottom) { FilterPopover() }
        }
        ToolbarItemGroup {
            Button {
                merging = selection
            } label: {
                Label("Merge", systemImage: "arrow.triangle.merge")
            }
            .help("Merge the selected playlists into one")
            .disabled(selection.isEmpty)

            Button {
                renaming = selection
            } label: {
                Label("Rename", systemImage: "character.cursor.ibeam")
            }
            .help("Find and replace in the names of the selected playlists you own")
            .disabled(!selected.contains(where: \.mine))

            Button(role: .destructive) {
                confirmDelete = selection
            } label: {
                Label("Remove", systemImage: "trash")
            }
            .help("Remove the selected playlists from your library (⌫)")
            .disabled(selection.isEmpty)

            Button {
                Task { await model.reloadPlaylists(refresh: true) }
            } label: {
                Label("Refresh", systemImage: "arrow.clockwise")
            }
            .help("Reload playlists from Spotify (⌘R)")
        }
    }

    private var activeFilterCount: Int {
        let f = model.playlistFilter
        return (f.mine ? 1 : 0) + (f.visibility != nil ? 1 : 0) + (f.exclude.isEmpty ? 0 : 1) + (f.caseSensitive ? 1 : 0)
    }

    private var statusBar: some View {
        HStack {
            if model.isLoadingPlaylists { ProgressView().controlSize(.small) }
            Text(statusText).font(.callout).foregroundStyle(.secondary)
            Spacer()
            if !selection.isEmpty {
                Text("\(selection.count) selected").font(.callout).foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
    }

    private var statusText: String {
        let shown = model.playlists.count, total = model.playlistTotal
        return shown == total ? "\(total) playlists" : "\(shown) of \(total) playlists"
    }

    // MARK: Delete

    private var deleteTitle: String {
        let n = confirmDelete?.count ?? 0
        return n == 1 ? "Remove “\(model.playlists.first { confirmDelete?.contains($0.id) == true }?.name ?? "")” from your library?"
            : "Remove \(n) playlists from your library?"
    }

    private func deleteMessage(_ ids: Set<String>) -> String {
        let pls = model.playlists.filter { ids.contains($0.id) }
        let owned = pls.filter(\.mine).count
        var parts: [String] = []
        if owned > 0 {
            parts.append("Playlists you own are deleted. Spotify keeps them for 90 days at spotify.com/account/recover-playlists.")
        }
        if owned < pls.count {
            parts.append("Playlists by others are unfollowed; you can follow them again later.")
        }
        return parts.joined(separator: " ")
    }

    private func delete(_ ids: Set<String>) async {
        if let result = await model.deletePlaylists(ids) {
            selection.subtract(result.outcomes.filter(\.ok).map(\.id))
            if result.outcomes.contains(where: { !$0.ok }) { report = .delete(result) }
        }
    }

    private func visibilityIcon(_ v: String) -> String {
        switch v {
        case "public": return "globe"
        case "private": return "lock"
        case "collaborative": return "person.2"
        default: return "questionmark.circle"
        }
    }
}

/// Wraps a selection so it can drive `.sheet(item:)`.
struct IDSet: Identifiable {
    let ids: Set<String>
    var id: String { ids.sorted().joined(separator: ",") }
}

struct FilterPopover: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        Form {
            Toggle("Only playlists I own", isOn: $model.playlistFilter.mine)
            Picker("Visibility", selection: $model.playlistFilter.visibility) {
                Text("Any").tag(String?.none)
                Text("Public").tag(String?.some("public"))
                Text("Private").tag(String?.some("private"))
            }
            TextField("Exclude names matching", text: $model.playlistFilter.exclude, prompt: Text("e.g. Daily Mix"))
            Toggle("Match case", isOn: $model.playlistFilter.caseSensitive)
            Text("Collaborative playlists count as private. The exclusion uses the same match style as the search field.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Spacer()
                Button("Reset") {
                    let pattern = model.playlistFilter.pattern
                    model.playlistFilter = PlaylistFilter()
                    model.playlistFilter.pattern = pattern
                }
            }
        }
        .formStyle(.grouped)
        .frame(width: 320)
    }
}
