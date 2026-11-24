"""Oye v41 local web dashboard.

Uses the existing v24 Runner without bypassing its foreground, OCR, or
calibration gates. The browser only talks to an ephemeral loopback server.
LIVE input consent is requested per run, never stored on disk.
"""
from __future__ import annotations

import base64
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import tempfile
from pathlib import Path
import queue
import secrets
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit, parse_qs
import webbrowser

from observer.config import TEMPLATE_NAMES, load, save
from observer.calibration_audit import audit_calibrations
from observer.reward_ocr import ocr_available
from observer.hud import HUD_CORE
from observer.qt_frontend_logic import mode_flags, save_calibration
from observer.runner import Runner
from observer.resume import load_checkpoint
from observer.runtime_paths import data_directory, initialize_data
from observer.upgrade import training_summary
from observer.teach_ocr import point_crop, suggest
from observer.menu_text import learn_reference, read_label, TARGETS as TEXT_TARGETS, load_references, update_label
from observer.learning import LearningStore
from observer.autopilot import LIMITS
from observer.windows import foreground_window, snapshot, enable_dpi_awareness, close_capture
from observer.screen_profiles import ScreenProfiles, BEHAVIOUR, validate_resolution, validate_region
from observer.region_ocr import read_single_region
from observer.ocr_detection_counter import OCRDetectionCounter
from observer.scanner_store import ScannerStore
from observer.visual_scanners import scan, crop_roi

RESOURCE_ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
ROOT = data_directory() if getattr(sys, 'frozen', False) else RESOURCE_ROOT
STATIC_DIR = RESOURCE_ROOT / 'web'
PORT = 0

TEACH_STEPS = {
    'gioca': ('Il pulsante Gioca', 'Apri la lobby e mostra il pulsante Gioca. Poi premi «Fammelo vedere».', 'button'),
    'reward_title': ('Il titolo Ricompensa ottenuta', 'Mostrami il testo «Ricompensa ottenuta» o «Ricompense ottenute» quando compare il pannello finale. Non è un pulsante.', 'button'),
    'riscatta': ('Il pulsante Riscatta', 'Quando vedi «Ricompense ottenute» con Equipaggia e Riscatta, mostrami Riscatta. Non selezionare Equipaggia.', 'button'),
    'continua': ('Il pulsante Continua', 'Quando compare Continua alla fine di una partita, mostralo a Oye.', 'button'),
    'torna_lobby': ('Torna alla lobby', 'Mostrami il pulsante Torna alla lobby nella schermata dei risultati.', 'button'),
    'gioca_di_nuovo': ('Gioca di nuovo', 'Mostrami il pulsante Gioca di nuovo nella schermata dei risultati, non la scheda Gioca in alto.', 'button'),
    'spettatore_prossimo': ('Spettatore: prossimo', 'Mostrami il comando per passare allo spettatore successivo. Non serve premere nulla.', 'point'),
    'spettatore_precedente': ('Spettatore: precedente', 'Mostrami il comando per passare allo spettatore precedente.', 'point'),
    'hud_salute': ('La barra della salute', 'Entra nel gioco e mostra la cornice della salute. Ti chiederò soltanto di indicarla.', 'point'),
    'hud_inventario': ('Gli slot inventario', 'Mostra gli slot inventario durante la partita. Indica il loro bordo stabile.', 'point'),
    'hud_minimappa': ('La minimappa', 'Mostra la cornice della minimappa mentre giochi. Indica il bordo, non il contenuto.', 'point'),
    'hud_in_partita': ('HUD partita', 'Mostrami un indicatore di partita riconoscibile sullo schermo.', 'point'),
}

MENU_LEARNING_ORDER = ('gioca', 'torna_lobby', 'reward_title', 'riscatta', 'continua', 'gioca_di_nuovo')
REQUIRED_BASE = ('gioca', 'spettatore_prossimo', 'spettatore_precedente')
DISPLAY_NAMES = {
    'annulla': 'Annulla', 'posizione_finale': 'Posizione finale',
    'gioca': 'Gioca', 'reward_title': 'Ricompensa ottenuta', 'riscatta': 'Riscatta', 'continua': 'Continua', 'torna_lobby': 'Torna alla lobby', 'gioca_di_nuovo': 'Gioca di nuovo',
    'spettatore_prossimo': 'Spettatore · prossimo',
    'spettatore_precedente': 'Spettatore · precedente',
    'hud_in_partita': 'Indicatore partita', 'hud_salute': 'Barra salute',
    'hud_minimappa': 'Minimappa', 'hud_inventario': 'Inventario',
    'bus_salto': 'Salto dal bus', 'caricamento': 'Caricamento',
    'eliminato': 'Eliminazione', 'lobby_indizio': 'Lobby',
}


