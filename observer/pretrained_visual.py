"""Numeric-only visual exemplar model; never supplies gameplay actions.

Bundled annotations are agent-reviewed hypotheses, not user confirmations.
Personal corrections and explicit OCR always take precedence.
"""
from pathlib import Path
import cv2
import numpy as np

MODEL_PATH = Path(__file__).resolve().parents[1] / 'assets' / 'pretrained' / 'visual.npz'
VISUAL_SIZE = 32 * 18


def layout_features(frame):
    """Coarse normalized positions of text-like HUD components, not RGB scenery.

    Ignore the central world/character and minimap interior. Resample once to
    normalize resolution; local contrast and component sizes suppress textures.
    A 32x18 occupancy grid retains layout while tolerating small glyph changes.
    """
    small = cv2.resize(frame[:, :, :3], (640, 360), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    # Mask before contrast extraction so the world cannot affect its boundary.
    gray[64:260, 160:480] = 0
    gray[8:82, 555:635] = 0
    gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    contrast = cv2.subtract(gray, cv2.GaussianBlur(gray, (0, 0), 3))
    mask = (contrast > 22).astype(np.uint8)
    count, components, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    accepted = np.zeros(count, dtype=np.uint8)
    for index in range(1, count):
        _, _, width, height, area = stats[index]
        if 1 <= width <= 45 and 2 <= height <= 18 and 2 <= area <= 240:
            accepted[index] = 255
    glyphs = accepted[components]
    occupancy = cv2.resize(glyphs, (32, 18), interpolation=cv2.INTER_AREA)
    return np.minimum(occupancy.astype(np.float32).ravel() / 255 * 8, 1)


class VisualModel:
    def __init__(self, features, labels):
        values = np.asarray(features, dtype=np.float32)
        names = np.asarray(labels, dtype='U100')
        if (values.ndim != 2 or not values.shape[0] or not values.shape[1]
                or names.shape != (values.shape[0],) or not np.isfinite(values).all()
                or np.any(names == '')):
            raise ValueError('Modello visivo non valido')
        self.features, self.labels = values.copy(), names.copy()

    @classmethod
    def fit(cls, features, labels):
        model = cls(features, labels)
        values, names = model.features, model.labels
        # Learn which layout cells separate classes, downweight cells varying
        # within a class (e.g. changing counters and scenery at screen edges).
        groups = [values[names == label] for label in np.unique(names)]
        centers = np.stack([group.mean(axis=0) for group in groups])
        within = np.mean([((group-group.mean(axis=0))**2).mean(axis=0) for group in groups], axis=0)
        importance = np.var(centers, axis=0) / (within + .02)
        model.weights = np.clip(importance, .05, 3).astype(np.float32)
        model.weights /= model.weights.mean()
        calibration = []
        for group in groups:
            if len(group) < 2:
                continue
            distances = np.sqrt(np.mean((group[:,None,:]-group[None,:,:])**2 * model.weights, axis=2))
            np.fill_diagonal(distances, np.inf)
            calibration.extend(distances.min(axis=1).tolist())
        model.max_distance = float(np.clip(np.quantile(calibration, .95)*1.1, .08, .35)) if calibration else .14
        return model

    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, version=np.array([2]), features=self.features, labels=self.labels,
                            weights=self.weights, max_distance=np.array([self.max_distance]))

    @classmethod
    def load(cls, path):
        with np.load(path, allow_pickle=False) as data:
            if data['version'].tolist() != [2]:
                raise ValueError('Versione modello non supportata')
            model = cls(data['features'], data['labels'])
            weights = data['weights']
            threshold = data['max_distance']
            if (weights.shape != (model.features.shape[1],) or not np.isfinite(weights).all()
                    or np.any(weights <= 0) or threshold.shape != (1,)
                    or not .08 <= float(threshold[0]) <= .35):
                raise ValueError('Parametri del layout non validi')
            model.weights = weights.copy()
            model.max_distance = float(threshold[0])
            return model

    def predict(self, feature):
        sample = np.asarray(feature, dtype=np.float32)
        if sample.shape != (self.features.shape[1],) or not np.isfinite(sample).all():
            return None
        distances = np.sqrt(np.mean((self.features-sample)**2 * self.weights, axis=1))
        nearest = int(np.argmin(distances))
        distance = float(distances[nearest])
        label = str(self.labels[nearest])
        alternatives = distances[self.labels != label]
        # Refuse unfamiliar or contradictory evidence instead of forcing a class.
        if distance > self.max_distance or (alternatives.size and float(alternatives.min()) - distance < .025):
            return None
        return label, round(min(.60, .60 * (1 - distance / (self.max_distance*2))), 3)
