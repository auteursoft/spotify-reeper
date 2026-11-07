"""Learning a mood from your seed songs.

Each track becomes a row of features:
  * audio mood probabilities ("audio:aggressive", "theme:romantic", ...), from audio.py
  * its overall sound, compressed from the 1280-number audio embedding to a few
    components ("sound:1".."sound:N") so a handful of seeds can't overfit 1280 numbers
  * Last.fm tag weights ("tag:power pop", "tag:jangle pop", ...)
Seeds are the positive examples; the rest of your library is the negative examples.
A heavily regularized logistic regression then learns which features set the seeds
apart. Its coefficients are readable, so you can see what it learned.
"""

import warnings
from collections import Counter

import numpy as np

MAX_TAGS = 300
MAX_SOUND_COMPONENTS = 12
MIN_POSITIVES = 5
MIN_NEGATIVES = 20

# The audio model's 56 MTG-Jamendo "mood/theme" labels come from a production-music library,
# and some name a use rather than a feeling. They still help the model; they just don't
# describe a mood, so the summary and the per-song "why" leave them out.
USE_CASE_THEMES = {
    "advertising", "background", "children", "christmas", "commercial", "corporate",
    "documentary", "drama", "film", "game", "holiday", "movie", "nature", "soundscape",
    "space", "sport", "summer", "trailer", "travel",
}
# Labels that need rewording to follow "sounds".
SOUND_WORDING = {
    "love": "like a love song", "ballad": "like a ballad", "party": "like party music",
    "dream": "dreamy", "action": "action-packed", "adventure": "adventurous",
}
MIN_EFFECT = 0.2          # smallest difference (in standard deviations) worth describing
MIN_SEED_SHARE = 0.10     # a tag must be on at least this share of seeds (and 3) to count
MIN_LIBRARY_SHARE = 0.02  # "less of" tags must be on at least this share of the library
REDUNDANT = 0.8           # correlation above which two features of a kind say the same thing


class NotEnoughData(Exception):
    pass


def has_signal(profile):
    return bool(profile.get("audio")) or bool(profile.get("tags"))


def quiet_math():
    """Silence NumPy floating-point warnings inside matrix maths.

    On Apple Silicon, the BLAS that NumPy uses can leave stale floating-point flags behind,
    so a perfectly finite matrix product reports "divide by zero / overflow / invalid value
    encountered in matmul". The results are checked explicitly instead (see _finite)."""
    return np.errstate(divide="ignore", over="ignore", invalid="ignore")


def _usable_audio(audio, dim):
    """The audio analysis if it has the expected shape, else None. (Whether its numbers are
    finite is checked later, for all tracks at once, in MoodModel._audio_rows.)"""
    if not audio:
        return None
    emb = audio.get("embedding")
    if not isinstance(emb, list) or len(emb) != dim or not isinstance(audio.get("moods"), dict):
        return None
    return audio


