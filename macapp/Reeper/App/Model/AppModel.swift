import AppKit
import Foundation
import Observation
import SwiftUI

/// A long-running job shown in the toolbar, with progress and a Stop button.
@MainActor @Observable
final class Activity: Identifiable {
    let id = UUID()
    let title: String
    var message = ""
    var fraction: Double?
    @ObservationIgnored var task: Task<Void, Never>?

    init(title: String) { self.title = title }

    func update(_ p: EngineProgress) {
        message = p.message
        fraction = p.fraction
    }

    func cancel() { task?.cancel() }
}

struct AlertItem: Identifiable {
    enum Action { case none, openSettings, signIn }
    let id = UUID()
    var title: String
    var message: String
    var action: Action = .none
}

enum SidebarItem: Hashable {
    case playlists
    case library
    case mood(String)
}

@MainActor @Observable
final class AppModel {
    private enum Keys {
        static let clientID = "spotifyClientID"
        static let useAudio = "useAudioAnalysis"
        static let includeLiked = "includeLikedSongs"
        static let onboarded = "finishedSetup"
    }

    // MARK: Settings

    var clientID: String {
        didSet { UserDefaults.standard.set(clientID.trimmingCharacters(in: .whitespacesAndNewlines), forKey: Keys.clientID) }
    }
    var useAudio: Bool {
        didSet { UserDefaults.standard.set(useAudio, forKey: Keys.useAudio); reconfigure() }
    }
    var includeLiked: Bool {
        didSet { UserDefaults.standard.set(includeLiked, forKey: Keys.includeLiked); reconfigure() }
    }
    private(set) var hasLastFMKey: Bool
    var finishedSetup: Bool {
        didSet { UserDefaults.standard.set(finishedSetup, forKey: Keys.onboarded) }
    }

    // MARK: State

    enum EngineState: Equatable { case starting, ready, unavailable(String) }
    private(set) var engineState: EngineState = .starting
    private(set) var account: Account?
    private(set) var isSigningIn = false
    private var tokens: SpotifyTokens?
    var isSignedIn: Bool { tokens != nil }
    var needsSetup: Bool {
        !SpotifyAuthCore.isValidClientID(clientID) || !isSignedIn || !hasLastFMKey || !finishedSetup
    }

    var selection: SidebarItem? = .playlists
    var showSetupGuide = false
    var alert: AlertItem?
    private(set) var activity: Activity?

    // Playlists
    private(set) var playlists: [PlaylistInfo] = []
    private(set) var playlistTotal = 0
    var playlistFilter = PlaylistFilter()
    private(set) var playlistFilterError: String?
    private(set) var isLoadingPlaylists = false

    // Moods and their latest results
    private(set) var moods: [Mood] = []
    var seedChecks: [String: SeedCheck] = [:]
    var trainResults: [String: TrainResult] = [:]
    var buildResults: [String: BuildResult] = [:]
    var buildOptions: [String: BuildOptions] = [:]

    // Library analysis
    private(set) var libraryStatus: LibraryStatus?
    /// False in the tags-only edition, which judges songs by Last.fm tags alone.
    var audioSupported: Bool { libraryStatus?.audioSupported ?? true }
    private(set) var lastAnalysis: AnalyzeResult?

    private var engine: EngineClient?
    private let signInFlow = SpotifySignIn()

    init() {
        let d = UserDefaults.standard
        clientID = d.string(forKey: Keys.clientID) ?? ""
        useAudio = d.object(forKey: Keys.useAudio) as? Bool ?? true
        includeLiked = d.object(forKey: Keys.includeLiked) as? Bool ?? true
        finishedSetup = d.bool(forKey: Keys.onboarded)
        hasLastFMKey = Keychain.lastFMKey != nil
        tokens = Keychain.spotifyTokens
    }

    // MARK: Engine