class Dashboard:
    def __init__(self, root: Path = ROOT):
        self.root = Path(root)
        self.lock = threading.RLock()
        self.runner: Runner | None = None
        self.last_frame = b''
        self.last_frame_serial = 0
        self.last_frame_info = {}
        self.telemetry = {}
        self.events = deque(maxlen=65)
        self.error = ''
        self.runtime_issue = ''
        self.runtime_next_step = ''
        self.telemetry_at = 0.0
        self.state = 'idle'
        self.mode = 'quick_cycle'
        self.session_name = ''
        self.hotkeys = None
        self.hotkey_pressed = set()
        self.calibration_frame = None
        self.calibration_serial = 0
        self.calibration_jpeg = b''
        self.calibration_status = 'Carica uno screenshot o catturane uno dal gioco.'
        self.calibration_busy = False
        self.calibration_error = ''
        self.teach_active = False
        self.teach_menu_flow = False
        self.teach_target = ''
        self.teach_stage = 'idle'
        self.teach_message = ''
        self.teach_last_completed = ''
        self.teach_capture_target = ''
        self._training_cache_at = 0.0
        self._training_cache = {}
        self.resolution_detection_requested = False
        self.regions_status = {}
        self.created_at = time.monotonic()
        self.exiting = threading.Event()
        self._worker = threading.Thread(target=self._collect, daemon=True, name='OyeDashboardEvents')
        self._worker.start()

    def _log(self, message: str, kind='ui'):
        self.events.appendleft({'at': time.strftime('%H:%M:%S'), 'event': kind, 'message': str(message)[:260]})

    def _collect(self):
        while not self.exiting.wait(.16):
            with self.lock:
                runner = self.runner
            if runner is None:
                continue
            for _ in range(90):
                try:
                    item = runner.messages.get_nowait()
                except queue.Empty:
                    break
                event = item.get('event')
                with self.lock:
                    if event == 'preview':
                        if isinstance(item.get('jpeg'), bytes):
                            self.last_frame = item['jpeg']
                            self.last_frame_serial += 1
                        self.last_frame_info = {k:v for k,v in item.items() if k not in ('jpeg',)}
                    elif event == 'region_ocr':
                        self.regions_status = item.get('regions', {})
                    elif event == 'telemetry':
                        self.telemetry = {k:v for k,v in item.items() if k not in ('jpeg',)}
                        self.telemetry_at = time.monotonic()
                    elif event not in ('runtime_ocr', 'learning_frame', 'capture_metrics'):
                        if event == 'focus':
                            msg = str(item.get('message') or '')
                            if msg.startswith('Fortnite non verificato'):
                                self.runtime_issue = 'Fortnite non è in primo piano: Oye non può cliccare GIOCA.'
                                self.runtime_next_step = 'Clicca la finestra di Fortnite e lasciala in primo piano; Oye riprenderà automaticamente.'
                            elif (msg.startswith('Finestra verificata:') and
                                  self.runtime_issue.startswith('Fortnite non è in primo piano')):
                                self.runtime_issue = ''
                                self.runtime_next_step = ''
                        advice = {
                            'error': 'Controlla il registro eventi. Riavvia la sessione solo dopo aver risolto l’errore.',
                            'checkpoint_error': 'Verifica i permessi di scrittura della cartella dati di Oye.',
                            'phase_ocr_error': 'Verifica Tesseract e la lingua OCR configurata.',
                            'ocr_notice': 'Per riconoscere spettatore e riavvio installa Tesseract; senza OCR il timer non prova a identificare questi testi.',
                            'camera_warning': 'Verifica focus, sensibilità e controlli mouse; la ricezione non è garantita.',
                            'menu_teach_needed': 'Premi STOP, apri «Fammelo vedere» e mostrami il pulsante sullo screenshot.',
                        }
                        if event in advice:
                            self.runtime_issue = str(item.get('message') or event)[:260]
                            self.runtime_next_step = advice[event]
                        msg = item.get('message') or item.get('reason') or event
                        self._log(msg, event)
            with self.lock:
                if self.runner is runner and runner.thread and not runner.thread.is_alive():
                    self.runner = None
                    self.state = 'idle'
                    self._reset_flags()
                    self._stop_hotkeys()
                    self._log('Sessione terminata. Gli input sono disabilitati.', 'lifecycle')

    def _reset_flags(self):
        try:
            c = load(self.root / 'config.json')
            c.update(autonomous_enabled=False, guided_explore_enabled=False, demonstration_enabled=False)
            save(self.root / 'config.json', c)
        except Exception as exc:
            self._log('Ripristino flag non riuscito: ' + str(exc), 'error')

    def _start_hotkeys(self):
        if self.hotkeys is not None and self.hotkeys.is_alive():
            return
        from pynput import keyboard
        def press(k):
            if k not in (keyboard.Key.f8, keyboard.Key.f9) or k in self.hotkey_pressed:
                return
            self.hotkey_pressed.add(k)
            if k == keyboard.Key.f8:
                self.pause()
            else:
                self.stop()
        def release(k):
            self.hotkey_pressed.discard(k)
        self.hotkeys = keyboard.Listener(on_press=press, on_release=release)
        self.hotkeys.daemon = True
        self.hotkeys.start()
        if not self.hotkeys.is_alive():
            raise RuntimeError('Impossibile attivare F8/F9 globali')

    def _stop_hotkeys(self):
        listener, self.hotkeys = self.hotkeys, None
        if listener is not None:
            try: listener.stop()
            except Exception: pass
        self.hotkey_pressed.clear()

    def readiness(self):
        legacy_found, legacy_invalid, legacy_issues = audit_calibrations(self.root / 'templates')
        found, invalid, issues = set(legacy_found), dict(legacy_invalid), list(legacy_issues)
        try:
            store = ScreenProfiles(self.root)
            assets = store.assets_dir()
            if assets is not None:
                selected_found, selected_invalid, selected_issues = audit_calibrations(assets)
                # Selected profile overrides the legacy reference for matching
                # and validity. A broken legacy Gioca must not reject a good one.
                for target in selected_found:
                    found.add(target)
                    invalid.pop(target, None)
                for target, reason in selected_invalid.items():
                    if target not in found:
                        invalid[target] = reason
                # OCR-only rules are valid for localized text matching, even
                # when the user did not draw a separate image template.
                for rule in store.rules():
                    if (rule['target'] in ('gioca','torna_lobby','continua','gioca_di_nuovo','riscatta','reward_title') and
                            rule.get('expected_text') and
                            load(self.root/'config.json')['ocr_enabled'] and ocr_available()):
                        found.add(rule['target'])
                issues.extend(selected_issues)
        except Exception as exc:
            issues.append('Profili risoluzione non leggibili: ' + str(exc)[:130])
        # Modular OCR can also supply PLAY without another duplicate template.
        try:
            scan_list=ScannerStore(self.root).list()
            has_modular_play=any(x['id']=='lobby' and x['enabled'] and x['method']=='ocr' and
                                 bool(x['expected_text'].strip()) for x in scan_list)
            if has_modular_play and load(self.root/'config.json')['ocr_enabled'] and ocr_available():
                found.add('gioca')
            # Packaged screenshot-based template also counts as trained GIOCA;
            # it requires no Tesseract but must have a readable reference.
            profile_play=next((x for x in scan_list if x['id']=='lobby' and
                         x['enabled'] and x['method']=='template'),None)
            if profile_play is not None and ScannerStore(self.root).reference('lobby') is not None:
                found.add('gioca')
        except Exception as exc:
            issues.append('Scanner modulari non disponibili: '+str(exc)[:130])
        missing = [x for x in REQUIRED_BASE if x not in found]
        cfg = load(self.root / 'config.json')
        has_results_path = bool(found & {'riscatta', 'continua', 'torna_lobby', 'gioca_di_nuovo'})
        ocr_ready = bool(cfg['ocr_enabled'] and ocr_available())
        if not has_results_path and not ocr_ready:
            missing.append('riscatta')
        hud = sorted(found.intersection(HUD_CORE))
        if 'hud_in_partita' not in found and len(hud) < 2:
            # Recommend the minimum number of static HUD anchors still needed.
            missing.extend([x for x in ('hud_salute', 'hud_inventario', 'hud_minimappa')
                            if x not in found][:2-len(hud)])
        return {
            'quick_ready': ocr_ready,
            'menu_text': load_references(self.root / 'templates'),
            'ready': not missing and not issues,
            'invalid': invalid,
            'issues': issues,
            'missing': missing,
            'present': sorted(found.intersection(TEMPLATE_NAMES)),
            'hud_count': len(hud),
            'hud_legacy': 'hud_in_partita' in found,
            'labels': DISPLAY_NAMES,
            'templates': list(TEMPLATE_NAMES),
            'tesseract': bool(ocr_available()),
            'rewards_ocr': ocr_ready,
        }

    def teach_status(self):
        missing = self.readiness()['missing']
        target = self.teach_target if (self.teach_target in missing or
                (self.teach_active and self.teach_target in MENU_LEARNING_ORDER)) else ''
        title, instruction, kind = TEACH_STEPS.get(target, ('Tutto pronto',
            'Hai insegnato tutti i riferimenti richiesti. Puoi avviare una partita.', 'none'))
        if not target and self.readiness()['issues']:
            title, instruction = ('Metadati da correggere',
                'La calibrazione ha un file di metadati non valido. Consulta il messaggio nella pagina calibrazione.')
        return {
            'active': self.teach_active, 'target': target,
            'stage': self.teach_stage, 'message': self.teach_message,
            'title': title, 'instruction': instruction, 'kind': kind,
            'last_completed': self.teach_last_completed,
            'remaining': missing, 'total_remaining': len(missing),
        }

    def teach_start(self, target=None):
        with self.lock:
            if self.runner and self.runner.thread and self.runner.thread.is_alive():
                raise ValueError('Ferma la partita prima di insegnare un elemento')
            if self.calibration_busy:
                raise ValueError('La cattura precedente è ancora in corso')
            readiness = self.readiness()
            remaining = readiness['missing']
            if target is not None and (not isinstance(target, str) or
                    (target not in remaining and target not in MENU_LEARNING_ORDER)):
                raise ValueError('Questo riferimento è già pronto o non è richiesto')
            self.teach_menu_flow = target in MENU_LEARNING_ORDER
            self.teach_active = bool(remaining or target in MENU_LEARNING_ORDER)
            self.teach_target = target or (remaining[0] if remaining else '')
            self.teach_capture_target = ''
            self.teach_stage = 'needs_capture' if self.teach_active else ('metadata_error' if readiness['issues'] else 'complete')
            self.teach_last_completed = ''
            self.teach_message = ('Mostrami ' + DISPLAY_NAMES.get(self.teach_target, self.teach_target)
                                  + '. Provo a riconoscerlo e salvo il riferimento.') if self.teach_active else (
                'Correggi i file di metadati: ' + '; '.join(readiness['issues'])
                if readiness['issues'] else 'Pronto. Puoi avviare la partita.')
            return self.get_status()

    def teach_later(self):
        with self.lock:
            if self.calibration_busy:
                raise ValueError('Attendi che termini la cattura')
            self.teach_active = False
            self.teach_menu_flow = False
            self.teach_capture_target = ''
            self.teach_stage = 'idle'
            self.teach_message = ''
            return self.get_status()

    def _learn_menu(self, frame, rect, name, label=''):
        if name not in TEXT_TARGETS:
            return
        # User-supplied text takes precedence; OCR at teaching time is optional.
        if not label:
            label = read_label(frame, rect)
        entry = learn_reference(self.root / 'templates', name, frame.shape, rect, label)
        self._log('Testo menu insegnato: ' + DISPLAY_NAMES.get(name, name) +
                  (' · OCR: ' + entry['label'] if entry['label'] else ' · uso parole attese'), 'insegnamento')

    def save_menu_text(self, name, label):
        with self.lock:
            if self.runner and self.runner.thread and self.runner.thread.is_alive() and self.runner.live:
                raise ValueError('Arresta gli input LIVE prima di cambiare il testo del pulsante')
            if name not in TEXT_TARGETS or not isinstance(label, str) or not 4 <= len(label.strip()) <= 120:
                raise ValueError('Indica il testo visibile del pulsante (almeno 4 caratteri)')
            templates = self.root / 'templates'
            if not (templates / (name + '.png')).exists():
                raise ValueError('Prima mostrami il pulsante: ' + name)
            prev = load_references(templates).get(name)
            if not prev:
                raise ValueError('Prima mostrami dove si trova il pulsante')
            result = update_label(templates, name, label)
            self._log('Testo personalizzato per ' + DISPLAY_NAMES.get(name,name) + ': ' + label, 'insegnamento')
            return {'ok': True, 'label': result['label']}

    def _teach_saved(self, name):
        if not self.teach_active or name != self.teach_target:
            return
        self.teach_last_completed = name
        self.teach_capture_target = ''
        remaining = self.readiness()['missing']
        if self.teach_menu_flow:
            taught = load_references(self.root / 'templates')
            try:
                position = MENU_LEARNING_ORDER.index(name)
            except ValueError:
                position = -1
            remaining = [item for item in MENU_LEARNING_ORDER[position+1:] if item not in taught]
        if remaining:
            self.teach_target = remaining[0]
            self.teach_stage = 'needs_capture'
            self.teach_message = ('Ho imparato ' + DISPLAY_NAMES.get(name,name) + '. Ora mostrami '
                                  + DISPLAY_NAMES.get(self.teach_target,self.teach_target) + '.')
        else:
            self.teach_target = ''
            self.teach_active = False
            self.teach_menu_flow = False
            issues = self.readiness()['issues']
            self.teach_stage = 'metadata_error' if issues else 'complete'
            self.teach_message = ('Restano metadati non validi: ' + '; '.join(issues)
                if issues else 'Perfetto: ho imparato tutto il necessario. Ora puoi avviare una partita.')
        self._log('Imparato: ' + DISPLAY_NAMES.get(name,name), 'insegnamento')

    def _try_teach_frame(self, frame):
        # OCR occurs off the state lock and never sends input to the game.
        with self.lock:
            if not self.teach_active or not self.teach_target:
                return
            target = self.teach_target
            serial = self.calibration_serial
            self.teach_capture_target = target
            self.teach_stage = 'analysing'
            self.teach_message = 'Sto cercando ' + DISPLAY_NAMES.get(target,target) + ' nello screenshot…'
            cfg = load(self.root/'config.json')
        found, how = suggest(frame, target, cfg['ocr_languages']) if cfg['ocr_enabled'] else (None, 'ocr_disattivato')
        with self.lock:
            if not self.teach_active or self.teach_target != target or self.calibration_serial != serial:
                return  # Stale OCR result from an earlier screenshot or different target.
            if found is not None:
                w,h = frame.shape[1],frame.shape[0]
                x1,y1,x2,y2 = found.rect
                try:
                    rect = (round(x1*w),round(y1*h),round(x2*w),round(y2*h))
                    save_calibration(frame,rect,target,self.root/'templates')
                    self._learn_menu(frame,rect,target)
                    self._teach_saved(target)
                    return
                except (ValueError, OSError):
                    how = 'ocr_ritaglio_non_valido'
            self.teach_stage = 'needs_point'
            if how == 'ocr_ambiguo':
                self.teach_message = 'Vedo più elementi simili. Tocca quello giusto nello screenshot.'
            elif how.startswith('ocr_assente') or how.startswith('ocr_non_disponibile'):
                self.teach_message = 'OCR non disponibile: indica il centro del pulsante o del riquadro nell’immagine.'
            else:
                self.teach_message = 'Screenshot acquisito. Tocca il centro di ' + DISPLAY_NAMES.get(target,target) + ' nell’immagine.'

    def teach_pick(self, point):
        with self.lock:
            if not self.teach_active or self.teach_stage != 'needs_point' or self.teach_capture_target != self.teach_target:
                raise ValueError('Prima usa «Fammelo vedere» per acquisire questa schermata')
            if self.calibration_frame is None:
                raise ValueError('Nessuno screenshot disponibile')
            if not isinstance(point, list):
                raise ValueError('Indica il centro del riferimento')
            box = point_crop(self.calibration_frame, self.teach_target, point)
            try:
                save_calibration(self.calibration_frame,box,self.teach_target,self.root/'templates')
                self._learn_menu(self.calibration_frame,box,self.teach_target)
            except ValueError as exc:
                raise ValueError(str(exc) + '. Puoi anche usare la selezione manuale.') from exc
            self._teach_saved(self.teach_target)
            return self.get_status()

    def training_status(self):
        if time.monotonic() - self._training_cache_at > 8 or not self._training_cache:
            self._training_cache = training_summary(self.root)
            self._training_cache_at = time.monotonic()
        return dict(self._training_cache)

    # Web Studio v37. Learning is read/written through the existing SQLite
    # store; no mirrored JSON state or mutable data shipped with the UI.
    def _learning(self):
        return LearningStore(self.root / 'learning')

    def learning_archive(self, session='', limit=600):
        with self.lock:
            store = self._learning()
            sessions = store.sessions()
            if session and session not in sessions:
                raise ValueError('Sessione sconosciuta')
            session = session or (sessions[0] if sessions else '')
            frames = store.recent(limit=min(2000, max(1, int(limit))), session=session) if session else []
            for record in frames:
                record['has_image'] = bool(record.pop('image_file', None))
                record.pop('observed_controls', None)
            note = ''
            if session:
                with store._connect() as db:
                    row = db.execute('SELECT narrative FROM session_notes WHERE session=?', (session,)).fetchone()
                    note = row[0] if row else ''
            return {'sessions': sessions, 'selected': session, 'frames': frames,
                    'stats': store.stats(), 'intervals': store.range_annotations(session) if session else [],
                    'markers': store.session_markers(session) if session else [],
                    'transitions': store.session_input_transitions(session, limit=250) if session else [],
                    'decisions': store.agent_history(session, limit=60) if session else [],
                    'note': note,
                    'actions': sorted(name for name in LIMITS if name not in ('aim', 'wait'))}

    def learning_image(self, frame_id: int) -> bytes:
        with self.lock:
            store = self._learning()
            with store._connect() as db:
                row = db.execute('SELECT image_file FROM frames WHERE id=?', (int(frame_id),)).fetchone()
            if not row or not row[0] or not re.fullmatch(r'[0-9a-f]{32}\.jpg', row[0]):
                raise ValueError('Screenshot non disponibile')
            image = store.images / row[0]
            if not image.is_file() or image.stat().st_size > 10_000_000:
                raise ValueError('Screenshot non disponibile')
            return image.read_bytes()

    def learning_edit(self, data):
        with self.lock:
            store = self._learning()
            op = data.get('operation')
            if op == 'event':
                store.correct(int(data['id']), str(data['label']), str(data.get('note','')))
            elif op == 'action':
                store.teach_action(int(data['id']), str(data['action']))
            elif op == 'interval':
                session = str(data.get('session',''))
                if session not in store.sessions(): raise ValueError('Sessione sconosciuta')
                store.annotate_interval(session=session, start_at=str(data['start']),
                                        end_at=str(data['end']), event=str(data['event']),
                                        note=str(data.get('note','')))
            elif op == 'delete_interval':
                session = str(data.get('session',''))
                if session not in store.sessions(): raise ValueError('Sessione sconosciuta')
                store.delete_interval(int(data['id']), session=session, undo_labels=bool(data.get('undo',False)))
            elif op == 'confirm_demo':
                session = str(data.get('session',''))
                if session not in store.sessions(): raise ValueError('Sessione sconosciuta')
                count = store.confirm_demonstration_range(session, str(data['start']), str(data['end']))
                self._training_cache_at = 0
                self._log(f'Dimostrazioni confermate: {count}', 'learning')
                return {'ok': True, 'confirmed': count}
            elif op == 'bulk':
                session = str(data.get('session',''))
                if session not in store.sessions(): raise ValueError('Sessione sconosciuta')
                ids = data.get('ids',[])
                if not isinstance(ids,list) or len(ids)>2000: raise ValueError('Troppi fotogrammi')
                store.bulk_update(ids, session=session, event=data.get('event'), action=data.get('action'),
                                  note=data.get('note'))
            elif op == 'note':
                session = str(data.get('session',''))
                if session not in store.sessions(): raise ValueError('Sessione sconosciuta')
                store.annotate_session(session, str(data.get('note',''))[:3000])
            else:
                raise ValueError('Modifica non supportata')
            self._training_cache_at = 0
            self._log('Archivio: ' + str(op) + ' salvato', 'learning')
            return {'ok': True}

    def learning_review(self, session='', limit=30, filter='pending', offset=0):
        with self.lock:
            report = self._learning().training_review(session=session,limit=limit,filter=filter,offset=offset)
            report['available_actions'] = sorted(k for k in LIMITS if k not in ('aim','wait'))
            return report

    def learning_review_action(self, data):
        with self.lock:
            if self.runner and self.runner.thread and self.runner.thread.is_alive():
                raise ValueError('Ferma prima la registrazione per rivedere gli esempi')
            result = self._learning().review_demonstration(
                int(data.get('id',0)),session=str(data.get('session','')),
                decision=str(data.get('decision','')),
                action=data.get('action') or None)
            self._training_cache_at = 0
            self._log('Training: '+result['decision']+' #'+str(result['id']),'learning')
            return result

    def confirm_last_demo(self):
        # Legacy bulk-confirmation was dangerous: UNKNOWN frames could be
        # promoted to training without ever seeing the screenshot. Keep the
        # endpoint with an explicit migration message for old clients.
        raise ValueError('Conferma in blocco disattivata: apri Insegna a Oye e rivedi gli screenshot uno alla volta')

    def learning_export(self, session='') -> bytes:
        with self.lock:
            store = self._learning()
            if session and session not in store.sessions():
                raise ValueError('Sessione sconosciuta')
            with tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / 'oye_training.jsonl'
                store.export_jsonl(target, session=session or None)
                data = target.read_bytes()
            if len(data) > 25_000_000: raise ValueError('Archivio troppo grande per esportazione web')
            return data

    def run_input_diagnostic(self, kind):
        if kind not in ('forward','camera'): raise ValueError('Test sconosciuto')
        with self.lock:
            if self.runner and self.runner.thread and self.runner.thread.is_alive():
                raise ValueError('Arresta la sessione prima del test input')
            if getattr(self, '_diagnostic_busy', False):
                raise ValueError('Diagnostica già in corso')
            self._diagnostic_busy = True
            self._log('Test ' + kind + ': torna alla finestra di Fortnite entro 3 secondi', 'diagnostic')

        def worker():
            try:
                from observer.input_probe import probe_forward_visual, probe_camera_visual
                time.sleep(3)
                cfg = load(self.root / 'config.json')
                result = (probe_forward_visual(cfg) if kind == 'forward' else probe_camera_visual(cfg))
                with self.lock:
                    self._log(result[1], 'diagnostic')
                    self.runtime_issue = result[1] if not result[0] else ''
                    self.runtime_next_step = ('Controlla i binding e la finestra in primo piano.' if not result[0] else '')
            except Exception as exc:
                with self.lock: self._log('Test non riuscito: '+str(exc), 'error')
            finally:
                with self.lock: self._diagnostic_busy = False
        threading.Thread(target=worker, daemon=True, name='OyeWebInputDiagnostic').start()
        return {'ok': True, 'message': 'Torna a Fortnite: test fra 3 secondi.'}

    def get_status(self):
        with self.lock:
            running = bool(self.runner and self.runner.thread and self.runner.thread.is_alive())
            paused = bool(running and self.runner.pause_event.is_set())
            attention = self.runtime_issue or (self.error if not running else '')
            advice = self.runtime_next_step or ('Torna a Fammelo vedere e controlla i riferimenti.' if attention else '')
            if running and self.telemetry_at and time.monotonic() - self.telemetry_at > 9:
                attention = 'La telemetria non si aggiorna da oltre nove secondi.'
                advice = 'Controlla che Fortnite sia visibile e che Oye stia acquisendo nuovi fotogrammi.'
            checkpoint = load_checkpoint(self.root / 'state' / 'auto_cycle.json')
            cfg = load(self.root / 'config.json')
            return {
                'running': running, 'paused': paused, 'state': 'paused' if paused else 'running' if running else 'idle',
                'mode': self.mode, 'error': self.error,
                'diagnostic': {'attention': attention, 'next_step': advice,
                               'camera_status': self.telemetry.get('camera_status') or 'untested',
                               'recovery_detail': self.telemetry.get('recovery_detail') or '',
                               'route_hint': self.telemetry.get('route_hint') or ''},
                'training': self.training_status(),
                'checkpoint': {'resume_available': bool(checkpoint and checkpoint['active']),
                               'matches_completed': checkpoint['matches_completed'] if checkpoint else 0},
                'matches_completed': self.telemetry.get('matches_completed', 0),
                'session_rollovers': self.runner.session_rollovers if running else 0,
                'scene': self.telemetry.get('scene') or self.last_frame_info.get('scene') or 'unknown',
                'phase': self.telemetry.get('phase') or 'In attesa',
                'ocr': self.telemetry.get('ocr_phase') or self.last_frame_info.get('ocr_phase') or 'unknown',
                'action': self.telemetry.get('action') or 'wait',
                'reason': self.telemetry.get('reason') or 'Nessuna sessione attiva',
                'last_signal': self.telemetry.get('last_signal') or '',
                'input_status': self.telemetry.get('input_status') or 'Nessun input inviato',
                'camera': self.telemetry.get('camera_feedback') or 'Da verificare',
                'hud': self.telemetry.get('hud') or 'Da calibrare',
                'frame_serial': self.last_frame_serial,
                'frame_count': self.last_frame_info.get('frame_count', 0),
                'events': list(self.events)[:15],
                'readiness': self.readiness(),
                'teach': self.teach_status(),
                'calibration': {'busy': self.calibration_busy, 'message': self.calibration_status,
                                'error': self.calibration_error, 'image_ready': self.calibration_frame is not None,
                                'image_serial': self.calibration_serial},
                'screen_profile': self.screen_profile_status(),
                'regions_ocr': self.regions_status,
                'ocr_detections': (self.runner.region_ocr.discovery.snapshot() if self.runner and self.runner.region_ocr
                                  else OCRDetectionCounter.saved_summary(self.root)),
                'settings': {
                    'profile': cfg['guided_activity_profile'],
                    'jump_seconds': cfg['single_match_jump_seconds'],
                    'jump_interval': cfg['single_match_jump_interval_seconds'],
                    'ignorante_loading_wait_seconds': cfg['ignorante_loading_wait_seconds'],
                    'ignorante_jump_min_seconds': cfg['ignorante_jump_min_seconds'],
                    'ignorante_jump_interval_seconds': cfg['ignorante_jump_interval_seconds'],
                    'ignorante_jump_max_seconds': cfg['ignorante_jump_max_seconds'],
                    'harvest': cfg['guided_explore_harvest'],
                    'camera_pixels': cfg['guided_camera_pixels'],
                    'ocr_enabled': cfg['ocr_enabled'],
                    'keys': cfg['keys'],
                    'use_learning': cfg['guided_use_learning'],
                    'smooth': cfg['guided_smooth_enabled'],
                    'recovery_limit': cfg['guided_recovery_limit'],
                    'motion_threshold': cfg['guided_motion_threshold'],
                    'min_fall': cfg['guided_min_fall_seconds'],
                    'ollama_enabled': cfg['ollama_enabled'],
                    'ollama_model': cfg['ollama_model'],
                    'mouse': {'primary': 'left', 'secondary': 'right'},
                },
            }

    def start(self, mode='quick_cycle', confirmed=False):
        if mode not in ('quick_cycle','auto_cycle','single_match','observe','train','guided'):
            raise ValueError('Modalità non supportata dalla dashboard')
        with self.lock:
            if self.runner and self.runner.thread and self.runner.thread.is_alive():
                raise ValueError('Sessione già attiva')
            flags = mode_flags('single_match' if mode in ('quick_cycle','auto_cycle') else mode)
            if flags['live'] and confirmed is not True:
                raise ValueError('Conferma esplicitamente gli input LIVE per questa sessione')
            if mode in ('quick_cycle', 'auto_cycle', 'single_match'):
                audit = self.readiness()
                if mode == 'quick_cycle' and not audit['quick_ready']:
                    raise ValueError('Ignorante richiede OCR attivo e Tesseract per leggere i pulsanti.')
                if mode != 'quick_cycle' and audit['missing']:
                    details = '; '.join(DISPLAY_NAMES.get(x,x) + ': ' + audit['invalid'][x]
                                        for x in audit['missing'] if x in audit['invalid'])
                    raise ValueError('Mi manca ancora la calibrazione di: ' +
                        ', '.join(DISPLAY_NAMES.get(x,x) for x in audit['missing']) +
                        '. Premi «Fammelo vedere».' + (f' Riferimenti non validi: {details}' if details else ''))
                if mode != 'quick_cycle' and audit['issues']:
                    raise ValueError('Calibrazione non valida: ' + '; '.join(audit['issues']))
            cfg = load(self.root/'config.json')
            cfg.update({k:v for k,v in flags.items() if k not in ('live','exploration')})
            if mode != 'quick_cycle':
                cfg['learning_enabled'] = True
            if mode == 'train':
                cfg['demonstration_mouse_shoot'] = True  # record left mouse with W/A/S/D/Space
            save(self.root/'config.json', cfg)
            try:
                runner = Runner(root=self.root,live=flags['live'],exploration=flags['exploration'],
                                auto_requeue=mode in ('guided','single_match','auto_cycle','quick_cycle'),shoot=False,
                                single_match=mode in ('single_match','auto_cycle','quick_cycle'),
                                repeat_matches=mode in ('auto_cycle','quick_cycle'),
                                quick_start=mode=='quick_cycle',
                                quick_wait_play_exit=mode=='quick_cycle')
                if flags['live']:
                    self._start_hotkeys()
                self.last_frame = b''
                self.last_frame_serial = 0
                self.last_frame_info = {}
                self.telemetry = {}
                runner.start()
                self.runner = runner
                self.state = 'running'
                self.mode = mode
                self.error = ''
                self.runtime_issue = ''
                self.runtime_next_step = ''
                self.telemetry_at = 0.0
                self._log('Avviata ' + {'quick_cycle':'Ignorante', 'auto_cycle':'Ciclo continuo', 'single_match':'Una partita', 'observe':'Osservazione',
                                     'train':'Apprendimento', 'guided':'Esplorazione'}[mode], 'lifecycle')
            except Exception as exc:
                self.error = str(exc)
                self._stop_hotkeys()
                self._reset_flags()
                raise
        return self.get_status()

    def pause(self):
        with self.lock:
            if not self.runner or not self.runner.thread or not self.runner.thread.is_alive():
                raise ValueError('Nessuna sessione in corso')
            result = self.runner.pause_toggle()
            self._log('Sessione in pausa' if result else 'Sessione ripresa', 'lifecycle')
        return self.get_status()

    def stop(self):
        with self.lock:
            if self.runner:
                self.runner.stop()
                self._log('Arresto richiesto', 'lifecycle')
        return self.get_status()

    def update_settings(self, values):
        names = {'profile': 'guided_activity_profile', 'jump_seconds': 'single_match_jump_seconds',
                 'ignorante_loading_wait_seconds':'ignorante_loading_wait_seconds',
                 'ignorante_jump_min_seconds':'ignorante_jump_min_seconds',
                 'ignorante_jump_interval_seconds':'ignorante_jump_interval_seconds',
                 'ignorante_jump_max_seconds':'ignorante_jump_max_seconds',
                 'jump_interval': 'single_match_jump_interval_seconds', 'harvest': 'guided_explore_harvest',
                 'camera_pixels': 'guided_camera_pixels', 'ocr_enabled': 'ocr_enabled',
                 'pickaxe_key': 'keys.pickaxe',
                 'use_learning':'guided_use_learning','smooth':'guided_smooth_enabled',
                 'recovery_limit':'guided_recovery_limit','motion_threshold':'guided_motion_threshold',
                 'min_fall':'guided_min_fall_seconds','ollama_enabled':'ollama_enabled',
                 'ollama_model':'ollama_model'}
        if not isinstance(values, dict) or not values or set(values)-set(names):
            raise ValueError('Impostazioni non valide')
        with self.lock:
            if self.runner and self.runner.thread and self.runner.thread.is_alive():
                raise ValueError('Arresta la sessione prima di modificare le impostazioni')
            cfg = load(self.root/'config.json')
            for key, value in values.items():
                if key == 'pickaxe_key':
                    if not isinstance(value, str) or value not in {'f','1','q','v','z','x','c','tab'}:
                        raise ValueError('Tasto piccone non valido')
                    cfg['keys']['pickaxe'] = value
                else:
                    cfg[names[key]] = value
            cfg.update(autonomous_enabled=False, guided_explore_enabled=False, demonstration_enabled=False)
            save(self.root/'config.json', cfg)
            self._log('Impostazioni salvate', 'ui')
        return self.get_status()

    def _can_teach_now(self) -> bool:
        running = bool(self.runner and self.runner.thread and self.runner.thread.is_alive())
        return not running or self.mode in ('observe', 'train')

    def upload_calibration(self, data: bytes):
        with self.lock:
            if self.calibration_busy:
                raise ValueError('Cattura già in corso, attendi che finisca')
            if not self._can_teach_now():
                raise ValueError('Metti in pausa la partita e arresta gli input LIVE prima di insegnare un riferimento')
        import cv2
        import numpy as np
        if not 100 <= len(data) <= 7_000_000:
            raise ValueError('Carica un PNG/JPEG di dimensione inferiore a 7 MB')
        raw = np.frombuffer(data, dtype=np.uint8)
        image = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        if image is None or image.ndim != 3:
            raise ValueError('Immagine PNG/JPEG non valida')
        if image.shape[0] * image.shape[1] > 16_000_000:
            raise ValueError('Immagine troppo grande')
        self._set_calibration(image, 'Screenshot caricato. Puoi mostrare il riferimento a Oye.')
        self._try_teach_frame(image)

    def _set_calibration(self, frame, message):
        import cv2
        with self.lock:
            detected=self.resolution_detection_requested
            self.resolution_detection_requested=False
        if detected:
            ScreenProfiles(self.root).select(int(frame.shape[1]),int(frame.shape[0]),
                                             mode='borderless',source='detected')
            message='Risoluzione Fortnite rilevata: '+str(frame.shape[1])+' × '+str(frame.shape[0])
        scale = min(1., 1150/frame.shape[1], 710/frame.shape[0])
        image = cv2.resize(frame, None, fx=scale, fy=scale) if scale < 1 else frame
        ok, img = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 84])
        if not ok: raise RuntimeError('Impossibile codificare lo screenshot')
        with self.lock:
            self.calibration_frame = frame.copy()
            self.calibration_serial += 1
            self.calibration_jpeg = img.tobytes()
            self.calibration_status = message
            self.calibration_error = ''
            self.calibration_busy = False
            self._log(message, 'calibrazione')

    def capture_calibration(self):
        with self.lock:
            if self.calibration_busy:
                raise ValueError('Cattura già in corso')
            if not self._can_teach_now():
                raise ValueError('Per insegnare durante il gioco scegli Osserva: nessun input automatico')
            passive = bool(self.runner and self.runner.thread and self.runner.thread.is_alive())
            capture_armed_at = time.monotonic()
            passive_runner = self.runner if passive else None
            self.calibration_busy = True
            self.calibration_error = ''
            self.calibration_status = 'Passa a Fortnite: acquisizione tra 5 secondi.'
            if self.teach_active:
                self.teach_stage = 'capturing'
                self.teach_message = 'Hai 5 secondi per mostrare ' + DISPLAY_NAMES.get(self.teach_target,self.teach_target) + ' in Fortnite.'
        def do_capture():
            try:
                time.sleep(5)
                if passive:
                    with self.lock:
                        if (self.runner is not passive_runner or
                            not passive_runner.thread or not passive_runner.thread.is_alive() or
                            self.mode not in ('observe', 'train')):
                            raise RuntimeError('Osservazione terminata: ripeti la cattura senza input LIVE')
                    # Preserve the original resolution. The dashboard preview
                    # is only 820px wide and would generate unusable templates.
                    frame = passive_runner.calibration_snapshot(after=capture_armed_at + 3.)
                    if frame is None:
                        raise RuntimeError('Nessun nuovo fotogramma completo dalla modalità Osserva. Controlla la cattura.')
                else:
                    enable_dpi_awareness()
                    cfg = load(self.root/'config.json')
                    win = foreground_window(cfg['window_executables'])
                    if win is None:
                        raise RuntimeError('Fortnite non era in primo piano. Riprova.')
                    frame = snapshot(win)
                self._set_calibration(frame, 'Cattura riuscita: riferimento da riconoscere.')
                self._try_teach_frame(frame)
            except Exception as exc:
                with self.lock:
                    self.resolution_detection_requested = False
                    self.calibration_error = str(exc)
                    self.calibration_status = 'Cattura non riuscita'
                    self.calibration_busy = False
                    if self.teach_active:
                        self.teach_stage = 'needs_capture'
                        self.teach_message = 'Non sono riuscito a catturare Fortnite: ' + str(exc)
            finally:
                if not passive:
                    close_capture()
        threading.Thread(target=do_capture, daemon=True, name='OyeCaptureCalibration').start()

    def save_calibration(self, name, rect):
        with self.lock:
            if not self._can_teach_now():
                raise ValueError('Ferma gli input LIVE prima di modificare i riferimenti')
            if self.calibration_frame is None:
                raise ValueError('Prima carica o cattura uno screenshot')
            if self.teach_active and name == self.teach_target and self.teach_capture_target != name:
                raise ValueError('Fammi vedere prima questa schermata: non posso usare uno screenshot precedente')
            if name not in TEMPLATE_NAMES:
                raise ValueError('Riferimento sconosciuto')
            if not isinstance(rect, list) or len(rect) != 4 or not all(isinstance(x, (int,float)) for x in rect):
                raise ValueError('Selezione non valida')
            w, h = self.calibration_frame.shape[1], self.calibration_frame.shape[0]
            if not all(0 <= x <= 1 for x in rect):
                raise ValueError('Coordinate normalizzate richieste')
            coords = (round(rect[0]*w),round(rect[1]*h),round(rect[2]*w),round(rect[3]*h))
            path = save_calibration(self.calibration_frame, coords, name, self.root/'templates')
            self._learn_menu(self.calibration_frame, coords, name)
            self._log('Riferimento salvato: ' + DISPLAY_NAMES.get(name, name), 'calibrazione')
            self._teach_saved(name)
        return {'saved': path.name, 'readiness': self.readiness()}


    def _reload_live_scanners(self):
        if self.runner and self.runner.thread and self.runner.thread.is_alive():
            worker=self.runner.region_ocr
            if worker:worker.reload()
            # Recreate Vision using updated profile files only after a passive
            # teach. Replacement is atomic from the Runner thread's viewpoint.
            from observer.vision import Vision
            folder=ScreenProfiles(self.root).assets_dir()
            self.runner.vision=Vision(self.root/'templates',self.runner.cfg['template_threshold'],profile_folder=folder)

    def scanner_status(self):
        store=ScannerStore(self.root)
        specs=store.list()
        verified=store.structural_validations()
        for item in specs:
            item['calibration'] = ('required' if item['method']=='structure'
                                   and item['id'] not in verified else 'ready')
        worker=self.runner.region_ocr if self.runner else None
        debug=dict(worker.debug_snapshot) if worker is not None else {
            'status':'offline','scanners':{},'state':None,'frame_at':0,
            'detections':OCRDetectionCounter.saved_summary(self.root)}
        return {'specs':specs,'debug':debug,
                'examples':len(store.examples()),
                'note':'I punteggi sono indicativi, non probabilità calibrate. I preset devono essere verificati su screenshot reali.'}

    def scanner_save(self, data):
        with self.lock:
            if not self._can_teach_now():
                raise ValueError('Metti Oye in Osserva o Stop per modificare gli scanner')
            result=ScannerStore(self.root).save(data)
            worker=self.runner.region_ocr if self.runner else None
            if worker:worker.reload()
        return result

    def scanner_remove(self, target):
        with self.lock:
            if not self._can_teach_now():raise ValueError('Ferma gli input LIVE')
            removed=ScannerStore(self.root).delete(target)
            worker=self.runner.region_ocr if self.runner else None
            if worker:worker.reload()
        return {'removed':removed}

    def scanner_reset(self, target):
        with self.lock:
            if not self._can_teach_now():raise ValueError('Ferma gli input LIVE')
            result=ScannerStore(self.root).reset(target)
            worker=self.runner.region_ocr if self.runner else None
            if worker:worker.reload()
            return result

    def scanner_import(self, data):
        with self.lock:
            if not self._can_teach_now():raise ValueError('Ferma gli input LIVE')
            ids=ScannerStore(self.root).import_config(data)
            worker=self.runner.region_ocr if self.runner else None
            if worker:worker.reload()
        return {'imported':ids}

    def scanner_frame(self):
        with self.lock:
            if self.calibration_frame is not None:return self.calibration_frame.copy()
            jpeg=self.last_frame
        if not jpeg:raise ValueError('Carica uno screenshot o avvia Osserva')
        import numpy as np
        import cv2
        image=cv2.imdecode(np.frombuffer(jpeg,dtype=np.uint8),cv2.IMREAD_COLOR)
        if image is None:raise ValueError('Anteprima corrotta')
        return image

    def scanner_test(self, target):
        store=ScannerStore(self.root)
        # Accept either a saved scanner ID or an UNSAVED editor draft. Testing
        # must never silently use yesterday's ROI/threshold from SQLite.
        spec=store.validate(target) if isinstance(target,dict) else store.get(target)
        if not spec:raise ValueError('Scanner sconosciuto')
        frame=self.scanner_frame()
        result=scan(frame,spec,at=time.monotonic(),
                    reference=store.reference(spec['id']) if spec['method'] in ('template','features') else None)
        payload=result.json()
        import base64
        import cv2
        region=crop_roi(frame,spec['roi'])
        height,width=region.shape[:2]
        if max(height,width)>600:
            scale=600/max(height,width)
            region=cv2.resize(region,(round(width*scale),round(height*scale)),interpolation=cv2.INTER_AREA)
        ok,encoded=cv2.imencode('.jpg',region,[cv2.IMWRITE_JPEG_QUALITY,78])
        if ok:payload['preview_jpeg']=base64.b64encode(encoded.tobytes()).decode('ascii')
        payload['tested_unsaved_draft']=isinstance(target,dict)
        return payload

    def scanner_example(self, data):
        with self.lock:
            if not self._can_teach_now():raise ValueError('Metti in Stop/Osserva prima di correggere')
            result=ScannerStore(self.root).add_example(data.get('id'),self.scanner_frame(),
                  data.get('positive'),note=data.get('note',''))
            if self.runner and self.runner.region_ocr:self.runner.region_ocr.reload()
            return result

    def scanner_delete_example(self, id_):
        with self.lock:
            if not self._can_teach_now():raise ValueError('Ferma gli input LIVE')
            result=ScannerStore(self.root).delete_example(id_)
            if self.runner and self.runner.region_ocr:self.runner.region_ocr.reload()
            return {'removed':result}

    def scanner_crop_history(self,target,offset):
        worker=self.runner.region_ocr if self.runner else None
        if worker is None:raise ValueError('Avvia Osserva per confrontare i fotogrammi')
        return worker.previous_crop(target,offset)

    def scanner_crop(self, target):
        spec=ScannerStore(self.root).get(target)
        if not spec:raise ValueError('Scanner sconosciuto')
        import cv2
        image=crop_roi(self.scanner_frame(),spec['roi'])
        ok,encoded=cv2.imencode('.jpg',image,[cv2.IMWRITE_JPEG_QUALITY,90])
        if not ok:raise ValueError('ROI non disponibile')
        return encoded.tobytes()

    def screen_profile_status(self):
        try:
            store = ScreenProfiles(self.root)
        except Exception as exc:
            return {'selected':None,'profiles':[],'rules':[],
                    'error':'Archivio dei profili non disponibile: '+str(exc)[:160],
                    'screenshot':None,'resolution_busy':False}
        with self.lock:
            frame = self.calibration_frame
            shot = {'width':int(frame.shape[1]),'height':int(frame.shape[0])} if frame is not None else None
        try:
            state = store.profiles()
            rules = store.rules()
        except Exception as exc:
            return {'selected':None,'profiles':[],'rules':[],
                    'error':'Impossibile leggere i profili: '+str(exc)[:160],
                    'screenshot':shot,'resolution_busy':False}
        return {**state, 'screenshot':shot,'rules':rules,
                'resolution_busy':bool(self.calibration_busy)}

    def select_screen_profile(self, data):
        if not isinstance(data, dict):
            raise ValueError('Inserisci una risoluzione valida')
        with self.lock:
            if not self._can_teach_now():
                raise ValueError('Arresta gli input LIVE per cambiare profilo')
        return ScreenProfiles(self.root).select(data.get('width'),data.get('height'),
                        mode=data.get('mode','borderless'),source=data.get('source','manual'))

    def detect_screen_profile(self):
        with self.lock:
            self.resolution_detection_requested=True
        # Uses the existing 5-second calibrated screenshot capture: DPI-aware
        # Windows CLIENT resolution (not monitor settings or an unrelated app).
        # The user returns to Fortnite; result appears in screen_profile_status.
        try:
            self.capture_calibration()
        except Exception:
            with self.lock:
                self.resolution_detection_requested=False
            raise
        return {'started':True,'message':'Torna a Fortnite: fra 5 secondi leggerò le dimensioni della finestra del gioco.'}

    def save_screen_rule(self, data):
        if not isinstance(data, dict):
            raise ValueError('Regola non valida')
        target = data.get('target')
        rect = validate_region(data.get('rect'))
        explanation = data.get('explanation','')
        expected_text = data.get('expected_text','')
        if target not in BEHAVIOUR:
            raise ValueError('Elemento non disponibile: scegli una voce prevista dal motore')
        with self.lock:
            if not self._can_teach_now():
                raise ValueError('Ferma gli input LIVE prima di insegnare')
            if self.calibration_frame is None:
                raise ValueError('Prima acquisisci o carica uno screenshot')
            frame = self.calibration_frame.copy()
            h,w=frame.shape[:2]
            profile = ScreenProfiles(self.root).profiles()['selected']
            if profile is None:
                raise ValueError('Prima seleziona la risoluzione')
            if (profile['width'],profile['height']) != (w,h):
                raise ValueError(f'Screenshot {w}×{h} diverso dal profilo {profile["width"]}×{profile["height"]}')
            coords=(round(rect[0]*w),round(rect[1]*h),round(rect[2]*w),round(rect[3]*h))
            # Version-specific assets: never overwrite user's legacy templates.
            assets=ScreenProfiles(self.root).assets_dir()
            if assets is None:
                raise ValueError('Seleziona la risoluzione prima di salvare')
            path=save_calibration(frame,coords,target,assets)
            if target in TEXT_TARGETS:
                label=expected_text or read_label(frame,coords)
                learn_reference(assets,target,frame.shape,coords,label)
            record=ScreenProfiles(self.root).add_rule(target,rect,explanation=explanation,
                expected_text=expected_text,image_size=(w,h))
            self._reload_live_scanners()
            self._log('Insegnato '+target+' in '+str(w)+'×'+str(h),'insegnamento')
        return {'saved':path.name,'rule':record,'profile':self.screen_profile_status()}

    def test_screen_ocr(self, target):
        # Single-ROI diagnostic, never issues an input or changes a rule.
        with self.lock:
            if self.calibration_frame is None:
                raise ValueError('Prima carica o acquisisci uno screenshot')
            frame=self.calibration_frame.copy()
        rules=ScreenProfiles(self.root).rules()
        rule=next((item for item in rules if item['target']==target),None)
        if rule is None:
            raise ValueError('Prima insegna la posizione di questo elemento')
        result=read_single_region(frame,rule,at=time.monotonic())
        return {'target':result.target,'text':result.text,'score':result.score,
                'matched':result.matched,'status':result.status}

    def delete_screen_rule(self, target):
        with self.lock:
            if not self._can_teach_now():
                raise ValueError('Ferma gli input LIVE prima di modificare il profilo')
            # Deliberately preserve the legacy PNG and any old demonstrations:
            # this only removes the user's semantic note from this profile.
            deleted=ScreenProfiles(self.root).remove_rule(target)
            self._reload_live_scanners()
        return {'removed':deleted,'profile':self.screen_profile_status()}

    def open_advanced_editor(self):
        with self.lock:
            if self.runner and self.runner.thread and self.runner.thread.is_alive():
                raise ValueError('Ferma la sessione prima di aprire l’editor')
            if getattr(sys, 'frozen', False):
                raise ValueError('L’editor timeline richiede la versione sorgente con Python installato')
            path = self.root / 'studio_legacy.py'
            if not path.is_file():
                raise ValueError('Editor avanzato non trovato')
            subprocess.Popen([sys.executable, str(path)], cwd=str(self.root))
            self._log('Editor timeline avanzato avviato', 'ui')
        return {'ok': True}

    def shutdown(self):
        self.exiting.set()
        self.stop()
        with self.lock:
            runner = self.runner
        if runner:
            runner.join(timeout=4)
        with self.lock:
            self._stop_hotkeys()
            self._reset_flags()


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, dashboard: Dashboard, token: str):
        super().__init__(address, make_handler(dashboard, token))
        self.dashboard = dashboard
        self.token = token