class MoodModel:
    def __init__(self, C=0.1, use_tags=True, use_audio=True):
        self.C = C
        self.use_tags = use_tags
        self.use_audio = use_audio

    # ---------------- features ----------------

    def _clean(self, profiles):
        """Copies of the profiles with unusable audio (damaged or a different model's
        output) treated as missing, so one bad cache entry can't skew the model."""
        out = []
        for p in profiles:
            audio = p.get("audio") if self.use_audio else None
            good = _usable_audio(audio, self.emb_dim)
            if audio and good is None:
                self.unusable_audio += 1
            out.append(p if good is audio else {**p, "audio": good})
        return out

    def _vocab(self, profiles, positives):
        audio_names = sorted({k for p in profiles if p.get("audio")
                              for k in p["audio"]["moods"]}) if self.use_audio else []
        tag_names = []
        if self.use_tags:
            df = {}
            for p in profiles:
                for t in p.get("tags") or {}:
                    df[t] = df.get(t, 0) + 1
            seed_tags = {t for p in positives for t in p.get("tags") or {}}
            keep = [t for t, n in df.items() if n >= 2 or t in seed_tags]
            tag_names = sorted(keep, key=lambda t: (-df[t], t))[:MAX_TAGS]
        return audio_names, tag_names

    def _audio_rows(self, profiles):
        """(rows, E): which profiles have usable audio, and their embeddings as one array.
        A track whose embedding holds a non-finite number is treated as having no audio."""
        rows = [i for i, p in enumerate(profiles) if p.get("audio")]
        if not rows:
            return [], np.zeros((0, self.emb_dim))
        try:
            E = np.asarray([profiles[i]["audio"]["embedding"] for i in rows], dtype=float)
        except (TypeError, ValueError):  # a non-numeric value somewhere: check row by row
            E = np.full((len(rows), self.emb_dim), np.nan)
            for k, i in enumerate(rows):
                try:
                    E[k] = np.asarray(profiles[i]["audio"]["embedding"], dtype=float)
                except (TypeError, ValueError):
                    pass  # stays NaN, so it's rejected below
        ok = np.isfinite(E).all(axis=1)
        if not ok.all():
            self.unusable_audio += int((~ok).sum())
            rows = [r for r, good in zip(rows, ok) if good]
            E = E[ok]
        return rows, E

    def _prepare(self, profiles):
        """(profiles, audio): profiles with unusable audio set to None, so that a track whose
        only data was a damaged analysis counts as having no data at all, plus the audio
        rows and embedding array for them (see _audio_rows)."""
        profiles = self._clean(profiles)
        rows, E = self._audio_rows(profiles)
        good = set(rows)
        profiles = [p if not p.get("audio") or i in good else {**p, "audio": None}
                    for i, p in enumerate(profiles)]
        return profiles, (rows, E)

    def _fit_sound(self, E):
        self.sound_mean = self.sound_basis = None
        if not self.use_audio or len(E) < 10:
            return
        self.sound_mean = E.mean(axis=0)
        # PCA via SVD; keep few components so they describe broad differences in sound.
        with quiet_math():
            _, _, vt = np.linalg.svd(E - self.sound_mean, full_matrices=False)
        if not np.isfinite(vt).all():
            return  # can't happen with finite input; never let it poison the model
        k = min(MAX_SOUND_COMPONENTS, len(E) - 1, vt.shape[0])
        self.sound_basis = vt[:k]

    def _raw_matrix(self, profiles, audio=None):
        """Features before filling in missing audio (missing values are NaN).
        `audio` is _audio_rows(profiles), when the caller already has it."""
        n = len(profiles)
        rows, E = audio if audio is not None else self._audio_rows(profiles)
        A = np.full((n, len(self.audio_names)), np.nan)
        for i in rows:
            moods = profiles[i]["audio"]["moods"]
            A[i] = [moods.get(name, np.nan) for name in self.audio_names]
        A[~np.isfinite(A)] = np.nan  # a damaged mood value counts as missing
        parts = [A]
        if self.sound_basis is not None:
            S = np.full((n, len(self.sound_basis)), np.nan)
            if rows:
                with quiet_math():
                    S[rows] = (E - self.sound_mean) @ self.sound_basis.T
            parts.append(S)
        T = np.zeros((n, len(self.tag_names)))
        col = {t: j for j, t in enumerate(self.tag_names)}
        for i, p in enumerate(profiles):
            for t, w in (p.get("tags") or {}).items():
                j = col.get(t)
                if j is not None:
                    T[i, j] = w
        return np.hstack(parts + [T])

    def _matrix(self, profiles, raw=None):
        X = self._raw_matrix(profiles) if raw is None else raw.copy()
        if X.size:
            # Missing audio -> the library average, so a track isn't judged on audio it lacks.
            missing = np.isnan(X)
            X[missing] = np.take(self.fill, np.nonzero(missing)[1])
        return self._finite(X)

    def _finite(self, X):
        """Every value must be a real number. If the maths ever produced inf/NaN, replace it
        with the column's typical value and say so once, rather than failing or hiding it."""
        bad = ~np.isfinite(X)
        if bad.any():
            X[bad] = np.take(self.fill, np.nonzero(bad)[1])
            if not self._warned:
                warnings.warn(f"{int(bad.sum())} feature values were not finite and were replaced "
                              "with typical values.", RuntimeWarning, stacklevel=3)
                self._warned = True
        return X

    # ---------------- training ----------------

    def fit(self, positives, negatives, min_positives=MIN_POSITIVES):
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler

        self._warned = False
        self.unusable_audio = 0
        lengths = Counter(len(p["audio"].get("embedding") or []) for p in positives + negatives
                          if self.use_audio and p.get("audio"))
        self.emb_dim = lengths.most_common(1)[0][0] if lengths else 0
        n_pos = len(positives)
        everyone, (rows, E) = self._prepare(list(positives) + list(negatives))
        # Only tracks with real data are examples, decided after damaged audio is dropped.
        keep = [i for i, p in enumerate(everyone) if has_signal(p)]
        positives = [everyone[i] for i in keep if i < n_pos]
        negatives = [everyone[i] for i in keep if i >= n_pos]
        new_index = {old: k for k, old in enumerate(keep)}
        kept_rows = [j for j, r in enumerate(rows) if r in new_index]
        audio = ([new_index[rows[j]] for j in kept_rows], E[kept_rows])
        if len(positives) < min_positives:
            raise NotEnoughData(
                f"Only {len(positives)} seed songs have tags or audio; at least {min_positives} needed.")
        if len(negatives) < MIN_NEGATIVES:
            raise NotEnoughData(
                f"Only {len(negatives)} other library tracks are analyzed; at least {MIN_NEGATIVES} "
                "needed. Run: python moods.py analyze")
        everyone = positives + negatives  # same order as `keep`, so `audio` lines up
        self.audio_names, self.tag_names = self._vocab(everyone, positives)
        self._fit_sound(audio[1])
        n_sound = 0 if self.sound_basis is None else len(self.sound_basis)
        self.feature_names = (list(self.audio_names) + [f"sound:{i + 1}" for i in range(n_sound)]
                              + [f"tag:{t}" for t in self.tag_names])
        if not self.feature_names:
            raise NotEnoughData("No features: the tracks have neither tags nor audio.")

        raw = self._raw_matrix(everyone, audio)
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN columns -> filled with 0
            fill = np.nanmean(raw, axis=0)
        self.fill = np.where(np.isfinite(fill), fill, 0.0)
        X = self._matrix(everyone, raw)
        y = np.array([1] * len(positives) + [0] * len(negatives))
        with quiet_math():
            self.scaler = StandardScaler().fit(X)
            self.clf = LogisticRegression(C=self.C, class_weight="balanced", max_iter=5000)
            self.clf.fit(self.scaler.transform(X), y)
        self.n_positives, self.n_negatives = len(positives), len(negatives)
        self._describe(raw, len(positives))
        return self

    def _describe(self, raw, n_pos):
        """Work out what sets the seeds apart, for people to read.

        The model's own weights are hard to read: when features overlap (\"punk\" and
        \"punk rock\" mostly mark the same songs) it may lean on one and offset with the
        other, so they'd show up on opposite sides. Instead, each feature is compared on
        its own: how far the seeds' average is from the library's, in standard deviations.
        A feature is kept only if the model weights it the same way, enough seeds have it
        (for tags), and it isn't a near-duplicate of a stronger one already kept."""
        pos, neg = raw[:n_pos], raw[n_pos:]
        with quiet_math(), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            pooled = np.sqrt((np.nanvar(pos, axis=0) + np.nanvar(neg, axis=0)) / 2)
            diff = np.nanmean(pos, axis=0) - np.nanmean(neg, axis=0)
            effect = np.nan_to_num(np.where(pooled > 1e-9, diff / pooled, 0.0))
        seed_share = (np.nan_to_num(pos) > 0).mean(axis=0)
        library_share = (np.nan_to_num(neg) > 0).mean(axis=0)
        coef = self.clf.coef_[0]
        min_seeds = max(3 / n_pos, MIN_SEED_SHARE)

        candidates = []
        for j, name in enumerate(self.feature_names):
            if not describable(name) or abs(effect[j]) < MIN_EFFECT:
                continue
            if coef[j] == 0 or np.sign(coef[j]) != np.sign(effect[j]):
                continue  # the model doesn't use it that way; don't claim it does
            if name.startswith("tag:"):
                if effect[j] > 0 and seed_share[j] < min_seeds:
                    continue
                if effect[j] < 0 and (library_share[j] < MIN_LIBRARY_SHARE
                                      or seed_share[j] >= library_share[j]):
                    continue
            candidates.append(j)
        candidates.sort(key=lambda j: (-round(abs(effect[j]), 6), self.feature_names[j]))
        candidates = candidates[:60]

        # Drop near-synonyms: of two features of the same kind (two tags, or two sound
        # qualities) on the same side that move together, keep the stronger. Different kinds
        # are different evidence ("sounds aggressive" vs "tagged punk"), and opposite sides
        # ("more aggressive", "less romantic") both describe the mood, so those all stay.
        if candidates:
            cols = raw[:, candidates].copy()
            with quiet_math(), warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                means = np.nanmean(cols, axis=0)
                cols = np.where(np.isnan(cols), np.nan_to_num(means), cols)
                corr = np.nan_to_num(np.corrcoef(cols, rowvar=False)) if len(candidates) > 1 \
                    else np.ones((1, 1))
        kept, labels = [], set()
        for a, j in enumerate(candidates):
            text = label(self.feature_names[j])
            kind = self.feature_names[j].startswith("tag:")
            same_side = [b for b, k in kept if np.sign(effect[k]) == np.sign(effect[j])
                         and self.feature_names[k].startswith("tag:") == kind]
            if text in labels or any(abs(corr[a, b]) > REDUNDANT for b in same_side):
                continue
            kept.append((a, j))
            labels.add(text)
        self.effects = [(self.feature_names[j], float(effect[j])) for _, j in kept]

        total = float(np.abs(coef).sum())
        sound = float(np.abs([c for n, c in zip(self.feature_names, coef) if n.startswith("sound:")]).sum())
        self.sound_share = sound / total if total else 0.0

    # ---------------- using it ----------------

    def score(self, profiles):
        """Probability-like fit, 0..1, for each profile (0 for ones with no data at all)."""
        if not profiles:
            return np.array([])
        profiles, audio = self._prepare(profiles)
        X = self._matrix(profiles, self._raw_matrix(profiles, audio))
        with quiet_math():
            s = self.clf.predict_proba(self.scaler.transform(X))[:, 1]
        return np.where([has_signal(p) for p in profiles], s, 0.0)

    def decision(self, profiles):
        """Raw scores (log-odds) for ranking. Unlike score(), they don't bunch up just below 1
        when there are many seeds, so they keep songs in a clear order. -inf for profiles
        with no data at all."""
        if not profiles:
            return np.array([])
        profiles, audio = self._prepare(profiles)
        X = self._matrix(profiles, self._raw_matrix(profiles, audio))
        with quiet_math():
            d = self.clf.decision_function(self.scaler.transform(X))
        return np.where([has_signal(p) for p in profiles], d, -np.inf)

    def contributions(self, profile):
        profiles, audio = self._prepare([profile])
        X = self._matrix(profiles, self._raw_matrix(profiles, audio))
        with quiet_math():
            z = self.scaler.transform(X)[0]
        return dict(zip(self.feature_names, z * self.clf.coef_[0]))

    def explain(self, profile, k=3):
        """The k reasons this track fits, in the same words as the summary's "More of"
        (plus "overall sound" when the sound fingerprint is what carries it)."""
        contrib = self.contributions(profile)
        vocabulary = {n for n, e in self.effects if e > 0}
        items = [(n, v) for n, v in contrib.items() if n in vocabulary and v > 0]
        sound = sum(v for n, v in contrib.items() if n.startswith("sound:"))
        if sound > 0:
            items.append(("overall sound", sound))
        items.sort(key=lambda nv: -nv[1])
        return [label(n) for n, _ in items[:k]]

    def summary(self, k=8):
        """What sets the mood apart: (more_of, less_of, sound_share).

        more_of / less_of: up to k (feature, effect) pairs, strongest first, where effect is
        how many standard deviations the seeds' average sits above or below the library's.
        sound_share: the share (0..1) of the model's weight on the overall sound fingerprint,
        beyond the named qualities."""
        ups = [(n, e) for n, e in self.effects if e > 0][:k]
        downs = [(n, e) for n, e in self.effects if e < 0][:k]
        return ups, downs, self.sound_share

    def top_tags(self, k=5):
        """The Last.fm tags most over-represented in the seeds, to search for new music."""
        return [n[4:] for n, e in self.effects if e > 0 and n.startswith("tag:")][:k]