    func start() async {
        guard engine == nil else { return }
        guard let launch = EngineLaunch.configuration() else {
            engineState = .unavailable("Reeper's analysis engine is missing from this copy of the app. "
                + "Please reinstall Reeper.")
            return
        }
        let client = EngineClient(launch: launch)
        client.onEvent = { [weak self] name, data in
            guard name == "tokens" else { return }
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            guard let tokens = try? decoder.decode(SpotifyTokens.self, from: data) else { return }
            Task { @MainActor in self?.store(tokens: tokens) }
        }
        client.onExit = { [weak self] _, detail in
            Task { @MainActor in self?.engineDied(detail) }
        }
        client.onLog = { line in
            #if DEBUG
            print("[engine] \(line)")
            #endif
        }
        engine = client
        engineState = .starting
        do {
            try await client.start()
            engineState = .ready
            await configureEngine()
            await loadMoods()
            if isSignedIn {
                await loadAccount()
                await reloadPlaylists()
            }
            await refreshLibraryStatus()
        } catch {
            engineState = .unavailable(error.localizedDescription)
        }
    }

    func stop() {
        engine?.stop()
    }

    private func engineDied(_ detail: String) {
        guard engine != nil else { return }
        engine = nil
        activity = nil
        engineState = .unavailable("Reeper's analysis engine stopped." + (detail.isEmpty ? "" : "\n\n\(detail)"))
    }

    func restartEngine() async {
        engine?.stop()
        engine = nil
        await start()
    }

    private func reconfigure() {
        Task { await configureEngine() }
    }

    private func configureEngine() async {
        guard let engine, engineState == .ready else { return }
        var params: [String: JSONValue] = [
            "data_dir": .string(EngineLaunch.dataDirectory.path),
            "client_id": .string(clientID.trimmingCharacters(in: .whitespacesAndNewlines)),
            "lastfm_api_key": JSONValue(Keychain.lastFMKey),
            "use_audio": .bool(useAudio),
            "include_liked": .bool(includeLiked),
            "tokens": tokens.map { (try? JSONValue.from($0)) ?? .null } ?? .null,
        ]
        if let models = EngineLaunch.bundledModels { params["models_dir"] = .string(models.path) }
        do {
            let _: OK = try await engine.call("session.configure", params)
        } catch {
            present(error)
        }
    }

    /// Calls the engine, reporting problems to the user. Returns nil on failure.
    private func call<R: Decodable & Sendable>(_ method: String, _ params: [String: JSONValue] = [:],
                                    progress: (@Sendable (EngineProgress) -> Void)? = nil,
                                    quiet: Bool = false) async -> R? {
        guard let engine, engineState == .ready else {
            if !quiet, case .unavailable(let why) = engineState {
                alert = AlertItem(title: "Reeper can't do that right now", message: why)
            }
            return nil
        }
        do {
            return try await engine.call(method, params, as: R.self, progress: progress)
        } catch {
            if !quiet { present(error) }
            return nil
        }
    }

    /// Runs a long job as the current activity (shown in the toolbar with a Stop button).
    func runActivity(_ title: String, _ work: @escaping @MainActor (Activity) async -> Void) {
        if let current = activity {
            alert = AlertItem(title: "Reeper is busy",
                              message: "\(current.title) is still running. Stop it or wait for it to finish.")
            return
        }
        let a = Activity(title: title)
        activity = a
        a.task = Task { @MainActor in
            await work(a)
            if self.activity === a { self.activity = nil }
        }
    }

    func present(_ error: Error) {
        if error is SignInCancelled { return }
        if let failure = error as? EngineFailure {
            switch failure.code {
            case "cancelled": return
            case "auth":
                signOut(keepAccount: false)
                alert = AlertItem(title: "Please sign in to Spotify again",
                                  message: failure.localizedDescription, action: .signIn)
                return
            case "not_configured":
                alert = AlertItem(title: "Setup needed", message: failure.localizedDescription, action: .openSettings)
                return
            default: break
            }
        }
        alert = AlertItem(title: "Something went wrong", message: error.localizedDescription)
    }

    // MARK: Accounts

    func signIn() async {
        let id = clientID.trimmingCharacters(in: .whitespacesAndNewlines)
        guard SpotifyAuthCore.isValidClientID(id) else {
            alert = AlertItem(title: "Enter your Spotify Client ID first",
                              message: "It's the 32-character ID shown on your app's page in the Spotify "
                                + "Developer Dashboard.", action: .openSettings)
            return
        }
        isSigningIn = true
        defer { isSigningIn = false }
        do {
            let t = try await signInFlow.signIn(clientID: id)
            store(tokens: t)
            await configureEngine()
            await loadAccount()
            await reloadPlaylists(refresh: true)
        } catch {
            present(error)
        }
    }

