from pathlib import Path
import tempfile
import unittest

import numpy as np

from observer.pretrained_visual import VisualModel


class PretrainedVisualTests(unittest.TestCase):
    def test_layout_ignores_world_and_survives_scaling_and_brightness(self):
        import cv2
        from observer.pretrained_visual import layout_features
        frame = np.zeros((360, 640, 3), dtype=np.uint8)
        cv2.putText(frame, 'GIOCA', (20, 320), cv2.FONT_HERSHEY_SIMPLEX, .7, (255, 255, 255), 2)
        baseline = layout_features(frame)
        changed = frame.copy()
        changed[80:240, 180:460] = np.random.default_rng(7).integers(0, 255, (160, 280, 3), dtype=np.uint8)
        np.testing.assert_array_equal(baseline, layout_features(changed))
        dimmed = (frame * .65).astype(np.uint8)
        self.assertLess(np.linalg.norm(baseline-layout_features(dimmed)), .3)
        resized = cv2.resize(frame, (1280, 720))
        self.assertLess(np.linalg.norm(baseline-layout_features(resized)), .5)

    def test_roundtrip_and_reject_unfamiliar_or_ambiguous_screen(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'model.npz'
            model = VisualModel.fit(np.array([[0., 0.], [1., 1.]]), ['lobby', 'results'])
            model.save(path)
            restored = VisualModel.load(path)
            self.assertEqual(restored.predict(np.array([0., 0.]))[0], 'lobby')
            self.assertIsNone(restored.predict(np.array([.5, .5])))
            self.assertIsNone(restored.predict(np.array([8., 8.])))

    def test_invalid_model_rejected(self):
        with self.assertRaises(ValueError):
            VisualModel.fit(np.array([[float('nan')]]), ['lobby'])
        with self.assertRaises(ValueError):
            VisualModel.fit(np.zeros((2, 3)), ['lobby'])

    def test_missing_optional_model_does_not_break_personal_learning(self):
        from observer.learning import LearningEngine, image_features
        with tempfile.TemporaryDirectory() as temp:
            engine = LearningEngine(Path(temp), ocr_enabled=False,
                                    pretrained_path=Path(temp) / 'missing.npz')
            frame = np.zeros((90, 160, 3), dtype=np.uint8)
            self.assertEqual(engine.predict(image_features(frame), scene='unknown').event, 'incerto')

    def test_bundle_is_a_hypothesis_and_does_not_create_confirmed_training(self):
        from observer.learning import LearningEngine, image_features
        from observer.pretrained_visual import layout_features
        import cv2
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            frame = np.zeros((360, 640, 3), dtype=np.uint8)
            cv2.putText(frame, 'GIOCA PARTITA', (20, 300), cv2.FONT_HERSHEY_SIMPLEX, .5, (255,255,255), 1)
            cv2.putText(frame, 'TORNA ALLA LOBBY', (20, 330), cv2.FONT_HERSHEY_SIMPLEX, .5, (255,255,255), 1)
            feature = image_features(frame)
            path = root / 'visual.npz'
            VisualModel.fit(layout_features(frame)[None, :], ['lobby']).save(path)
            engine = LearningEngine(root / 'learning', ocr_enabled=False, pretrained_path=path)
            prediction = engine.predict(feature, scene='unknown', frame=frame)
            self.assertEqual(prediction.event, 'lobby')
            self.assertEqual(prediction.source, 'modello-visivo-incluso')
            self.assertLessEqual(prediction.score, .6)
            self.assertEqual(engine.store.labeled(), [])
            self.assertEqual(engine.store.action_training_samples(), [])
            self.assertEqual(engine.predict(feature, scene='match').source, 'hud')
            self.assertEqual(engine.predict(feature, scene='unknown', text='VICTORY ROYALE').source, 'ocr')

    def test_packaged_model_and_report_agree(self):
        import json
        from observer.pretrained_visual import MODEL_PATH, VISUAL_SIZE
        model = VisualModel.load(MODEL_PATH)
        report = json.loads(MODEL_PATH.with_suffix('.json').read_text(encoding='utf-8'))
        self.assertEqual(model.features.shape, (report['training_samples'], VISUAL_SIZE))
        self.assertEqual(report['action_examples'], 0)
        self.assertFalse(report['validated_autonomous_play'])
