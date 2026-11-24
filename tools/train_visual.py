"""Build a portable exemplar model from checksum-bound visual annotations.

The manifest explicitly separates training and holdout images. No action labels,
user corrections, personal database or screenshots are exported into the bundle.
"""
from pathlib import Path
import argparse
from collections import Counter
import hashlib
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from observer.pretrained_visual import VisualModel, layout_features


def train(manifest, images, output):
    entries = json.loads(Path(manifest).read_text(encoding='utf-8'))['images']
    samples = {'train': [], 'holdout': []}
    seen = set()
    for entry in entries:
        path = Path(images) / entry['file']
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry['sha256'] or digest in seen:
            raise ValueError('Checksum errato o immagine duplicata: ' + entry['file'])
        seen.add(digest)
        frame = cv2.imread(str(path))
        if frame is None:
            raise ValueError('Immagine illeggibile: ' + entry['file'])
        samples[entry['split']].append((layout_features(frame), entry['label'], frame))
    if not samples['train'] or not samples['holdout']:
        raise ValueError('Servono train e holdout separati')
    model = VisualModel.fit(np.stack([x for x, _, _ in samples['train']]),
                            [y for _, y, _ in samples['train']])
    outcomes = []
    robustness = []
    for feature, label, frame in samples['holdout']:
        prediction = model.predict(feature)
        outcomes.append({'expected': label, 'predicted': prediction[0] if prediction else None})
        height, width = frame.shape[:2]
        changed = frame.copy()
        changed[round(height*.23):round(height*.66), round(width*.29):round(width*.71)] = 100
        variants = {'world_replaced': changed,
                    'brightness_65_percent': (frame*.65).astype(np.uint8),
                    'resolution_1280x720': cv2.resize(frame, (1280,720)),
                    'shift_2_pixels': cv2.warpAffine(frame, np.float32([[1,0,2],[0,1,2]]), (width,height))}
        for kind, image in variants.items():
            result = model.predict(layout_features(image))
            robustness.append({'variation': kind, 'expected': label,
                               'predicted': result[0] if result else None})
    covered = sum(row['predicted'] is not None for row in outcomes)
    correct = sum(row['predicted'] == row['expected'] for row in outcomes)
    report = {'model': 'hud-layout-exemplar-v2', 'annotation_source': 'AI visual review',
              'manifest_sha256': hashlib.sha256(Path(manifest).read_bytes()).hexdigest(),
              'training_samples': len(samples['train']), 'holdout_samples': len(outcomes),
              'training_classes': dict(Counter(y for _, y, _ in samples['train'])),
              'holdout_covered': covered, 'holdout_correct': correct,
              'action_examples': 0, 'validated_autonomous_play': False,
              'limitations': ['Holdout per immagine, non per partita: sessioni originali sconosciute.',
                              'Possibili fotogrammi simili tra train e holdout.',
                              'Etichette AI da verificare; nessun comando dedotto dalle immagini.'],
              'outcomes': outcomes, 'robustness': {
                  kind: {'checked': sum(r['variation']==kind for r in robustness),
                         'covered': sum(r['variation']==kind and r['predicted'] is not None for r in robustness),
                         'correct': sum(r['variation']==kind and r['predicted']==r['expected'] for r in robustness)}
                  for kind in variants}}
    model.save(output)
    Path(output).with_suffix('.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--images', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('assets/pretrained/visual.npz'))
    args = parser.parse_args()
    result = train(args.manifest, args.images, args.output)
    print(json.dumps({k: v for k, v in result.items() if k != 'outcomes'}, ensure_ascii=False, indent=2))