def describable(feature):
    """Whether a feature belongs in a description of a mood (see USE_CASE_THEMES)."""
    kind, _, name = feature.partition(":")
    if kind == "sound":
        return False
    return not (kind == "theme" and name in USE_CASE_THEMES)


def label(feature):
    """'theme:romantic' -> 'sounds romantic', 'theme:love' -> 'sounds like a love song',
    'tag:power pop' -> 'tagged power pop'."""
    kind, _, name = feature.partition(":")
    if kind in ("audio", "theme"):
        name = name.replace("_", " ")
        return f"sounds {SOUND_WORDING.get(name, name)}"
    if kind == "tag":
        return f"tagged {name}"
    return feature


def held_out_check(positives, negatives, folds=5, leave_one_out_max=20, **model_kwargs):
    """Hold seeds out, train on the rest, and see where each held-out seed ranks among your
    library. Returns (profile, percentile) for each seed (100 = scored above every track).
    High numbers mean the model learned something real rather than memorizing the seeds.

    With up to `leave_one_out_max` seeds each is left out on its own; with more, the seeds
    are split into `folds` groups and each group is left out in turn, so a mood with 100+
    seeds trains 5 models instead of 100+. Every seed is still held out exactly once."""
    positives = [p for p in positives if has_signal(p)]
    negatives = [p for p in negatives if has_signal(p)]
    n = len(positives)
    if n <= leave_one_out_max:
        groups = [[i] for i in range(n)]
    else:
        order = np.random.default_rng(0).permutation(n)  # fixed, so reruns agree
        groups = [sorted(int(i) for i in order[f::folds]) for f in range(folds)]
    results = [None] * n
    for group in groups:
        held = set(group)
        rest = [p for i, p in enumerate(positives) if i not in held]
        try:
            # One fewer seed than training needs, so a mood with exactly the minimum can be checked.
            m = MoodModel(**model_kwargs).fit(rest, negatives, min_positives=MIN_POSITIVES - 1)
        except NotEnoughData:
            return []
        neg_scores = m.score(negatives)
        for i, s in zip(group, m.score([positives[i] for i in group])):
            beaten = (neg_scores < s).mean() + 0.5 * (neg_scores == s).mean()  # ties count half
            results[i] = (positives[i], 100.0 * float(beaten))
    return results
