import SwiftUI

struct SidebarView: View {
    @Environment(AppModel.self) private var model
    @State private var renaming: Mood?
    @State private var deleting: Mood?

    var body: some View {
        @Bindable var model = model
        List(selection: $model.selection) {
            Section("Library") {
                Label("Playlists", systemImage: "music.note.list")
                    .tag(SidebarItem.playlists)
                Label("Library Analysis", systemImage: "waveform")
                    .tag(SidebarItem.library)
            }
            Section("Moods") {
                ForEach(model.moods) { mood in
                    Label(mood.name, systemImage: "sparkles")
                        .tag(SidebarItem.mood(mood.name))
                        .contextMenu {
                            Button("Rename…") { renaming = mood }
                            Button("Delete…", role: .destructive) { deleting = mood }
                        }
                }
                if model.moods.isEmpty {
                    Text("Add a mood, give it songs that nail the feel, and Reeper builds playlists from it.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .selectionDisabled()
                }
            }
        }
        .listStyle(.sidebar)
        .safeAreaInset(edge: .bottom) {
            VStack(spacing: 0) {
                Divider()
                HStack {
                    Button {
                        Task { await model.newMood() }
                    } label: {
                        Label("New Mood", systemImage: "plus")
                    }
                    .buttonStyle(.borderless)
                    .help("New Mood (⌘N)")
                    Spacer()
                    AccountBadge()
                }
                .padding(8)
            }
        }
        .sheet(item: $renaming) { mood in
            RenameMoodSheet(mood: mood)
        }
        .confirmationDialog("Delete the mood “\(deleting?.name ?? "")”?",
                            isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
                            titleVisibility: .visible, presenting: deleting) { mood in
            Button("Delete Mood", role: .destructive) { Task { await model.deleteMood(mood.name) } }
        } message: { _ in
            Text("Its seed songs are forgotten. Playlists already created in Spotify stay.")
        }
    }
}

struct AccountBadge: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openSettings) private var openSettings

    var body: some View {
        Button {
            openSettings()
        } label: {
            if let account = model.account {
                Label(account.name, systemImage: "person.crop.circle.fill")
                    .labelStyle(.titleAndIcon)
                    .lineLimit(1)
            } else {
                Label("Not signed in", systemImage: "person.crop.circle.badge.exclamationmark")
            }
        }
        .buttonStyle(.borderless)
        .font(.caption)
        .foregroundStyle(.secondary)
        .help("Accounts and settings (⌘,)")
    }
}

struct RenameMoodSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let mood: Mood
    @State private var name = ""

    var body: some View {
        Form {
            TextField("Name", text: $name)
                .onSubmit(save)
        }
        .formStyle(.grouped)
        .frame(width: 360)
        .navigationTitle("Rename Mood")
        .toolbar {
            ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
            ToolbarItem(placement: .confirmationAction) {
                Button("Rename", action: save)
                    .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .onAppear { name = mood.name }
    }

    private func save() {
        let trimmed = name.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return }
        var renamed = mood
        renamed.name = trimmed
        Task {
            if await model.saveMood(renamed, previousName: mood.name) { dismiss() }
        }
    }
}