    func signOut(keepAccount: Bool = false) {
        tokens = nil
        Keychain.spotifyTokens = nil
        account = nil
        playlists = []
        playlistTotal = 0
        reconfigure()
    }

    private func store(tokens new: SpotifyTokens) {
        tokens = new
        Keychain.spotifyTokens = new
    }

    func setLastFMKey(_ key: String) {
        Keychain.lastFMKey = key
        hasLastFMKey = Keychain.lastFMKey != nil
        reconfigure()
    }

    private func loadAccount() async {
        account = await call("account.me", quiet: true)
    }

    // MARK: Playlists

    func reloadPlaylists(refresh: Bool = false) async {
        guard isSignedIn else { return }
        isLoadingPlaylists = true
        defer { isLoadingPlaylists = false }
        guard let engine, engineState == .ready else { return }
        do {
            let result: PlaylistList = try await engine.call(
                "playlists.list", ["refresh": .bool(refresh), "filter": try JSONValue.from(playlistFilter)])
            playlists = result.playlists
            playlistTotal = result.total
            playlistFilterError = nil
        } catch let failure as EngineFailure where failure.code == "invalid_pattern" {
            playlistFilterError = failure.localizedDescription
        } catch {
            present(error)
        }
    }

    func deletePlaylists(_ ids: Set<String>) async -> DeleteResult? {
        let result: DeleteResult? = await call("playlists.delete", ["ids": JSONValue(Array(ids))])
        await reloadPlaylists()
        return result
    }

    func movePreview(destination: String) async -> MovePreview? {
        await call("playlists.move_preview", ["destination": .string(destination)], quiet: true)
    }

    func move(_ ids: Set<String>, into destination: String, keepSources: Bool, skipDuplicates: Bool) async -> MoveResult? {
        let result: MoveResult? = await call("playlists.move", [
            "ids": JSONValue(Array(ids)), "destination": .string(destination),
            "keep_source": .bool(keepSources), "dedupe": .bool(skipDuplicates)])
        await reloadPlaylists()
        return result
    }

    /// Returns the preview, or an error message for an invalid pattern.
    func renamePreview(_ ids: Set<String>, find: String, replace: String, regex: Bool,
                       caseSensitive: Bool) async -> Result<[RenameChange], EngineFailure> {
        guard let engine else { return .failure(.stopped("")) }
        do {
            let p: RenamePreview = try await engine.call("playlists.rename_preview", [
                "ids": JSONValue(Array(ids)), "pattern": .string(find), "replace": .string(replace),
                "regex": .bool(regex), "case_sensitive": .bool(caseSensitive)])
            return .success(p.changes)
        } catch let f as EngineFailure {
            return .failure(f)
        } catch {
            return .failure(.badReply("\(error)"))
        }
    }

    func rename(_ changes: [RenameChange]) async -> RenameResult? {
        let result: RenameResult? = await call("playlists.rename", ["changes": (try? JSONValue.from(changes)) ?? .array([])])
        await reloadPlaylists()
        return result
    }

    func myPlaylistNames() -> [String] {
        playlists.filter(\.mine).map(\.name).sorted { $0.localizedStandardCompare($1) == .orderedAscending }
    }

    // MARK: Moods

    func loadMoods() async {
        if let list: MoodList = await call("moods.list", quiet: true) { moods = list.moods }
    }

    func mood(named name: String) -> Mood? { moods.first { $0.name == name } }

    @discardableResult
    func saveMood(_ mood: Mood, previousName: String? = nil) async -> Bool {
        var params: [String: JSONValue] = ["name": .string(mood.name)]
        params["mood"] = (try? JSONValue.from(mood)) ?? .null
        if let previousName { params["previous_name"] = .string(previousName) }
        guard let _: MoodSaved = await call("moods.save", params) else { return false }
        if let previousName, previousName != mood.name {
            // Results follow the mood to its new name.
            seedChecks[mood.name] = seedChecks.removeValue(forKey: previousName)
            trainResults[mood.name] = trainResults.removeValue(forKey: previousName)
            buildResults[mood.name] = buildResults.removeValue(forKey: previousName)
            buildOptions[mood.name] = buildOptions.removeValue(forKey: previousName)
            if selection == .mood(previousName) { selection = .mood(mood.name) }
        }
        await loadMoods()
        return true
    }

