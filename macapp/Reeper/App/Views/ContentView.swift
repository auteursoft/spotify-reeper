import SwiftUI

struct ContentView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openSettings) private var openSettings
    @State private var importing = false
    @State private var exportDocument: MoodsDocument?

    var body: some View {
        @Bindable var model = model
        NavigationSplitView {
            SidebarView()
                .navigationSplitViewColumnWidth(min: 200, ideal: 230, max: 320)
        } detail: {
            detail
        }
        .toolbar {
            ToolbarItem(placement: .status) { ActivityStatusView() }
        }
        .sheet(isPresented: $model.showSetupGuide) {
            SetupGuideView()
        }
        .alert(model.alert?.title ?? "", isPresented: Binding(
            get: { model.alert != nil }, set: { if !$0 { model.alert = nil } }),
               presenting: model.alert) { item in
            switch item.action {
            case .openSettings:
                Button("Open Settings") { openSettings() }
                Button("OK", role: .cancel) {}
            case .signIn:
                Button("Sign In") { Task { await model.signIn() } }
                Button("Not Now", role: .cancel) {}
            case .none:
                Button("OK", role: .cancel) {}
            }
        } message: { item in
            Text(item.message)
        }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.json]) { result in
            if case .success(let url) = result { Task { await model.importMoods(from: url) } }
        }
        .fileExporter(isPresented: Binding(get: { exportDocument != nil },
                                           set: { if !$0 { exportDocument = nil } }),
                      document: exportDocument, contentType: .json, defaultFilename: "moods.json") { _ in }
        .onReceive(NotificationCenter.default.publisher(for: .importMoods)) { _ in importing = true }
        .onReceive(NotificationCenter.default.publisher(for: .exportMoods)) { _ in
            Task { if let content = await model.exportMoods() { exportDocument = MoodsDocument(content: content) } }
        }
        .onAppear {
            if model.needsSetup { model.showSetupGuide = true }
        }
    }

    @ViewBuilder
    private var detail: some View {
        switch model.engineState {
        case .starting:
            ProgressView("Starting Reeper…")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        case .unavailable(let why):
            ContentUnavailableView {
                Label("Reeper's engine isn't running", systemImage: "exclamationmark.triangle")
            } description: {
                Text(why)
            } actions: {
                Button("Try Again") { Task { await model.restartEngine() } }
                    .buttonStyle(.borderedProminent)
            }
        case .ready:
            if !model.isSignedIn && model.selection != .library {
                SignedOutView()
            } else {
                switch model.selection {
                case .playlists, .none: PlaylistsView()
                case .library: LibraryView()
                case .mood(let name):
                    if let mood = model.mood(named: name) {
                        MoodView(mood: mood).id(name)
                    } else {
                        ContentUnavailableView("No mood selected", systemImage: "sparkles")
                    }
                }
            }
        }
    }
}

struct SignedOutView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        ContentUnavailableView {
            Label("Sign in to Spotify", systemImage: "person.crop.circle.badge.questionmark")
        } description: {
            Text("Reeper needs your permission to read and organize your playlists.")
        } actions: {
            if SpotifyAuthCore.isValidClientID(model.clientID) {
                Button("Sign In with Spotify") { Task { await model.signIn() } }
                    .buttonStyle(.borderedProminent)
                    .disabled(model.isSigningIn)
            } else {
                Button("Open Setup Guide") { model.showSetupGuide = true }
                    .buttonStyle(.borderedProminent)
            }
        }
    }
}

/// The running job, its progress, and a Stop button, in the toolbar.
struct ActivityStatusView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        if let a = model.activity {
            HStack(spacing: 8) {
                if let f = a.fraction {
                    ProgressView(value: f).frame(width: 90)
                } else {
                    ProgressView().controlSize(.small)
                }
                VStack(alignment: .leading, spacing: 0) {
                    Text(a.title).font(.caption).bold().lineLimit(1)
                    if !a.message.isEmpty {
                        Text(a.message).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                    }
                }
                .frame(maxWidth: 260, alignment: .leading)
                Button {
                    a.cancel()
                } label: {
                    Image(systemName: "stop.circle.fill")
                }
                .buttonStyle(.borderless)
                .help("Stop. Work done so far is kept, and continues next time.")
                .accessibilityLabel("Stop \(a.title)")
            }
            .accessibilityElement(children: .combine)
        }
    }
}
