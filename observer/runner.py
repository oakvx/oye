"""Worker thread orchestration, bounded resource usage and event logging."""
from __future__ import annotations
import json
from collections import deque
from dataclasses import replace
import logging
from pathlib import Path
import queue
import threading
import time
from datetime import datetime

import cv2
from .brain import Brain
from .config import load
from .driver import Driver
from .gamepad import GamepadDriver
from .models import Observation
from .vision import Vision, motion_between, camera_turn_between, recognize_aerial_text
from .menu_supervisor import MenuSupervisor
from .hud import HUD_ANCHORS, HUD_CORE
from .learning import LearningEngine, LearningWorker, image_features
from .fast_policy import FastActionPolicy
from .frame_stream import LatestFrameCapture
from .exploration import ExplorationWorker
from .demonstration import DemonstrationWorker, WinGameInputReader
from .autopilot import AutonomousPlanner
from .guided import SimpleMatchRoutine
from .timed_mode import TimedMatchRoutine
from .reward_ocr import RewardOCRWorker
from .phase_ocr import PhaseOCRWorker
from .region_ocr import ScreenRegionOCRWorker
from .screen_profiles import ScreenProfiles
from .phase_gate import PhaseGate
from .single_match import SingleMatchController
from .cycle_db import CycleStore
from .session_budget import SessionBudget
from .p1_navigation import route_hint, CameraFeedback
from .resume import load_checkpoint, save_checkpoint, ResumeGate
from .unknown_watchdog import UnknownWatchdog
from .feedback import evaluate
from .state_estimator import PhaseEstimator
from .ollama_vision import LocalVisionModel
from .models import Action, Match
from .windows import enable_dpi_awareness, foreground_window, snapshot, close_capture

ROOT = Path(__file__).resolve().parents[1]

def _cached_play_match(now, scene, region_recent, scanner_play_recent, left, top):
    """Return the latest positive PLAY match and its *original* OCR timestamp."""
    if scene not in ('lobby', 'unknown'):
        return None
    cached = region_recent.get('gioca')
    if cached is not None and 0 <= now - cached[0] < 3.4:
        at, result = cached
        return at, Match('gioca', result['score'],
                         (result['center'][0] + left, result['center'][1] + top))
    if scanner_play_recent is not None and 0 <= now - scanner_play_recent[0] < 3.4:
        return scanner_play_recent
    return None