def make_handler(dashboard: Dashboard, token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'OyeLocal/44'
        protocol_version = 'HTTP/1.1'

        def log_message(self, fmt, *args):
            # No token or paths in console log.
            if not str(self.path).startswith('/api/status'):
                pass

        def _send(self, status, payload: bytes, kind='application/json; charset=utf-8'):
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(payload)))
            if self.close_connection: self.send_header('Connection','close')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(payload)

        def _json(self, status, data):
            self._send(status, json.dumps(data, ensure_ascii=False, default=str).encode('utf-8'))

        def _check_token(self):
            return secrets.compare_digest(self.headers.get('X-Oye-Token',''), token)

        def _guard(self):
            if not self._check_token():
                self.close_connection = True
                self._json(403, {'error': 'Token locale mancante o non valido'})
                return False
            origin = self.headers.get('Origin', '')
            if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'):
                self.close_connection = True
                self._json(403, {'error': 'Origine non valida'})
                return False
            return True

        def _request_body(self, limit=256_000):
            value = self.headers.get('Content-Length','')
            if not value.isdigit() or int(value) > limit:
                raise ValueError('Dimensione richiesta non valida')
            return self.rfile.read(int(value))

        def do_GET(self):
            path = urlsplit(self.path).path
            if path in ('/', '/mini', '/mini.html'):
                return self._static('mini.html','text/html; charset=utf-8')
            if path in ('/studio', '/studio.html', '/index.html'):
                return self._static('index.html','text/html; charset=utf-8')
            if path in ('/scanners', '/scanners.html'):
                return self._static('scanners.html','text/html; charset=utf-8')
            resources = {'/app.css': ('app.css','text/css; charset=utf-8'),
                         '/app.js': ('app.js','text/javascript; charset=utf-8'),
                         '/studio37.js': ('studio37.js','text/javascript; charset=utf-8'),
                         '/training44.js': ('training44.js','text/javascript; charset=utf-8'),
                         '/training44.css': ('training44.css','text/css; charset=utf-8'),
                         '/mini.css': ('mini.css','text/css; charset=utf-8'),
                         '/mini.js': ('mini.js','text/javascript; charset=utf-8'),
                         '/teach-screen.js': ('teach-screen.js','text/javascript; charset=utf-8'),
                         '/scanners.js': ('scanners.js','text/javascript; charset=utf-8'),
                         '/scanners.css': ('scanners.css','text/css; charset=utf-8')}
            if path in resources:
                return self._static(*resources[path])
            if not self._guard(): return
            if path == '/api/status':
                return self._json(200, dashboard.get_status())
            if path == '/api/screen-profile':
                return self._json(200, dashboard.screen_profile_status())
            if path == '/api/scanners':
                return self._json(200, dashboard.scanner_status())
            if path == '/api/scanners/examples':
                query=parse_qs(urlsplit(self.path).query)
                return self._json(200, {'examples':ScannerStore(dashboard.root).examples(query.get('id',[''])[0] or None)})
            if path == '/api/scanners/export':
                return self._json(200, ScannerStore(dashboard.root).export())
            if path == '/api/scanners/example/image':
                query=parse_qs(urlsplit(self.path).query)
                try:return self._send(200,ScannerStore(dashboard.root).example_image(int(query.get('id',['0'])[0])),'image/png')
                except (ValueError,OSError):return self._json(404,{'error':'Esempio non trovato'})
            if path == '/api/scanners/evaluate':
                query=parse_qs(urlsplit(self.path).query)
                try:return self._json(200,ScannerStore(dashboard.root).evaluate(query.get('id',[''])[0]))
                except ValueError as exc:return self._json(404,{'error':str(exc)})
            if path == '/api/scanners/crop-history':
                query=parse_qs(urlsplit(self.path).query)
                try:
                    data=dashboard.scanner_crop_history(query.get('id',[''])[0],
                        int(query.get('offset',['1'])[0]))
                    return self._send(200,data,'image/jpeg')
                except (ValueError,TypeError):return self._json(404,{'error':'ROI precedente non disponibile'})
            if path == '/api/scanners/crop':
                query=parse_qs(urlsplit(self.path).query)
                try:return self._send(200,dashboard.scanner_crop(query.get('id',[''])[0]),'image/jpeg')
                except ValueError as exc:return self._json(404,{'error':str(exc)})
            if path == '/api/learning':
                query = parse_qs(urlsplit(self.path).query)
                return self._json(200, dashboard.learning_archive(query.get('session',[''])[0],
                                       int(query.get('limit',['600'])[0])))
            if path == '/api/learning/review':
                query = parse_qs(urlsplit(self.path).query)
                try: return self._json(200,dashboard.learning_review(
                    query.get('session',[''])[0],int(query.get('limit',['30'])[0]),
                    query.get('filter',['pending'])[0],int(query.get('offset',['0'])[0])))
                except (TypeError,ValueError) as exc: return self._json(400,{'error':str(exc)})
            if path == '/api/learning/image':
                query = parse_qs(urlsplit(self.path).query)
                try:
                    data = dashboard.learning_image(int(query.get('id',['0'])[0]))
                    return self._send(200, data, 'image/jpeg')
                except (ValueError, TypeError):
                    return self._json(404, {'error':'Screenshot non disponibile'})
            if path == '/api/learning/export':
                query = parse_qs(urlsplit(self.path).query)
                try: data = dashboard.learning_export(query.get('session',[''])[0])
                except ValueError as exc: return self._json(400, {'error':str(exc)})
                self.send_response(200)
                self.send_header('Content-Type','application/x-ndjson; charset=utf-8')
                self.send_header('Content-Disposition','attachment; filename="oye_training.jsonl"')
                self.send_header('Content-Length',str(len(data)))
                self.send_header('Cache-Control','no-store')
                self.send_header('X-Content-Type-Options','nosniff')
                self.end_headers(); self.wfile.write(data)
                return
            if path == '/api/frame':
                with dashboard.lock: data = dashboard.last_frame
                return self._send(200, data, 'image/jpeg') if data else self._json(404, {'error':'Frame non disponibile'})
            if path == '/api/calibration/frame':
                with dashboard.lock: data = dashboard.calibration_jpeg
                return self._send(200, data, 'image/jpeg') if data else self._json(404, {'error':'Screenshot non disponibile'})
            return self._json(404, {'error':'Risorsa non trovata'})

        def _static(self, filename, mime):
            try:
                return self._send(200, (STATIC_DIR/filename).read_bytes(), mime)
            except OSError:
                return self._json(404, {'error':'Interfaccia non disponibile'})

        def do_POST(self):
            path = urlsplit(self.path).path
            if not self._guard(): return
            try:
                if path == '/api/calibration/upload':
                    dashboard.upload_calibration(self._request_body(7_000_000))
                    return self._json(200, {'ok': True})
                data = json.loads(self._request_body().decode('utf-8'))
                if path == '/api/start':
                    result = dashboard.start(data.get('mode','quick_cycle'), data.get('confirmed') is True)
                elif path == '/api/pause':
                    result = dashboard.pause()
                elif path == '/api/stop':
                    result = dashboard.stop()
                elif path == '/api/settings':
                    result = dashboard.update_settings(data)
                elif path == '/api/learning/review':
                    result = dashboard.learning_review_action(data)
                elif path == '/api/learning/confirm-latest':
                    result = dashboard.confirm_last_demo()
                elif path == '/api/learning/edit':
                    result = dashboard.learning_edit(data)
                elif path == '/api/input-test':
                    if data.get('confirmed') is not True: raise ValueError('Conferma esplicita richiesta per test LIVE')
                    result = dashboard.run_input_diagnostic(data.get('kind'))
                elif path == '/api/editor':
                    result = dashboard.open_advanced_editor()
                elif path == '/api/scanners/save':
                    result = dashboard.scanner_save(data)
                elif path == '/api/scanners/delete':
                    result = dashboard.scanner_remove(data.get('id'))
                elif path == '/api/scanners/reset':
                    result = dashboard.scanner_reset(data.get('id'))
                elif path == '/api/scanners/test':
                    result = dashboard.scanner_test(data if 'roi' in data else data.get('id'))
                elif path == '/api/scanners/example':
                    result = dashboard.scanner_example(data)
                elif path == '/api/scanners/example/delete':
                    result = dashboard.scanner_delete_example(data.get('id'))
                elif path == '/api/scanners/import':
                    result = dashboard.scanner_import(data)
                elif path == '/api/screen-profile/select':
                    result = dashboard.select_screen_profile(data)
                elif path == '/api/screen-profile/detect':
                    result = dashboard.detect_screen_profile()
                elif path == '/api/screen-profile/teach':
                    result = dashboard.save_screen_rule(data)
                elif path == '/api/screen-profile/test':
                    result = dashboard.test_screen_ocr(data.get('target'))
                elif path == '/api/screen-profile/remove':
                    result = dashboard.delete_screen_rule(data.get('target'))
                elif path == '/api/calibration/capture':
                    dashboard.capture_calibration(); result = {'ok': True}
                elif path == '/api/teach/start':
                    result = dashboard.teach_start(data.get('target'))
                elif path == '/api/teach/text':
                    result = dashboard.save_menu_text(data.get('name'), data.get('label'))
                elif path == '/api/teach/later':
                    result = dashboard.teach_later()
                elif path == '/api/teach/capture':
                    if not dashboard.teach_active: raise ValueError('Prima avvia «Fammelo vedere»')
                    dashboard.capture_calibration(); result = dashboard.get_status()
                elif path == '/api/teach/pick':
                    result = dashboard.teach_pick(data.get('point'))
                elif path == '/api/calibration/save':
                    result = dashboard.save_calibration(data.get('name'), data.get('rect'))
                else:
                    return self._json(404, {'error':'Operazione inesistente'})
                return self._json(200, result)
            except (ValueError, KeyError, TypeError) as exc:
                return self._json(400, {'error': str(exc)})
            except Exception as exc:
                return self._json(500, {'error': f'{type(exc).__name__}: {exc}'})
    return Handler


