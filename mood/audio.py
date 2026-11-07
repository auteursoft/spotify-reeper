"""What a track sounds like: Essentia's pretrained models, run on a 30-second preview.

One "embedding" model (Discogs-EffNet) turns audio into a 1280-number fingerprint of its
sound; small "classification heads" read that fingerprint and give probabilities for
moods (aggressive, happy, relaxed, party, sad, acoustic, electronic, danceable) and 56
mood/theme tags from the MTG-Jamendo dataset (energetic, romantic, sexy, calm, heavy, ...).

Models: https://essentia.upf.edu/models.html (CC BY-NC-SA 4.0, non-commercial use).
"""

import contextlib
import json
import os
import sys

MODELS_BASE = "https://essentia.upf.edu/models"
DEFAULT_MODEL_DIR = "models"
SAMPLE_RATE = 16000
MIN_SECONDS = 5

EMBEDDING = {"file": "discogs-effnet-bs64-1", "path": "feature-extractors/discogs-effnet"}
# (model name, feature prefix). Each lives at classification-heads/<name>/<name>-discogs-effnet-1.
HEADS = [
    ("mtg_jamendo_moodtheme", "theme"),
    ("mood_aggressive", "audio"),
    ("mood_happy", "audio"),
    ("mood_relaxed", "audio"),
    ("mood_party", "audio"),
    ("mood_sad", "audio"),
    ("mood_acoustic", "audio"),
    ("mood_electronic", "audio"),
    ("danceability", "audio"),
]


def model_files():
    """[(local file name, url)] for every model file, .pb graph and .json description."""
    out = []
    specs = [(EMBEDDING["file"], EMBEDDING["path"])]
    specs += [(f"{name}-discogs-effnet-1", f"classification-heads/{name}") for name, _ in HEADS]
    for stem, path in specs:
        for ext in (".pb", ".json"):
            out.append((stem + ext, f"{MODELS_BASE}/{path}/{stem}{ext}"))
    return out


def setup_models(model_dir, http, log=print):
    """Download any missing model files. Returns the list of (file, url, error) that failed."""
    os.makedirs(model_dir, exist_ok=True)
    failures = []
    for name, url in model_files():
        dest = os.path.join(model_dir, name)
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            log(f"  have        {name}")
            continue
        try:
            http.download(url, dest + ".part")
            os.replace(dest + ".part", dest)
            log(f"  downloaded  {name}")
        except Exception as err:
            failures.append((name, url, str(err)))
            log(f"  FAILED      {name}: {err}")
            if os.path.exists(dest + ".part"):
                os.remove(dest + ".part")
    return failures


def audio_supported():
    """Whether this build includes Essentia. The Mac App Store build leaves it out (Essentia is
    AGPL-3.0, which conflicts with App Store terms) and judges songs by Last.fm tags only."""
    import importlib.util
    try:
        return importlib.util.find_spec("essentia") is not None
    except (ImportError, ValueError):
        return False


def models_present(model_dir):
    return all(os.path.exists(os.path.join(model_dir, name)) for name, _ in model_files())


@contextlib.contextmanager
def quiet_native_stderr():
    """Silence TensorFlow's C++ log lines ("I0000 ... MLIR V1 optimization pass ...").

    They are written straight to file descriptor 2, so redirecting sys.stderr can't stop
    them. Python exceptions still propagate. Set REEPER_DEBUG=1 to see the messages.
    """
    if os.environ.get("REEPER_DEBUG"):
        yield
        return
    sys.stderr.flush()
    saved = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 2)
        yield
    finally:
        sys.stderr.flush()
        os.dup2(saved, 2)
        os.close(saved)
        os.close(devnull)


def _meta(model_dir, stem):
    with open(os.path.join(model_dir, stem + ".json")) as f:
        return json.load(f)


def _node(meta, kind, purpose, default):
    """Input/output node name from the model's description file, with a fallback."""
    nodes = (meta.get("schema") or {}).get(kind) or []
    for node in nodes:
        if purpose is None or node.get("output_purpose") == purpose:
            return node.get("name") or default
    return default


def positive_class(classes):
    """For a two-class head like ["aggressive", "non_aggressive"], the index of the first."""
    for i, c in enumerate(classes):
        if not c.lower().startswith(("non_", "not_", "non-", "not ")):
            return i
    return 0


class AudioAnalyzer:
    def __init__(self, model_dir=DEFAULT_MODEL_DIR):
        if not models_present(model_dir):
            raise FileNotFoundError(
                f"Audio models missing from {model_dir}/. Run: python moods.py setup")
        os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
        with quiet_native_stderr():
            self._load(model_dir)

    def _load(self, model_dir):
        import essentia
        import essentia.standard as es
        essentia.log.infoActive = False
        essentia.log.warningActive = False
        self.np = __import__("numpy")
        self.es = es

        emb_meta = _meta(model_dir, EMBEDDING["file"])
        self.embedder = es.TensorflowPredictEffnetDiscogs(
            graphFilename=os.path.join(model_dir, EMBEDDING["file"] + ".pb"),
            output=_node(emb_meta, "outputs", "embeddings", "PartitionedCall:1"))

        self.heads = []
        for name, prefix in HEADS:
            stem = f"{name}-discogs-effnet-1"
            meta = _meta(model_dir, stem)
            classes = meta.get("classes") or []
            multilabel = len(classes) > 2
            algo = es.TensorflowPredict2D(
                graphFilename=os.path.join(model_dir, stem + ".pb"),
                input=_node(meta, "inputs", None, "model/Placeholder"),
                output=_node(meta, "outputs", "predictions",
                             "model/Sigmoid" if multilabel else "model/Softmax"))
            self.heads.append((prefix, classes, multilabel, algo))

    def analyze(self, path):
        """Audio file -> {"moods": {feature: probability}, "embedding": [1280 floats]}."""
        with quiet_native_stderr():
            return self._analyze(path)

    def _analyze(self, path):
        audio = self.es.MonoLoader(filename=path, sampleRate=SAMPLE_RATE, resampleQuality=4)()
        if len(audio) < SAMPLE_RATE * MIN_SECONDS:
            raise ValueError(f"preview too short ({len(audio) / SAMPLE_RATE:.1f}s)")
        embeddings = self.embedder(audio)
        moods = {}
        for prefix, classes, multilabel, algo in self.heads:
            probs = self.np.asarray(algo(embeddings)).mean(axis=0)
            if multilabel:
                for label, p in zip(classes, probs):
                    moods[f"{prefix}:{label}"] = round(float(p), 4)
            else:
                i = positive_class(classes)
                moods[f"{prefix}:{classes[i]}"] = round(float(probs[i]), 4)
        emb = self.np.asarray(embeddings).mean(axis=0)
        return {"moods": moods, "embedding": [round(float(x), 4) for x in emb]}