class Runner:
    def __init__(self, *, live: bool = False, auto_requeue: bool = False,
                 shoot: bool = False, seed: int | None = None,
                 config_path: Path | None = None, root: Path = ROOT,
                 exploration: bool = False, single_match: bool = False,
                 repeat_matches: bool = False, quick_start: bool = False,
                 quick_wait_play_exit: bool = True):
        self.single_match = bool(single_match)
        self.repeat_matches = bool(repeat_matches)
        self.quick_start = bool(quick_start and single_match)
        self.quick_wait_play_exit = bool(self.quick_start and quick_wait_play_exit)
        if self.repeat_matches and not self.single_match:
            raise ValueError("Il ciclo richiede la modalità partita")
        if self.single_match and not live:
            raise ValueError("Una partita richiede input LIVE autorizzati")
        self.live, self.requeue, self.shoot = live, auto_requeue, shoot
        if exploration and live:
            raise ValueError('Esplorazione: gli input LIVE devono essere disabilitati')
        self.exploration = exploration
        self.seed, self.root = seed, root
        self.cfg = load(config_path or root / 'config.json')
        # A single-match controller is an explicit LIVE mode: it must not
        # silently become an inert observer when an older config.json still
        # contains autonomous_enabled=false / guided_explore_enabled=false.
        self.autonomous = bool(live and (self.single_match or self.cfg['autonomous_enabled']))
        self.guided = bool(self.autonomous and (self.single_match or self.cfg['guided_explore_enabled']))
        self.timed = bool(self.guided and self.cfg['guided_timed_enabled'] and not self.single_match)
        if self.cfg['autonomous_enabled'] and not live:
            raise ValueError('Guida autonoma: abilita esplicitamente gli input LIVE')
        # A manually armed timer does not require Tesseract to detect the HUD.
        # Without OCR only previously calibrated visual menu cues can veto it.
        # Never claim spectator/reboot recognition is available in that case.
        self._startup_warnings: list[str] = []
        try:
            try:
                profile_assets=ScreenProfiles(root).assets_dir()
            except Exception as exc:
                if not self.quick_start:
                    raise ValueError('Database dei profili non accessibile: '+str(exc)[:150]) from exc
                profile_assets=None
                self._startup_warnings.append('Profili risoluzione non leggibili: Ignorante usa i vecchi template o nessun template')
            self.profile_assets_dir=profile_assets
            self.vision = Vision(root / 'templates', self.cfg['template_threshold'], profile_folder=profile_assets)
        except (ValueError, OSError) as exc:
            if not self.quick_start:
                raise
            # Never mutate user templates: a clean empty Vision is diagnostic-only.
            self.vision = Vision(root / '_nonexistent_empty_templates', self.cfg['template_threshold'])
            self._startup_warnings.append('Calibrazione non utilizzabile: ' + str(exc)[:150] +
                                          '; Ignorante prosegue senza template')
        core_count = len(set(self.vision.templates).intersection(HUD_CORE))
        if live and not self.timed and not self.quick_start and core_count < 2 and 'hud_in_partita' not in self.vision.templates:
            raise ValueError('Per LIVE servono almeno 2 riferimenti strutturali: '
                             'salute, cornice minimappa o bordi degli slot inventario. '
                             'Bussola, mirino e squadra sono opzionali.')
        if live and not self.timed and not self.quick_start:
            # Without spectator cues the persistent gameplay HUD could cause actions
            # on a teammate's camera after elimination. Refuse unsafe configuration.
            spectator_cues = {'spettatore_prossimo', 'spettatore_precedente'}
            if not spectator_cues.issubset(self.vision.templates):
                raise ValueError('Per gli input LIVE calibra entrambi i controlli spettatore: '
                                 'spettatore_prossimo e spettatore_precedente')
        if live and auto_requeue and not self.timed:
            # A rewards-only playlist need not have CONTINUA or TORNA LOBBY.
            # RISCATTA is recognizable by OCR (when available) or calibration.
            from .reward_ocr import ocr_available
            # Manual quick mode is explicitly armed by the user and must not
            # require a trained Gioca template. Other modes keep the requirement.
            required = (set() if self.quick_start else
                        {'gioca'} if self.single_match else
                        {'gioca', 'continua', 'torna_lobby'})
            missing = sorted(required - set(self.vision.templates))
            has_end_cta = bool({'riscatta', 'continua', 'torna_lobby'} & set(self.vision.templates))
            if self.single_match and not self.quick_start and not has_end_cta and not (
                    self.cfg['ocr_enabled'] and ocr_available()):
                missing.append('riscatta')
            if missing:
                raise ValueError('Per il ciclo lobby completo calibra tutti i pulsanti: ' + ', '.join(missing))
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.messages: queue.Queue = queue.Queue(maxsize=160)
        self.thread: threading.Thread | None = None
        self.audio = None
        self.log_path: Path | None = None
        self._last_focus_notice = False
        self._last_alert = ''
        self._last_unknown_notice = 0.0
        self.learning_worker = None
        self.exploration_worker: ExplorationWorker | None = None
        self.demonstration_worker: DemonstrationWorker | None = None
        self._demo_allowed = threading.Event()
        self.fast_policy: FastActionPolicy | None = None
        self.planner: AutonomousPlanner | None = None
        self._last_ocr_text = ''
        self._last_ocr_status = ''
        self._last_ocr_at = 0.0
        self.session_id: str | None = None
        self.session_rollovers = 0
        self.frame_stream: LatestFrameCapture | None = None
        self._calibration_lock = threading.Lock()
        self._calibration_frame = None
        self._calibration_at = 0.0
        self.reward_ocr: RewardOCRWorker | None = None
        self.phase_ocr: PhaseOCRWorker | None = None
        self.region_ocr: ScreenRegionOCRWorker | None = None
        self._play_region_status: tuple[float,str] | None = None
        self._manual_stop = False
        self.checkpoint_path = self.root / 'state' / 'auto_cycle.json'

    def _play_visibility(self, reading, now: float) -> bool | None:
        if 'gioca' in reading.best:
            return True
        if self._play_region_status is not None:
            at, status = self._play_region_status
            if 0 <= now - at <= 3.0:
                if status == 'present': return True
                if status == 'absent': return False
                return None
        evidence = (reading.menu_checks or {}).get('gioca')
        return True if evidence == 'present' else False if evidence == 'absent' else None

    def _play_evidence_at(self, reading, frame_at: float, now: float) -> float:
        """Timestamp of the actual absence observation, not the video frame using it."""
        if ('gioca' not in reading.best and self._play_region_status is not None):
            at, status = self._play_region_status
            if status == 'absent' and 0 <= now - at <= 3.0:
                return at
        return frame_at

    def _emit(self, kind: str, message: str, *, info: dict | None = None) -> None:
        payload = {'at': datetime.now().isoformat(timespec='seconds'), 'event': kind, 'message': message}
        if info:
            payload.update(info)
            if kind in ('learning_frame', 'runtime_ocr'):
                self._last_ocr_text = str(info.get('ocr_text', ''))[:1200]
                self._last_ocr_status = str(info.get('ocr_status', ''))
                self._last_ocr_at = time.monotonic()
        if kind == 'runtime_ocr':
            # Keep OCR context for inference but do NOT flood the GUI with
            # a meaningless status line on every analyzed frame.
            return
        try:
            self.messages.put_nowait(payload)
        except queue.Full:
            # Discard only UI messages, never stop the worker or overwrite the logfile.
            pass
        if self.log_path and kind not in ('preview', 'runtime_ocr', 'telemetry', 'region_ocr'):
            # Binary images are only transported through the in-memory UI queue.
            serializable = {k: v for k, v in payload.items() if not isinstance(v, bytes)}
            with self.log_path.open('a', encoding='utf-8') as out:
                out.write(json.dumps(serializable, ensure_ascii=False) + '\n')

    def _count_external_ocr(self, target: str, *, at: float, matched: bool,
                            text: str = '', confidence: float = 1.,
                            label: str = '', count_scan: bool = True):
        """Track the existing phase/rewards OCR, without another capture.

        Source OCR readers already enforce their own timeouts; the discovery
        counter independently requires distinct, confirmed observations.
        """
        worker = self.region_ocr
        if worker is None:
            return
        if worker.discovery.observe(target, at=at, status='ok', matched=matched,
                                    text=text, confidence=confidence, label=label,
                                    count_scan=count_scan):
            self._emit('ocr_found', 'OCR trovato: ' + (label or target),
                       info={'ocr_target': target, 'ocr_text': text[:120]})

    def calibration_snapshot(self, *, after: float) -> object | None:
        """Full-resolution passive observation only; no extra capture handles."""
        if self.live:
            return None
        with self._calibration_lock:
            if self._calibration_frame is None or self._calibration_at <= after:
                return None
            return self._calibration_frame.copy()

    def _allowed(self) -> bool:
        return foreground_window(self.cfg['window_executables']) is not None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            raise RuntimeError('Sessione già attiva')
        self.root.joinpath('logs').mkdir(exist_ok=True)
        self.log_path = self.root / 'logs' / f'sessione_{datetime.now().strftime("%Y%m%d_%H%M%S")}.jsonl'
        self.stop_event.clear()
        self.pause_event.clear()
        self._manual_stop = False
        self._last_ocr_text = ''
        self._last_ocr_status = ''
        self._last_ocr_at = 0.0
        for warning in self._startup_warnings:
            self._emit('quick_warning', warning)
        self.thread = threading.Thread(target=self._loop, name='ObserverRunner', daemon=True)
        self.thread.start()

    def pause_toggle(self) -> bool:
        if self.pause_event.is_set():
            self.pause_event.clear()
            self._emit('lifecycle', 'Ripresa')
            return False
        self.pause_event.set()
        self._emit('lifecycle', 'Pausa: input sospesi')
        return True

    def stop(self) -> None:
        self._manual_stop = True
        self.stop_event.set()
        self._emit('lifecycle', 'Arresto richiesto')

    def join(self, timeout=4):
        if self.thread:
            self.thread.join(timeout)

    def _loop(self) -> None:
        enable_dpi_awareness()
        budget = SessionBudget(
            max_seconds=float(self.cfg['session_minutes']) * 60,
            max_actions=int(self.cfg['max_actions']),
            repeat=self.repeat_matches, started_at=time.monotonic())
        self.session_rollovers = 0
        last_scene = None
        was_spectator_latched = False
        last_preview = 0.0
        last_learning_capture = float('-inf')
        session_id = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.session_id = session_id
        sent_at = float('-inf')
        had_match = False
        finished_streak = 0
        planner_history = deque(maxlen=2)
        agent_was_permitted = False
        last_agent_action = 'nessuna'
        last_fast_action = float('-inf')
        last_policy_refresh = float('-inf')
        last_fast_report = float('-inf')
        last_input_report = float('-inf')
        last_quick_wait_report = float('-inf')
        last_quick_wait_reason = ''
        last_idle_report = float('-inf')
        last_speed_report = float('-inf')
        capture_times = deque(maxlen=48)
        frame_ages = deque(maxlen=48)
        driver_times = deque(maxlen=48)
        vision_times = deque(maxlen=48)
        feature_times = deque(maxlen=48)
        policy_times = deque(maxlen=48)
        learning_store = None
        feedback_context = ''
        last_hud_report = 0.0
        last_capture_notice = float('-inf')
        live_frames_received = 0
        last_demo_report = float('-inf')
        demo_status = ''
        actions_count = 0
        before_action = None
        motion_action_name = None
        motion_action_ended_at = float('-inf')
        last_visual_feedback = 'Nessuna prova: la ricezione dei tasti nel gioco non è verificata'
        last_camera_feedback = 'Telecamera non ancora verificata'
        camera_verifier = CameraFeedback()
        last_camera_alert_status = 'untested'
        last_route_eval = float('-inf')
        last_checkpoint_at = float('-inf')
        last_checkpoint_stage = ''
        last_checkpoint_matches = -1
        last_driver_feedback = 'Nessun comando inviato'
        last_telemetry_report = float('-inf')
        phase_estimator = PhaseEstimator()
        phase_gate = PhaseGate()
        last_phase_ocr_submit = float('-inf')
        last_region_ocr_submit = float('-inf')
        last_region_report = ''
        region_reward_scans = deque(maxlen=12)
        region_recent = {}
        scanner_play_recent = None  # (capture time, calibrated Match), never a new OCR observation
        last_phase_ocr_label = 'normal'
        last_phase_ocr_error = ''
        quick_ocr_fault = False
        quick_ocr_last_ok = float('-inf')
        reboot_reentry_armed = False
        unknown_watchdog = UnknownWatchdog()
        last_single_recovery_count = 0
        last_phase_marker = None
        single = (SingleMatchController(
            quick_start=self.quick_start,
            quick_wait_play_exit=self.quick_wait_play_exit,
            quick_loading_seconds=float(self.cfg['ignorante_loading_wait_seconds']),
            quick_jump_min_seconds=float(self.cfg['ignorante_jump_min_seconds']),
            quick_jump_max_seconds=float(self.cfg['ignorante_jump_max_seconds']),
            repeat=self.repeat_matches,
            jump_window_seconds=float(self.cfg['single_match_jump_seconds']),
            jump_period_seconds=float(self.cfg['ignorante_jump_interval_seconds'] if self.quick_wait_play_exit else self.cfg['single_match_jump_interval_seconds']),
            harvest=self.cfg['guided_explore_harvest'],
            smooth=self.cfg['guided_smooth_enabled'],
            profile=self.cfg['guided_activity_profile'],
            recovery_limit=int(self.cfg['guided_recovery_limit']),
            recovery_threshold=float(self.cfg['guided_motion_threshold'])) if self.single_match else None)
        menu_supervisor = MenuSupervisor() if single is not None else None
        # Add cycle-policy tables to the user's EXISTING learning SQLite without
        # recreating, deleting or modifying screenshots and corrected labels.
        cycle_store = CycleStore(self.root / 'learning' / 'observer.sqlite3') if single else None
        if cycle_store is not None:
            try:
                single.policy = cycle_store.prepare()
            except Exception as exc:
                if not self.quick_start:
                    self._emit('error','Database di apprendimento non accessibile: '+str(exc)[:200])
                    return
                cycle_store = None
                self._emit('quick_warning', 'SQLite non accessibile, training intatti: eseguo IGNORANTE con regole in memoria. ' + str(exc)[:160])
            self._emit('cycle_policy',
                'Regole caricate dal database: ricompense PRIMA di Gioca; '
                '18 s di controllo dopo la lobby; OCR e posizione confermati.',
                info={'db': 'learning/observer.sqlite3'})
        previous_checkpoint = load_checkpoint(self.checkpoint_path) if self.repeat_matches else None
        resume_gate = ResumeGate(active=bool(previous_checkpoint and previous_checkpoint['active'] and not self.quick_start),
                                 previous_stage=previous_checkpoint['stage'] if previous_checkpoint else '')
        if single is not None and previous_checkpoint is not None:
            single.matches_completed = previous_checkpoint['matches_completed']
            self._emit('checkpoint',
                ('Ripresa di una sessione interrotta: verifico tre nuove schermate, senza input' if resume_gate.active
                 else 'Contatore delle partite precedenti recuperato; nuova sessione avviata normalmente'),
                info={'matches_completed': single.matches_completed, 'resume_pending': resume_gate.active})
        if single is not None and self.quick_start:
            single.arm_quick(time.monotonic())
            self._emit('quick_timer', (
                'IGNORANTE: aspetto GIOCA, poi tre schermate senza GIOCA, 120 secondi di caricamento, 120–150 secondi solo Spazio, infine WASD/piccone.'
                if self.quick_wait_play_exit else
                'IGNORANTE ARMATO: 20 secondi effettivi con Fortnite in primo piano, poi Space/WASD/piccone; F8 pausa, F9 stop.'))
            from .reward_ocr import ocr_available
            if not self.cfg['ocr_enabled'] or not ocr_available():
                self._emit('ocr_notice', 'OCR non disponibile: avvio temporizzato consentito, ma schermate '
                           'SPETTATORE/RIAVVIO non riconoscibili tramite testo. '
                           'Restano attivi solo i riferimenti visivi insegnati; verifica Tesseract.')
        if single is not None and self.repeat_matches:
            try:
                save_checkpoint(self.checkpoint_path, stage='await_play',
                                matches_completed=single.matches_completed, active=True)
            except OSError as exc:
                self._emit('checkpoint_error', 'Checkpoint non salvato: ' + str(exc)[:140])
        guided = (SimpleMatchRoutine(
            harvest=self.cfg['guided_explore_harvest'],
            smooth=self.cfg['guided_smooth_enabled'],
            profile=self.cfg['guided_activity_profile'],
            jump_period=float(self.cfg['guided_jump_interval_seconds']),
            min_fall=float(self.cfg['guided_min_fall_seconds']),
            recovery_limit=int(self.cfg['guided_recovery_limit']),
            recovery_threshold=float(self.cfg['guided_motion_threshold'])) if self.guided else None)
        timed = (TimedMatchRoutine(
            load_seconds=float(self.cfg['guided_timed_load_seconds']),
            jump_period=float(self.cfg['guided_jump_interval_seconds']),
            jump_window_seconds=float(self.cfg['guided_timed_jump_window_seconds']),
            fall_seconds=float(self.cfg['guided_timed_fall_seconds']),
            ground_seconds=float(self.cfg['guided_timed_ground_seconds']),
            health_jump_enabled=bool(self.cfg['guided_health_jump_enabled']),
            health_threshold=float(self.cfg['guided_health_threshold']),
            health_min_wait_seconds=float(self.cfg['guided_health_min_wait_seconds']),
            health_jump_window_seconds=float(self.cfg['guided_health_jump_window_seconds']),
            harvest=bool(self.cfg['guided_explore_harvest']),
            smooth=bool(self.cfg['guided_smooth_enabled']),
            profile=self.cfg['guided_activity_profile'],
            recovery_limit=int(self.cfg['guided_recovery_limit']),
            recovery_threshold=float(self.cfg['guided_motion_threshold'])) if self.timed else None)
        timed_started = False
        last_reward_ocr = -1e9
        reward_last = None
        reward_streak = 0
        reward_verified = None
        reward_verified_at = -1e9
        reward_verified_source_at = -1e9
        reward_verified_overlay = False
        reward_verified_learned = False
        reward_overlay_streak = 0
        reward_overlay_checked_at = -1e9
        reward_overlay_suspect_at = -1e9
        reward_ocr_ok_times = deque(maxlen=12)
        last_cycle_stage = single.stage if single else ''
        last_cycle_matches = single.matches_completed if single else 0
        last_guided_command = None
        last_guided_stage = None
        brain = Brain(seed=self.seed, motion_threshold=self.cfg['motion_threshold'],
                      menu_cooldown=self.cfg['menu_cooldown_seconds'],
                      menu_retries=int(self.cfg['menu_retries']),
                      auto_requeue=self.requeue and single is None, enable_shoot=self.shoot,
                      menu_only=self.autonomous,
                      solo_mode=bool((self.timed or self.single_match) and self.cfg['guided_solo_mode']))
        driver = None
        try:
            backend = self.cfg['input_backend']
            driver_class = GamepadDriver if backend == 'gamepad' else Driver
            driver = driver_class(live=self.live, allowed=self._allowed, config=self.cfg,
                            stop=self.stop_event, paused=self.pause_event)
            def quick_without_frame(now: float, detail: str):
                """Run user-armed IGNORANTE even with zero frames or broken recognition."""
                nonlocal actions_count, last_driver_feedback, last_telemetry_report
                if (single is None or not self.quick_start or self.pause_event.is_set()
                        or self.stop_event.is_set() or not self._allowed()):
                    return
                action = single.quick_without_frame(now)
                if action.name != 'wait':
                    sent = driver.run(action)
                    if sent:
                        actions_count += 1
                    last_driver_feedback = ('Comando trasmesso (non verificato dal gioco)' if sent else
                                            'ERRORE: comando NON trasmesso, verifica input/focus')
                    self._emit('decision', f'{action.name}: {action.reason}',
                               info={'scene': 'unknown', 'capture': detail, 'quick': True, 'sent': sent})
                if now - last_telemetry_report >= 0.7:
                    self._emit('telemetry', '', info={
                        'scene': 'unknown', 'capture': detail, 'hud': 'non necessario',
                        'ocr_phase': 'non disponibile', 'phase': single.label,
                        'phase_detail': 'IGNORANTE: tempo indipendente dagli screenshot',
                        'objective': 'IGNORANTE: esplorazione semplice, salti, visuale e piccone',
                        'action': action.name, 'reason': action.reason,
                        'input_status': last_driver_feedback,
                        'matches_completed': single.matches_completed,
                    })
                    last_telemetry_report = now

            def timed_without_frame(now: float):
                """Opt-in time-only fallback, never acts if focus/pause was lost.

                Does not use stale frame data for input. Without fresh images,
                menus/rewards cannot be recognized until capture recovers.
                """
                nonlocal timed_started, actions_count, last_driver_feedback, last_telemetry_report
                if timed is None or self.pause_event.is_set() or not self._allowed() or phase_gate.locked:
                    return  # Never fall back to blind timed keys during reboot/spectating.
                if not timed_started:
                    timed_started = True
                    if self.cfg['guided_timed_start_on_launch']:
                        timed.arm(now)
                        self._emit('timed', 'Timer avviato anche senza screenshot; '
                                   'premi manualmente Gioca se il relativo pulsante non è calibrato.')
                    else:
                        self._emit('timed', 'Nessuna immagine: impossibile rilevare il clic Gioca; timer non avviato.')
                action = timed.choose(now)
                if action.name != 'wait':
                    executed = driver.run(action)
                    if executed:
                        actions_count += 1
                    last_driver_feedback = ('Comando temporizzato inviato, senza verifica visiva'
                                            if executed else 'Comando temporizzato non inviato')
                    self._emit('decision', f'{action.name}: {action.reason}',
                               info={'scene':'unknown', 'capture':'non disponibile', 'timed':True})
                # Keep the dashboard truthful even when capture fails. Without
                # images the timer is only an estimate, not a visual inference.
                if now - last_telemetry_report >= 1.0:
                    self._emit('telemetry', '', info={
                        'scene': 'unknown', 'capture': 'immagini non disponibili',
                        'hud': 'nessuna verifica', 'phase': timed.stage,
                        'objective': 'routine a tempo (non verificata)',
                        'activity': timed.explorer.planner.activity if timed.stage == 'ground' else '—',
                        'behavior_profile': self.cfg['guided_activity_profile'],
                        'action': action.name, 'reason': action.reason,
                        'authorized': bool(timed.stage in ('jumping', 'ground')),
                        'input_status': last_driver_feedback,
                        'visual_feedback': 'Nessun fotogramma per verificare lo spostamento',
                        'recoveries': timed.explorer.recovery_count,
                    })
                    last_telemetry_report = now
            if self.cfg['audio_enabled'] and (self.root / 'cassa.wav').exists():
                from .audio import SoundWatcher
                self.audio = SoundWatcher(self.root / 'cassa.wav', threshold=self.cfg['audio_threshold'])
                self.audio.start()
                self._emit('audio', 'Rilevamento audio sperimentale attivo')
            if self.cfg['learning_enabled']:
                try:
                    engine = LearningEngine(
                        self.root / 'learning', ocr_enabled=self.cfg['ocr_enabled'],
                        ocr_languages=self.cfg['ocr_languages'],
                        max_saved_frames=int(self.cfg['max_saved_frames']),
                        # One expensive VLM request at a time: in autonomous mode
                        # the planner owns Ollama and the recorder runs OCR/ML only.
                        ollama_enabled=self.cfg['ollama_enabled'] and not (self.autonomous or self.exploration),
                        ollama_model=self.cfg['ollama_model'],
                        ollama_every=int(self.cfg['ollama_every_n_frames']),
                        ollama_timeout=float(self.cfg['ollama_timeout_seconds']))
                except Exception as exc:
                    if not self.quick_start:
                        raise
                    engine = None
                    self._emit('quick_warning', 'Training SQLite non leggibile, nessuna modifica ai dati; IGNORANTE continua senza apprendimento: ' + str(exc)[:180])
                if engine is not None:
                    learning_store = engine.store  # Verified examples remain available in both modes.
                    if not self.autonomous:
                        learning_store.mark_session(session_id, 'start', 'Osservazione avviata')
                    if self.autonomous:
                        feedback_context = learning_store.feedback_context(exclude_session=session_id)
                        # The AI needs fresh pixels in RAM, NOT review screenshots on disk.
                        # OCR, when enabled, is read-only and never persists its input.
                        if self.cfg['ocr_enabled']:
                            self.learning_worker = LearningWorker(engine, self._emit, archive_frames=False)
                            self.learning_worker.start()
                        self._emit('learning', 'PILOTA AI: nessuno screenshot archiviato; ' +
                                   ('OCR temporaneo in memoria' if self.cfg['ocr_enabled']
                                    else 'nessun worker OCR'))
                    else:
                        self.learning_worker = LearningWorker(engine, self._emit, archive_frames=True, durable_archive=True)
                        self.learning_worker.start()
                        self._emit('learning', f'PARTITA GIOCATA DA TE: screenshot archiviati ogni '
                                   f'{self.cfg["capture_seconds"]} secondi in learning/frames per la revisione')
                    if self.exploration:
                        self._emit('exploration', 'FASE 1 · Osservazione passiva: nessun input al gioco; '
                                   'OCR, eventi e memoria salvati in locale')
                        if self.cfg['ollama_enabled']:
                            self.exploration_worker = ExplorationWorker(
                                LocalVisionModel(self.cfg['ollama_model'], self.cfg['ollama_timeout_seconds']),
                                learning_store, self._emit,
                                interval=float(self.cfg['exploration_vision_seconds']))
                            self.exploration_worker.start()
                        if self.cfg['demonstration_enabled']:
                            reader = WinGameInputReader(self.cfg['keys'],
                                 allow_mouse=self.cfg['demonstration_mouse_shoot'])

                            def record_demo(action: str, controls: tuple[str, ...]) -> None:
                                if not self._demo_allowed.is_set() or self.stop_event.is_set() or self.pause_event.is_set():
                                    return
                                target = foreground_window(self.cfg['window_executables'])
                                if target is None:
                                    return
                                shot = snapshot(target)
                                # The capture itself, not an earlier scan, determines
                                # whether this action is immediately fit for training.
                                try:
                                    demo_scene = self.vision.recognize(shot, target.left, target.top).scene
                                except Exception:
                                    demo_scene = 'unknown'
                                number, jpeg = learning_store.record_demonstration(
                                    shot, session=session_id, action=action,
                                    controls=controls, scene=demo_scene,
                                    process=target.executable)
                                self._emit('learning_frame', f'#{number} dimostrazione: {action}',
                                           info={'frame_id': number, 'session': session_id,
                                                 'prediction': 'azione dimostrata',
                                                 'score': .5, 'source': 'demo',
                                                 'description': 'Comandi osservati: ' + ', '.join(controls),
                                                 'action_label': action, 'action_source': 'demo',
                                                 'ocr_text': '', 'ocr_status': 'non acquisito',
                                                 'jpeg': jpeg})

                            def record_transition(actions: tuple[str, ...]) -> None:
                                # State changes preserve timings and simultaneous
                                # approved controls even when screenshot analysis is
                                # uncertain. No raw keyboard history is collected.
                                learning_store.record_input_transition(session=session_id, actions=actions)

                            self.demonstration_worker = DemonstrationWorker(
                                reader.read_actions, lambda action: record_demo(action, (action,)),
                                allowed=lambda: self._demo_allowed.is_set()
                                    and not self.pause_event.is_set()
                                    and not self.stop_event.is_set()
                                    and foreground_window(self.cfg['window_executables']) is not None,
                                record_multi=record_demo,
                                on_transition=record_transition,
                                on_error=lambda error: self._emit('demo_error',
                                    'Registrazione comando non riuscita: ' + error))
                            self.demonstration_worker.start()
                            self._emit('exploration', 'Dimostrazione attiva: tasti e clic di gioco autorizzati; '
                                       'salvataggio indipendente dall’HUD, senza testo digitato')
                        else:
                            self._emit('demo_status', 'Registrazione comandi DISATTIVATA: saranno salvati gli screenshot, '
                                       'ma nessuna azione per il pilota rapido. Attiva la casella prima della prossima sessione.')
            if self.autonomous or self.single_match:
                if (self.cfg['fast_policy_enabled'] and learning_store is not None and
                        (self.single_match or not self.guided or self.cfg['guided_use_learning'])):
                    self.fast_policy = FastActionPolicy(action_seconds=self.cfg['fast_action_duration_seconds'])
                    self.fast_policy.refresh(learning_store)
                    self._emit('agent', f'Policy veloce: {self.fast_policy.examples} esempi azione; '
                               + ('suggerimenti di navigazione nella modalità semplice' if self.guided
                                  else 'senza esempi concordanti usa il modello visivo'))
                if self.autonomous and self.cfg['ollama_enabled'] and not self.guided:
                    self.planner = AutonomousPlanner(
                        LocalVisionModel(self.cfg['ollama_model'], self.cfg['ollama_timeout_seconds']),
                        shoot_allowed=self.shoot,
                        min_confidence=float(self.cfg['autonomous_min_confidence']))
                    self.planner.start()
                    self._emit('agent', 'Modalità ibrida: policy rapida locale, modello visivo asincrono solo se incerto')
                elif self.autonomous and self.guided:
                    if self.single_match:
                        self._emit('agent', 'UNA PARTITA: Gioca → HUD stabile → salto + movimento per 120 s → esplora → risultati → torna in lobby → STOP')
                    elif self.timed:
                        self._emit('agent', 'Routine A TEMPO: attesa 90 s configurabile → Spazio periodico a durata limitata → '
                                   'discesa stimata → W + piccone. HUD e bus_salto NON richiesti; F9 arresta.')
                    else:
                        self._emit('agent', 'Routine intelligente: bus e HUD verificati per guidare il personaggio')
                elif self.autonomous:
                    self._emit('agent', 'Modalità FAST-ONLY: nessuna attesa Ollama, situazione ignota = wait')
            if (self.timed or self.single_match) and self.cfg['ocr_enabled']:
                self.reward_ocr = RewardOCRWorker(self.cfg['ocr_languages'].split('+')[0], self.root / 'templates',
                    profile_dir=self.profile_assets_dir)
                self.reward_ocr.start()
                self._emit('reward', 'Menu: OCR facoltativo in RAM; Riscatta richiede 2 letture, Torna lobby/Continua 3 e stato RESULTS. '
                           'Se OCR non è disponibile, calibra solo RISCATTA.')
            # Structural scanners remain operational without Tesseract.
            try:
                self.region_ocr = ScreenRegionOCRWorker(self.root,
                    allow_ocr=self.cfg['ocr_enabled'])
                self.region_ocr.start()
            except Exception as exc:
                self._emit('region_ocr_error','Scanner modulari non inizializzati: '+str(exc)[:150])
            if self.cfg['ocr_enabled']:
                self.phase_ocr = PhaseOCRWorker(self.cfg['ocr_languages'])
                self.phase_ocr.start()
                self._emit('phase_ocr', 'OCR delle fasi attivo: SPETTATORE / ATTESA RIAVVIO; '
                           'i testi non autorizzano mai tasti, conferma HUD necessaria per riprendere.')
            if self.cfg['async_capture_enabled']:
                self.frame_stream = LatestFrameCapture(
                    self.cfg['window_executables'],
                    interval=float(self.cfg['async_capture_interval_seconds']),
                    # A stale frame must NEVER authorize commands.
                    max_age=max(.30, float(self.cfg['async_capture_interval_seconds']) * 2.6),
                    provider=foreground_window, grab=snapshot)
                self.frame_stream.start()
                self._emit('performance', 'Acquisizione asincrona attiva: ultimo frame disponibile, senza coda')
            self._emit('lifecycle', 'Monitor avviato' + (f' (input {backend} abilitati)' if self.live else ' (sola osservazione)'))
            while not self.stop_event.is_set():
                now = time.monotonic()
                decision = budget.check(now, actions_count)
                if decision.kind == 'stop':
                    self._emit('limit', decision.reason)
                    break
                if decision.kind == 'rollover':
                    # Only bookkeeping rolls over: no W/A/S/D input is held or
                    # replayed here, and the match controller retains its phase.
                    actions_count = 0
                    self.session_rollovers = budget.rollovers
                    self.log_path = (self.root / 'logs' /
                                     f'sessione_{session_id}_blocco_{budget.rollovers + 1}.jsonl')
                    self._emit('lifecycle', decision.reason +
                               ': nuovo blocco di monitoraggio; partita invariata')
                if self.pause_event.is_set():
                    self._demo_allowed.clear()
                    if self.planner and agent_was_permitted:
                        self.planner.invalidate()
                        agent_was_permitted = False
                    driver.release()
                    if timed is not None:
                        timed.last_tick = None  # pausing must not consume the timer
                    if single is not None:
                        single.suspend_clock()
                    before_action = None
                    self.stop_event.wait(.15)
                    continue
                win = foreground_window(self.cfg['window_executables'])
                if win is None:
                    if timed is not None:
                        timed.last_tick = None  # focus loss pauses timer
                    if single is not None:
                        single.suspend_clock()
                    self._demo_allowed.clear()
                    if self.exploration and self.demonstration_worker is not None and now - last_demo_report >= 2.5:
                        self._emit('demo_status',
                            f'Dimostrazioni: {self.demonstration_worker.saved} esempi salvati; '
                            'IN ATTESA: Fortnite non è in primo piano o il processo non corrisponde')
                        last_demo_report = now
                    if self.planner and agent_was_permitted:
                        self.planner.invalidate()
                        agent_was_permitted = False
                    driver.release()
                    before_action = None
                    brain.decide(Observation(False, 'unfocused'), now)
                    if not self._last_focus_notice:
                        self._emit('focus', 'Fortnite non verificato in primo piano: nessun input')
                        self._last_focus_notice = True
                    self.stop_event.wait(.4)
                    continue
                if self._last_focus_notice:
                    self._emit('focus', f'Finestra verificata: {win.executable}')
                    self._last_focus_notice = False
                captured_at = time.monotonic()
                if self.frame_stream is not None:
                    latest = self.frame_stream.latest(win)
                    if latest is None:
                        # The producer may be between frames, or focus/geometry
                        # may have changed. NEVER reuse an old image for inputs.
                        # Surface the actual acquisition problem separately from
                        # successful capture followed by HUD recognition failure.
                        if now - last_capture_notice >= 3.0:
                            detail = (f'errore acquisizione: {self.frame_stream.error}'
                                      if self.frame_stream.error else
                                      'nessun fotogramma fresco disponibile; verificare focus e cattura')
                            self._emit('capture_status', f'Cattura LIVE in attesa: {detail}',
                                       info={'frame_count': live_frames_received, 'capture_error': self.frame_stream.error})
                            last_capture_notice = now
                        if self.quick_start:
                            quick_without_frame(now, 'acquisizione asincrona assente')
                            self.stop_event.wait(.10)
                        elif timed is not None:
                            timed_without_frame(now)
                            self.stop_event.wait(.15)
                        else:
                            self.stop_event.wait(.015)
                        continue
                    frame = latest.image
                    captured_at = latest.captured_at
                    capture_times.append(latest.duration_ms)
                    frame_ages.append(max(0., (time.monotonic() - latest.captured_at) * 1000.))
                else:
                    capture_started = time.perf_counter()
                    try:
                        frame = snapshot(win)
                    except Exception as exc:
                        if self.quick_start:
                            quick_without_frame(now, 'screenshot non disponibile: ' + str(exc)[:120])
                            self.stop_event.wait(.10)
                            continue
                        if timed is None:
                            raise
                        if now - last_capture_notice >= 6:
                            self._emit('capture_status', 'Screenshot sincrono non disponibile: '
                                       + str(exc)[:140] + '; continuo soltanto con i timer.')
                            last_capture_notice = now
                        timed_without_frame(now)
                        self.stop_event.wait(.15)
                        continue
                    captured_at = time.monotonic()
                    capture_times.append((time.perf_counter() - capture_started) * 1000)
                    frame_ages.append(0.)
                live_frames_received += 1
                if timed is not None and not timed_started:
                    timed_started = True
                    if self.cfg['guided_timed_start_on_launch']:
                        timed.arm(now)
                        self._emit('timed', 'Cronometro avviato con la sessione LIVE. '
                                   'Se non hai ancora premuto Gioca, avvialo manualmente adesso; '
                                   'un clic automatico riconosciuto riavvia il timer.')
                    else:
                        self._emit('timed', 'In attesa del clic GIOCA calibrato per far partire il timer.')
                # The asynchronous producer can deliver a frame taken BEFORE the
                # previous W press finished. That would create a misleading
                # no-movement result. Require a true post-action image.
                if before_action is not None and captured_at <= motion_action_ended_at + .02:
                    self.stop_event.wait(.02)
                    continue
                vision_started = time.perf_counter()
                try:
                    reading = self.vision.recognize(frame, win.left, win.top)
                    menu_evidence_at = {}  # source OCR capture, not receipt or current screenshot
                    stable_region = {k:r for k,(t,r) in region_recent.items() if now-t<3.4}
                    # Independent per-zone OCR: each user-taught region has its
                    # own phrase and two-fresh-frame confirmation. Positive
                    # stable evidence may annotate scene; absence never means
                    # the button has definitely disappeared.
                    if self.region_ocr is not None:
                        if now - last_region_ocr_submit >= 1.4:
                            if self.region_ocr.submit(frame,captured_at,single.stage if single else ''):
                                last_region_ocr_submit = now
                        regional = self.region_ocr.take()
                        # The worker is asynchronous: ignore expired screenshots
                        # before consulting ANY modular state or menu coordinates.
                        if regional is not None and not (0 <= now-regional.get('at',-1) <= 3.4):
                            regional=None
                        if regional is not None:
                            # Notify the dashboard only when a newly confirmed
                            # OCR appearance occurs. Never emit once per frame.
                            for found in regional.get('new_detections',())[:8]:
                                self._emit('ocr_found','OCR trovato: '+str(found.get('label',''))[:90],
                                           info={'ocr_target':found.get('target'),
                                                 'ocr_text':str(found.get('text',''))[:120]})
                            fusion=(regional.get('fusion') or {}) if regional.get('status')=='ok' else {}
                            confirmed=(fusion.get('state') not in (None,'UNKNOWN') and
                                       fusion.get('candidate')==fusion.get('state') and
                                       fusion.get('confidence',0)>=.47 and
                                       bool(fusion.get('evidence')))
                            if confirmed:
                                # Only use confirmed ROI fusion evidence. Hard
                                # legacy spectator/rewards signals retain priority.
                                stage_map={'IN_MATCH':'match','LANDED':'match',
                                  'LOBBY':'lobby','MATCHMAKING':'loading','LOADING':'loading',
                                  'ELIMINATED':'spectator','SPECTATING':'spectator',
                                  'RETURNING_TO_LOBBY':'results'}
                                guess=stage_map.get(fusion['state'])
                                if guess and reading.scene=='unknown':
                                    reading=replace(reading,scene=guess)
                                elif fusion['state'] in ('BATTLE_BUS','SKYDIVING','GLIDING') and reading.scene=='unknown':
                                    reading=replace(reading,
                                        phase_hint='battle_bus' if fusion['state']=='BATTLE_BUS' else 'skydiving',
                                        phase_source='scanner confermato')
                            scan_cache=regional.get('scanner_readings') or {}
                            # Results from previously analyzed frames belong in
                            # the debugger, not in the action/coordinate path.
                            scan_data={k:v for k,v in scan_cache.items()
                                       if v.get('at')==regional['at'] and regional.get('status')=='ok'}
                            scanner_gioca=scan_data.get('lobby',{})
                            if scanner_gioca.get('status') in ('present','absent'):
                                self._play_region_status=(regional['at'],scanner_gioca['status'])
                            # A fresh negative or failed reading invalidates cached coordinates.
                            if scanner_gioca and scanner_gioca.get('status') != 'present':
                                scanner_play_recent = None
                            if (confirmed and fusion.get('state')=='LOBBY' and
                                    scanner_gioca.get('status')=='present' and
                                    reading.scene in ('unknown','lobby') and
                                    'gioca' not in reading.best):
                                roi=scanner_gioca.get('roi') or []
                                if len(roi)==4:
                                    point=(win.left+round((roi[0]+roi[2])*frame.shape[1]/2),
                                           win.top+round((roi[1]+roi[3])*frame.shape[0]/2))
                                    reading.best['gioca']=Match('gioca',float(scanner_gioca['confidence']),point)
                                    scanner_play_recent = (regional['at'], reading.best['gioca'])
                                    menu_evidence_at['gioca']=regional['at']
                                    reading=replace(reading,scene='lobby')
                            # Only explicit, confirmed result states can provide
                            # menu coordinates from a user-taught modular ROI.
                            if (confirmed and fusion.get('state')=='RETURNING_TO_LOBBY' and
                                    reading.scene in ('results','unknown')):
                                for scanner_id,menu_id in [('return_to_lobby','torna_lobby'),('riscatta','riscatta')]:
                                    value=scan_data.get(scanner_id,{})
                                    if value.get('status')!='present':continue
                                    if menu_id=='riscatta' and scan_data.get('reward_title',{}).get('status')!='present':
                                        continue
                                    roi=value.get('roi') or []
                                    if len(roi)==4 and menu_id not in reading.best:
                                        point=(win.left+round((roi[0]+roi[2])*frame.shape[1]/2),
                                               win.top+round((roi[1]+roi[3])*frame.shape[0]/2))
                                        reading.best[menu_id]=Match(menu_id,float(value['confidence']),point)
                                        menu_evidence_at[menu_id]=regional['at']
                                        reading=replace(reading,scene='results')
                            if fusion:
                                self._emit('scanner_state', 'Scanner modulari: '+fusion.get('state','UNKNOWN'),
                                    info={'scanner_state':fusion.get('state','UNKNOWN'),
                                          'scanner_reason':fusion.get('reason',''),
                                          'scanner_scores':fusion.get('alternatives',{}),
                                          'scanner_transition':fusion.get('transition','')})
                            if regional.get('status') == 'resolution_mismatch':
                                # Old coordinates belong to another resolution and
                                # must not remain actionable during the TTL window.
                                region_recent.clear()
                                scanner_play_recent = None
                                self._play_region_status = None
                                tag='Risoluzione OCR diversa: '+str(regional.get('actual'))
                                if tag != last_region_report:
                                    self._emit('region_ocr_error',tag+'; il profilo non viene applicato')
                                    last_region_report=tag
                            elif regional.get('status')=='ok' and 0 <= now - regional['at'] <= 5.0:
                                data=regional.get('readings',{})
                                self._emit('region_ocr','OCR delle zone insegnate',info={
                                    'regions':{key:{'text':v['text'],'score':v['score'],
                                        'stable':v['stable'],'status':v['status']} for key,v in data.items()}})
                                if data.get('reward_title',{}).get('status') == 'ok':
                                    region_reward_scans.append(regional['at'])
                                # An independent fresh OCR result can indicate PRESENT,
                                # ABSENT or UNKNOWN; OCR failures/blank frames stay UNKNOWN.
                                play_part = data.get('gioca')
                                if play_part is not None:
                                    ps = ('present' if play_part.get('stable') else
                                          'absent' if play_part.get('status')=='ok' and
                                          not play_part.get('matched') and play_part.get('text') else
                                          'unknown')
                                    self._play_region_status = (regional['at'], ps)
                                    if ps != 'present':
                                        scanner_play_recent = None
                                stable={k:v for k,v in data.items() if v['stable'] and v['status']=='ok'}
                                for target, result in data.items():
                                    if result.get('stable') and result.get('status')=='ok':
                                        region_recent[target]=(regional['at'],result)
                                    else:
                                        region_recent.pop(target,None)
                                stable_region={k:r for k,(t,r) in region_recent.items() if now-t<3.4}
                                for k,v in stable_region.items():
                                    if k in ('gioca','continua','torna_lobby','gioca_di_nuovo','riscatta'):
                                        # A reward action is not justified by its
                                        # label alone: reward title must be known.
                                        if k=='riscatta' and 'reward_title' not in stable:
                                            continue
                                        if k not in reading.best:
                                            reading.best[k]=Match(k,v['score'],
                                               (v['center'][0]+win.left,v['center'][1]+win.top))
                                            menu_evidence_at[k]=region_recent[k][0]
                                if 'reward_title' in stable and reading.scene in ('unknown','lobby','match'):
                                    reading=replace(reading,scene='results')
                                elif ('eliminato' in stable or 'spettatore_prossimo' in stable or
                                        'spettatore_precedente' in stable):
                                    reading=replace(reading,scene='spectator')
                                elif ('gioca' in stable and reading.scene=='unknown'):
                                    reading=replace(reading,scene='lobby')

                    # OCR is asynchronous and usually slower than frame capture.
                    # Carry the latest positive PLAY evidence between results so
                    # the supervisor does not reset its streak on every video
                    # frame. Preserve the original capture time: repeated frames
                    # must never count as independent OCR confirmations.
                    if 'gioca' not in reading.best:
                        cached = _cached_play_match(now, reading.scene, region_recent,
                                                    scanner_play_recent, win.left, win.top)
                        if cached is not None:
                            at, reading.best['gioca'] = cached
                            menu_evidence_at['gioca'] = at

                except Exception as exc:
                    if self.quick_start:
                        quick_without_frame(now, 'riconoscimento visivo non disponibile: ' + str(exc)[:120])
                        self.stop_event.wait(.10)
                        continue
                    if timed is None:
                        raise RuntimeError(f'Errore lettura visiva: {exc}') from exc
                    if now - last_capture_notice >= 6:
                        self._emit('capture_status', 'Vision non disponibile; routine a tempo: '
                                   + str(exc)[:140])
                        last_capture_notice = now
                    timed_without_frame(now)
                    self.stop_event.wait(.15)
                    continue
                vision_times.append((time.perf_counter() - vision_started) * 1000)
                # OCR always uses the newest *captured* image, never a UI preview.
                # The result is processed in this thread; Tesseract itself runs
                # asynchronously and cannot stall movement or the F9 stop key.
                phase_ocr_result = None
                if self.phase_ocr is not None:
                    if captured_at > last_phase_ocr_submit and now - last_phase_ocr_submit >= float(self.cfg['phase_ocr_interval_seconds']):
                        if self.phase_ocr.submit(frame, captured_at):
                            last_phase_ocr_submit = captured_at
                    phase_ocr_result = self.phase_ocr.take()
                overlay = phase_gate.observe(
                    phase_ocr_result, scene=reading.scene,
                    menu=reading.menu.name if reading.menu else None, at=captured_at,
                    allow_solo_return=bool((timed is not None or single is not None) and self.cfg['guided_solo_mode']))
                if phase_ocr_result is not None and phase_ocr_result.status != 'ok':
                    if self.quick_start:
                        # Fail closed until an operational OCR sample arrives.
                        quick_ocr_fault = True
                    if phase_ocr_result.status != last_phase_ocr_error:
                        last_phase_ocr_error = phase_ocr_result.status
                        self._emit('phase_ocr_error', 'OCR fasi non disponibile: ' + phase_ocr_result.status +
                                   '. Le protezioni visive calibrate restano attive.')
                if phase_ocr_result is not None and phase_ocr_result.status == 'ok' and 0 <= now - phase_ocr_result.at <= 3.5:
                    for i, (cue, key, label) in enumerate((
                        ('spectator','fase_spettatore','Spettatore'),
                        ('reboot_wait','fase_attesa_riavvio','In attesa del riavvio'),
                        ('rebooting','fase_riavvio','Riavvio in corso'))):
                        self._count_external_ocr(key, at=phase_ocr_result.at,
                            matched=phase_ocr_result.cue == cue,
                            text=phase_ocr_result.text[:120], label=label,
                            count_scan=i==0)
                    quick_ocr_fault = False
                    quick_ocr_last_ok = now
                if overlay.state != last_phase_ocr_label:
                    self._emit('phase_ocr', overlay.detail,
                               info={'ocr_phase': overlay.state,
                                     'ocr_text': (phase_ocr_result.text[:180] if phase_ocr_result else '')})
                    last_phase_ocr_label = overlay.state
                if overlay.recovered:
                    if reading.scene == 'match':
                        reboot_reentry_armed = True
                    self._emit('phase_ocr', 'Testo di attesa scomparso su più letture; '
                               'verifica HUD e ripresa del controllo prima di muoversi.')
                if overlay.hold:
                    # Highest priority: a teammate HUD can look identical to the
                    # player's HUD. A positive spectator/reboot overlay vetoes it.
                    if timed is not None and timed.stage != 'idle':
                        timed.stop_for_menu()
                    reading = replace(reading, scene='spectator', phase_hint=None,
                                      phase_source='OCR spettatore/riavvio')
                elif reading.scene in ('lobby', 'results'):
                    reboot_reentry_armed = False
                # One-shot early jump window: fresh health-panel frames only.
                # This never promotes a scene to "match" and never re-arms after ground.
                if timed is not None and timed.observe_health(
                        (reading.hud_scores or {}).get('hud_salute'),
                        scene=reading.scene, frame_at=captured_at):
                    self._emit('timed', 'Salute confermata su più fotogrammi: '
                               'inizio anticipato della sequenza Spazio (bus NON verificato). '
                               'Timer di caricamento sostituito; finestra salto limitata.',
                               info={'health_score': round((reading.hud_scores or {})['hud_salute'], 3),
                                     'jump_window_seconds': timed.health_jump_window_seconds})
                # OCR for a reward CTA is performed on a separate thread. It is
                # never used for bus/ground authorization, and text alone is not
                # sufficient: same screen location must recur in two OCR frames.
                reward_overlay_confirmed = False
                reward_overlay_pending = False
                if self.reward_ocr is not None:
                    if now - last_reward_ocr >= 2.0:
                        if self.reward_ocr.submit(frame, win.left, win.top, captured_at,
                                                  single.stage if single else ''):
                            last_reward_ocr = now
                    reward = self.reward_ocr.take()
                    if reward is not None:
                        if reward.status == 'ok' and 0 <= now - reward.at <= 5.0:
                            # Include the existing results/rewards OCR in the
                            # same counters used by the configurable ROI reader.
                            for i, (key, label, found) in enumerate((
                                ('reward_title','Ricompense ottenute',reward.reward_screen),
                                ('riscatta','Riscatta',reward.name=='riscatta' and reward.center is not None and reward.reward_screen),
                                ('continua','Continua',reward.name=='continua' and reward.center is not None),
                                ('torna_lobby','Torna alla lobby',reward.name=='torna_lobby' and reward.center is not None),
                                ('gioca_di_nuovo','Gioca di nuovo',reward.name=='gioca_di_nuovo' and reward.center is not None))):
                                self._count_external_ocr(key, at=reward.at,
                                    matched=bool(found), text=label if found else '',
                                    confidence=reward.score if reward.center is not None else float(bool(found)),
                                    label=label, count_scan=i==0)
                            reward_ocr_ok_times.append(now)
                        # Count only distinct fresh OCR captures, never dozens
                        # of game frames reusing a cached OCR result.
                        if reward.status == 'ok' and 0 <= now - reward.at <= 5.0 and reward.reward_screen:
                            reward_overlay_suspect_at = now
                            reward_overlay_streak += 1
                            reward_overlay_checked_at = now
                        else:
                            reward_overlay_streak = 0
                            reward_overlay_checked_at = -1e9
                            reward_overlay_suspect_at = -1e9
                        same = (reward.center is not None and reward_last is not None and
                                reward.name == reward_last[0] and reward.learned == reward_last[3] and
                                0 <= now - reward.at < 5.0 and
                                abs(reward.center[0] - reward_last[1][0]) <= 48 and
                                abs(reward.center[1] - reward_last[1][1]) <= 48)
                        if same:
                            reward_streak += 1
                        elif reward.center is not None:
                            reward_streak = 1
                            reward_verified = None
                            reward_verified_learned = False
                        else:
                            reward_streak = 0
                            reward_verified = None
                            reward_verified_learned = False
                        if reward.center is None:
                            reward_verified_overlay = False
                        elif same:
                            reward_verified_overlay = bool(reward.reward_screen and reward_last[2])
                        else:
                            reward_verified_overlay = False
                        reward_last = (reward.name, reward.center, reward.reward_screen, reward.learned) if reward.center is not None else None
                        required = 2 if reward.name == 'riscatta' else 3
                        if reward.center is not None and reward_streak >= required:
                            reward_verified = Match(reward.name, reward.score, reward.center)
                            reward_verified_at = now
                            reward_verified_source_at = reward.at
                            reward_verified_learned = bool(reward.learned and reward_last and reward_last[3])
                    reward_overlay_confirmed = (
                        reward_overlay_streak >= 2 and now - reward_overlay_checked_at <= 4.5)
                    # User-taught title and CTA are separate OCR readers. Only
                    # their own stable positive hits are merged into the reward
                    # confirmation pipeline. A lone RISCATTA is never enough.
                    if 'reward_title' in stable_region:
                        reward_overlay_confirmed = True
                    if reward_overlay_confirmed and 'riscatta' in stable_region:
                        r=stable_region['riscatta']
                        reward_verified=Match('riscatta',r['score'],
                            (r['center'][0]+win.left,r['center'][1]+win.top))
                        reward_verified_at=now
                        reward_verified_source_at=regional['at'] if regional is not None else captured_at
                        reward_verified_learned=True
                        reward_verified_overlay=True
                    reward_overlay_pending = (
                        now - reward_overlay_suspect_at <= 5.5 and not reward_overlay_confirmed)
                    # Even during gameplay, a verified reward title has precedence
                    # over all movement decisions; no need to see PLAY first.
                    if (reward_overlay_confirmed and single is not None
                            and single.stage in ('joining','jumping','exploring','ending')
                            and not overlay.hold and reading.scene in ('unknown','match','results','lobby')):
                        reading = replace(reading, scene='results')
                    # A confirmed reward TITLE vetoes PLAY even when the button
                    # is missing: the user may need to show RISCATTA first.
                    if (reward_overlay_confirmed and single is not None
                            and single.stage in ('await_play','post_lobby','claim_rewards')
                            and not overlay.hold and reading.scene in ('unknown','lobby','results')):
                        reading = replace(reading, scene='results',
                            best={k:v for k,v in reading.best.items() if k not in ('gioca','gioca_di_nuovo')})
                    if reward_verified is not None and now - reward_verified_at <= 3.6:
                        name = reward_verified.name
                        if name == 'riscatta':
                            # Never click a generic RISCATTA (e.g. in shop).
                            # A distinct, recently confirmed reward title is
                            # mandatory for the post-lobby claim.
                            permit = bool(reward_overlay_confirmed and not overlay.hold and
                                ((single is not None and single.stage in
                                  ('post_lobby','claim_rewards','await_play')) or
                                 (timed is not None and reading.scene == 'results')))
                        else:
                            # Return/Continue are common UI words: require a
                            # separately recognized RESULTS state. OCR alone
                            # cannot label a screenshot as end-of-match.
                            permit = (reading.scene == 'results' or
                                      (reward_verified_learned and single is not None and
                                       single.has_entered and single.stage in ('jumping','exploring','ending') and
                                       reading.scene == 'unknown' and not authorized and
                                       not overlay.hold))
                        if name == 'gioca':
                            permit = bool(reward_verified_learned and single is not None and
                                single.stage in ('await_play', 'joining', 'post_lobby', 'claim_rewards') and
                                reading.scene in ('unknown', 'lobby') and not overlay.hold and
                                not reward_overlay_confirmed)
                        if permit and name not in reading.best:
                            reading = replace(reading,
                                scene='lobby' if name == 'gioca' else 'results',
                                best={**reading.best, name: reward_verified})
                            menu_evidence_at[name]=reward_verified_source_at
                # Without two recent *successful* reward OCR acquisitions, a
                # lobby cannot silently bypass the post-match claim check.
                reward_scan_confirmed = ((len(reward_ocr_ok_times) >= 2 and
                    now - reward_ocr_ok_times[-1] <= 6.0) or
                    (len(region_reward_scans) >= 2 and now-region_reward_scans[-1] <= 6.0))
                if single is not None and single.stage == 'post_lobby' and single.post_lobby_at is not None:
                    reward_scan_confirmed = reward_scan_confirmed and (
                        (sum(1 for scanned in reward_ocr_ok_times if scanned >= single.post_lobby_at) >= 2 or
                         sum(1 for scanned in region_reward_scans if scanned >= single.post_lobby_at) >= 2))
                # Aerial prompts are CONTEXT, not an authorization to move.
                # Template evidence is preferred; fresh OCR merely explains
                # an otherwise unknown frame and cannot trigger input.
                phase_hint = reading.phase_hint
                if (phase_hint is None and reading.scene == 'unknown' and
                        time.monotonic() - self._last_ocr_at < 5.0):
                    phase_hint = recognize_aerial_text(self._last_ocr_text)
                if timed is not None and timed.observe_descent(
                        phase=(reading.phase_hint if reading.phase_source in ('template calibrato','scanner confermato') else None),
                        frame_at=captured_at):
                    self._emit('timed', 'Discesa confermata dal riferimento visivo calibrato: '
                               'interrompo i tentativi Spazio e passo all’attesa atterraggio stimata.')
                if phase_hint != last_phase_marker:
                    if phase_hint:
                        label = ('BUS DELLA BATTAGLIA' if phase_hint == 'battle_bus' else 'DISCESA / DELTAPLANO')
                        self._emit('game_phase', label + ': fase probabile; '
                                   'la routine semplice verifica separatamente il prompt SALTA',
                                   info={'phase_hint': phase_hint})
                    last_phase_marker = phase_hint
                motion = motion_between(before_action, frame) if before_action is not None else None
                evaluated_action = motion_action_name if before_action is not None else None
                if evaluated_action is not None and evaluated_action in ('turn_left', 'turn_right'):
                    camera_verifier.observe(camera_turn_between(before_action, frame), isolated=True)
                    last_camera_feedback = camera_verifier.detail
                    self._emit('camera_feedback', last_camera_feedback,
                        info={'camera_status': camera_verifier.status,
                              'camera_confirmed': camera_verifier.good,
                              'camera_failed': camera_verifier.failed})
                    if camera_verifier.status == 'attention' and last_camera_alert_status != 'attention':
                        self._emit('camera_warning', 'Telecamera: due rotazioni isolate senza risposta visiva. '
                                   'Verifica Fortnite in primo piano e i controlli del mouse; niente input alternativo invasivo.')
                    last_camera_alert_status = camera_verifier.status
                elif evaluated_action is not None and evaluated_action.startswith(('patrol_look_', 'patrol_weave_')):
                    # With W/strafe held, optical flow cannot isolate camera yaw.
                    camera_verifier.observe(None, isolated=False)
                    if camera_verifier.good == 0:
                        last_camera_feedback = camera_verifier.detail
                if evaluated_action is not None and evaluated_action not in ('turn_left', 'turn_right'):
                    outcome = evaluate(evaluated_action, motion,
                                       threshold=float(self.cfg['guided_motion_threshold']))
                    last_visual_feedback = outcome.detail
                    # The displayed status is intentionally NOT "W funzionante":
                    # pixels alone cannot prove game input acceptance.
                    if outcome.status == 'low_visual':
                        self._emit('movement_feedback', outcome.detail,
                                   info={'action': outcome.action, 'pixels': outcome.pixels,
                                         'status': outcome.status})
                before_action = None
                motion_action_name = None
                heard = self.audio.consume_event() if self.audio else False
                obs = Observation(True, reading.scene, reading.menu, motion,
                                  heard, reading.chest, reading.prompt)
                decision = brain.decide(obs, now)
                # Exploration is read-only; this flag only allows recording
                # approved *human* controls while the target game is focused.
                # HUD uncertainty must not suppress the input history.
                if self.exploration and self.demonstration_worker is not None:
                    self._demo_allowed.set()
                else:
                    self._demo_allowed.clear()
                if self.exploration and self.demonstration_worker is not None:
                    if self._demo_allowed.is_set():
                        reason = ('registrazione ATTIVA; HUD incerto: esempi da confermare in revisione'
                                  if reading.scene == 'unknown' else
                                  'registrazione ATTIVA; scena: ' + reading.scene)
                    else:
                        reason = 'in attesa: finestra non disponibile'
                    if now - last_demo_report >= 2.5 or (reason != demo_status and 'BLOCCATO' in reason):
                        self._emit('demo_status',
                            f'Dimostrazioni: {self.demonstration_worker.saved} immagini+azioni, '
                            f'{self.demonstration_worker.transitions} cambi di comando; '
                            f'{self.demonstration_worker.errors} errori; {reason}')
                        last_demo_report = now
                        demo_status = reason
                if (self.autonomous and reading.scene == 'results' and
                        brain.scene_streak >= 3 and now - last_idle_report >= 6):
                    if reading.menu is None:
                        why = 'Risultati/ricompense: nessun pulsante cliccabile riconosciuto. Usa «Fammelo vedere» e mostrami Riscatta, Continua o Torna alla lobby.'
                    elif brain.spectator_latched:
                        why = 'Risultati, ma blocco spettatore ancora attivo. In squadre serve CONTINUA/RISCATTA; in Solo attiva modalità Solo.'
                    elif not self.requeue:
                        why = 'Click menu automatici non abilitati: attiva per uscire dai risultati.'
                    else:
                        why = f'Pulsante {reading.menu.name} visibile; {decision.reason}'
                    self._emit('menu_status', why, info={'menu_target': reading.menu.name if reading.menu else 'non trovato'})
                    last_idle_report = now
                if self.exploration:
                    # The observer may classify scenes, but it has no authority
                    # to pilot the game, even with an injected/custom driver.
                    decision = Action('wait', reason='esplorazione passiva')
                if reading.scene == 'match' and brain.scene_streak >= 3 and not brain.spectator_latched:
                    had_match = True
                if self.autonomous:
                    # Calibrated HUD and latch are authoritative, not the model.
                    authorized = (reading.scene == 'match' and brain.scene_streak >= 3
                                  and not brain.spectator_latched)
                    if not authorized:
                        if self.planner and agent_was_permitted:
                            self.planner.invalidate()
                        agent_was_permitted = False
                    else:
                        agent_was_permitted = True
                    if had_match and reading.scene in ('results', 'lobby'):
                        finished_streak += 1
                    else:
                        finished_streak = 0
                    if not self.timed and not self.single_match and self.cfg['autonomous_stop_after_match'] and finished_streak >= 4:
                        self._emit('agent', 'Fine partita riconosciuta: sessione autonoma terminata')
                        break
                    if (had_match and reading.scene in ('results', 'lobby') and
                            self.cfg['autonomous_stop_after_match'] and not self.timed and not self.single_match):
                        # Never requeue after the match has ended.
                        decision = Action('wait', reason='fine partita: arresto imminente')
                    if not authorized and not self.timed and not self.single_match and now - last_idle_report >= 12.:
                        hint_label = ('Bus della battaglia' if phase_hint == 'battle_bus'
                                      else 'Discesa' if phase_hint == 'skydiving' else reading.scene)
                        self._emit('agent_status',
                                   f'NESSUN MOVIMENTO: {hint_label}; '
                                   f'HUD stabile={brain.scene_streak}/3; '
                                   f'spettatore={brain.spectator_latched}. '
                                   'Serve un HUD di partita calibrato e stabile per inviare i comandi.')
                        last_idle_report = now
                    if self.guided:
                        # A menu action from Brain remains valid only when
                        # confidently identified/calibrated; the routine owns
                        # bus, fall and walking actions in all other scenes.
                        if single is not None:
                            if authorized and captured_at > last_route_eval + .75:
                                hint = route_hint(frame)
                                single.explorer.observe_route(hint)
                                last_route_eval = captured_at
                            proposed_menu_action = decision
                            learned_hint = None
                            menu_offer = None
                            menu_target = None
                            if (not resume_gate.active and single.stage in ('jumping', 'exploring')
                                    and self.fast_policy is not None
                                    and self.fast_policy.examples >= 3
                                    and (authorized or (self.quick_start and reading.scene == 'unknown'))
                                    and not overlay.hold and not reward_overlay_pending
                                    and not reward_overlay_confirmed
                                    and now - last_fast_action >= float(self.cfg['fast_policy_interval_seconds'])):
                                try:
                                    if learning_store is not None and now - last_policy_refresh >= 5.0:
                                        self.fast_policy.refresh(learning_store)
                                        last_policy_refresh = now
                                    visual_feature = image_features(frame, '', reading.scene)
                                    prediction = self.fast_policy.predict(visual_feature, allow_shoot=False)
                                    last_fast_action = now
                                    if (prediction and prediction.confidence >= float(self.cfg['fast_policy_min_confidence'])):
                                        learned_hint = prediction.action
                                        self._emit('learned_navigation',
                                            f'Dimostrazioni: {learned_hint.name}, confidenza {prediction.confidence:.2f}',
                                            info={'action': learned_hint.name, 'confidence': prediction.confidence,
                                                  'examples': self.fast_policy.examples})
                                except (ValueError, TypeError) as exc:
                                    if now - last_idle_report >= 10:
                                        self._emit('learning_error', 'Esempi non utilizzabili: ' + str(exc)[:120])
                                        last_idle_report = now
                            # All match-menu clicks use one stage-aware confirmation,
                            # send and retry supervisor. Never let legacy Brain
                            # pre-count a click that SingleMatch vetoes.
                            if menu_supervisor is not None and not resume_gate.active:
                                menu_offer = menu_supervisor.offer(
                                    now=now, frame_at=captured_at, stage=single.stage,
                                    scene=reading.scene, matches=reading.best,
                                    repeat=single.repeat, reward_overlay=reward_overlay_confirmed,
                                    reward_scan_confirmed=reward_scan_confirmed,
                                    locked=overlay.hold, spectator=brain.spectator_latched,
                                    evidence_times=menu_evidence_at,
                                    prefer_requeue=self.quick_wait_play_exit)
                                if menu_supervisor.last_completed == 'gioca_di_nuovo':
                                    single.on_requeue_confirmed(now)
                                    self._emit('menu_verified', 'GIOCA DI NUOVO: nuova ricerca confermata, timer partita riarmato')
                                elif menu_supervisor.last_completed is not None:
                                    self._emit('menu_verified',
                                        menu_supervisor.last_completed + ': cambio schermata confermato')
                                if menu_offer.name is not None and menu_offer.action.name == 'menu_click':
                                    menu_target = menu_offer.name
                                if ('clic senza conferma' in menu_offer.detail and
                                        now - last_idle_report >= 8):
                                    self._emit('menu_teach_needed', menu_offer.detail +
                                        '. Verifica la finestra in primo piano e mostra nuovamente il pulsante.')
                                    last_idle_report = now
                            if resume_gate.active:
                                restored = resume_gate.reconcile(single, frame_at=captured_at, now=now,
                                    scene=reading.scene, menu=reading.menu.name if reading.menu else None,
                                    authorized=authorized, locked=overlay.hold,
                                    spectator=brain.spectator_latched,
                                    bus_confirmed=bool(reading.phase_hint == 'battle_bus' and
                                                       reading.phase_source in ('template calibrato','scanner confermato')),
                                    reward_overlay=reward_overlay_confirmed)
                                decision = Action('wait', reason=resume_gate.detail)
                                if restored:
                                    self._emit('checkpoint', resume_gate.detail,
                                        info={'resumed_stage': single.stage,
                                              'matches_completed': single.matches_completed})
                            else:
                                decision = single.decide(
                                    now=now, frame_at=captured_at,
                                    scene=reading.scene,
                                    # Observed menu and authorized click are different: 
                                    # the lifecycle needs to SEE Gioca in lobby even
                                    # when the click supervisor is waiting.
                                    menu=menu_target or ('gioca' if single.stage == 'joining' and
                                        'gioca' in reading.best else
                                        (reading.menu.name if reading.menu else None)),
                                    candidate=(menu_offer.action if menu_target is not None else Action('wait')),
                                    menu_candidate_authorized=menu_target is not None,
                                    authorized=authorized,
                                    locked=overlay.hold, spectator=brain.spectator_latched,
                                    bus_confirmed=bool(reading.phase_hint == 'battle_bus' and reading.phase_source in ('template calibrato','scanner confermato')),
                                    motion_pixels=(motion.pixels if motion and evaluated_action in ('forward','patrol_forward') else None),
                                    motion_reliable=bool(motion and motion.reliable),
                                    feedback_action=evaluated_action or '',
                                    reward_overlay=reward_overlay_confirmed,
                                    reward_scan_confirmed=reward_scan_confirmed,
                                    reward_pending=reward_overlay_pending,
                                    learned_hint=learned_hint,
                                    play_visible=self._play_visibility(reading, now),
                                    play_evidence_at=self._play_evidence_at(reading, captured_at, now),
                                    play_point=(reading.best['gioca'].center if 'gioca' in reading.best else None))
                            # Persist real state changes, not every OCR frame.
                            # The database policy is loaded at startup and
                            # its thresholds are consulted by the controller.
                            if cycle_store and (single.stage != last_cycle_stage or
                                                single.matches_completed != last_cycle_matches):
                                events = {
                                    'post_lobby': 'lobby_confirmed_check_rewards',
                                    'claim_rewards': 'reward_title_confirmed',
                                    'await_play': 'reward_check_finished',
                                    'joining': 'gioca_sent',
                                    'jumping': 'match_started',
                                    'exploring': 'exploration_started',
                                    'ending': 'results_confirmed',
                                    'done': 'cycle_complete',
                                }
                                try:
                                    cycle_store.record(stage=single.stage,
                                        previous_stage=last_cycle_stage,
                                        event=events.get(single.stage, 'phase_transition'),
                                        matches_completed=single.matches_completed,
                                        active=single.stage not in ('done','error'))
                                except Exception as exc:
                                    self._emit('cycle_db_error',
                                        'Cronologia SQLite non aggiornata: ' + str(exc)[:140])
                                last_cycle_stage = single.stage
                                last_cycle_matches = single.matches_completed
                            if (self.quick_wait_play_exit and single.stage == 'joining' and
                                    decision.name == 'wait' and menu_offer is not None and
                                    ('gioca' in reading.best or self._play_visibility(reading, now) is True) and
                                    decision.reason.startswith('GIOCA visibile')):
                                decision = Action('wait', reason='GIOCA: ' + menu_offer.detail)
                            if (self.quick_start and decision.name == 'wait' and
                                    (now - last_quick_wait_report >= 6 or
                                     (decision.reason != last_quick_wait_reason and
                                      now - last_quick_wait_report >= 1.5))):
                                self._emit('quick_wait', decision.reason,
                                    info={'phase': single.stage, 'scene': reading.scene,
                                          'ocr_locked': overlay.hold,
                                          'ocr_available': not quick_ocr_fault and now - quick_ocr_last_ok <= 12})
                                last_quick_wait_report = now
                                last_quick_wait_reason = decision.reason
                            if (single.stage in ('post_lobby','claim_rewards') and
                                    now - last_idle_report >= 8):
                                self._emit('reward',
                                   'Controllo ricompense: ' +
                                   ('cerco RISCATTA, mai EQUIPAGGIA' if single.stage == 'claim_rewards'
                                    else 'attendo 18 s dopo la lobby prima di ripremere Gioca'))
                                last_idle_report = now
                            if (proposed_menu_action.name == 'menu_click' and
                                    decision.name != 'menu_click'):
                                brain.discard_unexecuted_menu_click(proposed_menu_action, reading.menu)
                            if self.repeat_matches and not resume_gate.active and (
                                    now - last_checkpoint_at >= 8. or
                                    single.stage != last_checkpoint_stage or
                                    single.matches_completed != last_checkpoint_matches):
                                try:
                                    save_checkpoint(self.checkpoint_path, stage=single.stage,
                                        matches_completed=single.matches_completed, active=True)
                                    last_checkpoint_at = now
                                    last_checkpoint_stage = single.stage
                                    last_checkpoint_matches = single.matches_completed
                                except OSError as exc:
                                    if now - last_checkpoint_at >= 20.:
                                        self._emit('checkpoint_error', 'Checkpoint non aggiornato: ' + str(exc)[:140])
                                        last_checkpoint_at = now
                            if (single.stage in ('post_lobby','await_play') and
                                    not reward_scan_confirmed and now - last_idle_report >= 18):
                                self._emit('reward_ocr_error',
                                    'Riscatto: mancano due letture OCR recenti. '
                                    'Controlla Tesseract; non premo Gioca senza verificare le ricompense.')
                                last_idle_report = now
                            if (single.stage == 'claim_rewards' and
                                    single.claim_clicked_at is None and
                                    single.post_lobby_at is not None and
                                    now - single.post_lobby_at >= 15 and
                                    (reading.menu is None or reading.menu.name != 'riscatta') and
                                    now - last_idle_report >= 18):
                                self._emit('menu_teach_needed',
                                    'Vedo RICOMPENSA OTTENUTA ma non trovo RISCATTA. '
                                    'Premi Stop, apri Fammelo vedere e mostrami RISCATTA, non EQUIPAGGIA.')
                                last_idle_report = now
                            if (single.stage == 'ending' and reading.menu is None and
                                    single.results_at is not None and now - single.results_at > 25 and
                                    now - last_idle_report >= 18):
                                self._emit('menu_teach_needed',
                                           'Non riconosco il pulsante finale. Ferma Oye e mostrami '
                                           'TORNA ALLA LOBBY, RISCATTA o CONTINUA: imparerò posizione e testo.')
                                last_idle_report = now
                            if single.recovery_count != last_single_recovery_count:
                                last_single_recovery_count = single.recovery_count
                                self._emit('recovery', single.last_error +
                                           '. Il ciclo resta attivo e attende una schermata verificata.',
                                           info={'recoveries': single.recovery_count})
                            if single.stage != last_guided_stage:
                                self._emit('single_match', f'Ciclo partita: {single.label}',
                                           info={'guided_stage': single.stage, 'elapsed': round(single.elapsed, 1),
                                                 'matches_completed': single.matches_completed})
                                last_guided_stage = single.stage
                            if single.stage in ('done', 'error'):
                                self._emit('single_match',
                                           'Lobby confermata: sessione da una partita completata.' if single.stage == 'done'
                                           else single.last_error)
                                break
                        elif self.timed:
                            # Timer mode deliberately does not depend on calibrated HUD.
                            # A strongly confirmed result/spectator/lobby only suspends the
                            # current timer AFTER the estimated gameplay has started.
                            terminal = (brain.scene_streak >= 3 and (
                                (reading.scene == 'results' and reading.menu and
                                 reading.menu.name in ('riscatta', 'continua', 'torna_lobby')) or
                                (timed.stage in ('ground', 'finished') and
                                 reading.scene in ('results', 'lobby', 'spectator'))))
                            if terminal:
                                timed.stop_for_menu()
                                # Keep Brain's calibrated menu click if available.
                            elif (reading.scene in ('results', 'spectator') or
                                  (reading.scene == 'lobby' and timed.stage in ('ground', 'finished'))):
                                # A visible terminal/menu cue stops gameplay immediately,
                                # even while the menu Brain is waiting for confirmations.
                                decision = Action('wait', reason='Schermata menu/spettatore: attesa conferma')
                            elif (decision.name == 'menu_click' and reading.menu is not None and
                                  reading.menu.name == 'gioca' and reading.scene == 'lobby'):
                                # Preserve a confirmed Play/Ready click even if the
                                # startup timer was armed before opening the lobby.
                                pass
                            elif timed.stage != 'idle' or reading.scene not in ('lobby', 'results'):
                                # A genuine reboot restarts the exploration planner,
                                # not the pre-game 90s bus timer. Only after Brain
                                # confirms fresh match HUD and releases the
                                # spectator latch can movement resume.
                                if (reboot_reentry_armed and authorized and timed.stage == 'idle'):
                                    timed.resume_after_reboot(now)
                                    reboot_reentry_armed = False
                                    self._emit('reboot', 'Rientro confermato: breve prova W, '
                                               'poi riprendo esplorazione con verifica visiva.')
                                previous_motion = (evaluated_action in ('forward', 'patrol_forward') and motion is not None)
                                decision = timed.choose(
                                    now, motion_pixels=motion.pixels if previous_motion else None,
                                    motion_reliable=bool(previous_motion and motion.reliable),
                                    feedback_action=evaluated_action or '',
                                    reward_overlay=reward_overlay_confirmed)
                            # If idle, give the menu detector a chance to click Gioca.
                            if timed.stage != last_guided_stage:
                                self._emit('guided_stage', f'Routine a tempo: {timed.stage}',
                                           info={'guided_stage': timed.stage, 'estimated_seconds': round(timed.elapsed, 1)})
                                last_guided_stage = timed.stage
                            if decision.name == 'wait' and now - last_idle_report >= 8:
                                self._emit('agent_status', decision.reason)
                                last_idle_report = now
                        elif reading.scene not in ('lobby', 'results'):
                            # The guided routine may consult the supervised
                            # policy only for navigation hints, never to override
                            # phase transitions or generate combat/menu actions.
                            learned_hint = None
                            if (authorized and self.fast_policy and self.fast_policy.examples >= 3 and
                                    now - last_fast_action >= float(self.cfg['fast_policy_interval_seconds'])):
                                if now - last_policy_refresh > 5:
                                    self.fast_policy.refresh(learning_store)
                                    last_policy_refresh = now
                                feature_started = time.perf_counter()
                                feature = image_features(frame, '', reading.scene)
                                feature_times.append((time.perf_counter() - feature_started) * 1000.)
                                prediction = self.fast_policy.predict(feature, allow_shoot=False)
                                if prediction is not None:
                                    policy_times.append(prediction.latency_ms)
                                    if prediction.confidence >= float(self.cfg['fast_policy_min_confidence']):
                                        learned_hint = prediction.action
                                last_fast_action = now
                            previous_motion = (evaluated_action in ('forward', 'patrol_forward') and motion is not None)
                            decision = guided.choose(
                                scene=reading.scene, phase=phase_hint,
                                phase_source=('template calibrato' if reading.phase_hint == phase_hint
                                              and reading.phase_source in ('template calibrato','scanner confermato')
                                              else 'OCR indiziario'),
                                authorized_ground=authorized,
                                spectator=brain.spectator_latched, now=now,
                                motion_pixels=motion.pixels if previous_motion else None,
                                motion_reliable=bool(previous_motion and motion.reliable),
                                feedback_action=evaluated_action or '',
                                learned_hint=learned_hint)
                            if guided.explorer.last_feedback != 'nessuna prova di movimento' and (
                                    guided.explorer.halted or guided.explorer.recovery_plan):
                                last_visual_feedback = guided.explorer.last_feedback
                            last_guided_command = decision if decision.name != 'wait' else None
                            if guided.stage != last_guided_stage:
                                if guided.stage != 'ground':
                                    last_visual_feedback = 'Attesa fase di gioco a terra'
                                self._emit('guided_stage', f'Routine semplice: {guided.stage}',
                                           info={'guided_stage': guided.stage})
                                last_guided_stage = guided.stage
                            if decision.name == 'wait' and now - last_idle_report >= 5.:
                                self._emit('agent_status', 'Routine semplice in attesa: '
                                           + decision.reason)
                                last_idle_report = now
                    elif reading.scene == 'match':
                        # Never execute the old random/scripted gameplay plan in AI mode.
                        decision = Action('wait', reason='attesa decisione multimodale')
                        if authorized:
                            # The lightweight learner runs on fresh pixels and needs
                            # no LLM request. If uncertain we keep the VLM fallback.
                            fast = None
                            if (self.fast_policy and self.fast_policy.examples >= 3 and
                                    now - last_fast_action >= self.cfg['fast_policy_interval_seconds']):
                                if now - last_policy_refresh > 5.:
                                    self.fast_policy.refresh(learning_store)
                                    last_policy_refresh = now
                                feature_started = time.perf_counter()
                                feature = image_features(frame, '', reading.scene)
                                feature_times.append((time.perf_counter() - feature_started) * 1000)
                                fast = self.fast_policy.predict(feature, allow_shoot=self.shoot)
                                if fast:
                                    policy_times.append(fast.latency_ms)
                                if fast and fast.confidence < self.cfg['fast_policy_min_confidence']:
                                    self.fast_policy.last_reason = (
                                        f'confidenza troppo bassa: {fast.confidence:.2f} < '
                                        f'{self.cfg["fast_policy_min_confidence"]:.2f}')
                                    fast = None
                            answer = self.planner.get_result() if self.planner else None
                            if answer:
                                age = now - answer.submitted_at
                                if answer.error:
                                    self._emit('agent_error', f'Inferenza fallita: {answer.error}')
                                elif age > self.cfg['autonomous_max_stale_seconds'] or last_fast_action > answer.submitted_at:
                                    self._emit('agent', f'Piano superato da nuova osservazione/azione ({age:.1f}s)')
                                elif answer.proposal and fast is None:
                                    plan = answer.proposal
                                    # The model cannot assert a contradicting game state.
                                    if plan.scene and plan.scene not in ('match', 'incerto', 'unknown'):
                                        self._emit('agent', 'Scena incompatibile nel piano: nessun input')
                                    else:
                                        decision = plan.action
                                    self._emit('agent', f'Vedo: {plan.event} · {plan.description} | '
                                               f'azione: {decision.name} ({plan.confidence:.2f})',
                                               info={'agent_event': plan.event, 'agent_description': plan.description,
                                                     'agent_confidence': plan.confidence})
                                    if learning_store:
                                        # Set after run to include whether input really happened.
                                        pending_agent_plan = plan
                                    else:
                                        pending_agent_plan = None
                            if fast is None and self.fast_policy and now - last_idle_report >= 4.:
                                self._emit('agent_status',
                                           f'NESSUN MOVIMENTO: {self.fast_policy.last_reason}; '
                                           f'esempi caricati={self.fast_policy.examples}; '
                                           f'fallback Ollama={"attivo" if self.planner else "spento"}')
                                last_idle_report = now
                            if fast is not None:
                                decision = fast.action
                                last_fast_action = now
                                if now - last_fast_report >= 3.:
                                    self._emit('fast_decision',
                                               f'Policy rapida: {decision.name} '
                                               f'({fast.confidence:.2f}; {fast.latency_ms:.2f} ms; '
                                               f'{fast.support} esempi simili)',
                                               info={'fast_latency_ms': fast.latency_ms,
                                                     'fast_confidence': fast.confidence})
                                    last_fast_report = now
                            if (self.planner is not None and not self.planner.busy and
                                    now - sent_at >= self.cfg['autonomous_decision_interval_seconds'] and
                                    fast is None and decision.name == 'wait'):
                                # Smaller payload, fewer vision tokens, no blocking the
                                # decision thread. Use original resolution for HUD checks.
                                width = min(512, frame.shape[1])
                                height = max(1, round(frame.shape[0] * width / frame.shape[1]))
                                scaled = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
                                ok, encoded = cv2.imencode('.jpg', scaled, [cv2.IMWRITE_JPEG_QUALITY, 65])
                                if ok:
                                    shot = encoded.tobytes()
                                    recent = learning_store.recent(5, session=session_id) if learning_store else []
                                    context = ('Schermata locale: match confermato. Cronologia OCR/ML: ' + '; '.join(
                                        str(item['user_label'] or item['predicted_event'])[:55]
                                        for item in reversed(recent)) + '. Ultimo comando: '
                                        + last_agent_action + '. Memoria dalle correzioni: ' + feedback_context
                                        + '. Manuale utente (indicativo, non supera i controlli input): '
                                        + self.cfg['guided_explore_manual'][:450])
                                    if self.planner.submit(shot, context=context,
                                                           ocr=self._last_ocr_text,
                                                           history_images=list(planner_history)[-1:]):
                                        planner_history.append(shot)
                                        sent_at = now
                    else:
                        # Only template-confirmed menu clicks from Brain are possible here.
                        # With auto_requeue disabled, the system simply observes.
                        if self.planner:
                            planner_history.clear()
                if self.learning_worker and now - last_learning_capture >= self.cfg['capture_seconds']:
                    self.learning_worker.submit(frame, scene=reading.scene,
                                                process=win.executable, session=session_id)
                    last_learning_capture = now
                if self.exploration_worker:
                    self.exploration_worker.submit(frame, session=session_id,
                                                   scene=reading.scene, ocr=self._last_ocr_text)
                if reading.scene != last_scene:
                    if reading.scene != 'match':
                        last_visual_feedback = 'Nessuna nuova misura di locomozione in questa fase'
                        last_driver_feedback = 'Input di locomozione non autorizzati in questa schermata'
                    title = {
                        'spectator': 'SPETTATORE — nessuna azione, attesa compagni / riavvio',
                        'match': 'HUD partita riconosciuto',
                        'results': 'Schermata risultati riconosciuta',
                        'lobby': 'Lobby riconosciuta',
                        'loading': 'Caricamento riconosciuto: nessun input',
                        'unknown': 'Schermata incerta: nessun input',
                    }.get(reading.scene, reading.scene)
                    if reading.scene == 'unknown' and phase_hint:
                        title = ('Probabile BUS DELLA BATTAGLIA' if phase_hint == 'battle_bus'
                                 else 'Probabile DISCESA') + ': verifica marcatori; non è un combattimento'
                    self._emit('state', title)
                    if learning_store is not None and not self.autonomous:
                        learning_store.mark_session(session_id, 'state', title)
                    last_scene = reading.scene
                if was_spectator_latched and not brain.spectator_latched:
                    self._emit('state', 'Uscita dallo stato spettatore confermata: '
                               + ('riavvio riuscito' if reading.scene == 'match' else 'nuovo stato conclusivo'))
                was_spectator_latched = brain.spectator_latched
                if reading.scene == 'unknown' and phase_hint is None and now - self._last_unknown_notice > 30:
                    hud_info = (f'{reading.hud.core_votes}/{reading.hud.core_configured} indizi stabili; '
                                + reading.hud.reason if reading.hud else 'nessun HUD calibrato riconosciuto')
                    self._emit('scene', f'Schermata non identificata ({hud_info}). ' + ('Routine a tempo attiva: HUD solo diagnostico.' if (self.timed or self.quick_start) else 'Nessun input.'))
                    self._last_unknown_notice = now
                unknown_state = unknown_watchdog.observe(now=now, scene=reading.scene,
                    live=self.live, timed=(self.timed or self.quick_start), repeat=self.repeat_matches)
                if unknown_state == 'stop':
                    self._emit('limit', '180 secondi senza riconoscimento: arresto preventivo')
                    break
                if unknown_state == 'recover':
                    self._emit('recovery', 'Schermata sconosciuta da 180 secondi: continuo a osservare senza input. '
                               'Controlla la calibrazione se non torna leggibile.')
                if brain.alert and brain.alert != self._last_alert:
                    self._emit('alert', brain.alert)
                    self._last_alert = brain.alert
                if heard:
                    self._emit('audio', 'Possibile suono cassa (direzione NON determinata)')
                if reading.hud is not None and now - last_hud_report >= 8.0:
                    self._emit('hud', f'HUD stabili: {reading.hud.core_votes}/{reading.hud.core_configured} (totali {reading.hud.votes}/{reading.hud.configured}) '
                               f'(confidenza {reading.hud.confidence:.2f}); '
                               'riconosciuti: ' + (', '.join(reading.hud.matched) or 'nessuno') + '; ' + reading.hud.reason,
                               info={'scores': {k: round(v, 3) for k, v in (reading.hud_scores or {}).items()}})
                    last_hud_report = now
                if now - last_preview >= 1.0:
                    if not self.live:
                        # Full-resolution frame for 'Fammelo vedere' while
                        # the human is playing in observation-only mode.
                        with self._calibration_lock:
                            self._calibration_frame = frame.copy()
                            self._calibration_at = captured_at
                    # Diagnostic-only preview: keep the game's aspect ratio and
                    # refresh at most once per second to bound GUI/JPEG costs.
                    h, w = frame.shape[:2]
                    scale = min(1.0, 820.0 / w, 460.0 / h)
                    thumb = cv2.resize(frame, (max(1, round(w * scale)), max(1, round(h * scale))),
                                       interpolation=cv2.INTER_AREA)
                    ok, encoded = cv2.imencode('.jpg', thumb, [cv2.IMWRITE_JPEG_QUALITY, 78])
                    if ok:
                        # Preview is a RAM-only diagnostic. The GUI must render
                        # the SAME frame evaluated by Vision, never a disk image.
                        self._emit('preview', '', info={
                            'jpeg': encoded.tobytes(), 'scene': reading.scene,
                            'ocr_phase': overlay.state, 'ocr_phase_locked': overlay.hold,
                            'frame_count': live_frames_received,
                            'capture_age_ms': round(max(0., (time.monotonic() - captured_at) * 1000.), 1),
                            'hud_scores': {k: round(v, 3) for k, v in (reading.hud_scores or {}).items()},
                            'hud_matched': list(reading.hud.matched) if reading.hud else [],
                            'hud_votes': reading.hud.votes if reading.hud else 0,
                            'hud_configured': reading.hud.configured if reading.hud else 0,
                            'hud_core_votes': reading.hud.core_votes if reading.hud else 0,
                            'hud_core_configured': reading.hud.core_configured if reading.hud else 0,
                            'hud_reason': reading.hud.reason if reading.hud else '',
                            'hud_confidence': reading.hud.confidence if reading.hud else 0.,
                        })
                    last_preview = now
                # No independent menu-click implementations inside the match:
                # only supervisor-authorized coordinates may reach the driver.
                if single is not None and decision.name == 'menu_click' and (
                        menu_target is None or menu_offer is None or
                        menu_offer.action.name != 'menu_click' or
                        menu_offer.action.point != decision.point):
                    self._emit('menu_blocked','Clic menu rifiutato: nessuna transazione verificata')
                    decision=Action('wait',reason='Menu: attendo riconoscimento e conferme')
                executed = False
                if decision.name != 'wait':
                    self._emit('decision', f'{decision.name}: {decision.reason}', info={
                        'scene': reading.scene, 'confidence': round(reading.menu.score, 3) if reading.menu else None,
                        'motion_px': round(motion.pixels, 2) if motion and motion.reliable else None,
                    })
                    # Save the image BEFORE actual displacement, measure on next screenshot.
                    if self.live and (decision.is_motion or decision.name in ('turn_left', 'turn_right')):
                        before_action = frame.copy()
                        motion_action_name = decision.name
                    driver_started = time.perf_counter()
                    executed = driver.run(decision)
                    driver_times.append((time.perf_counter() - driver_started) * 1000.)
                    motion_action_ended_at = time.monotonic()
                    last_driver_feedback = ('Driver: comando inviato al sistema; ricezione nel gioco da verificare'
                                            if executed else 'Driver: comando non inviato')
                    if not executed:
                        before_action = None
                        motion_action_name = None
                    if self.autonomous and now - last_input_report >= 3.:
                        self._emit('input_status',
                                   f'Comando {decision.name}: '
                                   + ('inviato dal driver (ricezione nel gioco da verificare)' if executed
                                      else 'NON inviato dal driver'))
                        last_input_report = now
                    if executed:
                        if single is not None:
                            old_stage = single.stage
                            menu_name = (menu_target if 'menu_target' in locals() else None)
                            if decision.name == 'menu_click' and menu_name is not None and menu_supervisor is not None:
                                menu_supervisor.on_executed(menu_name, decision,
                                    now=time.monotonic(), scene=reading.scene)
                                self._emit('menu_sent', menu_supervisor.last_event,
                                    info={'menu_target':menu_name, 'verified':False})
                            single.on_executed(decision, menu_name, time.monotonic())
                            if cycle_store is not None:
                                if single.stage != old_stage:
                                    try:
                                        cycle_store.record(stage=single.stage,
                                            previous_stage=old_stage, event='gioca_sent',
                                            matches_completed=single.matches_completed)
                                        last_cycle_stage = single.stage
                                    except Exception as exc:
                                        self._emit('cycle_db_error', str(exc)[:140])
                                elif menu_name == 'riscatta' and decision.name == 'menu_click':
                                    try:
                                        cycle_store.event(stage=single.stage, event='riscatta_clicked',
                                            matches_completed=single.matches_completed)
                                    except Exception as exc:
                                        self._emit('cycle_db_error', str(exc)[:140])
                        if timed is not None and decision.name == 'menu_click' and reading.menu and reading.menu.name == 'gioca':
                            timed.arm(time.monotonic())
                            self._emit('timed', 'Clic GIOCA inviato: cronometro azzerato; caricamento stimato '
                                       + str(self.cfg['guided_timed_load_seconds']) + ' s.')
                        actions_count += 1
                        if self.autonomous and reading.scene == 'match':
                            last_agent_action = decision.name
                if self.autonomous and 'pending_agent_plan' in locals() and pending_agent_plan is not None:
                    plan = pending_agent_plan
                    if learning_store:
                        learning_store.record_agent_decision(
                            session=session_id, action=plan.action.name, executed=executed,
                            confidence=plan.confidence, event=plan.event,
                            description=plan.description, reasoning=plan.reasoning,
                            message='input autorizzato' if executed else 'attesa o rifiutato')
                    pending_agent_plan = None
                if self.autonomous and now - last_telemetry_report >= .8:
                    estimate = phase_estimator.observe(
                        reading.scene, phase_hint,
                        'template calibrato' if reading.phase_hint == phase_hint and
                        reading.phase_source in ('template calibrato','scanner confermato') else 'OCR indiziario')
                    phase_label = single.label if single is not None else guided.stage if guided else estimate.confirmed
                    explorer_live = (single.explorer if single is not None else timed.explorer if timed is not None else
                                     guided.explorer if guided is not None else None)
                    activity = (explorer_live.planner.activity if explorer_live is not None
                                and (single.stage in ('jumping', 'exploring') if single is not None else
                                     timed.stage == 'ground' if timed is not None else
                                     guided.stage == 'ground') else '—')
                    objective = ((f'Salto e movimento · {max(0, int(single.jump_window_seconds-single.elapsed))} s' if single.stage == 'jumping' else
                                  'Esplorazione fino alla fine partita' if single.stage == 'exploring' else
                                  'Riscatta le ricompense / ritorna alla lobby' if single.stage == 'ending' else
                                  'Attendo ingresso verificato') if single is not None else
                                 'esplorazione e piccone' if self.guided and (
                                     timed.stage == 'ground' if timed is not None else guided.stage == 'ground')
                                 else 'entrata / salto / discesa' if self.guided
                                 else 'decisione ML / vision')
                    self._emit('telemetry', '', info={
                        'scene': reading.scene,
                        'ocr_phase': overlay.state,
                        'ocr_phase_locked': overlay.hold,
                        'capture': f'{live_frames_received} frame ricevuti · ultimo {max(0., (time.monotonic() - captured_at) * 1000.):.0f} ms fa',
                        'hud': (f'{reading.hud.votes}/{reading.hud.configured} riconosciuti'
                                if reading.hud is not None else 'nessun riferimento HUD'),
                        'phase': single.label if single is not None else timed.stage if timed is not None else phase_label,
                        'matches_completed': single.matches_completed if single is not None else 0,
                        'phase_detail': (('Avvio rapido a tempo: stato di gioco NON verificato' if self.quick_start else
                                          'Ciclo automatico: verifiche visive attive' if single.repeat else
                                          'Ciclo singolo: verifiche visive attive') if single is not None else 'Fase stimata dai tempi, non verificata visivamente'
                                         if timed is not None else estimate.detail),
                        'objective': objective,
                        'activity': activity,
                        'behavior_profile': self.cfg['guided_activity_profile'] if self.guided else '—',
                        'action': decision.name,
                        'reason': decision.reason,
                        'authorized': bool(authorized or (single is not None and self.quick_start and
                                           single.stage in ('jumping', 'exploring') and not overlay.hold and
                                           reading.scene not in ('spectator', 'results', 'lobby')) or
                                           (timed is not None and timed.stage in ('jumping', 'ground'))),
                        'input_status': last_driver_feedback,
                        'visual_feedback': last_visual_feedback,
                        'camera_feedback': last_camera_feedback,
                        'camera_status': camera_verifier.status,
                        'route_hint': (single.explorer.route_side if single else None),
                        'resume_pending': resume_gate.active,
                        'recovery_detail': (single.explorer.last_feedback if single else ''),
                        'recoveries': (single.explorer.recovery_count if single else guided.explorer.recovery_count if guided else 0),
                        'halted': bool(single.explorer.halted if single else guided.explorer.halted) if guided else False,
                    })
                    last_telemetry_report = now
                if self.autonomous and now - last_speed_report >= 10. and len(capture_times) >= 2:
                    # These are measured component times, NOT end-to-end input lag.
                    def average(items):
                        return round(sum(items) / len(items), 2) if items else None
                    metrics = {'capture_ms': average(capture_times),
                               'vision_ms': average(vision_times),
                               'features_ms': average(feature_times),
                               'policy_ms': average(policy_times),
                               'frame_age_ms': average(frame_ages),
                               'driver_ms': average(driver_times),
                               'dropped_capture_frames': (self.frame_stream.skipped if self.frame_stream else 0),
                               'scan_interval_seconds': self.cfg['scan_seconds']}
                    self._emit('performance', 'Pipeline: cattura {capture_ms} ms, età frame {frame_age_ms} ms, visione '
                               '{vision_ms} ms, feature {features_ms} ms, ML {policy_ms} ms, comandi {driver_ms} ms'.format(**metrics),
                               info=metrics)
                    last_speed_report = now
                # Treat scan_seconds as start-to-start cadence, not extra idle
                # time AFTER an action. This removes accumulated driver/vision
                # latency without permitting overlapping commands.
                # In menus/results, a slower scan reduces CPU while inputs
                # remain forbidden unless the calibrated scene authorizes them.
                # During a match the full low-latency cadence is retained.
                scan_period = float(self.cfg['scan_seconds'])
                if reading.scene in ('loading', 'lobby', 'results', 'spectator', 'unknown'):
                    scan_period = max(scan_period, .33)
                sleep_for = max(0.0, scan_period - (time.monotonic() - now))
                if self.learning_worker:
                    until_capture = last_learning_capture + self.cfg['capture_seconds'] - time.monotonic()
                    if until_capture > 0:
                        sleep_for = min(sleep_for, until_capture)
                self.stop_event.wait(max(.02, sleep_for))
                if self.audio and self.audio.error:
                    self._emit('audio', f'Errore audio: {self.audio.error}')
                    self.audio.stop()
                    self.audio = None
        except Exception as exc:
            self._emit('error', f'{type(exc).__name__}: {exc}')
        finally:
            if cycle_store is not None and single is not None:
                try:
                    cycle_store.record(stage=single.stage,previous_stage=single.stage,
                        event='session_stopped' if self._manual_stop else 'process_exited',
                        matches_completed=single.matches_completed,active=False)
                except Exception as exc:
                    self._emit('cycle_db_error','Chiusura SQLite: '+str(exc)[:140])
            if self.repeat_matches and single is not None:
                try:
                    save_checkpoint(self.checkpoint_path, stage=single.stage,
                                    matches_completed=single.matches_completed,
                                    active=not self._manual_stop and single.stage not in ('done', 'error'))
                except OSError as exc:
                    self._emit('checkpoint_error', 'Ultimo checkpoint non salvato: ' + str(exc)[:140])
            # Shut down capture before releasing other resources. The grabber
            # closes its native handles inside its own worker thread.
            if self.region_ocr:
                self.region_ocr.stop()
            if self.reward_ocr:
                self.reward_ocr.stop()
            if self.phase_ocr:
                self.phase_ocr.stop(timeout=1.5)
            if self.frame_stream:
                self.frame_stream.stop(timeout=2.)
            close_capture()
            self.stop_event.set()
            self._demo_allowed.clear()
            if self.demonstration_worker:
                self.demonstration_worker.stop(timeout=.7)
            if driver:
                try:
                    driver.release()
                except Exception as exc:
                    self._emit('error', f'Rilascio input: {exc}')
            if self.audio:
                self.audio.stop()
            if self.planner:
                self.planner.stop(timeout=.5)
            if self.exploration_worker:
                self.exploration_worker.stop(timeout=.5)
            if self.learning_worker:
                self.learning_worker.stop(timeout=6.0)
                if self.learning_worker.dropped:
                    self._emit('learning', f'Analisi OCR saltate per coda lenta: {self.learning_worker.dropped}; immagini già salvate dove possibile')
                if self.learning_worker.dropped_archive:
                    self._emit('learning_error', f'Screenshot non archiviati per I/O lento: {self.learning_worker.dropped_archive}')
            if learning_store:
                try:
                    if not self.autonomous:
                        learning_store.mark_session(session_id, 'end', 'Osservazione terminata')
                    folder = self.root / 'reports'
                    folder.mkdir(exist_ok=True)
                    report = folder / f'partita_{session_id}.txt'
                    report.write_text(learning_store.session_summary(session_id), encoding='utf-8')
                    self._emit('report', f'Cronologia della partita salvata in {report.relative_to(self.root)}')
                except Exception as exc:
                    self._emit('learning_error', 'Resoconto non salvato: ' + str(exc)[:160])
            self._emit('lifecycle', 'Sessione terminata: input rilasciati')
