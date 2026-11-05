import Foundation

// Replies from the engine (reeper_engine/handlers.py). Keys arrive in snake_case and are
// decoded with .convertFromSnakeCase, so `owner_name` becomes `ownerName`.

public struct Account: Codable, Equatable, Sendable {
    public var id: String
    public var name: String
    public var image: String?
    public var product: String?
}

public struct PlaylistInfo: Codable, Identifiable, Hashable, Sendable {
    public var id: String
    public var name: String
    public var ownerId: String?
    public var ownerName: String?
    public var mine: Bool
    public var visibility: String
    public var tracks: Int?
    public var url: String?
    public var image: String?
}

public struct PlaylistList: Codable, Sendable {
    public var playlists: [PlaylistInfo]
    public var total: Int
}

public struct DeleteOutcome: Codable, Identifiable, Sendable {
    public var id: String
    public var name: String
    public var ok: Bool
    public var error: String?
}

public struct DeleteResult: Codable, Sendable { public var outcomes: [DeleteOutcome] }

public struct MovePreview: Codable, Sendable {
    public var destination: PlaylistInfo?
    public var createsNew: Bool
}

public struct MoveOutcome: Codable, Identifiable, Sendable {
    public var id: String
    public var name: String
    /// moved, copied, kept or failed
    public var status: String
    public var added: Int
    public var duplicates: Int
    public var reason: String?
}

public struct MoveResult: Codable, Sendable {
    public var destination: PlaylistInfo?
    public var created: Bool
    public var error: String?
    public var outcomes: [MoveOutcome]
}

public struct RenameChange: Codable, Identifiable, Hashable, Sendable {
    public var id: String
    public var name: String
    public var newName: String
    public var valid: Bool
}

public struct RenamePreview: Codable, Sendable { public var changes: [RenameChange] }

public struct RenameOutcome: Codable, Identifiable, Sendable {
    public var id: String
    public var ok: Bool
    public var error: String?
    public var newName: String?
}

public struct RenameResult: Codable, Sendable { public var outcomes: [RenameOutcome] }

/// A seed song: a Spotify link, or an artist and title (with the Spotify URI when known).
public enum Seed: Codable, Hashable, Identifiable, Sendable {
    case link(String)
    case song(artist: String, title: String, uri: String?)

    public var id: String {
        switch self {
        case .link(let l): return l
        case .song(let a, let t, let u): return u ?? "\(a)|\(t)"
        }
    }

    public var displayTitle: String {
        switch self {
        case .link(let l): return l
        case .song(_, let t, _): return t
        }
    }

    public var displayArtist: String {
        switch self {
        case .link: return "Spotify link"
        case .song(let a, _, _): return a
        }
    }

    private enum Keys: String, CodingKey { case artist, title, uri }

    public init(from decoder: Decoder) throws {
        if let s = try? decoder.singleValueContainer().decode(String.self) {
            self = .link(s)
            return
        }
        let c = try decoder.container(keyedBy: Keys.self)
        self = .song(artist: try c.decodeIfPresent(String.self, forKey: .artist) ?? "",
                     title: try c.decodeIfPresent(String.self, forKey: .title) ?? "",
                     uri: try c.decodeIfPresent(String.self, forKey: .uri))
    }

    public func encode(to encoder: Encoder) throws {
        switch self {
        case .link(let l):
            var c = encoder.singleValueContainer()
            try c.encode(l)
        case .song(let a, let t, let u):
            var c = encoder.container(keyedBy: Keys.self)
            try c.encode(a, forKey: .artist)
            try c.encode(t, forKey: .title)
            try c.encodeIfPresent(u, forKey: .uri)
        }
    }
}

public struct Mood: Codable, Identifiable, Hashable, Sendable {
    public var name: String
    public var seeds: [Seed]
    public var seedPlaylists: [String]
    public var id: String { name }

    public init(name: String, seeds: [Seed] = [], seedPlaylists: [String] = []) {
        self.name = name
        self.seeds = seeds
        self.seedPlaylists = seedPlaylists
    }
}

public struct MoodList: Codable, Sendable { public var moods: [Mood] }
public struct MoodSaved: Codable, Sendable { public var name: String }
public struct MoodsImported: Codable, Sendable { public var added: [String] }
public struct MoodsExport: Codable, Sendable { public var content: String }

