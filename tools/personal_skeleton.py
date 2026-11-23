"""Inspect a local screenshot skeleton without inputs or copying personal images.

This is not a live-pipeline replay or a validated confusion matrix. Each image
is independent, labels are provisional and the scanner uses the packaged v42
seed rather than the user's database configuration.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
from observer.vision import Vision
from observer.v42_profile import SEEDS, packaged_reference
from observer.visual_scanners import scan


def inspect(manifest: Path, templates: Path):
    data = json.loads(manifest.read_text(encoding='utf-8'))
    images = data.get('images')
    if not isinstance(images, list):
        raise ValueError('Manifest: images deve essere un array')
    vision = Vision(templates)
    rows = []
    for index, entry in enumerate(images):
        path = manifest.parent / entry['file']
        image = cv2.imread(str(path))
        if image is None:
            rows.append({'index': index, 'error': 'Immagine non leggibile'})
            continue
        reading = vision.recognize(image)
        scanners = {}
        for name, seed in SEEDS.items():
            reference = packaged_reference(name)
            spec = {**seed, 'id': name, 'enabled': True}
            result = scan(image, spec, at=0., reference=cv2.imread(str(reference)) if reference else None)
            scanners[name] = {'status': result.status, 'score': round(result.confidence, 4)}
        rows.append({'index': index, 'phase_provisional': entry.get('phase_provisional'),
                     'resolution': [image.shape[1], image.shape[0]],
                     'vision_scene': reading.scene, 'vision_buttons': sorted(reading.best),
                     'seed_template_scanners': scanners})
    return {'warning': 'Etichette non verificate; immagini indipendenti; non è il replay live completo.',
            'vision_templates_loaded': sorted(vision.templates), 'frames': rows}


def main(argv=None):
    p = argparse.ArgumentParser(description='Analisi locale preliminare: nessun input, nessuna calibrazione')
    p.add_argument('--manifest', type=Path, default=Path('personal-dataset/manifest.json'))
    p.add_argument('--templates', type=Path, default=Path('templates'))
    p.add_argument('--output', type=Path, default=Path('personal-dataset/skeleton-report.json'))
    args = p.parse_args(argv)
    if not args.manifest.is_file():
        print('SKIP: manifest personale assente; specifica --manifest')
        return 0
    report = inspect(args.manifest, args.templates)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"Analizzati {len(report['frames'])} frame; report locale: {args.output}. Non è una calibrazione.")
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
