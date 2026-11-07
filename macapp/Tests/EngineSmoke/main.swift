// Integration test for the Swift engine client against the real Python engine protocol.
// Builds on Linux or macOS with just Foundation; see macapp/Tests/run_core_tests.sh.
//
//   EngineSmoke <python> <repo root>
//
// It launches tests/fake_engine.py (real server and handlers, fake Spotify/Last.fm/audio).

import Foundation

var failures = 0
func check(_ ok: Bool, _ what: String, file: StaticString = #file, line: UInt = #line) {
    print((ok ? "PASS " : "FAIL ") + what)
    if !ok { failures += 1 }
}

final class Counter: @unchecked Sendable {
    private let lock = NSLock()
    private var n = 0
    private var last: EngineProgress?
    func add(_ p: EngineProgress) { lock.lock(); n += 1; last = p; lock.unlock() }
    var count: Int { lock.lock(); defer { lock.unlock() }; return n }
    var lastProgress: EngineProgress? { lock.lock(); defer { lock.unlock() }; return last }
}

final class Box<T>: @unchecked Sendable {
    private let lock = NSLock()
    private var value: T
    init(_ v: T) { value = v }
    func set(_ v: T) { lock.lock(); value = v; lock.unlock() }
    func get() -> T { lock.lock(); defer { lock.unlock() }; return value }
}

func makeClient(python: String, repo: String) -> EngineClient {
    var env = ProcessInfo.processInfo.environment
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    return EngineClient(launch: .init(executable: URL(fileURLWithPath: python),
                                      arguments: ["\(repo)/tests/fake_engine.py"],
                                      environment: env,
                                      currentDirectory: URL(fileURLWithPath: repo)))
}