public struct TrackInfo: Codable, Identifiable, Hashable, Sendable {
    public var uri: String?
    public var title: String
    public var artist: String
    public var album: String?
    public var image: String?
    public var id: String { uri ?? "\(artist)|\(title)" }
}

public struct SearchResult: Codable, Sendable { public var tracks: [TrackInfo] }

public struct PlaylistLookup: Codable, Sendable {
    public var id: String
    public var name: String
    public var mine: Bool
    public var link: String
}

public struct SeedStatus: Codable, Identifiable, Sendable {
    public var title: String
    public var artist: String
    public var uri: String?
    public var onSpotify: Bool
    public var tags: [String]
    public var tagSource: String
    /// ok, no_match, no_preview, error or off
    public var audioStatus: String
    public var id: String { uri ?? "\(artist)|\(title)" }
}

public struct SeedCheck: Codable, Sendable {
    public var mood: String
    public var seeds: [SeedStatus]
    public var problems: [String]
}

public struct LibraryStatus: Codable, Sendable {
    public var audioAnalyzed: Int
    public var tagged: Int
    public var modelsPresent: Bool
    /// False in the tags-only edition (built without Essentia, e.g. for the Mac App Store).
    public var audioSupported: Bool?
}

public struct AnalyzeResult: Codable, Sendable {
    public var tracks: Int
    public var withAudio: Int
    public var withTags: Int
    public var audioStatus: [String: Int]
}

public struct MoodSummary: Codable, Sendable {
    public var moreOf: [String]
    public var lessOf: [String]
    /// Share (0...1) of the model's weight on the overall sound, beyond the named qualities.
    public var soundShare: Double
    public var seedsUsed: Int
    public var comparedWith: Int
}

public struct CheckRow: Codable, Identifiable, Sendable {
    public var title: String
    public var artist: String
    /// Share of your library this seed out-scored when left out of training (0...100).
    public var percentile: Double
    public var id: String { "\(artist)|\(title)" }
}

public struct TrainResult: Codable, Sendable {
    public var mood: String
    public var summary: MoodSummary
    public var check: [CheckRow]
    public var medianPercentile: Double?
    /// good, fair or weak
    public var verdict: String?
    public var verdictText: String?
}

public struct Pick: Codable, Identifiable, Hashable, Sendable {
    public var uri: String?
    public var title: String
    public var artist: String
    public var fit: Double
    public var new: Bool
    public var via: String?
    public var why: [String]
    public var id: String { uri ?? "\(artist)|\(title)" }
}

public struct BuildResult: Codable, Sendable {
    public var mood: String
    public var summary: MoodSummary
    public var defaultName: String
    public var picks: [Pick]
}

public struct CreatedPlaylist: Codable, Sendable {
    public var id: String
    public var url: String
}

public struct ModelsStatus: Codable, Sendable {
    public var present: Bool
    public var dir: String
    public var audioSupported: Bool?
}

public struct OK: Codable, Sendable { public var ok: Bool? }

/// Spotify tokens, shared by the app (Keychain) and the engine (which refreshes them).
public struct SpotifyTokens: Codable, Equatable, Sendable {
    public var accessToken: String
    public var refreshToken: String?
    /// Seconds since 1970.
    public var expiresAt: Double

    public init(accessToken: String, refreshToken: String?, expiresAt: Double) {
        self.accessToken = accessToken
        self.refreshToken = refreshToken
        self.expiresAt = expiresAt
    }
}

/// Filters for the playlist list, matching cleaner.filter_playlists.
public struct PlaylistFilter: Codable, Equatable, Sendable {
    public enum Mode: String, Codable, CaseIterable, Sendable { case substring, glob, regex }
    public var pattern: String = ""
    public var mode: Mode = .substring
    public var caseSensitive: Bool = false
    public var exclude: String = ""
    public var mine: Bool = false
    /// nil, "public" or "private"
    public var visibility: String? = nil

    public init() {}
}

/// Settings for building a playlist (mood.build), with the CLI's defaults.
public struct BuildOptions: Codable, Equatable, Sendable {
    public var size: Int = 40
    public var horizon: Double = 0.3
    public var maxPerArtist: Int = 3
    public var minFit: Double = 0.5
    public var depth: Int = 300

    public init() {}
}
