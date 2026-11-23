from __future__ import annotations
import argparse
import sys
from observer.demo import run_demo
from observer.config import load
from observer.hud import HUD_ANCHORS
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description='Observer Studio: test e diagnostica locale')
    parser.add_argument('--demo', action='store_true', help='simulazione deterministica offline')
    parser.add_argument('--preflight', action='store_true', help='verifica file e dipendenze')
    parser.add_argument('--analyze-image', type=Path, help='analizza offline un PNG/JPG e memorizza la previsione')
    parser.add_argument('--label-frame', type=int, help='id del fotogramma da correggere')
    parser.add_argument('--label', type=str, help='etichetta dell’evento reale')
    parser.add_argument('--teach-action', choices=('wait', 'forward', 'forward_jump', 'reverse',
            'strafe_left', 'strafe_right', 'turn_left', 'turn_right', 'interact', 'equip',
            'harvest', 'shoot', 'jump', 'crouch', 'reload', 'slot_1', 'slot_2'),
            help='azione manuale da associare a --label-frame')
    parser.add_argument('--note', default='', type=str, help='descrizione aggiuntiva della correzione')
    parser.add_argument('--no-ocr', action='store_true', help='disattiva OCR per analisi offline')
    parser.add_argument('--learning-stats', action='store_true', help='statistiche dataset locale')
    parser.add_argument('--learning-readiness', action='store_true', help='verifica locale della generalizzazione tra partite')
    parser.add_argument('--summary', type=str, help='riepilogo temporale di una sessione')
    parser.add_argument('--export-jsonl', type=Path, help='esporta metadata e annotazioni')
    args = parser.parse_args()
    if args.demo:
        for i, (scene, action, why) in enumerate(run_demo()):
            print(f'{i:02d}  {scene:11s} {action:15s} {why}')
    elif args.preflight:
        try:
            import cv2, numpy, mss
            cfg = load(ROOT / 'config.json')
            from observer.vision import Vision
            vis = Vision(ROOT / 'templates', cfg['template_threshold'])
            print('Dipendenze OK. Template:', ', '.join(vis.templates) or '(nessuno)')
            hud_count = len(set(vis.templates).intersection(HUD_ANCHORS))
            print(f'HUD multi-indizio: {hud_count}/3 riferimenti consigliati per LIVE')
            if hud_count < 2 and 'hud_in_partita' not in vis.templates:
                print('ATTENZIONE: servono almeno due indizi HUD indipendenti oppure il template legacy')
            if hud_count == 2:
                print('ATTENZIONE: aggiungi un terzo riferimento per abilitare input LIVE')
            missing = {'spettatore_prossimo','spettatore_precedente'} - set(vis.templates)
            if missing:
                print('ATTENZIONE: per LIVE sono obbligatori i due comandi spettatore:', ', '.join(sorted(missing)))
            print('Il monitoraggio di Fortnite richiede Windows e la finestra in primo piano')
            from observer.learning import LearningStore
            store = LearningStore(ROOT / 'learning', max_saved_frames=int(cfg['max_saved_frames']))
            print('Apprendimento: screenshot ogni', cfg['capture_seconds'], 'secondi',
                  '| eventi corretti:', store.stats()['labeled'],
                  '| esempi azione:', store.stats()['actions'])
            if cfg['ocr_enabled']:
                try:
                    import pytesseract
                    print('OCR Tesseract:', pytesseract.get_tesseract_version())
                except Exception as exc:
                    print('OCR non disponibile:', type(exc).__name__, str(exc)[:150])
            print('Ollama multimodale:', 'attivato (verificare modello disponibile)' if cfg['ollama_enabled'] else 'disattivato')
            if cfg['autonomous_enabled']:
                from observer.ollama_vision import LocalVisionModel
                LocalVisionModel(cfg['ollama_model'], cfg['ollama_timeout_seconds']).check_ready()
                print('Pilota AI: Ollama pronto; avvio LIVE richiede calibrazione HUD e conferma esplicita')
            else:
                print('Pilota AI: disattivato (la sola osservazione resta disponibile)')
        except Exception as exc:
            print('ERRORE PREFLIGHT:', type(exc).__name__, str(exc))
            return 1
    elif any((args.analyze_image, args.label_frame is not None, args.learning_stats,
              args.summary, args.export_jsonl, args.teach_action, args.learning_readiness)):
        from observer.learning import LearningStore, LearningEngine
        import json
        cfg = load(ROOT / 'config.json')
        store = LearningStore(ROOT / 'learning', max_saved_frames=int(cfg['max_saved_frames']))
        if args.analyze_image:
            import cv2
            from observer.vision import Vision
            frame = cv2.imread(str(args.analyze_image))
            if frame is None:
                parser.error('File immagine inesistente o illeggibile')
            scene = Vision(ROOT / 'templates', cfg['template_threshold']).recognize(frame).scene
            engine = LearningEngine(ROOT / 'learning', ocr_enabled=cfg['ocr_enabled'] and not args.no_ocr,
                ocr_languages=cfg['ocr_languages'], max_saved_frames=int(cfg['max_saved_frames']),
                ollama_enabled=cfg['ollama_enabled'], ollama_model=cfg['ollama_model'],
                ollama_every=1, ollama_timeout=cfg['ollama_timeout_seconds'])
            result = engine.analyze(frame, scene=scene, session='cli_offline', process='screenshot importato')
            result.pop('jpeg', None)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            if args.label:
                store.correct(result['frame_id'], args.label, args.note)
                print('Correzione memorizzata sul frame', result['frame_id'])
        elif args.label_frame is not None:
            if args.label:
                store.correct(args.label_frame, args.label, args.note)
                print('Corretto il fotogramma', args.label_frame)
            if args.teach_action:
                store.teach_action(args.label_frame, args.teach_action)
                print('Azione insegnata al fotogramma', args.label_frame)
            if not args.label and not args.teach_action:
                parser.error('--label-frame richiede --label o --teach-action')
        elif args.teach_action:
            parser.error('--teach-action richiede --label-frame')
        if args.learning_stats:
            print(json.dumps(store.stats(), indent=2, ensure_ascii=False))
        if args.learning_readiness:
            from observer.readiness import inspect_training
            print(inspect_training(store).describe())
        if args.summary:
            print(store.session_summary(args.summary))
        if args.export_jsonl:
            print('Esportate righe:', store.export_jsonl(args.export_jsonl))
    else:
        parser.print_help()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