func run(python: String, repo: String) async {
    let engine = makeClient(python: python, repo: repo)
    let events = Box<[String: Data]>([:])
    engine.onEvent = { name, data in var e = events.get(); e[name] = data; events.set(e) }
    let exited = Box<Int32?>(nil)
    engine.onExit = { status, _ in exited.set(status) }

    do {
        try await engine.start()
        check(engine.isRunning, "engine starts and reports ready")

        let dataDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("reeper-smoke-\(UUID().uuidString)").path
        let _: OK = try await engine.call("session.configure", [
            "data_dir": .string(dataDir), "lastfm_api_key": "k", "use_audio": true,
            "include_liked": true])

        let me: Account = try await engine.call("account.me")
        check(me.name == "Sean" && me.id == "me", "account.me decodes Account")

        let all: PlaylistList = try await engine.call("playlists.list")
        check(all.total == 3 && all.playlists.first?.ownerName == "Me", "playlists decode with snake_case keys")
        check(all.playlists.first?.visibility == "public" && all.playlists.first?.tracks == 54,
              "playlist visibility and track count")

        var filter = PlaylistFilter()
        filter.pattern = "zb-"
        filter.mine = true
        filter.visibility = "private"
        let filtered: PlaylistList = try await engine.call("playlists.list",
                                                           ["filter": try JSONValue.from(filter)])
        check(filtered.playlists.map(\.id) == ["pl2"], "PlaylistFilter encodes to what the engine expects")

        do {
            filter = PlaylistFilter()
            filter.pattern = "("
            filter.mode = .regex
            let _: PlaylistList = try await engine.call("playlists.list", ["filter": try JSONValue.from(filter)])
            check(false, "invalid regex should fail")
        } catch let e as EngineFailure {
            check(e.code == "invalid_pattern" && !(e.errorDescription ?? "").isEmpty,
                  "invalid regex -> invalid_pattern with a message")
        }

        // Moods round-trip, including both kinds of seed.
        let mood = Mood(name: "Lets go",
                        seeds: [.song(artist: "R.E.M.", title: "Superman", uri: nil),
                                .song(artist: "The Knack", title: "My Sharona", uri: nil),
                                .song(artist: "R.E.M.", title: "Begin The Begin", uri: nil),
                                .song(artist: "The Replacements", title: "Alex Chilton", uri: nil),
                                .song(artist: "10,000 Maniacs", title: "These Are Days", uri: nil),
                                .song(artist: "The Lemonheads", title: "Mrs. Robinson", uri: nil)],
                        seedPlaylists: [])
        let saved: MoodSaved = try await engine.call("moods.save", ["name": .string(mood.name),
                                                                     "mood": try JSONValue.from(mood)])
        check(saved.name == "Lets go", "moods.save")
        let withLink = Mood(name: "Links", seeds: [.link("https://open.spotify.com/track/abc")],
                            seedPlaylists: ["https://open.spotify.com/playlist/xyz"])
        let _: MoodSaved = try await engine.call("moods.save", ["name": "Links", "mood": try JSONValue.from(withLink)])
        let moods: MoodList = try await engine.call("moods.list")
        check(moods.moods.map(\.name) == ["Lets go", "Links"], "moods.list order")
        check(moods.moods.first?.seeds == mood.seeds, "song seeds survive the round trip")
        check(moods.moods.last?.seeds == withLink.seeds && moods.moods.last?.seedPlaylists == withLink.seedPlaylists,
              "link seeds and seed playlists survive the round trip")

        do {
            let _: MoodSaved = try await engine.call("moods.save", ["name": "LETS GO", "mood": try JSONValue.from(Mood(name: "x"))])
            check(false, "duplicate mood name should fail")
        } catch let e as EngineFailure {
            check(e.code == "user_error", "duplicate mood name -> user_error")
        }

        let search: SearchResult = try await engine.call("tracks.search", ["query": "Sharona"])
        check(search.tracks.first?.title == "My Sharona", "tracks.search decodes")

        // A heavy job with progress.
        let counter = Counter()
        var opts = BuildOptions()
        opts.size = 8
        opts.horizon = 0.25
        var params: [String: JSONValue] = ["mood": "lets go"]
        if case .object(let o) = try JSONValue.from(opts) { params.merge(o) { a, _ in a } }
        let built: BuildResult = try await engine.call("mood.build", params, progress: { counter.add($0) })
        check(built.picks.count == 8, "mood.build returns 8 picks")
        check(built.picks.allSatisfy { (0...1).contains($0.fit) && !$0.why.isEmpty }, "picks have fit and reasons")
        check(built.picks.contains { $0.new }, "some picks are new music")
        check(counter.count > 0 && counter.lastProgress?.fraction != nil, "progress arrives with a fraction")
        check(built.defaultName.hasPrefix("Lets go"), "default playlist name")

        let trained: TrainResult = try await engine.call("mood.train", ["mood": "Lets go"])
        check(trained.summary.moreOf.contains("tagged power pop") && trained.check.count == 6,
              "mood.train decodes summary and check")

        let created: CreatedPlaylist = try await engine.call("playlist.create", [
            "name": "LFG", "uris": .array(built.picks.compactMap { $0.uri }.map { .string($0) }), "mood": "Lets go"])
        check(created.url.hasSuffix("newpl"), "playlist.create")

        // Cancelling the Swift task cancels the engine job.
        let firstProgress = Box(false)
        let slow = Task { () -> String in
            do {
                let _: [String: Bool] = try await engine.call("mood.slowtest", ["steps": 500],
                                                              progress: { _ in firstProgress.set(true) })
                return "finished"
            } catch let e as EngineFailure {
                return e.code
            } catch {
                return "other: \(error)"
            }
        }
        while !firstProgress.get() { try await Task.sleep(nanoseconds: 10_000_000) }
        // While the long job runs, a light request still answers promptly.
        let t0 = Date()
        let _: MoodList = try await engine.call("moods.list")
        check(Date().timeIntervalSince(t0) < 2, "light request answers while a long job runs")
        slow.cancel()
        let outcome = await slow.value
        check(outcome == "cancelled", "task cancellation -> engine job cancelled (got \(outcome))")

        // Unsolicited events.
        let _: OK = try await engine.call("debug.tokens_event")
        try await Task.sleep(nanoseconds: 100_000_000)
        if let data = events.get()["tokens"] {
            let d = JSONDecoder()
            d.keyDecodingStrategy = .convertFromSnakeCase
            let tokens = try? d.decode(SpotifyTokens.self, from: data)
            check(tokens?.accessToken == "fresh" && tokens?.refreshToken == "r2", "tokens event decodes")
        } else {
            check(false, "tokens event received")
        }

        // Engine dies mid-request: the call fails cleanly and onExit fires.
        let doomed = Task { () -> String in
            do {
                let _: [String: Bool] = try await engine.call("mood.slowtest", ["steps": 1000])
                return "finished"
            } catch let e as EngineFailure {
                return e.code
            } catch { return "other" }
        }
        try await Task.sleep(nanoseconds: 200_000_000)
        kill(pidOf(engine), SIGKILL)
        let doomedOutcome = await doomed.value
        check(doomedOutcome == "stopped", "engine crash -> pending call fails with stopped (got \(doomedOutcome))")
        try await Task.sleep(nanoseconds: 200_000_000)
        check(exited.get() != nil, "onExit called")
        do {
            let _: MoodList = try await engine.call("moods.list")
            check(false, "call after exit should fail")
        } catch let e as EngineFailure {
            check(e.code == "stopped", "call after exit -> stopped")
        }
    } catch {
        check(false, "unexpected error: \(error)")
    }

    // Clean shutdown.
    let second = makeClient(python: python, repo: repo)
    do {
        try await second.start()
        second.stop()
        check(!second.isRunning, "stop() shuts the engine down")
    } catch {
        check(false, "second engine: \(error)")
    }
}

func pidOf(_ engine: EngineClient) -> Int32 {
    let mirror = Mirror(reflecting: engine)
    for child in mirror.children where child.label == "process" {
        if let p = child.value as? Process { return p.processIdentifier }
    }
    return 0
}

let args = CommandLine.arguments
guard args.count == 3 else {
    print("usage: EngineSmoke <python> <repo root>")
    exit(2)
}
let done = DispatchSemaphore(value: 0)
Task {
    await run(python: args[1], repo: args[2])
    done.signal()
}
done.wait()
print(failures == 0 ? "\nALL PASSED" : "\n\(failures) FAILED")
exit(failures == 0 ? 0 : 1)