    func newMood() async {
        var name = "New Mood"
        var n = 2
        while moods.contains(where: { $0.name.lowercased() == name.lowercased() }) {
            name = "New Mood \(n)"
            n += 1
        }
        if await saveMood(Mood(name: name)) { selection = .mood(name) }
    }

    func deleteMood(_ name: String) async {
        let _: OK? = await call("moods.delete", ["name": .string(name)])
        if selection == .mood(name) { selection = .playlists }
        await loadMoods()
    }

    func importMoods(from url: URL) async {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        guard let content = try? String(contentsOf: url, encoding: .utf8) else {
            alert = AlertItem(title: "Couldn't read that file", message: url.lastPathComponent)
            return
        }
        if let result: MoodsImported = await call("moods.import", ["content": .string(content)]) {
            await loadMoods()
            if let first = result.added.first { selection = .mood(first) }
        }
    }

    func exportMoods() async -> String? {
        let result: MoodsExport? = await call("moods.export")
        return result?.content
    }

    func searchTracks(_ query: String) async -> [TrackInfo] {
        let result: SearchResult? = await call("tracks.search", ["query": .string(query)], quiet: true)
        return result?.tracks ?? []
    }

    func lookupPlaylist(_ link: String) async -> PlaylistLookup? {
        await call("playlists.lookup", ["link": .string(link)])
    }

    func checkSeeds(_ mood: String) {
        runActivity("Checking seeds for “\(mood)”") { a in
            if let r: SeedCheck = await self.call("seeds.check", ["mood": .string(mood)],
                                                  progress: { p in Task { @MainActor in a.update(p) } }) {
                self.seedChecks[mood] = r
            }
        }
    }

    func train(_ mood: String) {
        runActivity("Learning “\(mood)”") { a in
            if let r: TrainResult = await self.call("mood.train", ["mood": .string(mood)],
                                                    progress: { p in Task { @MainActor in a.update(p) } }) {
                self.trainResults[mood] = r
            }
            await self.refreshLibraryStatus()
        }
    }

    func build(_ mood: String) {
        let opts = buildOptions[mood] ?? BuildOptions()
        runActivity("Building a “\(mood)” playlist") { a in
            var params: [String: JSONValue] = ["mood": .string(mood)]
            if case .object(let o)? = try? JSONValue.from(opts) { params.merge(o) { a, _ in a } }
            if let r: BuildResult = await self.call("mood.build", params,
                                                    progress: { p in Task { @MainActor in a.update(p) } }) {
                self.buildResults[mood] = r
            }
            await self.refreshLibraryStatus()
        }
    }

    func createPlaylist(name: String, picks: [Pick], mood: String) async -> CreatedPlaylist? {
        let uris = picks.compactMap(\.uri)
        let result: CreatedPlaylist? = await call("playlist.create", [
            "name": .string(name), "uris": JSONValue(uris), "mood": .string(mood)])
        if result != nil { await reloadPlaylists() }
        return result
    }

    // MARK: Library

    func refreshLibraryStatus() async {
        libraryStatus = await call("library.status", quiet: true)
    }

    func analyzeLibrary(refresh: Bool = false) {
        runActivity("Analyzing your library") { a in
            if let r: AnalyzeResult = await self.call("library.analyze", ["refresh": .bool(refresh)],
                                                      progress: { p in Task { @MainActor in a.update(p) } }) {
                self.lastAnalysis = r
            }
            await self.refreshLibraryStatus()
        }
    }

    func downloadModels() {
        runActivity("Downloading audio models") { a in
            let _: ModelsStatus? = await self.call("models.download",
                                                   progress: { p in Task { @MainActor in a.update(p) } })
            await self.refreshLibraryStatus()
        }
    }

    func revealDataFolder() {
        NSWorkspace.shared.activateFileViewerSelecting([EngineLaunch.dataDirectory])
    }

    func open(_ urlString: String?) {
        guard let urlString, let url = URL(string: urlString) else { return }
        NSWorkspace.shared.open(url)
    }
}
