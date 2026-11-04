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

import numpy as np

MAX_TAGS = 300
MAX_SOUND_COMPONENTS = 12
MIN_POSITIVES = 5
MIN_NEGATIVES = 20


class NotEnoughData(Exception):
    pass


def has_signal(profile):
    return bool(profile.get("audio")) or bool(profile.get("tags"))


class MoodModel:
    def __init__(self, C=0.1, use_tags=True, use_audio=True):
        self.C = C
        self.use_tags = use_tags
        self.use_audio = use_audio

    # ---------------- features ----------------

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

    def _fit_sound(self, profiles):
        embs = [p["audio"]["embedding"] for p in profiles if p.get("audio")]
        self.sound_mean = self.sound_basis = None
        if not self.use_audio or len(embs) < 10:
            return
        E = np.asarray(embs, dtype=float)
        self.sound_mean = E.mean(axis=0)
        # PCA via SVD; keep few components so they describe broad differences in sound.
        _, _, vt = np.linalg.svd(E - self.sound_mean, full_matrices=False)
        k = min(MAX_SOUND_COMPONENTS, len(embs) - 1, vt.shape[0])
        self.sound_basis = vt[:k]

    def _raw_row(self, p):
        row = []
        audio = p.get("audio") if self.use_audio else None
        moods = (audio or {}).get("moods") or {}
        row += [moods.get(n, np.nan) if audio else np.nan for n in self.audio_names]
        if self.sound_basis is not None:
            if audio:
                row += list((np.asarray(audio["embedding"]) - self.sound_mean) @ self.sound_basis.T)
            else:
                row += [np.nan] * len(self.sound_basis)
        tags = p.get("tags") or {}
        row += [tags.get(t, 0.0) for t in self.tag_names]
        return row

    def _matrix(self, profiles):
        X = np.asarray([self._raw_row(p) for p in profiles], dtype=float)
        if X.size:
            # Missing audio -> the library average, so a track isn't judged on audio it lacks.
            nan = np.isnan(X)
            X[nan] = np.take(self.fill, np.nonzero(nan)[1])
        return X

    # ---------------- training ----------------

    def fit(self, positives, negatives):
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler

        positives = [p for p in positives if has_signal(p)]
        negatives = [p for p in negatives if has_signal(p)]
        if len(positives) < MIN_POSITIVES:
            raise NotEnoughData(
                f"Only {len(positives)} seed songs have tags or audio; at least {MIN_POSITIVES} needed.")
        if len(negatives) < MIN_NEGATIVES:
            raise NotEnoughData(
                f"Only {len(negatives)} other library tracks are analyzed; at least {MIN_NEGATIVES} "
                "needed. Run: python moods.py analyze")
        everyone = positives + negatives
        self.audio_names, self.tag_names = self._vocab(everyone, positives)
        self._fit_sound(everyone)
        n_sound = 0 if self.sound_basis is None else len(self.sound_basis)
        self.feature_names = (list(self.audio_names) + [f"sound:{i + 1}" for i in range(n_sound)]
                              + [f"tag:{t}" for t in self.tag_names])
        if not self.feature_names:
            raise NotEnoughData("No features: the tracks have neither tags nor audio.")

        raw = np.asarray([self._raw_row(p) for p in everyone], dtype=float)
        with np.errstate(all="ignore"):
            fill = np.nanmean(raw, axis=0)
        self.fill = np.where(np.isnan(fill), 0.0, fill)
        X = self._matrix(everyone)
        y = np.array([1] * len(positives) + [0] * len(negatives))
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
        Z = self.scaler.transform(self._matrix(profiles))
        s = self.clf.predict_proba(Z)[:, 1]
        return np.where([has_signal(p) for p in profiles], s, 0.0)

    def contributions(self, profile):
        z = self.scaler.transform(self._matrix([profile]))[0]
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


def held_out_check(positives, negatives, **model_kwargs):
    """Leave each seed out, train on the rest, and see where the held-out seed ranks among
    your library. Returns the percentile for each seed (100 = scored above every track).
    High numbers mean the model learned something real rather than memorizing the seeds."""
    positives = [p for p in positives if has_signal(p)]
    negatives = [p for p in negatives if has_signal(p)]
    results = []
    for i, held in enumerate(positives):
        rest = positives[:i] + positives[i + 1:]
        try:
            m = MoodModel(**model_kwargs).fit(rest, negatives)
        except NotEnoughData:
            return []
        neg_scores = m.score(negatives)
        s = m.score([held])[0]
        beaten = (neg_scores < s).mean() + 0.5 * (neg_scores == s).mean()  # ties count half
        results.append((held, 100.0 * float(beaten)))
    return results
