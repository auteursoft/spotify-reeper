import AppKit
import SwiftUI
import UniformTypeIdentifiers

final class AppDelegate: NSObject, NSApplicationDelegate {
    var model: AppModel?

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        MainActor.assumeIsolated { model?.stop() }
    }
}

@main
struct ReeperApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var model = AppModel()

    var body: some Scene {
        Window("Reeper", id: "main") {
            ContentView()
                .environment(model)
                .frame(minWidth: 860, minHeight: 540)
                .task {
                    delegate.model = model
                    await model.start()
                }
        }
        .defaultSize(width: 1100, height: 720)
        .commands { ReeperCommands(model: model) }

        Settings {
            SettingsView()
                .environment(model)
        }

        Window("Acknowledgements", id: "acknowledgements") {
            AcknowledgementsView()
        }
        .windowResizability(.contentSize)
    }
}

struct ReeperCommands: Commands {
    let model: AppModel
    @Environment(\.openWindow) private var openWindow

    var body: some Commands {
        CommandGroup(replacing: .newItem) {
            Button("New Mood") { Task { await model.newMood() } }
                .keyboardShortcut("n")
            Divider()
            Button("Import Moods…") { NotificationCenter.default.post(name: .importMoods, object: nil) }
                .keyboardShortcut("i", modifiers: [.command, .shift])
            Button("Export Moods…") { NotificationCenter.default.post(name: .exportMoods, object: nil) }
                .keyboardShortcut("e", modifiers: [.command, .shift])
        }
        CommandGroup(after: .sidebar) {
            Button("Refresh Playlists") { Task { await model.reloadPlaylists(refresh: true) } }
                .keyboardShortcut("r")
                .disabled(!model.isSignedIn)
            Divider()
            Button("Playlists") { model.selection = .playlists }
                .keyboardShortcut("1")
            Button("Library Analysis") { model.selection = .library }
                .keyboardShortcut("2")
        }
        CommandGroup(replacing: .help) {
            Button("Reeper Setup Guide") { model.showSetupGuide = true }
            Button("Spotify Developer Dashboard") { model.open("https://developer.spotify.com/dashboard") }
            Button("Last.fm API Accounts") { model.open("https://www.last.fm/api/accounts") }
            Divider()
            Button("Acknowledgements") { openWindow(id: "acknowledgements") }
        }
    }
}

extension Notification.Name {
    static let importMoods = Notification.Name("ReeperImportMoods")
    static let exportMoods = Notification.Name("ReeperExportMoods")
}

/// moods.json as a document, for Export Moods.
struct MoodsDocument: FileDocument {
    static var readableContentTypes: [UTType] { [.json] }
    var content: String

    init(content: String) { self.content = content }

    init(configuration: ReadConfiguration) throws {
        content = String(decoding: configuration.file.regularFileContents ?? Data(), as: UTF8.self)
    }

    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper {
        FileWrapper(regularFileWithContents: Data(content.utf8))
    }
}
