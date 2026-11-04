import SwiftUI

// MARK: Merge

struct MergeSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let sources: [PlaylistInfo]
    let onDone: (MoveResult) -> Void

    @State private var destination = ""
    @State private var keepSources = false
    @State private var skipDuplicates = true
    @State private var preview: MovePreview?
    @State private var working = false

    private var trimmed: String { destination.trimmingCharacters(in: .whitespaces) }

    var body: some View {
        Form {
            Section {
                LabeledContent("Playlists") {
                    Text(sources.count == 1 ? sources[0].name : "\(sources.count) selected")
                }
                HStack {
                    TextField("Into playlist", text: $destination, prompt: Text("Name of an existing or new playlist"))
                    Menu {
                        ForEach(model.myPlaylistNames(), id: \.self) { name in
                            Button(name) { destination = name }
                        }
                    } label: {
                        Image(systemName: "chevron.down")
                    }
                    .menuStyle(.borderlessButton)
                    .fixedSize()
                    .help("Choose one of your playlists")
                    .accessibilityLabel("Choose destination playlist")
                }
                if !trimmed.isEmpty, let preview {
                    Label(preview.createsNew
                          ? "Creates a new private playlist named “\(trimmed)”."
                          : "Adds to your existing playlist “\(preview.destination?.name ?? trimmed)”.",
                          systemImage: preview.createsNew ? "plus.circle" : "arrow.down.circle")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
            }
            Section {
                Toggle("Keep the original playlists", isOn: $keepSources)
                Toggle("Skip songs the destination already has", isOn: $skipDuplicates)
            } footer: {
                Text(keepSources
                     ? "Songs are copied; the original playlists stay in your library."
                     : "After its songs are added, each original playlist is removed from your library. A playlist that's empty, or has local files or songs no longer on Spotify, is kept so nothing is lost.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .frame(width: 460)
        .navigationTitle("Merge Playlists")
        .toolbar {
            ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(working) }
            ToolbarItem(placement: .confirmationAction) {
                if working {
                    ProgressView().controlSize(.small)
                } else {
                    Button(keepSources ? "Copy Songs" : "Merge") { Task { await run() } }
                        .disabled(trimmed.isEmpty)
                }
            }
        }
        .task(id: trimmed) {
            preview = nil
            guard !trimmed.isEmpty else { return }
            try? await Task.sleep(for: .milliseconds(250))
            guard !Task.isCancelled else { return }
            preview = await model.movePreview(destination: trimmed)
        }
        .interactiveDismissDisabled(working)
    }

    private func run() async {
        working = true
        defer { working = false }
        if let result = await model.move(Set(sources.map(\.id)), into: trimmed,
                                         keepSources: keepSources, skipDuplicates: skipDuplicates) {
            dismiss()
            onDone(result)
        }
    }
}

// MARK: Rename

struct RenameSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let targets: [PlaylistInfo]
    let onDone: (RenameResult) -> Void

    @State private var find = ""
    @State private var replace = ""
    @State private var regex = false
    @State private var caseSensitive = false
    @State private var changes: [RenameChange] = []
    @State private var problem: String?
    @State private var working = false

    private struct Query: Equatable { var find, replace: String; var regex, caseSensitive: Bool }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Form {
                TextField("Find", text: $find, prompt: Text(regex ? "Regular expression" : "Text in the name"))
                TextField("Replace with", text: $replace, prompt: Text(regex ? "Use \\1 for groups" : "Leave empty to remove"))
                HStack {
                    Toggle("Regular expression", isOn: $regex)
                    Toggle("Match case", isOn: $caseSensitive)
                }
                if let problem {
                    Label(problem, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                }
                let others = targets.filter { !$0.mine }.count
                if others > 0 {
                    Text("\(others) selected playlist\(others == 1 ? "" : "s") by other people can't be renamed and \(others == 1 ? "is" : "are") left out.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .formStyle(.grouped)
            .frame(height: 230)

            Table(changes) {
                TableColumn("Current name", value: \.name)
                TableColumn("New name") { c in
                    Text(c.valid ? c.newName : "(empty: will be skipped)")
                        .foregroundStyle(c.valid ? .primary : .secondary)
                }
            }
            .frame(minHeight: 180)
            .overlay {
                if changes.isEmpty {
                    Text(find.isEmpty ? "Type text to find to preview new names." : "No names would change.")
                        .foregroundStyle(.secondary)
                }
            }
        }
        .frame(width: 560, height: 480)
        .navigationTitle("Rename Playlists")
        .toolbar {
            ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(working) }
            ToolbarItem(placement: .confirmationAction) {
                if working {
                    ProgressView().controlSize(.small)
                } else {
                    Button(validCount == 1 ? "Rename 1 Playlist" : "Rename \(validCount) Playlists") {
                        Task { await run() }
                    }
                    .disabled(validCount == 0 || problem != nil)
                }
            }
        }
        .task(id: Query(find: find, replace: replace, regex: regex, caseSensitive: caseSensitive)) {
            try? await Task.sleep(for: .milliseconds(200))
            guard !Task.isCancelled else { return }
            guard !find.isEmpty else { changes = []; problem = nil; return }
            switch await model.renamePreview(Set(targets.map(\.id)), find: find, replace: replace,
                                             regex: regex, caseSensitive: caseSensitive) {
            case .success(let c): changes = c; problem = nil
            case .failure(let f): changes = []; problem = f.localizedDescription
            }
        }
        .interactiveDismissDisabled(working)
    }

    private var validCount: Int { changes.filter(\.valid).count }

    private func run() async {
        working = true
        defer { working = false }
        if let result = await model.rename(changes.filter(\.valid)) {
            dismiss()
            if result.outcomes.contains(where: { !$0.ok }) { onDone(result) }
        }
    }
}

// MARK: Results

enum OperationReport: Identifiable {
    case delete(DeleteResult)
    case move(MoveResult)
    case rename(RenameResult)

    var id: String {
        switch self {
        case .delete: return "delete"
        case .move: return "move"
        case .rename: return "rename"
        }
    }
}

struct OperationReportView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(AppModel.self) private var model
    let report: OperationReport

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title).font(.title3).bold()
            if let subtitle { Text(subtitle).foregroundStyle(.secondary) }
            List(rows) { row in
                HStack(alignment: .firstTextBaseline) {
                    Image(systemName: row.ok ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                        .foregroundStyle(row.ok ? .green : .orange)
                        .accessibilityLabel(row.ok ? "Done" : "Problem")
                    VStack(alignment: .leading) {
                        Text(row.name)
                        Text(row.detail).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            .frame(minHeight: 160)
            HStack {
                if case .move(let r) = report, let url = r.destination?.url {
                    Button("Open in Spotify") { model.open(url) }
                }
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 480, height: 380)
    }

    private var title: String {
        switch report {
        case .delete: return "Some playlists couldn't be removed"
        case .move(let r):
            if r.error == "nothing_to_add" { return "Nothing was merged" }
            if r.error != nil { return "Nothing was changed" }
            return r.outcomes.contains { $0.status == "failed" } ? "Merged, with problems" : "Merged"
        case .rename: return "Some playlists couldn't be renamed"
        }
    }

    private var subtitle: String? {
        switch report {
        case .move(let r):
            if r.error == "nothing_to_add" { return "None of the playlists had songs that could be added." }
            if let e = r.error { return e }
            return r.destination.map { (r.created ? "Created “\($0.name)”." : "Added to “\($0.name)”.") }
        default: return nil
        }
    }

    struct Row: Identifiable {
        let id = UUID()
        let name: String
        let detail: String
        let ok: Bool
    }

    private var rows: [Row] {
        switch report {
        case .delete(let r):
            return r.outcomes.map { Row(name: $0.name, detail: $0.ok ? "Removed" : ($0.error ?? "Failed"), ok: $0.ok) }
        case .rename(let r):
            return r.outcomes.map { Row(name: $0.newName ?? $0.id, detail: $0.ok ? "Renamed" : ($0.error ?? "Failed"), ok: $0.ok) }
        case .move(let r):
            return r.outcomes.map { o in
                var detail: String
                switch o.status {
                case "moved": detail = "\(o.added) songs added; playlist removed"
                case "copied": detail = "\(o.added) songs added; playlist kept"
                case "kept": detail = "\(o.added) songs added; kept because \(o.reason ?? "it couldn't be fully moved")"
                default: detail = o.reason ?? "Failed"
                }
                if o.duplicates > 0 && o.status != "failed" { detail += " (\(o.duplicates) duplicates skipped)" }
                return Row(name: o.name, detail: detail, ok: o.status != "failed")
            }
        }
    }
}
