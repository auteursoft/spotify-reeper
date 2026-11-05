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
        E = np.asarray([profiles[i]["audio"]["embedding"] for i in rows], dtype=float)
        ok = np.isfinite(E).all(axis=1)
        if not ok.all():
            self.unusable_audio += int((~ok).sum())
            rows = [r for r, good in zip(rows, ok) if good]
            E = E[ok]
        return rows, E

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

    def fit(self, positives, negatives):
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler

        self._warned = False
        self.unusable_audio = 0
        lengths = Counter(len(p["audio"].get("embedding") or []) for p in positives + negatives
                          if self.use_audio and p.get("audio"))
        self.emb_dim = lengths.most_common(1)[0][0] if lengths else 0
        positives = [p for p in self._clean(positives) if has_signal(p)]
        negatives = [p for p in self._clean(negatives) if has_signal(p)]
        if len(positives) < MIN_POSITIVES:
            raise NotEnoughData(
                f"Only {len(positives)} seed songs have tags or audio; at least {MIN_POSITIVES} needed.")
        if len(negatives) < MIN_NEGATIVES:
            raise NotEnoughData(
                f"Only {len(negatives)} other library tracks are analyzed; at least {MIN_NEGATIVES} "
                "needed. Run: python moods.py analyze")
        everyone = positives + negatives
        self.audio_names, self.tag_names = self._vocab(everyone, positives)
        audio = self._audio_rows(everyone)  # the big embedding array, built once per fit
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
        return self

    # ---------------- using it ----------------

    def score(self, profiles):
        """Probability-like fit, 0..1, for each profile (0 for ones with no data at all)."""
        if not profiles:
            return np.array([])
        profiles = self._clean(profiles)
        with quiet_math():
            s = self.clf.predict_proba(self.scaler.transform(self._matrix(profiles)))[:, 1]
        return np.where([has_signal(p) for p in profiles], s, 0.0)

    def contributions(self, profile):
        with quiet_math():
            z = self.scaler.transform(self._matrix(self._clean([profile])))[0]
        return dict(zip(self.feature_names, z * self.clf.coef_[0]))

    def explain(self, profile, k=3):
        """The k features that pushed this track's score up the most, in plain words."""
        contrib = self.contributions(profile)
        sound = sum(v for n, v in contrib.items() if n.startswith("sound:"))
        items = [(n, v) for n, v in contrib.items() if not n.startswith("sound:")]
        if sound:
            items.append(("overall sound", sound))
        items = [(n, v) for n, v in items if v > 0]
        items.sort(key=lambda nv: -nv[1])
        return [label(n) for n, _ in items[:k]]

    def summary(self, k=10):
        """What the model learned: the features that most raise and lower the score."""
        coefs = [(n, c) for n, c in zip(self.feature_names, self.clf.coef_[0])
                 if not n.startswith("sound:")]
        ups = sorted([nc for nc in coefs if nc[1] > 0], key=lambda nc: -nc[1])[:k]
        downs = sorted([nc for nc in coefs if nc[1] < 0], key=lambda nc: nc[1])[:k]
        sound = float(np.abs([c for n, c in zip(self.feature_names, self.clf.coef_[0])
                              if n.startswith("sound:")]).sum())
        return ups, downs, sound

    def top_tags(self, k=5):
        """The Last.fm tags most associated with the mood, to search for new music."""
        ups, _, _ = self.summary(k=100)
        return [n[4:] for n, _ in ups if n.startswith("tag:")][:k]


def label(feature):
    """'theme:romantic' -> 'sounds romantic', 'tag:power pop' -> 'tagged power pop'."""
    kind, _, name = feature.partition(":")
    if kind in ("audio", "theme"):
        return f"sounds {name.replace('_', ' ')}"
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
            m = MoodModel(**model_kwargs).fit(rest, negatives)
        except NotEnoughData:
            return []
        neg_scores = m.score(negatives)
        for i, s in zip(group, m.score([positives[i] for i in group])):
            beaten = (neg_scores < s).mean() + 0.5 * (neg_scores == s).mean()  # ties count half
            results[i] = (positives[i], 100.0 * float(beaten))
    return results