def smoke_test() -> int:
    """Exercise installed entrypoint, static web files and authenticated local API.

    Never starts LIVE input or a browser. CI runs this through the *frozen EXE*
    after both packaging and installation, so missing DLLs/assets fail the job.
    """
    import urllib.error
    import urllib.request

    from observer.pretrained_visual import MODEL_PATH, VisualModel, VISUAL_SIZE
    model = VisualModel.load(MODEL_PATH)
    if model.features.shape[1] != VISUAL_SIZE:
        raise RuntimeError('Modello visivo incluso non compatibile')

    initialize_data(ROOT, RESOURCE_ROOT)
    dashboard = Dashboard()
    token = secrets.token_urlsafe(32)
    server = LocalServer(('127.0.0.1', 0), dashboard, token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f'http://127.0.0.1:{server.server_port}'
        for endpoint, expected in (('/', b'Oye'), ('/studio', b'Oye'),
                                   ('/mini.js', b'fetch'), ('/scanners', b'Scanner'), ('/scanners.js', b'api'), ('/app.js', b'modeDescriptions'),
                                   ('/app.css', b'body'), ('/training44.js', b'Oye 44'), ('/training44.css', b'training44')):
            with urllib.request.urlopen(origin + endpoint, timeout=6) as response:
                if response.status != 200 or expected not in response.read():
                    raise RuntimeError(f'Risorsa web non valida: {endpoint}')
        try:
            urllib.request.urlopen(origin + '/api/status', timeout=6)
        except urllib.error.HTTPError as error:
            if error.code != 403:
                raise
        else:
            raise RuntimeError('API senza token: controllo di accesso assente')
        request = urllib.request.Request(origin + '/api/status',
                                         headers={'X-Oye-Token': token})
        with urllib.request.urlopen(request, timeout=6) as response:
            data = json.load(response)
        if data.get('running') or 'readiness' not in data or 'settings' not in data:
            raise RuntimeError('API di stato non inizializzata correttamente')
        return 0
    finally:
        dashboard.shutdown()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def run_app(browser_only=False, open_browser=True, initial_tab='mini'):
    initialize_data(ROOT, RESOURCE_ROOT)
    dashboard = Dashboard()
    token = secrets.token_urlsafe(32)
    server = LocalServer(('127.0.0.1', PORT), dashboard, token)
    page = '/studio' if initial_tab in ('studio','timeline') else '/'
    address = f'http://127.0.0.1:{server.server_port}{page}?token={token}' + ('#timeline' if initial_tab == 'timeline' else '')
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.15}, daemon=True)
    thread.start()
    try:
        if not browser_only:
            try:
                import webview
                mini = initial_tab not in ('studio','timeline')
                window = webview.create_window('Oye Mini' if mini else 'Oye — Web Studio', address,
                                               width=460 if mini else 1360, height=780 if mini else 850,
                                               min_size=(390, 600) if mini else (860, 640), background_color='#080e1b')
                def on_close():
                    dashboard.stop()
                window.events.closing += on_close
                webview.start(debug=False)
                return
            except Exception as exc:
                print('Finestra web non disponibile, apro il browser locale:', exc)
        if open_browser:
            webbrowser.open(address)
        print('Oye v44.2 avviato. Lascia aperto questo terminale. Ctrl+C = chiusura.')
        print('Se il browser non si apre, riavvia tramite apri_studio.bat.')
        while not dashboard.exiting.wait(.5):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        dashboard.shutdown()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


if __name__ == '__main__':
    run_app(browser_only='--browser' in sys.argv, initial_tab='timeline' if '--timeline' in sys.argv else 'studio' if '--studio' in sys.argv else 'mini')
