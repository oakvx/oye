"""Windowed calibration/diagnostics studio; no automatic inputs unless explicitly enabled.

Launch with py studio.py. Source code and local captures remain under this folder.
"""
from __future__ import annotations
import json
import io
import os
import subprocess
import queue
import re
import threading
from collections import deque
from pathlib import Path
import sys
import time
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from PIL import Image, ImageTk
import cv2
import numpy as np
from observer.config import TEMPLATE_NAMES, load
from observer.behavior import activity_preview
from observer.hud import HUD_ANCHORS, HUD_CORE
from observer.ui_theme import (BG, SURFACE, BORDER, WHITE, MUTED, ACCENT,
                               install_dark_theme, decorate_legacy_widgets,
                               RoundButton, RoundedCard)
from observer.vision import Vision
from observer.windows import foreground_window, snapshot, enable_dpi_awareness
from observer.runner import Runner
from observer.learning import LearningStore, LearningEngine, SCENE_EVENTS
from observer.autopilot import LIMITS
from observer.temporal import infer_states, inferred_spans, NAMES, NEXT
from observer.activities import infer_activities, activity_spans
from observer.review_player import FrameIndex, ReviewImageLoader, TimeLapseClock
from observer.readiness import inspect_training
from observer.timeline import (event_spans, review_state, effective_event,
                               display_time, display_elapsed, session_window,
                               elapsed_seconds, parse_time, timecode, parse_timecode, sampled_in_range)

ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
LABELS = {
    'gioca': 'Lobby • GIOCA (giallo, basso sinistra)',
    'lobby_indizio': 'Lobby • Indizio statico facoltativo (non cliccato)',
    'continua': 'Fine partita • CONTINUA (basso destra)',
    'riscatta': 'RICOMPENSE • RISCATTA (facoltativo, solo pulsante)',
    'torna_lobby': 'Fine partita • TORNA ALLA LOBBY',
    'gioca_di_nuovo': 'Facoltativo • GIOCA DI NUOVO (solo rilevato)',
    'caricamento': 'Schermata • CARICAMENTO (facoltativo)',
    'hud_in_partita': 'Legacy • Vecchio HUD singolo (non consigliato)',
    'eliminato': 'Schermata • ELIMINATO (facoltativo)',
    'spettatore_prossimo': 'SPETTATORE • PROSSIMO GIOCATORE',
    'spettatore_precedente': 'SPETTATORE • GIOCATORE PRECEDENTE',
    'interazione': 'Partita • Prompt interazione (facoltativo)',
    'cassa_visiva': 'Partita • Indicatore cassa (facoltativo)',
    'bus_salto': 'BUS • Prompt SALTA (facoltativo in modalità a tempo)',
    'discesa_apri': 'VOLO • Prompt APRI DELTAPLANO (solo riconoscimento)',
    'hud_minimappa': 'HUD • Cornice MINIMAPPA (non il contenuto)',
    'hud_salute': 'HUD • Cornici SALUTE / SCUDO (non i valori)',
    'hud_inventario': 'HUD • Cornici SLOT INVENTARIO (non le armi)',
    'hud_bussola': 'HUD • BUSSOLA (dinamico e facoltativo)',
    'hud_squadra': 'HUD • SQUADRA (assente in singolo: non calibrare)',
    'hud_mirino': 'HUD • MIRINO (assente in volo: facoltativo)',
}
REQUIRED = ('gioca', 'continua', 'torna_lobby',
            'spettatore_prossimo', 'spettatore_precedente')


class Studio:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title('Oye · Observer Studio 22 · OCR Reboot Guard')
        root.geometry('1320x920')
        root.minsize(1010, 735)
        install_dark_theme(root)
        self.cfg = load(ROOT / 'config.json')
        self.runner: Runner | None = None
        self.listener = None
        self._keys_down = set()
        self.frame: np.ndarray | None = None
        self.live_preview_photo = None  # Preview autonoma solo in RAM
        self._last_home_frame_at = None
        self.photo = None
        self.display_scale = 1.0
        self.preview_offset = (0, 0)
        self.selection: tuple[int, int, int, int] | None = None
        self.anchor = None
        self.rect_id = None
        self.label_var = tk.StringVar(value='gioca')
        self.live_var = tk.BooleanVar(value=False)
        self.auto_var = tk.BooleanVar(value=False)
        self.shoot_var = tk.BooleanVar(value=False)
        self.backend_var = tk.StringVar(value=self.cfg['input_backend'])
        self.status_var = tk.StringVar(value='Pronto. Puoi osservare anche senza calibrazione.')
        self.capture_seconds_var = tk.DoubleVar(value=self.cfg['capture_seconds'])
        self.ocr_enabled_var = tk.BooleanVar(value=self.cfg['ocr_enabled'])
        self.ollama_enabled_var = tk.BooleanVar(value=self.cfg['ollama_enabled'])
        # LIVE/autopilot must be deliberately armed on every launch.
        self.autonomous_var = tk.BooleanVar(value=False)
        self.guided_var = tk.BooleanVar(value=False)
        self.guided_harvest_var = tk.BooleanVar(value=self.cfg['guided_explore_harvest'])
        self.guided_smooth_var = tk.BooleanVar(value=self.cfg['guided_smooth_enabled'])
        self.guided_profile_var = tk.StringVar(value=self.cfg['guided_activity_profile'])
        self.guided_solo_var = tk.BooleanVar(value=self.cfg['guided_solo_mode'])
        self.guided_camera_var = tk.IntVar(value=int(self.cfg['guided_camera_pixels']))
        self.guided_jump_period_var = tk.DoubleVar(value=self.cfg['guided_jump_interval_seconds'])
        self.guided_timed_var = tk.BooleanVar(value=self.cfg['guided_timed_enabled'])
        self.guided_start_now_var = tk.BooleanVar(value=self.cfg['guided_timed_start_on_launch'])
        self.guided_loading_var = tk.DoubleVar(value=self.cfg['guided_timed_load_seconds'])
        self.guided_jump_window_var = tk.DoubleVar(value=self.cfg['guided_timed_jump_window_seconds'])
        self.guided_fall_var = tk.DoubleVar(value=self.cfg['guided_timed_fall_seconds'])
        self.guided_ground_var = tk.DoubleVar(value=self.cfg['guided_timed_ground_seconds'])
        self.guided_health_jump_var = tk.BooleanVar(value=self.cfg['guided_health_jump_enabled'])
        self.guided_health_threshold_var = tk.DoubleVar(value=self.cfg['guided_health_threshold'])
        self.guided_health_wait_var = tk.DoubleVar(value=self.cfg['guided_health_min_wait_seconds'])
        self.guided_health_window_var = tk.DoubleVar(value=self.cfg['guided_health_jump_window_seconds'])
        self.guided_min_fall_var = tk.DoubleVar(value=self.cfg['guided_min_fall_seconds'])
        self.guided_recovery_var = tk.IntVar(value=self.cfg['guided_recovery_limit'])
        self.guided_motion_var = tk.DoubleVar(value=self.cfg['guided_motion_threshold'])
        self.guided_use_ml_var = tk.BooleanVar(value=self.cfg['guided_use_learning'])
        self.guided_key_vars = {key: tk.StringVar(value=self.cfg['keys'][key])
                                for key in ('forward', 'back', 'left', 'right', 'jump', 'pickaxe')}
        self.exploration_var = tk.BooleanVar(value=False)
        self.fast_policy_var = tk.BooleanVar(value=self.cfg['fast_policy_enabled'])
        self.fast_interval_var = tk.DoubleVar(value=self.cfg['fast_policy_interval_seconds'])
        self.fast_action_var = tk.DoubleVar(value=self.cfg['fast_action_duration_seconds'])
        self.scan_interval_var = tk.DoubleVar(value=self.cfg['scan_seconds'])
        self.async_capture_var = tk.BooleanVar(value=self.cfg['async_capture_enabled'])
        self.demo_capture_var = tk.BooleanVar(value=False)
        self.demo_mouse_var = tk.BooleanVar(value=False)
        self.ai_agent_status = tk.StringVar(value='Pilota autonomo: disattivato')
        self.monitor_vars = {key: tk.StringVar(value=value) for key, value in {
            'scene':'—', 'capture':'0 frame ricevuti', 'hud':'—', 'phase':'—', 'objective':'—', 'action':'—',
            'reason':'In attesa di una sessione', 'authorized':'NO',
            'menu_target':'—',
            'input_status':'Non inviato', 'visual_feedback':'Non verificato',
            'ocr_phase':'normal', 'camera_feedback':'Telecamera non verificata',
            'recoveries':'0', 'activity':'In attesa', 'behavior_profile':'equilibrato',}.items()}
        self._last_activity = None
        self._activity_history = deque(maxlen=5)
        self.activity_feed_var = tk.StringVar(value='In attesa della prima attività…')
        self.demo_status_var = tk.StringVar(value='Comandi dimostrati: inattivo — attiva la casella prima della Fase 1')
        self.ollama_model_var = tk.StringVar(value=self.cfg['ollama_model'])
        self.learning_store = LearningStore(ROOT / 'learning', max_saved_frames=int(self.cfg['max_saved_frames']))
        self._readiness_cache = None
        self.last_learning_id = None
        self.active_session = None
        self.offline_results = queue.Queue(maxsize=20)
        self.history_ids = {}
        self.timeline_ids = {}
        self.last_bulk_undo = None
        self._range_session = ''
        self._range_origin = None
        self._range_duration = 0.0
        self._range_rows = []
        self._range_spans = []
        self._temporal_spans = []
        self._temporal_estimates = {}
        self._activity_estimates = {}
        self._activity_spans = []
        self._range_annotations = []
        self._range_start = 0.0
        self._range_end = 0.0
        self._range_playhead = 0.0
        self._range_drag = None
        self._range_selected_id = None
        self.ai_photo = None
        self._video_index = -1
        self._video_index_map = FrameIndex([], None)
        self._video_loader = ReviewImageLoader(self.learning_store.images)
        self._video_token = 0
        self._video_clock = TimeLapseClock()
        self._filmstrip_generation = 0
        self._filmstrip_visible = {}
        self._filmstrip_photos = {}
        self._make_ui()
        decorate_legacy_widgets(self.root)
        self._refresh_templates()
        self.root.after(250, self._poll)
        self.root.after(1500, self._home_heartbeat)
        self.root.after(35, self._video_poll)
        self.root.protocol('WM_DELETE_WINDOW', self._close)

    def _make_ui(self):
        # Compact global header and a dashboard-first information architecture.
        top = tk.Frame(self.root, bg=BG)
        top.pack(fill='x', padx=24, pady=(12, 0))
        tk.Label(top, text='◈', font=('Segoe UI', 21), bg=BG, fg=ACCENT).pack(side='left')
        tk.Label(top, text='  OYE', font=('Segoe UI Semibold', 17), bg=BG, fg=WHITE).pack(side='left')
        tk.Label(top, text='  /  OBSERVER STUDIO', font=('Segoe UI', 10), bg=BG,
                 fg=MUTED).pack(side='left', pady=(4, 0))
        tk.Label(top, textvariable=self.status_var, font=('Segoe UI', 9),
                 fg=ACCENT, bg=BG, anchor='e').pack(side='right', pady=(6, 0))
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both', expand=True, padx=13, pady=(5, 8))
        self.home_tab = ttk.Frame(self.notebook)
        calibration_tab = ttk.Frame(self.notebook)
        learning_tab = ttk.Frame(self.notebook)
        self.review_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.home_tab, text='  Panoramica  ')
        self.notebook.add(learning_tab, text='  Apprendimento  ')
        self.notebook.add(self.review_tab, text='  Editor partite  ')
        self.notebook.add(calibration_tab, text='  Calibrazione  ')
        self.learning_tab = learning_tab
        self.calibration_tab = calibration_tab
        main = ttk.Frame(calibration_tab, padding=12)
        main.pack(fill='both', expand=True)
        header = ttk.Label(main, text='Oye · Observer Studio v21', font=('Segoe UI', 19, 'bold'))
        header.pack(anchor='w')
        ttk.Label(main, text='Calibra menu, HUD e comandi spettatore. In spettatore il sistema non interagisce con il gioco.').pack(anchor='w', pady=(0, 8))
        upper = ttk.Frame(main)
        upper.pack(fill='x', pady=(0, 6))
        self.btn_capture = ttk.Button(upper, text='1. Cattura Fortnite tra 6 secondi', command=self._capture_delayed)
        self.btn_capture.pack(side='left', padx=(0, 5))
        ttk.Button(upper, text='Apri screenshot…', command=self._open_image).pack(side='left', padx=5)
        ttk.Label(upper, text='2. Seleziona:').pack(side='left', padx=(13,4))
        self.combobox = ttk.Combobox(upper, state='readonly', width=42,
            values=list(LABELS.values()))
        self.combobox.current(0)
        self.combobox.pack(side='left', padx=(0, 6))
        ttk.Button(upper, text='3. Salva selezione', command=self._save_template).pack(side='left')
        ttk.Button(upper, text='4. Verifica screenshot', command=self._test_current_frame).pack(side='left', padx=5)
        guide_row = ttk.Frame(main)
        guide_row.pack(fill='x', pady=(0, 7))
        ttk.Label(guide_row, text='HUD: 2 indizi stabili tra salute, cornice minimappa e bordi inventario. Bussola/mirino/squadra facoltativi.').pack(side='left')
        ttk.Button(guide_row, text='Come calibrare HUD?', command=self._hud_calibration_help).pack(side='left', padx=10)
        self.canvas = tk.Canvas(main, background='#202630', height=455, highlightthickness=1, highlightbackground='#7b8190')
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<ButtonPress-1>', self._down)
        self.canvas.bind('<B1-Motion>', self._drag)
        self.canvas.bind('<ButtonRelease-1>', self._up)
        self.canvas.bind('<Configure>', lambda event: self._show_frame() if self.frame is not None else None)
        self.canvas.create_text(400, 200, text='Cattura uno screenshot oppure apri un file PNG', fill='#d6dae3', font=('Segoe UI', 16))
        self.template_status = ttk.Label(main, text='', wraplength=1050)
        self.template_status.pack(anchor='w', pady=7)
        row = ttk.Frame(main)
        row.pack(fill='x', pady=(2, 8))
        ttk.Checkbutton(row, text='INVIA input al gioco (disattivato di default)', variable=self.live_var).pack(side='left', padx=(0, 18))
        ttk.Checkbutton(row, text='Click automatici nei menu', variable=self.auto_var).pack(side='left', padx=(0, 18))
        ttk.Checkbutton(row, text='Consenti sparo (anche al pilota AI)', variable=self.shoot_var).pack(side='left', padx=(0,12))
        ttk.Checkbutton(row, text='PILOTA AI · Ollama', variable=self.autonomous_var).pack(side='left', padx=(0,12))
        ttk.Label(row, text='Input:').pack(side='left')
        ttk.Combobox(row, textvariable=self.backend_var, state='readonly', width=11,
                     values=('keyboard', 'gamepad')).pack(side='left', padx=5)
        controls = ttk.Frame(main)
        controls.pack(fill='x', pady=(0, 9))
        self.start_btn = ttk.Button(controls, text='Avvia osservazione', command=self._start)
        self.start_btn.pack(side='left', padx=(0, 6))
        ttk.Button(controls, text='Pausa / Riprendi  F8', command=self._pause).pack(side='left', padx=6)
        ttk.Button(controls, text='ARRESTA  F9', command=self._stop).pack(side='left', padx=6)
        ttk.Label(controls, textvariable=self.status_var).pack(side='left', padx=12)
        ttk.Label(main, text='Eventi sessione (soltanto locali)').pack(anchor='w')
        self.console = tk.Text(main, height=9, state='disabled', font=('Consolas', 9), wrap='word')
        self.console.pack(fill='x')
        self._log('Gli input sono disabilitati per impostazione predefinita. F8 pausa, F9 arresta.')
        self._make_learning_ui(learning_tab)
        self._make_home_ui(self.home_tab)

    def _log(self, message):
        self.console.configure(state='normal')
        self.console.insert('end', time.strftime('%H:%M:%S ') + str(message) + '\n')
        self.console.see('end')
        self.console.configure(state='disabled')
        if hasattr(self, 'home_event_var'):
            self.home_event_var.set(str(message)[:160])

    def _hud_calibration_help(self):
        messagebox.showinfo('HUD · calibrazione semplice',
            'Non serve calibrare ogni elemento.\n\n'
            '1. SALUTE: seleziona le sagome dei due indicatori senza numeri.\n'
            '2. MINIMAPPA: seleziona solo un tratto di cornice o angolo fisso; '
            'non includere il paesaggio che cambia.\n'
            '3. INVENTARIO (alternativa): scegli i contorni dei riquadri, non gli oggetti.\n\n'
            'Per la modalità INTELLIGENTE servono due riferimenti stabili indipendenti.\n'
            'Nella modalità A TEMPO puoi ignorare tutti gli HUD, inclusa la minimappa.\n'
            'BUSSOLA, MIRINO e SQUADRA sono opzionali. In singolo la squadra manca; '
            'in volo il mirino può scomparire. Non usarli come condizione obbligatoria.\n\n'
            'Verifica con una SECONDA immagine a terra, poi prova anche lobby e volo: '
            'non devono risultare MATCH. La verifica deve essere ripetuta alla stessa risoluzione di gioco.')

    def _refresh_templates(self):
        found = {p.stem for p in (ROOT / 'templates').glob('*.png')}
        marks = '   '.join(('✓ ' if key in found else '· ') + key for key in REQUIRED)  # solo menu: facoltativi in modalità a tempo
        hud = len(found.intersection(HUD_CORE))
        optional = len(found.intersection(set(HUD_ANCHORS) - set(HUD_CORE)))
        legacy = ' (HUD legacy disponibile)' if 'hud_in_partita' in found else ''
        self.template_status.configure(text=f'Routine a tempo: HUD NON richiesto · Pulsanti menu facoltativi: {marks}   |   Modalità intelligente: HUD strutturali {hud}/2 (+{optional} facoltativi)' + legacy)
        if hasattr(self, 'home_template_var'):
            self.home_template_var.set(
                f'Routine a tempo pronta · HUD calibrati: {hud}/2 (solo per modalità intelligente)')

    def _test_current_frame(self):
        if self.frame is None:
            messagebox.showinfo('Screenshot richiesto', 'Apri o cattura uno screenshot prima di verificare.')
            return
        try:
            reading = Vision(ROOT / 'templates', self.cfg['template_threshold']).recognize(self.frame)
            lines = ['SCENA: ' + reading.scene.upper()]
            if reading.phase_hint:
                lines.append('Fase aerea rilevata: ' + reading.phase_hint + ' (non autorizza movimento LIVE)')
            if reading.hud:
                lines.append(f'HUD stabili: {reading.hud.core_votes}/{reading.hud.core_configured} (tutti {reading.hud.votes}/{reading.hud.configured}) '
                             f'(confidenza {reading.hud.confidence:.2f})')
                lines.append('Interpretazione: ' + reading.hud.reason)
                for cue, score in sorted((reading.hud_scores or {}).items()):
                    lines.append(f'  {cue}: {score:.2f}' + ('  ✓' if cue in reading.hud.matched else ''))
            elif 'hud_in_partita' in reading.best:
                lines.append('HUD: rilevato con il vecchio riferimento singolo (meno affidabile)')
            lines.append('Pulsanti/indizi: ' + ', '.join(reading.best) if reading.best
                         else 'Pulsanti/indizi: nessuno')
            info = '\n'.join(lines)
            self._log(info.replace('\n', ' | '))
            messagebox.showinfo('Verifica del riconoscimento', info)
        except Exception as exc:
            messagebox.showerror('Verifica non riuscita', str(exc))

    def _capture_delayed(self):
        self._log('Fortnite deve tornare in primo piano. Cattura tra 6 secondi…')
        self.status_var.set('Cattura fra 6 secondi…')
        self.root.iconify()
        self.root.after(6000, self._capture_now)

    def _capture_now(self):
        try:
            enable_dpi_awareness()
            win = foreground_window(self.cfg['window_executables'])
            if win is None:
                raise RuntimeError('Fortnite non è la finestra in primo piano. Aprilo e riprova.')
            self._set_frame(snapshot(win))
            (ROOT / 'captures').mkdir(exist_ok=True)
            path = ROOT / 'captures' / f'cattura_{time.strftime("%Y%m%d_%H%M%S")}.png'
            cv2.imwrite(str(path), self.frame)
            self._log('Screenshot acquisito: ' + str(path.name))
        except Exception as exc:
            self._log('Cattura non riuscita: ' + str(exc))
            messagebox.showerror('Cattura non riuscita', str(exc))
        finally:
            self.root.deiconify()
            self.root.lift()
            self.status_var.set('Seleziona il pulsante e salvalo.')

    def _open_image(self):
        filename = filedialog.askopenfilename(title='Scegli uno screenshot', filetypes=[('Immagini', '*.png *.jpg *.jpeg *.bmp')])
        if not filename:
            return
        img = cv2.imread(filename, cv2.IMREAD_COLOR)
        if img is None:
            messagebox.showerror('Errore', 'Immagine non leggibile')
            return
        self._set_frame(img)
        self._log('Caricato ' + Path(filename).name)

    def _set_frame(self, frame):
        self.frame = frame
        self.selection = None
        self._show_frame()

    def _show_frame(self):
        if self.frame is None:
            return
        height, width = self.frame.shape[:2]
        cw = max(self.canvas.winfo_width(), 500)
        ch = max(self.canvas.winfo_height(), 300)
        self.display_scale = min((cw - 10) / width, (ch - 10) / height)
        render_w = int(width * self.display_scale)
        render_h = int(height * self.display_scale)
        self.preview_offset = ((cw - render_w) // 2, (ch - render_h) // 2)
        image = cv2.cvtColor(self.frame, cv2.COLOR_BGRA2RGB) if self.frame.shape[-1] == 4 else cv2.cvtColor(self.frame, cv2.COLOR_BGR2RGB)
        self.photo = ImageTk.PhotoImage(Image.fromarray(cv2.resize(image, (render_w, render_h), interpolation=cv2.INTER_AREA)))
        self.canvas.delete('all')
        self.canvas.create_image(*self.preview_offset, image=self.photo, anchor='nw')
        if self.selection:
            x1, y1, x2, y2 = self.selection
            ox, oy = self.preview_offset
            s = self.display_scale
            self.rect_id = self.canvas.create_rectangle(ox+x1*s, oy+y1*s, ox+x2*s, oy+y2*s,
                                                        outline='#ffca28', width=3)

    def _coords(self, event):
        ox, oy = self.preview_offset
        h, w = self.frame.shape[:2]
        x = min(w, max(0, int((event.x - ox) / self.display_scale)))
        y = min(h, max(0, int((event.y - oy) / self.display_scale)))
        return x, y

    def _down(self, event):
        if self.frame is None:
            return
        self.anchor = self._coords(event)
        self.selection = None

    def _drag(self, event):
        if self.anchor is None or self.frame is None:
            return
        self._update_rectangle(self._coords(event))

    def _up(self, event):
        if self.anchor is None or self.frame is None:
            return
        self._update_rectangle(self._coords(event))
        self.anchor = None

    def _update_rectangle(self, cursor):
        x1, y1 = self.anchor
        x2, y2 = cursor
        self.selection = (min(x1,x2), min(y1,y2), max(x1,x2), max(y1,y2))
        if self.rect_id:
            self.canvas.delete(self.rect_id)
        a,b,c,d = self.selection
        s = self.display_scale
        ox, oy = self.preview_offset
        self.rect_id = self.canvas.create_rectangle(ox+a*s, oy+b*s, ox+c*s, oy+d*s,
                                                     outline='#ffca28', width=3)

    def _save_template(self):
        if self.frame is None or self.selection is None:
            messagebox.showwarning('Selezione mancante', 'Apri uno screenshot e trascina un rettangolo sul pulsante.')
            return
        name = next((key for key, label in LABELS.items() if label == self.combobox.get()), None)
        if name is None:
            return
        x1,y1,x2,y2 = self.selection
        crop = self.frame[y1:y2, x1:x2]
        if x2-x1 < 24 or y2-y1 < 18 or crop.size == 0 or np.std(crop) < 7:
            messagebox.showerror('Ritaglio non valido', 'Usa un rettangolo più grande e con contenuto visivo (testo e bordo).')
            return
        folder = ROOT / 'templates'
        folder.mkdir(exist_ok=True)
        if not cv2.imwrite(str(folder / f'{name}.png'), crop):
            messagebox.showerror('Errore', 'Impossibile salvare il ritaglio PNG')
            return
        h,w = self.frame.shape[:2]
        # Limit search to a region surrounding the measured button placement.
        margin_x, margin_y = max(60, w*.09), max(45, h*.085)
        roi = [round(max(0,(x1-margin_x)/w), 6), round(max(0,(y1-margin_y)/h),6),
               round(min(1,(x2+margin_x)/w), 6), round(min(1,(y2+margin_y)/h),6)]
        rois = folder / '_rois.json'
        data = json.loads(rois.read_text('utf8')) if rois.exists() else {}
        data[name] = roi
        rois.write_text(json.dumps(data, indent=2) + '\n', encoding='utf8')
        meta_file = folder / '_resolution.json'
        meta = json.loads(meta_file.read_text('utf8')) if meta_file.exists() else {}
        meta[name] = [w, h]
        meta_file.write_text(json.dumps(meta, indent=2) + '\n', encoding='utf8')
        self._log(f'Template salvato: {name} ({x2-x1}×{y2-y1}, origine {w}×{h})')
        self._refresh_templates()
        self.status_var.set(f'{name} calibrato')

    def _preview_activity(self):
        """Show the next actions in a read-only offline dialog; never start LIVE."""
        try:
            preview = activity_preview(self.guided_profile_var.get(),
                                       harvest=bool(self.guided_harvest_var.get()))
        except ValueError as exc:
            messagebox.showerror('Anteprima', str(exc))
            return
        window = tk.Toplevel(self.root)
        window.title('Oye · Anteprima attività (offline)')
        window.geometry('670x590')
        window.minsize(480, 390)
        window.configure(bg=BG)
        tk.Label(window, text='La prossima routine, senza avviare Fortnite',
                 bg=BG, fg=WHITE, font=('Segoe UI Semibold', 15)).pack(anchor='w', padx=20, pady=(18, 6))
        tk.Label(window, text='Simulazione riproducibile: le decisioni reali possono cambiare '
                 'in base alle osservazioni e ai recuperi.',
                 bg=BG, fg=MUTED, wraplength=615, justify='left').pack(anchor='w', padx=20, pady=(0, 12))
        area = tk.Text(window, wrap='none', bg=SURFACE, fg=WHITE, insertbackground=WHITE,
                       relief='flat', font=('Consolas', 10), padx=14, pady=14)
        area.insert('1.0', preview)
        area.configure(state='disabled')
        area.pack(fill='both', expand=True, padx=20, pady=(0, 14))
        ttk.Button(window, text='Chiudi', command=window.destroy).pack(anchor='e', padx=20, pady=(0, 16))

    def _start(self):
        if self.runner and self.runner.thread and self.runner.thread.is_alive():
            messagebox.showinfo('Già avviato', 'La sessione è già in corso.')
            return
        is_live = self.live_var.get()
        if self.exploration_var.get() and is_live:
            messagebox.showerror('Esplorazione', 'La fase di apprendimento deve restare senza input LIVE.')
            return
        if self.auto_var.get() and not is_live:
            messagebox.showinfo('Osservazione', 'I clic automatici richiedono la spunta di invio input. Avvio in osservazione.')
        if self.autonomous_var.get() and not is_live:
            messagebox.showerror('Pilota AI', 'Seleziona LIVE oppure usa «Avvia partita autonoma» nella scheda AI.')
            return
        if (self.autonomous_var.get() and not self.guided_var.get() and
                not (self.ollama_enabled_var.get() or self.fast_policy_var.get())):
            messagebox.showerror('Pilota AI', 'Attiva la policy rapida oppure Ollama Vision.')
            return
        if self.autonomous_var.get() and self.fast_policy_var.get() and not self.guided_var.get():
            # Dataset from human-play only. A fast classifier with no examples
            # must not be presented as an AI that already knows how to play.
            readiness = self._training_readiness()
            if readiness.action_examples < 3 and not self.ollama_enabled_var.get():
                messagebox.showwarning('Oye deve prima imparare',
                    'Non ci sono ancora abbastanza azioni dimostrate.\n\n'
                    'Gioca con «Esplora e impara» e abilita la dimostrazione, '
                    'oppure insegna alcune azioni dalla timeline.')
                return
            if not readiness.trial_ready:
                message = ('Oye ha un dataset ancora limitato:\n'
                           f'{readiness.matches} partite con comandi · '
                           f'{readiness.action_examples} esempi · '
                           f'{readiness.action_types} azioni diverse.\n\n'
                           'Con 2–3 partite può iniziare a fare tentativi, ma non è ancora '
                           'dimostrato che generalizzi a situazioni nuove. '
                           'Vuoi comunque procedere con una prova sorvegliata?')
                if not messagebox.askyesno('Verifica apprendimento', message):
                    return
        if is_live and self.guided_var.get() and not self.guided_timed_var.get() and not (ROOT / 'templates' / 'bus_salto.png').is_file():
            messagebox.showwarning('Bus non calibrato', 'Nella modalità intelligente il template bus_salto è ancora necessario. '
                                   'Attiva «Routine a tempo» per non dipendere dal riconoscimento dell’HUD.')
        if is_live:
            ok = messagebox.askyesno('Attivazione input',
                'La modalità LIVE invia comandi a Fortnite. Può violare le regole del gioco e comportare sanzioni.\n\n'
                + ('La routine A TEMPO agisce in base ai secondi trascorsi, NON sa se è davvero sul bus o a terra.\n\n'
                   if self.guided_var.get() and self.guided_timed_var.get() else '')
                + 'Non aggira anti-cheat o controlli AFK. Premi F9 per fermare e F8 per sospendere.\n\n'
                + ('Il modello potrà scegliere movimenti e interazioni: la sua interpretazione può essere errata.\n\n'
                   if self.autonomous_var.get() else '') + 'Confermi?')
            if not ok:
                return
        try:
            from observer.config import save
            self.cfg['input_backend'] = self.backend_var.get()
            self.cfg['capture_seconds'] = float(self.capture_seconds_var.get())
            self.cfg['ocr_enabled'] = bool(self.ocr_enabled_var.get())
            self.cfg['ollama_enabled'] = bool(self.ollama_enabled_var.get())
            self.cfg['ollama_model'] = self.ollama_model_var.get().strip()
            self.cfg['learning_enabled'] = True
            self.cfg['autonomous_enabled'] = bool(self.autonomous_var.get())
            self.cfg['guided_explore_enabled'] = bool(self.guided_var.get())
            self.cfg['guided_explore_harvest'] = bool(self.guided_harvest_var.get())
            self.cfg['guided_smooth_enabled'] = bool(self.guided_smooth_var.get())
            self.cfg['guided_activity_profile'] = self.guided_profile_var.get()
            self.cfg['guided_solo_mode'] = bool(self.guided_solo_var.get())
            self.cfg['guided_camera_pixels'] = int(self.guided_camera_var.get())
            self.cfg['guided_timed_enabled'] = bool(self.guided_timed_var.get())
            self.cfg['guided_timed_start_on_launch'] = bool(self.guided_start_now_var.get())
            self.cfg['guided_timed_load_seconds'] = float(self.guided_loading_var.get())
            self.cfg['guided_timed_jump_window_seconds'] = float(self.guided_jump_window_var.get())
            self.cfg['guided_timed_fall_seconds'] = float(self.guided_fall_var.get())
            self.cfg['guided_timed_ground_seconds'] = float(self.guided_ground_var.get())
            self.cfg['guided_health_jump_enabled'] = bool(self.guided_health_jump_var.get())
            self.cfg['guided_health_threshold'] = float(self.guided_health_threshold_var.get())
            self.cfg['guided_health_min_wait_seconds'] = float(self.guided_health_wait_var.get())
            self.cfg['guided_health_jump_window_seconds'] = float(self.guided_health_window_var.get())
            self.cfg['guided_jump_interval_seconds'] = float(self.guided_jump_period_var.get())
            self.cfg['guided_min_fall_seconds'] = float(self.guided_min_fall_var.get())
            self.cfg['guided_recovery_limit'] = int(self.guided_recovery_var.get())
            self.cfg['guided_motion_threshold'] = float(self.guided_motion_var.get())
            self.cfg['guided_use_learning'] = bool(self.guided_use_ml_var.get())
            self.cfg['keys'].update({k: v.get().strip() for k, v in self.guided_key_vars.items()})
            self.cfg['guided_explore_manual'] = self.guided_manual.get('1.0', 'end-1c').strip()
            self.cfg['fast_policy_enabled'] = bool(self.fast_policy_var.get())
            self.cfg['fast_policy_interval_seconds'] = float(self.fast_interval_var.get())
            self.cfg['fast_action_duration_seconds'] = float(self.fast_action_var.get())
            self.cfg['scan_seconds'] = float(self.scan_interval_var.get())
            self.cfg['async_capture_enabled'] = bool(self.async_capture_var.get())
            self.cfg['demonstration_enabled'] = bool(self.demo_capture_var.get())
            self.cfg['demonstration_mouse_shoot'] = bool(self.demo_mouse_var.get())
            if self.cfg['autonomous_enabled'] and self.cfg['ollama_enabled'] and not self.cfg['guided_explore_enabled']:
                from observer.ollama_vision import LocalVisionModel
                LocalVisionModel(self.cfg['ollama_model'], self.cfg['ollama_timeout_seconds']).check_ready()
            save(ROOT / 'config.json', self.cfg)
            self.runner = Runner(live=is_live,
                                 auto_requeue=is_live and self.auto_var.get(),
                                 shoot=is_live and self.shoot_var.get(), root=ROOT,
                                 exploration=bool(self.exploration_var.get()))
            if is_live and not self._install_hotkeys():
                raise RuntimeError('Impossibile attivare F8/F9 globali: input LIVE annullati.')
            self.runner.start()
            if not is_live:
                self._install_hotkeys()
            self._log('Sessione avviata: ' + ('PILOTA AI LIVE' if self.autonomous_var.get() else 'LIVE'
                    if is_live else 'ESPLORAZIONE' if self.exploration_var.get() else 'OSSERVAZIONE'))
            if is_live:
                self.root.iconify()
                self.status_var.set(('Pilota AI LIVE' if self.autonomous_var.get() else 'LIVE')
                                    + ' • F8 pausa • F9 arresta')
            else:
                self.status_var.set('Esplorazione e apprendimento attivi' if self.exploration_var.get()
                                    else 'Osservazione attiva')
        except Exception as exc:
            self._log('Avvio fallito: ' + str(exc))
            messagebox.showerror('Avvio fallito', str(exc))

    def _install_hotkeys(self):
        if self.listener and self.listener.is_alive():
            return True
        try:
            from pynput import keyboard
            def pressed(key):
                if key not in (keyboard.Key.f8, keyboard.Key.f9) or key in self._keys_down:
                    return
                self._keys_down.add(key)
                if key == keyboard.Key.f8:
                    self._pause()
                elif key == keyboard.Key.f9:
                    self._stop()
            def released(key):
                self._keys_down.discard(key)
            self.listener = keyboard.Listener(on_press=pressed, on_release=released)
            self.listener.daemon = True
            self.listener.start()
            return True
        except Exception as exc:
            self._log('Hotkey globali non disponibili: ' + str(exc))
            return False

    def _pause(self):
        if self.runner and not self.runner.stop_event.is_set():
            self.runner.pause_toggle()

    def _probe_forward(self):
        if self.runner and self.runner.thread and self.runner.thread.is_alive():
            messagebox.showwarning('Diagnostica W', 'Arresta Oye prima del test manuale.')
            return
        if not messagebox.askyesno('Prova locomozione',
              'Verifica che tu sia già in partita, con Fortnite aperto.\n\n'
              'Dopo aver confermato hai 3 secondi per tornare a Fortnite. '
              'Oye proverà a tenere W premuto per 0,75 secondi, SOLO se '
              'Fortnite è in primo piano.\n\n'
              'Il test usa input standard di Windows: non aggira alcun anti-cheat.\n\n'
              'Vuoi eseguirlo?'):
            return
        config = dict(self.cfg)
        config['keys'] = dict(self.cfg['keys'])
        self.root.iconify()
        def work():
            time.sleep(3.)
            try:
                from observer.input_probe import probe_forward_visual
                _sent, message = probe_forward_visual(config)
            except Exception as exc:
                message = f'Errore input: {type(exc).__name__}: {exc}'
            def finished():
                self.root.deiconify()
                self._log('[test_input] ' + message)
                self.ai_agent_status.set(message)
                messagebox.showinfo('Risultato diagnostica W', message)
            self.root.after(0, finished)
        threading.Thread(target=work, daemon=True, name='OyeInputProbe').start()

    def _probe_camera(self):
        if self.runner and self.runner.thread and self.runner.thread.is_alive():
            messagebox.showwarning('Diagnostica visuale', 'Arresta Oye prima del test manuale.')
            return
        if not messagebox.askyesno('Prova rotazione visuale',
                'Entra in partita e lascia Fortnite pronto in background.\n\n'
                'Dopo la conferma avrai 3 secondi per tornarci. Oye invierà '
                'un unico movimento relativo del mouse verso destra.\n\n'
                'Non aggira i sistemi anti-cheat. Procedo?'):
            return
        config = dict(self.cfg)
        config['keys'] = dict(self.cfg['keys'])
        self.root.iconify()
        def work():
            time.sleep(3.)
            try:
                from observer.input_probe import probe_camera_visual
                _sent, message = probe_camera_visual(config)
            except Exception as exc:
                message = f'Errore test visuale: {type(exc).__name__}: {exc}'
            def finished():
                self.root.deiconify()
                self._log('[test_visuale] ' + message)
                self.ai_agent_status.set(message)
                messagebox.showinfo('Diagnostica mouse', message)
            self.root.after(0, finished)
        threading.Thread(target=work, daemon=True, name='OyeCameraProbe').start()

    def _stop(self):
        if self.runner and not self.runner.stop_event.is_set():
            self.runner.stop()
            if threading.current_thread() is threading.main_thread():
                self.status_var.set('Arresto richiesto (rilascio tasti in corso)')

    def _poll(self):
        if self.runner:
            for _ in range(75):
                try:
                    record = self.runner.messages.get_nowait()
                except Exception:
                    break
                if record['event'] == 'learning_frame':
                    self._handle_learning_record(record)
                elif record['event'] == 'preview':
                    self._update_live_preview(record)
                else:
                    if record['event'] == 'capture_status' and hasattr(self, 'home_hint_var'):
                        self.home_hint_var.set(record['message'][:200])
                    if record['event'] == 'telemetry':
                        activity = str(record.get('activity') or '—')
                        if activity not in ('—', 'Pronto a esplorare') and activity != self._last_activity:
                            self._last_activity = activity
                            stamp = time.strftime('%H:%M:%S')
                            self._activity_history.appendleft(f'{stamp}   {activity}')
                            self.activity_feed_var.set('\n'.join(self._activity_history))
                        for key, variable in self.monitor_vars.items():
                            if key in record:
                                value = record[key]
                                if key == 'authorized':
                                    value = 'SÌ' if value else 'NO'
                                variable.set(str(value)[:200])
                        continue
                    if record['event'] == 'menu_status':
                        self.monitor_vars['menu_target'].set(
                            (record.get('menu_target') or 'non trovato') + ' · ' + record.get('message','')[:60])
                    if record['event'] == 'demo_status':
                        self.demo_status_var.set(record['message'][:245])
                    elif record['event'] == 'demo_error':
                        self.demo_status_var.set('ERRORE: ' + record['message'][:220])
                    if record['event'] in ('phase_ocr', 'phase_ocr_error', 'camera_feedback',
                                           'agent', 'agent_status', 'input_status', 'game_phase',
                                           'fast_decision', 'exploration_insight'):
                        self.ai_agent_status.set(record['message'][:200])
                    elif record['event'] == 'agent_error':
                        self.ai_agent_status.set('Errore inferenza: ' + record['message'][:120])
                    self._log(f"[{record['event']}] {record['message']}")
                    if record['event'] == 'lifecycle' and record['message'].startswith('Sessione terminata'):
                        self.status_var.set('Terminato: input rilasciati')
        for _ in range(3):
            try:
                result = self.offline_results.get_nowait()
            except queue.Empty:
                break
            if isinstance(result, Exception):
                self._log('Analisi immagine fallita: ' + str(result))
                messagebox.showerror('Analisi fallita', str(result))
            else:
                self._handle_learning_record({'event': 'learning_frame', 'message': 'File analizzato', **result})
        self.root.after(250, self._poll)

    def _update_live_preview(self, record: dict):
        """Render exactly what Vision inspected; keep both previews in RAM."""
        jpeg = record.get('jpeg')
        if not isinstance(jpeg, bytes):
            return
        try:
            with Image.open(io.BytesIO(jpeg)) as source:
                image = source.convert('RGB')
                legacy = image.copy()
                legacy.thumbnail((450, 240), Image.Resampling.LANCZOS)
                self.live_preview_photo = ImageTk.PhotoImage(legacy)
                if hasattr(self, 'home_preview_label'):
                    image.thumbnail((770, 445), Image.Resampling.LANCZOS)
                    self.home_preview_photo = ImageTk.PhotoImage(image)
                    self.home_preview_label.configure(image=self.home_preview_photo, text='')
            self.live_preview_label.configure(image=self.live_preview_photo, text='')
            self._last_home_frame_at = time.monotonic()
            values = record.get('hud_scores') or {}
            ranked = ', '.join(f'{name}: {score:.2f}'
                               for name, score in sorted(values.items(), key=lambda item: item[0]))
            core_v = int(record.get('hud_core_votes') or 0)
            core_n = int(record.get('hud_core_configured') or 0)
            summary = (f"Frame #{record.get('frame_count', '?')} · scena: {record.get('scene','?')} "
                       f"· HUD stabili: {core_v}/{core_n} "
                       f"· età: {record.get('capture_age_ms',0)} ms"
                       f" · OCR: {record.get('ocr_phase', 'normal')}")
            self.live_preview_status.set(summary + ('\nPunteggi: ' + ranked if ranked else
                                                 '\nNessun riferimento HUD misurato'))
            if hasattr(self, 'home_frame_info'):
                self.home_frame_info.set(summary)
                votes = core_v
                total = core_n
                scene = str(record.get('scene') or 'unknown')
                if record.get('ocr_phase_locked'):
                    hint = 'OCR: ' + str(record.get('ocr_phase')) + ' · input sospesi in attesa di riavvio o conclusione.'
                elif total < 2:
                    hint = (f'Cattura funzionante, ma solo {total} riferimenti strutturali disponibili. '
                            'Bastano 2 tra salute, cornice minimappa e inventario.')
                elif scene == 'unknown':
                    hint = (f'Fotogramma ricevuto · HUD stabili {votes}/{total}. '
                            + str(record.get('hud_reason') or 'Controlla i due riferimenti statici.'))
                else:
                    hint = f'Schermo acquisito · scena: {scene} · HUD stabili: {votes}/{total}.'
                self.home_hint_var.set(hint)
                self.home_scores_var.set(ranked if ranked else 'Nessuna corrispondenza misurata')
        except Exception as exc:
            self.live_preview_status.set(f'Anteprima diagnostica non disponibile: {exc}')

    def _make_home_ui(self, host):
        """The user-facing control room; advanced configuration remains in separate tabs."""
        viewport = tk.Canvas(host, bg=BG, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(host, orient='vertical', command=viewport.yview)
        scrollbar.pack(side='right', fill='y')
        viewport.pack(side='left', fill='both', expand=True)
        viewport.configure(yscrollcommand=scrollbar.set)
        body = tk.Frame(viewport, bg=BG)
        item = viewport.create_window((0, 0), window=body, anchor='nw')
        body.bind('<Configure>', lambda _e: viewport.configure(scrollregion=viewport.bbox('all')))
        viewport.bind('<Configure>', lambda ev: viewport.itemconfigure(item, width=ev.width))
        self.home_canvas = viewport
        # Wheel scrolling must also work while hovering inner cards/labels.
        def scroll_home(event):
            if self.notebook.select() != str(self.home_tab):
                return
            steps = -max(1, abs(event.delta) // 120) if event.delta > 0 else max(1, abs(event.delta) // 120)
            viewport.yview_scroll(steps, 'units')
        def scroll_linux(event):
            if self.notebook.select() == str(self.home_tab):
                viewport.yview_scroll(-1 if event.num == 4 else 1, 'units')

        masthead = tk.Frame(body, bg=BG)
        masthead.pack(fill='x', padx=24, pady=(22, 12))
        tk.Label(masthead, text='CONTROL ROOM  /  OYE 22' , font=('Segoe UI Semibold', 9),
                 fg=ACCENT, bg=BG).pack(anchor='w')
        tk.Label(masthead, text='Oye osserva. Poi agisce.', font=('Segoe UI Semibold', 27),
                 fg=WHITE, bg=BG).pack(anchor='w', pady=(2, 5))
        tk.Label(masthead, text='Avvia una sessione, osserva il fotogramma LIVE e scopri perché il pilota agisce o aspetta.',
                 font=('Segoe UI', 11), fg=MUTED, bg=BG).pack(anchor='w')

        actions = tk.Frame(body, bg=BG)
        actions.pack(fill='x', padx=24, pady=(9, 19))
        for title, desc, action, accent in (
            ('01   Osserva e impara', 'Tu giochi · Oye registra', self._start_exploration, True),
            ('02   Gioca con Oye', 'Policy ML + visione', self._start_autonomous, False),
            ('03   Esplora e piccona', 'Routine senza ML', self._start_guided, False),
        ):
            button = RoundButton(actions, title, desc, action, width=257, height=76, accent=accent)
            button.pack(side='left', padx=(0, 11))
        stop_buttons = tk.Frame(actions, bg=BG)
        stop_buttons.pack(side='right', padx=(5, 0), pady=2)
        ttk.Button(stop_buttons, text='Pausa  F8', command=self._pause).pack(fill='x', pady=(0, 5))
        ttk.Button(stop_buttons, text='Arresta  F9', command=self._stop).pack(fill='x')

        # Quick-profile control on the dashboard, bound to the SAME variable
        # as the advanced tab; the next session uses the selected plan.
        quick_style = tk.Frame(body, bg=BG)
        quick_style.pack(fill='x', padx=24, pady=(0, 18))
        tk.Label(quick_style, text='STILE DI ESPLORAZIONE', bg=BG, fg=ACCENT,
                 font=('Segoe UI Semibold', 9)).pack(side='left', padx=(0, 12))
        choice = ttk.Combobox(quick_style, textvariable=self.guided_profile_var,
                              state='readonly', width=16,
                              values=('equilibrato', 'esploratore', 'raccoglitore'))
        choice.pack(side='left')
        self.home_style_hint_var = tk.StringVar()
        descriptions = {
            'equilibrato': 'Movimento, osservazione e raccolta in proporzioni simili.',
            'esploratore': 'Più cambi di percorso e visuale; poco piccone.',
            'raccoglitore': 'Più piccone, senza rinunciare a muoversi.',
        }
        self.home_style_hint_var.set(descriptions.get(self.guided_profile_var.get(), ''))
        choice.bind('<<ComboboxSelected>>',
                    lambda _e: self.home_style_hint_var.set(
                        descriptions.get(self.guided_profile_var.get(), '') +
                        '  · Si applica alla prossima sessione.'))
        tk.Label(quick_style, textvariable=self.home_style_hint_var,
                 bg=BG, fg=MUTED, font=('Segoe UI', 9)).pack(side='left', padx=(16, 0))
        ttk.Button(quick_style, text='Anteprima routine',
                   command=self._preview_activity).pack(side='right', padx=(12, 0))

        monitor_header = tk.Frame(body, bg=BG)
        monitor_header.pack(fill='x', padx=24, pady=(0, 9))
        tk.Label(monitor_header, text='MONITOR LIVE', bg=BG, fg=WHITE,
                 font=('Segoe UI Semibold', 12)).pack(side='left')
        tk.Label(monitor_header, text='Fotogrammi in memoria · non salvati quando gioca Oye',
                 bg=BG, fg=MUTED, font=('Segoe UI', 9)).pack(side='right')
        panels = tk.Frame(body, bg=BG)
        panels.pack(fill='x', padx=24)
        panels.columnconfigure(0, weight=7, minsize=540)
        panels.columnconfigure(1, weight=4, minsize=320)
        left = RoundedCard(panels, width=725, height=530)
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
        right = RoundedCard(panels, width=360, height=530)
        right.grid(row=0, column=1, sticky='nsew')

        preview_heading = tk.Frame(left.inner, bg=SURFACE)
        preview_heading.pack(fill='x', pady=(1, 7))
        tk.Label(preview_heading, text='ANTEPRIMA DEL GIOCO', bg=SURFACE, fg=ACCENT,
                 font=('Segoe UI Semibold', 9)).pack(side='left')
        self.home_feed_state_var = tk.StringVar(value='○ IN ATTESA')
        tk.Label(preview_heading, textvariable=self.home_feed_state_var, bg=SURFACE, fg=MUTED,
                 font=('Segoe UI Semibold', 9)).pack(side='right')
        self.home_preview_label = tk.Label(left.inner, text='Nessun fotogramma ricevuto\n\n'
                  'Avvia una sessione per osservare il gioco in tempo reale.',
                  bg='#090D13', fg=MUTED, font=('Segoe UI', 11), anchor='center', justify='center')
        self.home_preview_label.pack(fill='both', expand=True, pady=(1, 9))
        self.home_frame_info = tk.StringVar(value='In attesa · 0 fotogrammi analizzati')
        tk.Label(left.inner, textvariable=self.home_frame_info, bg=SURFACE, fg=MUTED,
                 font=('Consolas', 9), anchor='w').pack(fill='x')

        tk.Label(right.inner, text='STATO DEL PILOTA', bg=SURFACE, fg=ACCENT,
                 font=('Segoe UI Semibold', 9)).pack(anchor='w', pady=(1, 13))
        def status_row(label, value, *, weight='normal'):
            tk.Label(right.inner, text=label.upper(), bg=SURFACE, fg=MUTED,
                     font=('Segoe UI Semibold', 8)).pack(anchor='w', pady=(3, 2))
            tk.Label(right.inner, textvariable=value, bg=SURFACE, fg=WHITE,
                     font=('Segoe UI Semibold' if weight == 'bold' else 'Segoe UI', 11),
                     anchor='w', justify='left', wraplength=270).pack(anchor='w', pady=(0, 8))
        status_row('Scena visiva', self.monitor_vars['scene'], weight='bold')
        status_row('Fase stimata', self.monitor_vars['phase'])
        status_row('Attività in corso', self.monitor_vars['activity'], weight='bold')
        status_row('Decisione', self.monitor_vars['action'])
        status_row('Profilo', self.monitor_vars['behavior_profile'])
        status_row('Input', self.monitor_vars['input_status'])
        status_row('Pulsante risultati', self.monitor_vars['menu_target'])
        status_row('Riconoscimento HUD', self.monitor_vars['hud'])
        tk.Label(right.inner, text='Per i dettagli vedi Apprendimento → Monitor LIVE',
                 bg=SURFACE, fg=MUTED, font=('Segoe UI', 9),
                 wraplength=265).pack(anchor='w', pady=(7, 0))

        details = tk.Frame(body, bg=BG)
        details.pack(fill='x', padx=24, pady=(13, 4))
        left_notes = RoundedCard(details, width=700, height=167)
        left_notes.pack(side='left', fill='both', expand=True, padx=(0, 12))
        self.home_hint_var = tk.StringVar(value='Per diagnosticare l’HUD, avvia una sessione e controlla i fotogrammi qui sopra.')
        self.home_scores_var = tk.StringVar(value='Nessun punteggio HUD disponibile')
        tk.Label(left_notes.inner, text='DIAGNOSTICA VISIVA', bg=SURFACE, fg=ACCENT,
                 font=('Segoe UI Semibold', 9)).pack(anchor='w', pady=(0, 7))
        tk.Label(left_notes.inner, textvariable=self.home_hint_var,
                 bg=SURFACE, fg=WHITE, font=('Segoe UI', 10),
                 wraplength=685, justify='left').pack(anchor='w')
        tk.Label(left_notes.inner, textvariable=self.home_scores_var,
                 bg=SURFACE, fg=MUTED, font=('Consolas', 9),
                 wraplength=690, justify='left').pack(anchor='w', pady=(8, 0))
        right_notes = RoundedCard(details, width=355, height=167)
        right_notes.pack(side='right', fill='both')
        self.home_template_var = tk.StringVar(value='Controllo calibrazione non ancora eseguito')
        tk.Label(right_notes.inner, text='CALIBRAZIONE HUD', bg=SURFACE, fg=ACCENT,
                 font=('Segoe UI Semibold', 9)).pack(anchor='w', pady=(0, 7))
        tk.Label(right_notes.inner, textvariable=self.home_template_var,
                 bg=SURFACE, fg=WHITE, font=('Segoe UI', 10),
                 wraplength=300, justify='left').pack(anchor='w')
        ttk.Button(right_notes.inner, text='Apri calibrazione',
                   command=lambda: self.notebook.select(self.calibration_tab)).pack(anchor='w', pady=(8, 0))

        # Compact activity history: a real timeline of planner state changes,
        # not fabricated 'thoughts' or anthropomorphic claims.
        activity_card = RoundedCard(body, width=1120, height=158)
        activity_card.pack(fill='x', padx=24, pady=(13, 4))
        tk.Label(activity_card.inner, text='ATTIVITÀ RECENTI', bg=SURFACE, fg=ACCENT,
                 font=('Segoe UI Semibold', 9)).pack(anchor='w', pady=(0, 5))
        tk.Label(activity_card.inner, textvariable=self.activity_feed_var, bg=SURFACE,
                 fg=WHITE, font=('Consolas', 10), justify='left', anchor='nw').pack(fill='both', expand=True)

        footer = tk.Frame(body, bg=BG)
        footer.pack(fill='x', padx=24, pady=(12, 22))
        self.home_event_var = tk.StringVar(value='Nessuna attività recente')
        tk.Label(footer, text='ULTIMO EVENTO', bg=BG, fg=ACCENT,
                 font=('Segoe UI Semibold', 9)).pack(anchor='w')
        tk.Label(footer, textvariable=self.home_event_var, bg=BG, fg=MUTED,
                 font=('Segoe UI', 10), wraplength=900, justify='left').pack(side='left')
        ttk.Button(footer, text='Rivedi le partite',
                   command=lambda: self.notebook.select(self.review_tab)).pack(side='right')

        def bind_wheel(widget):
            widget.bind('<MouseWheel>', scroll_home, add='+')
            widget.bind('<Button-4>', scroll_linux, add='+')
            widget.bind('<Button-5>', scroll_linux, add='+')
            for child in widget.winfo_children():
                bind_wheel(child)
        bind_wheel(body)
        bind_wheel(viewport)

    def _home_heartbeat(self):
        if hasattr(self, 'home_feed_state_var'):
            age = (time.monotonic() - self._last_home_frame_at
                   if self._last_home_frame_at is not None else None)
            if age is not None and age <= 3.5:
                self.home_feed_state_var.set('● LIVE')
            elif age is not None:
                self.home_feed_state_var.set(f'○ FERMO DA {int(age)} s')
            else:
                self.home_feed_state_var.set('○ IN ATTESA')
        self.root.after(1500, self._home_heartbeat)

    def _make_learning_ui(self, host):
        # The two-phase controls and review panel must remain accessible on
        # 720p/768p Windows displays: the entire learning tab is scrollable.
        canvas = tk.Canvas(host, highlightthickness=0, borderwidth=0)
        scroll = ttk.Scrollbar(host, orient='vertical', command=canvas.yview)
        scroll.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        canvas.configure(yscrollcommand=scroll.set)
        outer = ttk.Frame(canvas, padding=13)
        container = canvas.create_window((0, 0), window=outer, anchor='nw')
        outer.bind('<Configure>', lambda _event: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(container, width=event.width))
        self.ai_canvas = canvas
        ttk.Label(outer, text='Oye · Esplora, impara, gioca',
                  font=('Segoe UI', 19, 'bold')).pack(anchor='w')
        ttk.Label(outer, text='FASE 1: quando giochi TU, Oye salva le schermate in learning/frames per revisionarle dopo. '
                  'Poi assegna gli eventi e le azioni corrette a qualche fotogramma. '
                  'FASE 2: quando gioca OYE, legge i frame solo in RAM e NON archivia screenshot. '
                  'Usa prima una policy supervisionata molto rapida; su schermate sconosciute '
                  'consulta Ollama in background solo se attivo. La modalità FAST-ONLY non attende il modello visivo; '
                  'la modalità intelligente richiede HUD, la routine a tempo NO.',
                  wraplength=1100).pack(anchor='w', pady=(2, 11))
        options = ttk.LabelFrame(outer, text='Acquisizione e motori di riconoscimento', padding=8)
        options.pack(fill='x', pady=(0, 8))
        top = ttk.Frame(options)
        top.pack(fill='x')
        ttk.Label(top, text='Salva ogni (s), quando giochi tu:').pack(side='left')
        ttk.Spinbox(top, from_=1, to=60, increment=.5, width=6,
                    textvariable=self.capture_seconds_var).pack(side='left', padx=(5, 16))
        ttk.Checkbutton(top, text='OCR Tesseract · spettatore e riavvio', variable=self.ocr_enabled_var).pack(side='left', padx=8)
        ttk.Checkbutton(top, text='Ollama Vision locale (opzionale)',
                        variable=self.ollama_enabled_var).pack(side='left', padx=8)
        ttk.Label(top, text='Modello:').pack(side='left', padx=(10, 3))
        ttk.Entry(top, textvariable=self.ollama_model_var, width=22).pack(side='left')
        speed = ttk.Frame(options)
        speed.pack(fill='x', pady=(7, 0))
        ttk.Checkbutton(speed, text='Policy rapida (ML locale)', variable=self.fast_policy_var).pack(side='left')
        ttk.Label(speed, text='Scansione (s):').pack(side='left', padx=(13, 3))
        ttk.Spinbox(speed, from_=0.10, to=4.0, increment=.05, width=5,
                    textvariable=self.scan_interval_var).pack(side='left')
        ttk.Label(speed, text='Intervallo decisioni rapide (s):').pack(side='left', padx=(18, 4))
        ttk.Spinbox(speed, from_=0.10, to=5.0, increment=.05, width=6,
                    textvariable=self.fast_interval_var).pack(side='left')
        ttk.Checkbutton(speed, text='Cattura asincrona', variable=self.async_capture_var).pack(side='left', padx=12)
        ttk.Label(speed, text='Durata azione (s, mov. ≥0,28):').pack(side='left', padx=(5, 2))
        ttk.Spinbox(speed, from_=0.10, to=.50, increment=.02, width=5,
                    textvariable=self.fast_action_var).pack(side='left')
        ttk.Label(outer, text='FAST-ONLY senza Ollama: sulle situazioni sconosciute Oye attende. '
                  'Le modalità LIVE richiedono consenso; la routine a tempo non richiede HUD.', wraplength=900).pack(anchor='w')
        demo = ttk.Frame(options)
        demo.pack(fill='x', pady=(5, 0))
        ttk.Checkbutton(demo, text='Durante la fase 1, apprendi dai miei comandi di gioco (facoltativo)',
                        variable=self.demo_capture_var).pack(side='left')
        ttk.Checkbutton(demo, text='Includi clic sinistro (sparo)',
                        variable=self.demo_mouse_var).pack(side='left', padx=14)
        ttk.Label(demo, text='Solo Fortnite in primo piano; registra comandi consentiti, non testo. HUD incerto = revisione.',
                  wraplength=460).pack(side='left')
        guide = ttk.LabelFrame(options, text='Modalità guidata · cammina e piccona (nessun ML richiesto)', padding=8)
        guide.pack(fill='x', pady=(8, 4))
        ttk.Label(guide, text='Routine a tempo: avvia Gioca/Pronto, attendi 90 s (regolabili), premi Spazio '
                  'in una finestra limitata, attendi la discesa stimata e poi cammina e piccona. '
                  'Non richiede HUD, minimappa, piccone o bus_salto. I tempi NON certificano che sei a terra.',
                  wraplength=1030).pack(anchor='w')
        ttk.Checkbutton(guide, text='Routine a tempo (senza riconoscimento HUD per movimento)',
                        variable=self.guided_timed_var).pack(anchor='w', pady=(5, 0))
        ttk.Checkbutton(guide, text='Avvia timer quando premo il pulsante di avvio (anche se GIOCA non è calibrato); '
                        'se Oye clicca Gioca, il timer riparte da quel clic',
                        variable=self.guided_start_now_var).pack(anchor='w')
        timer_row = ttk.Frame(guide)
        timer_row.pack(fill='x', pady=(3, 4))
        for caption, var, low, high, step in (
            ('Caricamento (s)', self.guided_loading_var, 15, 240, 5),
            ('Spazio per (s)', self.guided_jump_window_var, 10, 120, 5),
            ('Discesa (s)', self.guided_fall_var, 15, 120, 5),
            ('Cammina max (s)', self.guided_ground_var, 60, 3600, 30)):
            ttk.Label(timer_row, text=caption+':').pack(side='left', padx=(8, 3))
            ttk.Spinbox(timer_row, from_=low, to=high, increment=step, width=6,
                        textvariable=var).pack(side='left')
        health_line = ttk.Frame(guide)
        health_line.pack(fill='x', pady=(4, 4))
        ttk.Checkbutton(health_line, text='Inizia Spazio appena riconosce la SALUTE (senza attendere 90 s)',
                        variable=self.guided_health_jump_var).pack(side='left', padx=(2, 10))
        for caption, variable, low, high, increment in (
            ('Soglia', self.guided_health_threshold_var, .45, .90, .01),
            ('Attesa minima (s)', self.guided_health_wait_var, 0, 60, 1),
            ('Spazio max (s)', self.guided_health_window_var, 10, 120, 5),
        ):
            ttk.Label(health_line, text=caption+':').pack(side='left', padx=(5, 3))
            ttk.Spinbox(health_line, from_=low, to=high, increment=increment, width=6,
                        textvariable=variable).pack(side='left')
        ttk.Label(guide,
                  text='Salute: richiede 3 fotogrammi nuovi. Non significa che sei sul bus: '
                       'può comparire anche prima del decollo o a terra. Se non appare, resta il timer.',
                  wraplength=1010).pack(anchor='w', pady=(1, 3))
        self.guided_manual = tk.Text(guide, height=3, wrap='word', font=('Segoe UI', 10))
        self.guided_manual.insert('1.0', self.cfg['guided_explore_manual'])
        self.guided_manual.pack(fill='x', pady=(4, 4))
        ttk.Checkbutton(guide, text='Usa piccone e clic sinistro per raccogliere',
                        variable=self.guided_harvest_var).pack(anchor='w')
        smooth_row = ttk.Frame(guide)
        smooth_row.pack(fill='x', pady=(4, 2))
        ttk.Checkbutton(smooth_row, text='Camminata fluida: mantieni W mentre guardi/picconi',
                        variable=self.guided_smooth_var).pack(side='left', padx=(0, 16))
        ttk.Label(smooth_row, text='Ampiezza rotazione mouse (px):').pack(side='left')
        ttk.Spinbox(smooth_row, from_=20, to=320, increment=10,
                    textvariable=self.guided_camera_var, width=5).pack(side='left', padx=(4, 16))
        ttk.Checkbutton(smooth_row, text='Partita SOLO: esci dai risultati confermati',
                        variable=self.guided_solo_var).pack(side='left')
        profile_row = ttk.Frame(guide)
        profile_row.pack(fill='x', pady=(5, 4))
        ttk.Label(profile_row, text='Stile di esplorazione:').pack(side='left', padx=(0, 8))
        profile_box = ttk.Combobox(profile_row, textvariable=self.guided_profile_var,
                                   state='readonly', width=17,
                                   values=('equilibrato', 'esploratore', 'raccoglitore'))
        profile_box.pack(side='left')
        ttk.Label(profile_row, text='Combina camminata, visuale, deviazioni, salti e piccone; '
                  'non rende più preciso il riconoscimento del gioco.',
                  wraplength=720).pack(side='left', padx=(14, 0))
        guided_timing = ttk.Frame(guide)
        guided_timing.pack(fill='x', pady=(4, 2))
        ttk.Label(guided_timing, text='Intervallo Spazio sul BUS (s):').pack(side='left')
        ttk.Spinbox(guided_timing, from_=0.75, to=5, increment=.25, width=6,
                    textvariable=self.guided_jump_period_var).pack(side='left', padx=(5, 18))
        ttk.Label(guided_timing, text='Tempo minimo di discesa prima di camminare (s):').pack(side='left')
        ttk.Spinbox(guided_timing, from_=8, to=60, increment=1, width=6,
                    textvariable=self.guided_min_fall_var).pack(side='left', padx=5)
        bindings = ttk.Frame(guide)
        bindings.pack(fill='x', pady=(5, 3))
        for key, caption in (('forward','Avanti'), ('back','Indietro'), ('left','Sinistra'),
                             ('right','Destra'), ('jump','Salto'), ('pickaxe','Piccone')):
            ttk.Label(bindings, text=caption + ':').pack(side='left', padx=(7, 3))
            ttk.Entry(bindings, width=7, textvariable=self.guided_key_vars[key]).pack(side='left')
        recovery = ttk.Frame(guide)
        recovery.pack(fill='x', pady=(4, 3))
        ttk.Label(recovery, text='Tentativi recupero ostacolo:').pack(side='left')
        ttk.Spinbox(recovery, from_=1, to=8, increment=1, width=4,
                    textvariable=self.guided_recovery_var).pack(side='left', padx=(5, 15))
        ttk.Label(recovery, text='Soglia variazione visiva (px):').pack(side='left')
        ttk.Spinbox(recovery, from_=.1, to=5, increment=.05, width=6,
                    textvariable=self.guided_motion_var).pack(side='left', padx=(5, 15))
        ttk.Checkbutton(recovery, text='Usa suggerimenti di movimento dalle mie dimostrazioni',
                        variable=self.guided_use_ml_var).pack(side='left')
        ttk.Label(guide, text='La variazione visiva non dimostra che W sia stato recepito: se Oye non avanza, '
                  'esegui la diagnostica W e verifica i binding di Fortnite. I suggerimenti ML non '
                  'possono premere menu, sparare o bypassare lo stato della partita.',
                  wraplength=1030).pack(anchor='w', pady=(2, 4))
        ttk.Label(guide, text='Per uscire dalle ricompense puoi calibrare il solo pulsante RISCATTA. '
                  'Il riconoscimento del pulsante è separato dalla minimappa e non richiede un HUD. '
                  'Se gli input sintetici vengono ignorati dal gioco, Oye non può forzarli.',
                  wraplength=1030).pack(anchor='w')
        ttk.Label(options, textvariable=self.demo_status_var, wraplength=1050,
                  foreground='#b36b00').pack(anchor='w', pady=(4, 0))
        buttons = ttk.Frame(options)
        buttons.pack(fill='x', pady=(7, 0))
        ttk.Button(buttons, text='1. Esplora e impara (nessun input)',
                   command=self._start_exploration).pack(side='left', padx=(0, 8))
        ttk.Button(buttons, text='2. Gioca con Oye (LIVE)',
                   command=self._start_autonomous).pack(side='left', padx=(0, 8))
        ttk.Button(buttons, text='3. Gioca → Bus → Cammina + Piccona',
                   command=self._start_guided).pack(side='left', padx=(0, 8))
        ttk.Button(buttons, text='Pausa / Riprendi', command=self._pause).pack(side='left', padx=7)
        ttk.Button(buttons, text='Arresta', command=self._stop).pack(side='left', padx=7)
        ttk.Button(buttons, text='Analizza screenshot da file…',
                   command=self._ai_analyze_file).pack(side='left', padx=(18, 5))
        ttk.Button(buttons, text='Aggiorna cronologia',
                   command=self._refresh_learning_history).pack(side='left', padx=5)
        ttk.Button(buttons, text='Apri archivio screenshot',
                   command=self._ai_open_archive).pack(side='left', padx=5)
        ttk.Button(buttons, text='Valuta apprendimento',
                   command=self._ai_training_report).pack(side='left', padx=5)
        input_diag = ttk.Frame(options)
        input_diag.pack(fill='x', pady=(5, 0))
        ttk.Button(input_diag, text='DIAGNOSTICA: prova W + risposta visiva',
                   command=self._probe_forward).pack(side='left', padx=(0, 10))
        ttk.Button(input_diag, text='DIAGNOSTICA: prova rotazione mouse',
                   command=self._probe_camera).pack(side='left', padx=(0, 10))
        ttk.Label(input_diag, text='3 s per tornare in partita; solo test manuali.',
                  wraplength=400).pack(side='left')
        migration_row = ttk.Frame(options)
        migration_row.pack(fill='x', pady=(4, 0))
        ttk.Button(migration_row, text='Importa partite da versione precedente…',
                   command=self._import_learning_archive).pack(side='left')
        ttk.Label(migration_row, text='Usalo solo se l’archivio della nuova versione è ancora vuoto.',
                  wraplength=630).pack(side='left', padx=10)
        telemetry = ttk.LabelFrame(outer, text='Monitor LIVE · cosa decide Oye e cosa osserva', padding=9)
        telemetry.pack(fill='x', pady=(10, 8))
        for left, right in ((('scene','Scena HUD'), ('phase','Fase stimata')),
                            (('capture','Cattura LIVE'), ('hud','Indizi HUD riconosciuti')),
                            (('objective','Obiettivo'), ('activity','Attività in corso')),
                            (('behavior_profile','Profilo comportamentale'), ('authorized','Input autorizzati')),
                            (('action','Ultima decisione'), ('recoveries','Recuperi ostacolo')),
                            (('reason','Motivo'), ('input_status','Invio input')),
                            (('visual_feedback','Risposta visiva'), ('camera_feedback','Rotazione telecamera')),
                            (('ocr_phase','OCR spettatore / riavvio'), None)):
            line = ttk.Frame(telemetry)
            line.pack(fill='x', pady=1)
            for entry in (left, right):
                if entry:
                    key, label = entry
                    field = ttk.Frame(line)
                    field.pack(side='left', fill='x', expand=True)
                    ttk.Label(field, text=label + ':', width=21).pack(side='left')
                    ttk.Label(field, textvariable=self.monitor_vars[key],
                              wraplength=340).pack(side='left', fill='x', expand=True)
        self.live_preview_status = tk.StringVar(value='Nessun fotogramma LIVE ricevuto.')
        live_preview = ttk.LabelFrame(telemetry, text='Fotogramma LIVE in RAM · stesso frame analizzato dal riconoscitore', padding=5)
        live_preview.pack(fill='x', pady=(7, 4))
        self.live_preview_label = ttk.Label(live_preview, text='Avvia il monitor per vedere la cattura LIVE')
        self.live_preview_label.pack(anchor='w')
        ttk.Label(live_preview, textvariable=self.live_preview_status, wraplength=950,
                  justify='left').pack(anchor='w', pady=(3, 1))
        ttk.Label(live_preview, text='Anteprima temporanea: non viene salvata nel database o tra gli screenshot delle partite autonome.',
                  wraplength=950).pack(anchor='w')
        ttk.Label(telemetry, text='Il monitor mostra l’invio del driver e indizi di movimento. '
                  'Non può attestare la ricezione dei comandi da parte di Fortnite.',
                  wraplength=1020).pack(anchor='w', pady=(6, 0))
        ttk.Label(options, textvariable=self.ai_agent_status, wraplength=1000).pack(anchor='w', pady=(5, 0))
        ttk.Label(options, text='Per OCR: installa Tesseract e pytesseract. '
                  'Per Ollama: installa un modello multimodale locale; le risposte non sono garantite.').pack(anchor='w', pady=(7, 0))
        ttk.Label(outer, text='Dopo la partita apri «Timeline · Revisione» per visualizzare eventi, '
                  'orari precisi, OCR, previsioni e modificare più immagini insieme.',
                  wraplength=1060).pack(anchor='w', pady=(10, 0))
        self._make_review_ui(self.review_tab)

    def _make_review_ui(self, host):
        canvas = tk.Canvas(host, highlightthickness=0, borderwidth=0)
        scroll = ttk.Scrollbar(host, orient='vertical', command=canvas.yview)
        scroll.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        canvas.configure(yscrollcommand=scroll.set)
        outer = ttk.Frame(canvas, padding=12)
        window = canvas.create_window((0, 0), window=outer, anchor='nw')
        outer.bind('<Configure>', lambda _event: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(window, width=event.width))
        ttk.Label(outer, text='Timeline · Revisione delle tue partite',
                  font=('Segoe UI', 18, 'bold')).pack(anchor='w')
        ttk.Label(outer, text='I segmenti di evento sono stime basate sui fotogrammi; '
                  'l’inizio/fine reali possono trovarsi fra due catture. '
                  'Le predizioni AI non sono conferme.', wraplength=1050).pack(anchor='w', pady=(4, 8))
        session_bar = ttk.Frame(outer)
        session_bar.pack(fill='x', pady=(0, 6))
        ttk.Label(session_bar, text='Partita:').pack(side='left')
        self.ai_session_var = tk.StringVar()
        self.ai_session_box = ttk.Combobox(session_bar, textvariable=self.ai_session_var,
                                           state='readonly', width=25)
        self.ai_session_box.pack(side='left', padx=(6, 12))
        self.ai_session_box.bind('<<ComboboxSelected>>', lambda _event: self._refresh_learning_history())
        ttk.Button(session_bar, text='Aggiorna', command=self._refresh_learning_history).pack(side='left', padx=3)
        ttk.Button(session_bar, text='Apri JPEG', command=self._ai_open_archive).pack(side='left', padx=3)
        self.ai_window_var = tk.StringVar(value='Nessuna partita archiviata')
        ttk.Label(outer, textvariable=self.ai_window_var, wraplength=1050).pack(anchor='w', pady=(0, 7))
        upper = ttk.Frame(outer)
        upper.pack(fill='x', pady=(0, 4))
        image_group = ttk.LabelFrame(upper, text='Screenshot del fotogramma selezionato', padding=8)
        image_group.pack(side='left', fill='both', expand=True, padx=(0, 8))
        image_group.configure(height=325)
        image_group.pack_propagate(False)
        self.ai_image = ttk.Label(image_group, text='Avvia una sessione o analizza un PNG per vedere la schermata.',
                                  anchor='center', justify='center')
        self.ai_image.pack(fill='both', expand=True)
        info_group = ttk.LabelFrame(upper, text='Ipotesi AI ed evidenze', padding=8, width=340)
        info_group.pack(side='right', fill='y')
        info_group.pack_propagate(False)
        self.ai_event_var = tk.StringVar(value='Evento: nessuna osservazione')
        self.ai_temporal_var = tk.StringVar(value='Fase temporale: in attesa di analisi')
        self.ai_source_var = tk.StringVar(value='Origine: —')
        self.ai_ocr_var = tk.StringVar(value='OCR: —')
        self.ai_change_var = tk.StringVar(value='Variazione immagine: —')
        for var in (self.ai_event_var, self.ai_temporal_var, self.ai_source_var, self.ai_ocr_var, self.ai_change_var):
            ttk.Label(info_group, textvariable=var, wraplength=320).pack(anchor='w', pady=(0, 7))
        ttk.Label(info_group, text='Testo identificato (OCR):').pack(anchor='w', pady=(8, 3))
        self.ai_ocr_text = tk.Text(info_group, height=9, wrap='word', font=('Consolas', 9))
        self.ai_ocr_text.pack(fill='both', expand=True)
        # The player stays above the event timeline so image, playhead and
        # IN/OUT tools are visible together on ordinary monitors.
        transport = ttk.Frame(outer)
        transport.pack(fill='x', pady=(3, 5))
        self.video_play_btn = ttk.Button(transport, text='▶ Play', width=11, command=self._video_toggle)
        self.video_play_btn.pack(side='left', padx=(0, 7))
        ttk.Button(transport, text='|◀', width=5, command=lambda: self._video_jump(False)).pack(side='left', padx=2)
        ttk.Button(transport, text='◀', width=5, command=lambda: self._video_step(-1)).pack(side='left', padx=2)
        ttk.Button(transport, text='▶', width=5, command=lambda: self._video_step(1)).pack(side='left', padx=2)
        ttk.Button(transport, text='▶|', width=5, command=lambda: self._video_jump(True)).pack(side='left', padx=2)
        ttk.Label(transport, text='Velocità:').pack(side='left', padx=(18, 4))
        self.video_fps_var = tk.StringVar(value='4 fps')
        self.video_fps_box = ttk.Combobox(transport, textvariable=self.video_fps_var, width=7,
                                         state='readonly', values=('1 fps', '2 fps', '4 fps', '8 fps', '12 fps'))
        self.video_fps_box.pack(side='left')
        self.video_fps_box.bind('<<ComboboxSelected>>', lambda _event: self._video_speed_change())
        self.video_range_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(transport, text='Riproduci solo IN–OUT', variable=self.video_range_var).pack(side='left', padx=12)
        self.video_sample_var = tk.StringVar(value='Nessun fotogramma')
        ttk.Label(transport, textvariable=self.video_sample_var, font=('Consolas', 10)).pack(side='right', padx=4)
        film = ttk.LabelFrame(outer, text='Pellicola · miniature cronologiche · clicca per saltare al fotogramma', padding=4)
        film.pack(fill='x', pady=(0, 8))
        self.filmstrip_canvas = tk.Canvas(film, height=102, bg='#121c29', highlightthickness=0,
                                          cursor='hand2')
        self.filmstrip_canvas.pack(fill='x')
        self.filmstrip_canvas.bind('<Configure>', lambda _event: self._filmstrip_draw())
        self.filmstrip_canvas.bind('<Button-1>', self._filmstrip_click)
        scrub_bar = ttk.Frame(outer)
        scrub_bar.pack(fill='x', pady=(0, 7))
        ttk.Label(scrub_bar, text='00:00').pack(side='left', padx=(3, 5))
        self.video_scrub_var = tk.DoubleVar(value=0.0)
        self._video_scrub_update = False
        self.video_scrub = ttk.Scale(scrub_bar, from_=0, to=1, variable=self.video_scrub_var,
                                      command=self._video_scrub)
        self.video_scrub.pack(side='left', fill='x', expand=True)
        self.video_end_var = tk.StringVar(value='00:00')
        ttk.Label(scrub_bar, textvariable=self.video_end_var).pack(side='left', padx=(5, 3))
        editor = ttk.LabelFrame(outer, text='Editor temporale · trascina sul righello per selezionare una scena', padding=8)
        editor.pack(fill='x', pady=(0, 8))
        ttk.Label(editor, text='Time-lapse dagli screenshot archiviati: nessun fotogramma inventato. Clic: scorri; trascina: IN–OUT. '
                  'Puoi spostare i bordi della selezione e annotare anche tratti privi di screenshot.',
                  wraplength=1000).pack(anchor='w', pady=(0, 6))
        self.temporal_status_var = tk.StringVar(value='Sequenza AI: apri una partita per vedere le fasi previste')
        ttk.Label(editor, textvariable=self.temporal_status_var,
                  wraplength=1000).pack(anchor='w', pady=(0, 6))
        self.range_canvas = tk.Canvas(editor, height=122, background='#161e2c',
                                      highlightbackground='#5c6780', highlightthickness=1,
                                      cursor='crosshair')
        self.range_canvas.pack(fill='x', expand=True)
        self.range_canvas.bind('<Configure>', lambda _event: self._range_draw())
        self.range_canvas.bind('<ButtonPress-1>', self._range_down)
        self.range_canvas.bind('<B1-Motion>', self._range_motion)
        self.range_canvas.bind('<ButtonRelease-1>', self._range_up)
        editor_control = ttk.Frame(editor)
        editor_control.pack(fill='x', pady=(7, 3))
        self.range_in_var = tk.StringVar(value='00:00:00.0')
        self.range_out_var = tk.StringVar(value='00:00:00.0')
        self.range_status_var = tk.StringVar(value='Nessuna sessione')
        ttk.Label(editor_control, text='IN:').pack(side='left')
        ttk.Entry(editor_control, textvariable=self.range_in_var, width=14).pack(side='left', padx=(3, 9))
        ttk.Label(editor_control, text='OUT:').pack(side='left')
        ttk.Entry(editor_control, textvariable=self.range_out_var, width=14).pack(side='left', padx=(3, 6))
        ttk.Button(editor_control, text='Usa questi tempi', command=self._range_from_entries).pack(side='left', padx=4)
        ttk.Button(editor_control, text='IN al cursore', command=self._range_set_in).pack(side='left', padx=4)
        ttk.Button(editor_control, text='OUT al cursore', command=self._range_set_out).pack(side='left', padx=4)
        ttk.Button(editor_control, text='Seleziona fotogrammi nel tratto',
                   command=self._range_select_frames).pack(side='right', padx=4)
        ttk.Label(editor, textvariable=self.range_status_var, wraplength=1000).pack(anchor='w', pady=(4, 6))
        editor_label = ttk.Frame(editor)
        editor_label.pack(fill='x')
        ttk.Label(editor_label, text='Cosa è successo nel tratto:').pack(side='left')
        self.range_event_var = tk.StringVar()
        ttk.Entry(editor_label, textvariable=self.range_event_var, width=36).pack(side='left', padx=7)
        ttk.Label(editor_label, text='Descrizione:').pack(side='left', padx=3)
        self.range_note_var = tk.StringVar()
        ttk.Entry(editor_label, textvariable=self.range_note_var, width=39).pack(side='left', padx=5, fill='x', expand=True)
        editor_buttons = ttk.Frame(editor)
        editor_buttons.pack(fill='x', pady=(6, 4))
        ttk.Button(editor_buttons, text='Salva annotazione e correggi tutti i fotogrammi del tratto',
                   command=self._range_apply).pack(side='left', padx=(0, 10))
        ttk.Button(editor_buttons, text='Conferma le AZIONI del tratto (HUD incerto)',
                   command=self._range_confirm_demonstrations).pack(side='left')
        editor_saved = ttk.Frame(editor)
        editor_saved.pack(fill='x', pady=(5, 0))
        cols = ('in', 'out', 'event', 'note', 'samples')
        self.range_list = ttk.Treeview(editor_saved, columns=cols, show='headings', height=3, selectmode='browse')
        for column, header, width in (('in','IN',94),('out','OUT',94),('event','Evento confermato',220),
                                      ('note','Dettaglio del tratto',380),('samples','Screenshot',80)):
            self.range_list.heading(column, text=header)
            self.range_list.column(column, width=width, stretch=column in ('event','note'))
        self.range_list.pack(side='left', fill='x', expand=True)
        self.range_list.bind('<<TreeviewSelect>>', self._range_pick_saved)
        editor_actions = ttk.Frame(editor_saved)
        editor_actions.pack(side='right', padx=(9, 0))
        ttk.Button(editor_actions, text='Annulla annotazione',
                   command=self._range_undo_saved).pack(fill='x', pady=2)
        ttk.Button(editor_actions, text='Rimuovi dall’elenco',
                   command=self._range_remove_saved).pack(fill='x', pady=2)
        ttk.Label(editor, text='Le etichette confermate sostituiscono quelle dei campioni nel tratto, '
                  'ma la predizione originale resta visibile. Eliminare un’annotazione non elimina le etichette già apprese.',
                  wraplength=1000).pack(anchor='w', pady=(5, 0))
        timeline = ttk.LabelFrame(outer, text='Fasi, attività ed eventi · intervalli stimati da screenshot', padding=7)
        timeline.pack(fill='x', pady=(0, 8))
        headings = (('da', 'Da (partita)', 120), ('a', 'A (partita)', 120),
                    ('evento', 'Evento / cambio stato', 360), ('motore', 'Motore', 130),
                    ('campioni', 'Fotogrammi', 90), ('revisioni', 'Revisionati', 100))
        self.ai_timeline = ttk.Treeview(timeline, columns=tuple(name for name, _, _ in headings),
                                        show='headings', height=6, selectmode='browse')
        for key, heading, width in headings:
            self.ai_timeline.heading(key, text=heading)
            self.ai_timeline.column(key, width=width, stretch=True)
        self.ai_timeline.pack(side='left', fill='x', expand=True)
        tl_scroll = ttk.Scrollbar(timeline, orient='vertical', command=self.ai_timeline.yview)
        tl_scroll.pack(side='right', fill='y')
        self.ai_timeline.configure(yscrollcommand=tl_scroll.set)
        self.ai_timeline.bind('<<TreeviewSelect>>', self._ai_timeline_select)
        correction = ttk.LabelFrame(outer, text='Insegna al modello: conferma o correggi il fotogramma selezionato', padding=8)
        correction.pack(fill='x', pady=8)
        first = ttk.Frame(correction)
        first.pack(fill='x')
        ttk.Label(first, text='Evento reale:').pack(side='left')
        self.ai_label_var = tk.StringVar()
        self.ai_event_entry = ttk.Combobox(first, textvariable=self.ai_label_var, width=39,
                                           values=tuple(dict.fromkeys(SCENE_EVENTS.values())) +
                                           ('eliminazione', 'vittoria', 'combattimento', 'atterraggio',
                                            'raccolta risorse', 'inventario', 'morte', 'movimento'))
        self.ai_event_entry.pack(side='left', padx=8)
        ttk.Label(first, text='Dettaglio (facoltativo):').pack(side='left', padx=(10, 3))
        self.ai_note_var = tk.StringVar()
        ttk.Entry(first, textvariable=self.ai_note_var, width=48).pack(side='left', fill='x', expand=True, padx=6)
        ttk.Button(first, text='Conferma / Correggi e apprendi', command=self._ai_correct).pack(side='right')
        teach = ttk.Frame(correction)
        teach.pack(fill='x', pady=(7, 0))
        ttk.Label(teach, text='Azione corretta in questa situazione:').pack(side='left')
        self.ai_action_var = tk.StringVar(value='wait')
        ttk.Combobox(teach, textvariable=self.ai_action_var, state='readonly', width=20,
                     values=tuple(name for name in LIMITS if name != 'aim')).pack(side='left', padx=8)
        ttk.Button(teach, text='Insegna azione (policy rapida)',
                   command=self._ai_teach_action).pack(side='left', padx=6)
        ttk.Label(teach, text='Servono più esempi di almeno due azioni; non viene registrata la tastiera.',
                  wraplength=530).pack(side='left', padx=12)
        history = ttk.LabelFrame(outer, text='Fotogrammi · Ctrl/Cmd e Maiusc per selezione multipla', padding=7)
        history.pack(fill='both', expand=True, pady=(0, 8))
        toolbar = ttk.Frame(history)
        toolbar.pack(fill='x', pady=(0, 5))
        ttk.Label(toolbar, text='Mostra:').pack(side='left')
        self.review_follow_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(toolbar, text='Segui nuove catture',
                        variable=self.review_follow_var).pack(side='right', padx=10)
        self.review_filter_var = tk.StringVar(value='Tutti')
        ttk.Combobox(toolbar, textvariable=self.review_filter_var, state='readonly', width=19,
                     values=('Tutti', 'Da rivedere', 'Confermati', 'Corretti', 'Incerti',
                             'Con OCR', 'Cambio evento', 'Cambio fase', 'Fase incerta')).pack(side='left', padx=(5, 10))
        self.review_filter_var.trace_add('write', lambda *_: self._refresh_learning_history())
        ttk.Button(toolbar, text='Seleziona visibili', command=self._ai_select_all).pack(side='left', padx=3)
        ttk.Button(toolbar, text='Deseleziona', command=self._ai_deselect).pack(side='left', padx=3)
        self.review_selected_var = tk.StringVar(value='0 fotogrammi selezionati')
        ttk.Label(toolbar, textvariable=self.review_selected_var).pack(side='right')
        columns = ('tempo', 'ora', 'stato', 'fase', 'fase_conf', 'predizione', 'confidenza', 'motore',
                   'hud', 'ocr', 'reale', 'azione')
        self.ai_history = ttk.Treeview(history, columns=columns, show='headings', height=10,
                                       selectmode='extended')
        for name, heading, width in (
                ('tempo', 'Partita', 88), ('ora', 'Ora locale', 87),
                ('stato', 'Revisione', 98), ('fase', 'Fase temporale', 134),
                ('fase_conf', 'Affidabilità fase', 100), ('predizione', 'Predizione AI', 170),
                ('confidenza', 'Punteggio', 90), ('motore', 'Motore', 120),
                ('hud', 'Stato HUD', 110), ('ocr', 'OCR', 95),
                ('reale', 'Evento corretto', 160), ('azione', 'Azione insegnata', 130)):
            self.ai_history.heading(name, text=heading)
            self.ai_history.column(name, width=width, stretch=name in ('predizione', 'reale', 'motore'))
        self.ai_history.tag_configure('corrected', foreground='#2879bb')
        self.ai_history.tag_configure('uncertain', foreground='#a0661f')
        self.ai_history.pack(side='left', fill='both', expand=True)
        scrollbar = ttk.Scrollbar(history, orient='vertical', command=self.ai_history.yview)
        self.ai_history.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        horizontal = ttk.Scrollbar(history, orient='horizontal', command=self.ai_history.xview)
        self.ai_history.configure(xscrollcommand=horizontal.set)
        horizontal.pack(side='bottom', fill='x')
        self.ai_history.bind('<<TreeviewSelect>>', self._ai_history_select)
        bulk = ttk.LabelFrame(outer, text='Modifica multipla · vengono cambiati solo i campi selezionati', padding=8)
        bulk.pack(fill='x', pady=(0, 8))
        group = ttk.Frame(bulk)
        group.pack(fill='x')
        self.bulk_event_var = tk.BooleanVar(value=True)
        self.bulk_action_var = tk.BooleanVar(value=False)
        self.bulk_note_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(group, text='Evento', variable=self.bulk_event_var).pack(side='left', padx=(0, 20))
        ttk.Checkbutton(group, text='Azione', variable=self.bulk_action_var).pack(side='left', padx=(0, 20))
        ttk.Checkbutton(group, text='Nota (sostituisci)', variable=self.bulk_note_var).pack(side='left')
        ttk.Label(group, text='Usa i valori dei campi qui sopra; campi non spuntati invariati.',
                  wraplength=600).pack(side='left', padx=14)
        controls = ttk.Frame(bulk)
        controls.pack(fill='x', pady=(6, 0))
        ttk.Button(controls, text='Applica ai fotogrammi selezionati',
                   command=self._ai_bulk_apply).pack(side='left', padx=(0, 8))
        ttk.Button(controls, text='Annulla ultima modifica multipla',
                   command=self._ai_bulk_undo).pack(side='left', padx=8)
        ttk.Label(controls, text='Tutte le modifiche sono atomiche e aggiornano il training ML.',
                  wraplength=500).pack(side='left', padx=12)
        final = ttk.LabelFrame(outer, text='Fine sessione: racconta cosa è successo e genera la cronologia', padding=8)
        final.pack(fill='x')
        select = ttk.Frame(final)
        select.pack(fill='x')
        ttk.Button(select, text='Salva resoconto', command=self._ai_save_narrative).pack(side='left', padx=4)
        ttk.Button(select, text='Visualizza riepilogo', command=self._ai_summary).pack(side='left', padx=4)
        ttk.Button(select, text='Esporta JSONL', command=self._ai_export).pack(side='left', padx=4)
        ttk.Button(select, text='Posso provare a farlo giocare?',
                   command=self._ai_training_report).pack(side='left', padx=4)
        self.ai_stats_var = tk.StringVar()
        ttk.Label(select, textvariable=self.ai_stats_var).pack(side='right')
        self.ai_narrative = tk.Text(final, height=3, wrap='word')
        self.ai_narrative.pack(fill='x', pady=(7, 0))
        self._refresh_learning_history()

    def _import_learning_archive(self):
        if self.runner and self.runner.thread and self.runner.thread.is_alive():
            messagebox.showwarning('Importazione', 'Arresta la sessione prima di importare dati.')
            return
        folder = filedialog.askdirectory(title='Seleziona cartella Oye precedente (oppure learning)')
        if not folder:
            return
        if self.learning_store.stats()['frames']:
            messagebox.showerror('Importazione',
                'L’archivio attuale non è vuoto. Non sostituisco dati già registrati. '
                'Puoi continuare a usarli o trasferire manualmente una copia di sicurezza.')
            return
        if not messagebox.askyesno('Importazione archivio',
                                  'Importare tutti gli screenshot e le azioni dalla cartella scelta? '
                                  'Non eliminare i dati originali finché non hai verificato il risultato.'):
            return
        try:
            from observer.migration import import_learning_archive
            result = import_learning_archive(self.learning_store.root, Path(folder))
            self.learning_store = LearningStore(ROOT / 'learning',
                           max_saved_frames=int(self.cfg['max_saved_frames']))
            self._readiness_cache = None
            self._refresh_learning_history()
            text = (f'Importati {result["frames"]} fotogrammi, '
                    f'{result["screenshots"]} JPEG e {result["actions"]} etichette d’azione. '
                    'Apri «Valuta apprendimento» per sapere quali sono utilizzabili.')
            self.status_var.set(text)
            messagebox.showinfo('Archivio importato', text)
        except Exception as exc:
            messagebox.showerror('Importazione non riuscita', str(exc))

    def _training_readiness(self):
        revision = self.learning_store.revision()
        if self._readiness_cache is None or self._readiness_cache[0] != revision:
            self._readiness_cache = (revision, inspect_training(self.learning_store))
        return self._readiness_cache[1]

    def _ai_training_report(self):
        try:
            report = self._training_readiness()
        except Exception as exc:
            messagebox.showerror('Controllo apprendimento fallito', str(exc))
            return
        popup = tk.Toplevel(self.root)
        popup.title('Oye · Prima prova autonoma')
        popup.geometry('680x550')
        content = tk.Text(popup, wrap='word', font=('Consolas', 10), padx=15, pady=15)
        content.insert('1.0', 'Archivio corrente: ' + str(self.learning_store.root) + '\n\n' + report.describe())
        content.configure(state='disabled')
        content.pack(fill='both', expand=True)
        ttk.Button(popup, text='Chiudi', command=popup.destroy).pack(pady=8)

    def _start_autonomous(self):
        self.guided_var.set(False)
        self.exploration_var.set(False)
        self.live_var.set(True)
        self.autonomous_var.set(True)
        # Respect the chosen engine: Ollama is optional when fast learning is enabled.
        self._start()

    def _start_guided(self):
        # Menu clicks use calibrated targets; timed gameplay requires no HUD.
        self.auto_var.set(True)
        self.exploration_var.set(False)
        self.live_var.set(True)
        self.autonomous_var.set(True)
        self.guided_var.set(True)
        self.shoot_var.set(False)
        self._start()

    def _start_exploration(self):
        if self.demo_capture_var.get() and self.backend_var.get() == 'gamepad':
            messagebox.showwarning('Registrazione comandi',
                'La dimostrazione automatica della Fase 1 rileva solo i tasti della tastiera e '
                'il clic sinistro autorizzato, NON i pulsanti del gamepad. '
                'Se stai giocando con un controller, salva gli screenshot e insegna le azioni nella timeline.')
        if self.demo_capture_var.get():
            self.demo_status_var.set('Pronto: tasti e clic consentiti verranno registrati anche se l’HUD è incerto. '
                'I comandi su scene incerte andranno confermati dopo la partita.')
        self.exploration_var.set(True)
        self.guided_var.set(False)
        self.autonomous_var.set(False)
        self.live_var.set(False)
        self.auto_var.set(False)
        self.shoot_var.set(False)
        self._start()

    def _start_safe_observation(self):
        self.exploration_var.set(False)
        self.guided_var.set(False)
        self.autonomous_var.set(False)
        self.live_var.set(False)
        self.auto_var.set(False)
        self.shoot_var.set(False)
        self._start()

    def _ai_open_archive(self):
        """Open only the local review archive; AI-play images are never stored here."""
        folder = self.learning_store.images
        folder.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == 'win32':
                os.startfile(str(folder))
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', str(folder)])
            else:
                subprocess.Popen(['xdg-open', str(folder)])
        except (OSError, AttributeError) as exc:
            messagebox.showerror('Archivio screenshot', f'Apri manualmente: {folder}\n{exc}')

    def _refresh_learning_history(self):
        sessions = self.learning_store.sessions()
        self.ai_session_box['values'] = sessions
        if self.ai_session_var.get() not in sessions:
            self.ai_session_var.set(self.active_session if self.active_session in sessions
                                    else sessions[0] if sessions else '')
        session = self.ai_session_var.get()
        old_selection = tuple(self.ai_history.selection())
        self.ai_history.delete(*self.ai_history.get_children())
        self.ai_timeline.delete(*self.ai_timeline.get_children())
        self.history_ids = {}
        self.timeline_ids = {}
        if not session:
            self.ai_window_var.set('Nessuna partita archiviata')
            self.review_selected_var.set('0 fotogrammi selezionati')
            self._temporal_spans = []
            self._temporal_estimates = {}
            self._activity_estimates = {}
            self._activity_spans = []
            self.temporal_status_var.set('Nessuna sequenza disponibile')
            self._range_refresh('', [], [], None)
            return
        # Session-relative time always starts at the recorded start marker,
        # falling back to the first capture for pre-v13 databases.
        rows = list(reversed(self.learning_store.recent(10000, session=session)))
        markers = self.learning_store.session_markers(session)
        insights = self.learning_store.session_insights(session)
        window = session_window(rows, markers)
        if window is None:
            return
        zero = window['start']
        self.ai_window_var.set(
            f"Inizio: {display_time(zero)}" + ('' if window['exact_start'] else ' (prima immagine)') +
            f" · Fine: {display_time(window['end'])}" +
            ('' if window['exact_end'] else ' (ultima immagine)') +
            f" · Durata osservata: {display_elapsed(window['duration'])}" +
            f" · {len(rows)} fotogrammi (intervallo configurabile)")
        spans = event_spans(rows)
        estimates = infer_states(rows, learned=self.learning_store.confirmed_stage_transitions())
        self._temporal_estimates = {estimate.frame_id: estimate for estimate in estimates}
        self._temporal_spans = inferred_spans(rows, estimates)
        activities = infer_activities(rows, estimates)
        self._activity_estimates = {activity.frame_id: activity for activity in activities}
        self._activity_spans = activity_spans(rows, activities)
        self._range_refresh(session, rows, spans, window)
        if self._temporal_spans:
            transitions = '  →  '.join(span['label'] for span in self._temporal_spans[:12])
            if len(self._temporal_spans) > 12:
                transitions += '  …'
            uncertain = sum(estimate.review_required for estimate in estimates)
            self.temporal_status_var.set(
                f'Fasi inferite: {transitions} · {uncertain} fotogrammi da verificare. '
                f'{len(self._activity_spans)} segmenti di attività identificati. '
                'Sequenza AI ≠ evento confermato: correggi tramite la timeline.')
        # The marker log is real observation metadata. Event spans are
        # predictions/corrections sampled from screenshots, not exact events.
        entries = []
        for index, span in enumerate(self._temporal_spans):
            entries.append((span['start'], -1, f'p{index}', span))
        for index, span in enumerate(spans):
            entries.append((span['start'], 1, f's{index}', span))
        for index, span in enumerate(self._activity_spans):
            entries.append((span['start'], 2, f'a{index}', span))
        for index, marker in enumerate(markers):
            entries.append((marker['occurred_at'], 0, f'm{index}', marker))
        for index, insight in enumerate(insights):
            entries.append((insight['captured_at'], 2, f'v{index}', insight))
        # Input changes are exact events, unlike inferred phase segments.
        for index, transition in enumerate(self.learning_store.session_input_transitions(session, limit=1200)):
            entries.append((transition['observed_at'], 3, f'i{index}', transition))
        entries.sort(key=lambda item: (datetime.fromisoformat(item[0].replace('Z', '+00:00')), item[1]))
        for at, _, key, item in entries:
            if key[0] == 'p':
                self.timeline_ids[key] = item['frame_ids']
                desc = ('da rivedere' if item['review_count'] else 'stima coerente')
                self.ai_timeline.insert('', 'end', iid=key,
                    values=(display_elapsed(elapsed_seconds(zero, item['start'])),
                            display_elapsed(elapsed_seconds(zero, item['end'])),
                            'FASE AI · ' + item['label'], 'modello temporale',
                            item['count'], f"{item['confidence']:.0%} · {desc}"))
            elif key[0] == 's':
                self.timeline_ids[key] = item['frame_ids']
                desc = ('Revisione' if item['confirmed'] == item['count'] else 'AI · stima')
                self.ai_timeline.insert('', 'end', iid=key,
                    values=(display_elapsed(elapsed_seconds(zero, item['start'])),
                            display_elapsed(elapsed_seconds(zero, item['end'])),
                            item['event'], item['sources'],
                            item['count'], f"{item['confirmed']}/{item['count']} · {desc}"))
            elif key[0] == 'a':
                self.timeline_ids[key] = item['frame_ids']
                quality = (f"{item['verified_count']}/{item['count']} confermati"
                           if item['verified_count'] else 'ipotesi da verificare')
                self.ai_timeline.insert('', 'end', iid=key,
                    values=(display_elapsed(elapsed_seconds(zero, item['start'])),
                            display_elapsed(elapsed_seconds(zero, item['end'])),
                            'ATTIVITÀ AI · ' + item['label'], 'contesto + eventi',
                            item['count'], quality))
            elif key[0] == 'm':
                caption = {'start': 'INIZIO OSSERVAZIONE',
                           'end': 'FINE OSSERVAZIONE', 'state': 'CAMBIO STATO HUD'}[item['kind']]
                self.timeline_ids[key] = []
                elapsed = display_elapsed(elapsed_seconds(zero, item['occurred_at']))
                self.ai_timeline.insert('', 'end', iid=key,
                    values=(elapsed, elapsed, caption + ' · ' + item['title'],
                            'sistema/HUD', '—', 'registrato'))
            elif key[0] == 'i':
                elapsed = display_elapsed(elapsed_seconds(zero, item['observed_at']))
                label = 'INPUT · ' + (' + '.join(item['actions']) if item['actions'] else 'rilascio tasti')
                self.timeline_ids[key] = []
                self.ai_timeline.insert('', 'end', iid=key,
                    values=(elapsed, elapsed, label, 'dimostrazione umana', '—', 'registrato'))
            else:
                elapsed = display_elapsed(elapsed_seconds(zero, item['captured_at']))
                self.timeline_ids[key] = []
                label = 'VISION (ipotesi) · ' + item['event']
                detail = ('incerto' if item['uncertain'] else 'ipotesi non verificata')
                self.ai_timeline.insert('', 'end', iid=key,
                    values=(elapsed, elapsed, label + ' · ' + item['description'][:130],
                            'Ollama Vision', '—', detail))
        starts = {span['first_id'] for span in spans}
        phase_starts = {span['frame_ids'][0] for span in self._temporal_spans}
        chosen_filter = self.review_filter_var.get()
        for row in rows:
            frame_id = row['id']
            state = review_state(row)
            if chosen_filter == 'Da rivedere' and row['user_label'] is not None:
                continue
            if chosen_filter == 'Confermati' and state != 'Confermato':
                continue
            if chosen_filter == 'Corretti' and state != 'Corretto':
                continue
            if chosen_filter == 'Incerti' and row['predicted_event'] != 'incerto' and row['predicted_score'] >= .5:
                continue
            if chosen_filter == 'Con OCR' and not row.get('ocr_text'):
                continue
            if chosen_filter == 'Cambio evento' and frame_id not in starts:
                continue
            estimate = self._temporal_estimates.get(frame_id)
            if chosen_filter == 'Cambio fase' and frame_id not in phase_starts:
                continue
            if chosen_filter == 'Fase incerta' and estimate and not estimate.review_required:
                continue
            self.history_ids[frame_id] = row
            tags = (('corrected',) if state == 'Corretto'
                    else ('uncertain',) if state == 'Da rivedere' and row['predicted_score'] < .5
                    else ())
            self.ai_history.insert('', 'end', iid=str(frame_id), tags=tags,
                values=(display_elapsed(elapsed_seconds(zero, row['captured_at'])),
                        display_time(row['captured_at']), state,
                        estimate.label if estimate else '—',
                        ('Confermata' if estimate.verified else f'{estimate.confidence:.0%} · stima') if estimate else '—',
                        row['predicted_event'], f"{row['predicted_score']:.0%}", row['predicted_source'],
                        row['hud_scene'], row['ocr_status'] or '—',
                        row['user_label'] or '—', row['action_label'] or '—'))
        still_present = tuple(item for item in old_selection if self.ai_history.exists(item))
        if still_present:
            self.ai_history.selection_set(still_present)
        self.review_selected_var.set(f'{len(self.ai_history.selection())} fotogrammi selezionati')
        stats = self.learning_store.stats()
        self.ai_stats_var.set(f"{stats['frames']} immagini · {stats['labeled']} eventi · "
                              f"{stats['actions']} azioni · {stats['input_transitions']} cambi input")
        self.ai_event_entry['values'] = tuple(dict.fromkeys(
            [*SCENE_EVENTS.values(), 'eliminazione', 'vittoria', 'combattimento', 'atterraggio',
             'raccolta risorse', 'inventario', 'movimento', *stats['classes'].keys()]))

    # Video-editor-style timeline for sampled human-play screenshots. No video decoding.
    def _range_refresh(self, session, rows, spans, window):
        fresh_session = session != self._range_session
        if fresh_session:
            self._video_pause()
            self._video_index = -1
            self._filmstrip_photos.clear()
        self._video_index_map = FrameIndex(rows, window['start'] if window else None)
        self._range_session = session
        self._range_origin = window['start'] if window else None
        self._range_duration = float(window['duration']) if window else 0.0
        self.video_scrub.configure(to=max(0.001, self._range_duration))
        self.video_end_var.set(timecode(self._range_duration, precision=0))
        self._range_rows = rows
        self._range_spans = spans
        self._range_annotations = self.learning_store.range_annotations(session) if session else []
        if fresh_session:
            self._range_start = 0.0
            self._range_end = min(15.0, self._range_duration)
            self._range_playhead = 0.0
            self._range_selected_id = None
            self.range_event_var.set('')
            self.range_note_var.set('')
        self._range_start = min(self._range_start, self._range_duration)
        self._range_end = min(self._range_end, self._range_duration)
        self._range_playhead = min(self._range_playhead, self._range_duration)
        self.range_list.delete(*self.range_list.get_children())
        for ann in self._range_annotations:
            start = elapsed_seconds(self._range_origin, ann['start_at'])
            end = elapsed_seconds(self._range_origin, ann['end_at'])
            self.range_list.insert('', 'end', iid=str(ann['id']), values=(
                timecode(max(0,start)), timecode(max(0,end)), ann['event'],
                ann['note'][:120], ann['frames_labeled']))
        if self._range_selected_id and self.range_list.exists(str(self._range_selected_id)):
            self.range_list.selection_set(str(self._range_selected_id))
        self._range_sync()
        if self._video_index_map:
            self._range_seek(self._range_playhead)
        else:
            self.video_sample_var.set('Nessuna immagine da rivedere')
            self._video_token = self._video_loader.invalidate_preview()
            self.ai_image.configure(image='', text='Nessuna cattura disponibile per questa sessione')
            self._filmstrip_draw()

    def _range_x(self, sec):
        width = max(100, self.range_canvas.winfo_width())
        return 24 + min(1.0, max(0.0, sec / max(0.1, self._range_duration))) * (width - 44)

    def _range_sec(self, x):
        width = max(100, self.range_canvas.winfo_width())
        return max(0., min(self._range_duration, (x - 24) / max(1., width - 44) * self._range_duration))

    def _range_draw(self):
        c = self.range_canvas
        c.delete('all')
        width = max(100, c.winfo_width())
        if not self._range_origin or self._range_duration <= 0:
            c.create_text(width // 2, 55, text='Apri una partita con almeno due istanti registrati',
                          fill='#c6d1e4')
            return
        c.create_rectangle(24, 34, width - 20, 100, fill='#263347', outline='#56657b')
        for i in range(9):
            sec = self._range_duration * i / 8
            x = self._range_x(sec)
            c.create_line(x, 23, x, 100, fill='#45546b')
            c.create_text(x, 13, text=timecode(sec, precision=0), fill='#d7e1ef',
                          font=('Consolas', 9))
        # Upper track: predicted game phases, distinct from custom event ranges.
        phase_colors = {'lobby': '#356a9c', 'loading': '#725a9b',
                        'match': '#276f69', 'spectator': '#996a36',
                        'results': '#97614a', 'unknown': '#4c586c'}
        for span in self._temporal_spans:
            a = self._range_x(elapsed_seconds(self._range_origin, span['start']))
            b = max(a + 2, self._range_x(elapsed_seconds(self._range_origin, span['end'])))
            c.create_rectangle(a, 35, b, 52, fill=phase_colors[span['state']], outline='')
            if b - a > 92:
                c.create_text((a+b)/2, 43, text=span['label'], fill='#eff5ff',
                              font=('Segoe UI', 8, 'bold'))
        palette = ['#416b90', '#37677b', '#655c91', '#6c6a38']
        for i, span in enumerate(self._range_spans):
            a = self._range_x(elapsed_seconds(self._range_origin, span['start']))
            b = self._range_x(elapsed_seconds(self._range_origin, span['end']))
            b = max(a + 2, b)
            c.create_rectangle(a, 53, b, 76, fill=palette[i % len(palette)], outline='')
            if b - a >= 80:
                c.create_text((a+b)/2, 64, text=span['event'][:16],
                              fill='white', font=('Segoe UI', 9))
        # Activity track is distinct from phase and raw predictions.
        activity_colors = {'combat': '#db675e', 'movement': '#2e9ca9',
                           'loot': '#c2a352', 'landing': '#9e85c9',
                           'healing': '#68b17b', 'inventory': '#8a9ab0',
                           'exploration': '#4d99a0'}
        for span in self._activity_spans:
            a = self._range_x(elapsed_seconds(self._range_origin, span['start']))
            b = max(a + 2, self._range_x(elapsed_seconds(self._range_origin, span['end'])))
            c.create_rectangle(a, 77, b, 83,
                               fill=activity_colors.get(span['activity'], '#667788'), outline='')
        # Compact sample pips, bounded to one per horizontal pixel.
        sample_x = {int(self._range_x(elapsed_seconds(self._range_origin, row['captured_at'])))
                    for row in self._range_rows}
        for x in sample_x:
            c.create_line(x, 77, x, 84, fill='#93b0c6')
        for ann in self._range_annotations:
            a = self._range_x(elapsed_seconds(self._range_origin, ann['start_at']))
            b = self._range_x(elapsed_seconds(self._range_origin, ann['end_at']))
            c.create_rectangle(a, 83, max(b, a + 2), 97, fill='#319b86', outline='')
        a = self._range_x(self._range_start)
        b = self._range_x(self._range_end)
        c.create_rectangle(a, 30, b, 102, fill='#b9ccf0', outline='#eef4ff', stipple='gray25', width=2)
        for x, label in ((a, 'IN'), (b, 'OUT')):
            c.create_line(x, 26, x, 107, fill='#f9e3a3', width=3)
            c.create_text(x, 115, text=label, fill='#f9e3a3', font=('Consolas', 8, 'bold'))
        p = self._range_x(self._range_playhead)
        c.create_line(p, 21, p, 105, fill='#ff8787', width=2, dash=(3, 2))

    def _range_sync(self):
        self.range_in_var.set(timecode(self._range_start))
        self.range_out_var.set(timecode(self._range_end))
        num = (len(sampled_in_range(self._range_rows, self._range_origin,
                                     self._range_start, self._range_end))
               if self._range_origin and self._range_end > self._range_start else 0)
        self.range_status_var.set(
            f'Cursore {timecode(self._range_playhead)} · Selezione {timecode(self._range_start)} '
            f'→ {timecode(self._range_end)} · {num} screenshot disponibili · '
            f'{len(self._range_annotations)} annotazioni confermate')
        self._range_draw()

    def _range_seek(self, sec):
        self._range_playhead = min(self._range_duration, max(0., sec))
        self._video_scrub_update = True
        try:
            self.video_scrub_var.set(self._range_playhead)
        finally:
            self._video_scrub_update = False
        frame_index = self._video_index_map.nearest(self._range_playhead)
        if frame_index >= 0 and frame_index != self._video_index:
            self._video_index = frame_index
            row = self._video_index_map.at(frame_index)
            # The preview and the current correction target MUST be identical:
            # otherwise dragging the filmstrip could teach an action to a stale
            # (previously selected) screenshot. Range labels use separate fields.
            self.last_learning_id = row['id']
            self.active_session = row['session']
            self._show_learning_record(row)
            self.video_sample_var.set(
                f'{frame_index + 1}/{len(self._video_index_map)}  '
                f'{timecode(self._video_index_map.times[frame_index])} · '
                f'{display_time(row["captured_at"])}')
            self._filmstrip_draw()
        self._range_sync()

    def _video_scrub(self, value):
        if getattr(self, '_video_scrub_update', False):
            return
        self._video_pause()
        self._range_seek(float(value))

    def _video_poll(self):
        for result in self._video_loader.poll():
            if result.kind == 'preview':
                if result.token != self._video_token:
                    continue
                if result.image is None:
                    self.ai_photo = None
                    self.ai_image.configure(image='', text=result.error or 'JPEG non disponibile')
                else:
                    self.ai_photo = ImageTk.PhotoImage(result.image)
                    self.ai_image.configure(image=self.ai_photo, text='')
            elif result.token == self._filmstrip_generation and result.index in self._filmstrip_visible:
                if result.image is not None:
                    x, y = self._filmstrip_visible[result.index]
                    photo = ImageTk.PhotoImage(result.image)
                    self._filmstrip_photos[result.index] = photo
                    self.filmstrip_canvas.create_image(x, y, image=photo, anchor='nw',
                                                         tags='thumb-images')
        if self._video_clock.playing:
            index = self._video_clock.tick(time.monotonic())
            if index >= 0 and index < len(self._video_index_map):
                if index != self._video_index:
                    self._range_seek(self._video_index_map.times[index])
            if not self._video_clock.playing:
                self.video_play_btn.configure(text='▶ Play')
        self.root.after(35, self._video_poll)

    def _video_pause(self):
        self._video_clock.stop()
        if hasattr(self, 'video_play_btn'):
            self.video_play_btn.configure(text='▶ Play')

    def _video_toggle(self):
        if self._video_clock.playing:
            self._video_pause()
            return
        if not self._video_index_map:
            return
        first, last = 0, len(self._video_index_map) - 1
        if self.video_range_var.get():
            first, last = self._video_index_map.within(self._range_start, self._range_end)
        if first < 0:
            messagebox.showwarning('Intervallo senza immagini', 'Scegli un intervallo che contenga almeno uno screenshot.')
            return
        speed = float(self.video_fps_var.get().split()[0])
        current = max(first, self._video_index)
        current = self._video_clock.start(index=current, first=first, last=last,
                                          fps=speed, now=time.monotonic())
        self._range_seek(self._video_index_map.times[current])
        self.video_play_btn.configure(text='⏸ Pausa')

    def _video_speed_change(self):
        if self._video_clock.playing:
            current = self._video_clock.index
            self._video_clock.start(index=current, first=self._video_clock.first,
                                    last=self._video_clock.last,
                                    fps=float(self.video_fps_var.get().split()[0]),
                                    now=time.monotonic())

    def _video_step(self, delta):
        if not self._video_index_map:
            return
        self._video_pause()
        index = min(len(self._video_index_map) - 1, max(0, self._video_index + delta))
        self._range_seek(self._video_index_map.times[index])

    def _video_jump(self, end):
        if not self._video_index_map:
            return
        self._video_pause()
        index = len(self._video_index_map) - 1 if end else 0
        self._range_seek(self._video_index_map.times[index])

    def _filmstrip_draw(self):
        if not hasattr(self, 'filmstrip_canvas'):
            return
        c = self.filmstrip_canvas
        c.delete('all')
        self._filmstrip_photos.clear()
        self._filmstrip_visible = {}
        self._filmstrip_generation += 1
        width = max(240, c.winfo_width())
        count = len(self._video_index_map)
        if not count:
            c.create_text(width / 2, 45, text='Nessun JPEG disponibile', fill='#b8c4d3')
            return
        tile_w, tile_h = 116, 68
        count_visible = max(1, min(count, (width - 10) // (tile_w + 6)))
        first = max(0, min(count - count_visible, self._video_index - count_visible // 2))
        for index in range(first, first + count_visible):
            x = 8 + (index - first) * (tile_w + 6)
            selected = index == self._video_index
            c.create_rectangle(x - 2, 0, x + tile_w + 2, 100,
                               fill='#2b415d' if selected else '#1d293b',
                               outline='#f8ca72' if selected else '#46586b', width=2 if selected else 1,
                               tags=('tile', f'frame_{index}'))
            c.create_rectangle(x, 4, x + tile_w, 72, fill='#253346', outline='',
                               tags=('tile', f'frame_{index}'))
            c.create_text(x + tile_w / 2, 38, text='· · ·', fill='#8699a9',
                          tags=('tile', f'frame_{index}'))
            c.create_text(x + tile_w / 2, 88,
                          text=timecode(self._video_index_map.times[index], precision=0),
                          fill='#ffe6b2' if selected else '#c6d2dd',
                          font=('Consolas', 9), tags=('tile', f'frame_{index}'))
            self._filmstrip_visible[index] = (x, 4)
        entries = [(i, self._video_index_map.at(i)) for i in self._filmstrip_visible]
        self._video_loader.request_thumbnails(entries, (tile_w, tile_h), self._filmstrip_generation)

    def _filmstrip_click(self, event):
        if not self._video_index_map:
            return
        tile_w = 122
        index = min(self._filmstrip_visible, key=lambda i: abs(
            self._filmstrip_visible[i][0] + 58 - event.x)) if self._filmstrip_visible else None
        if index is not None:
            self._video_pause()
            self._range_seek(self._video_index_map.times[index])

    def _range_down(self, e):
        x = self._range_sec(e.x)
        a, b = self._range_x(self._range_start), self._range_x(self._range_end)
        if abs(e.x - a) <= 9:
            self._range_drag = ('in', x)
        elif abs(e.x - b) <= 9:
            self._range_drag = ('out', x)
        else:
            self._range_drag = ('new', x)
        self._range_dragged = False
        self._range_seek(x)

    def _range_motion(self, e):
        if not self._range_drag:
            return
        mode, anchor = self._range_drag
        pos = self._range_sec(e.x)
        if abs(self._range_x(pos) - self._range_x(anchor)) > 3:
            self._range_dragged = True
        if mode == 'in':
            self._range_start = min(pos, self._range_end)
        elif mode == 'out':
            self._range_end = max(pos, self._range_start)
        else:
            self._range_start, self._range_end = sorted((anchor, pos))
        self._range_seek(pos)

    def _range_up(self, e):
        if self._range_drag is None:
            return
        mode, anchor = self._range_drag
        self._range_drag = None
        if not self._range_dragged and mode == 'new':
            self._range_seek(anchor)
        else:
            self._range_sync()

    def _range_from_entries(self):
        try:
            start = parse_timecode(self.range_in_var.get())
            end = parse_timecode(self.range_out_var.get())
            if not 0 <= start < end <= self._range_duration:
                raise ValueError('IN deve precedere OUT, entro la durata della sessione')
            self._range_start, self._range_end = start, end
            self._range_sync()
        except ValueError as exc:
            messagebox.showerror('Timecode non valido', str(exc))

    def _range_set_in(self):
        self._range_start = min(self._range_playhead, self._range_end)
        self._range_sync()

    def _range_set_out(self):
        self._range_end = max(self._range_playhead, self._range_start)
        self._range_sync()

    def _range_select_frames(self):
        if not self._range_origin or self._range_start >= self._range_end:
            messagebox.showwarning('Tratto vuoto', 'Trascina per selezionare un intervallo temporale.')
            return
        ids = sampled_in_range(self._range_rows, self._range_origin,
                               self._range_start, self._range_end)
        if not ids:
            messagebox.showinfo('Nessuna cattura', 'Nel tratto selezionato non ci sono screenshot.')
            return
        if self.review_filter_var.get() != 'Tutti':
            self.review_filter_var.set('Tutti')
        available = [str(i) for i in ids if self.ai_history.exists(str(i))]
        self.ai_history.selection_set(available)
        self.ai_history.see(available[0])
        self._ai_history_select()

    def _range_confirm_demonstrations(self):
        if not self._range_origin or self._range_start >= self._range_end:
            messagebox.showwarning('Tratto vuoto', 'Seleziona il tempo in cui stavi realmente giocando.')
            return
        if self.runner and self.runner.thread and self.runner.thread.is_alive():
            messagebox.showwarning('Sessione attiva', 'Termina la registrazione prima di confermare.')
            return
        message = ('Hai controllato le immagini da IN a OUT e sei sicuro che STAVI GIOCANDO?\n\n'
                   'Le azioni registrate in quei fotogrammi con HUD incerto diventeranno '
                   'esempi per la policy rapida. Lobby, caricamento e spettatore rimangono esclusi.\n\n'
                   'Non confermare intervalli in cui scrivevi nella chat del gioco.')
        if not messagebox.askyesno('Conferma dimostrazione di gioco', message):
            return
        from datetime import timezone
        try:
            start_at = (parse_time(self._range_origin) + timedelta(seconds=self._range_start)).astimezone(timezone.utc).isoformat()
            end_at = (parse_time(self._range_origin) + timedelta(seconds=self._range_end)).astimezone(timezone.utc).isoformat()
            changed = self.learning_store.confirm_demonstration_range(self._range_session, start_at, end_at)
            self._readiness_cache = None
            self._refresh_learning_history()
            messagebox.showinfo('Dimostrazione verificata',
                f'{changed} esempi ora utilizzabili dalla policy rapida.\n'
                'Se il risultato è 0, controlla che ci siano azioni dimostrate nel tratto.')
        except Exception as exc:
            messagebox.showerror('Conferma non riuscita', str(exc))

    def _range_apply(self):
        if not self._range_origin or self._range_start >= self._range_end:
            messagebox.showwarning('Tratto vuoto', 'Seleziona prima una parte della partita.')
            return
        event = self.range_event_var.get().strip()
        note = self.range_note_var.get().strip()
        if not event:
            messagebox.showwarning('Evento mancante', 'Descrivi cosa è successo nel tratto.')
            return
        ids = sampled_in_range(self._range_rows, self._range_origin,
                               self._range_start, self._range_end)
        if not messagebox.askyesno('Annota il tratto',
                f'Confermi «{event}» tra {timecode(self._range_start)} e {timecode(self._range_end)}?\n'
                f'Verranno corrette le etichette di {len(ids)} screenshot.\n'
                'Le predizioni originali non saranno modificate.'):
            return
        from datetime import timezone
        try:
            start_at = (parse_time(self._range_origin) + timedelta(seconds=self._range_start)).astimezone(timezone.utc).isoformat()
            end_at = (parse_time(self._range_origin) + timedelta(seconds=self._range_end)).astimezone(timezone.utc).isoformat()
            result = self.learning_store.annotate_interval(session=self._range_session,
                        start_at=start_at, end_at=end_at, event=event, note=note)
            self.last_bulk_undo = None
            self._range_selected_id = result['id']
            self._refresh_learning_history()
            self.status_var.set(f'Intervallo confermato: {result["frames_labeled"]} esempi corretti')
            self._log(f'[Intervallo] {event} · {result["frames_labeled"]} screenshot')
        except Exception as exc:
            messagebox.showerror('Annotazione non salvata', str(exc))

    def _range_pick_saved(self, _event=None):
        selected = self.range_list.selection()
        if not selected or not self._range_origin:
            return
        ann = next((item for item in self._range_annotations if str(item['id']) == selected[0]), None)
        if not ann:
            return
        self._range_selected_id = ann['id']
        self._range_start = max(0., elapsed_seconds(self._range_origin, ann['start_at']))
        self._range_end = min(self._range_duration, elapsed_seconds(self._range_origin, ann['end_at']))
        self.range_event_var.set(ann['event'])
        self.range_note_var.set(ann['note'])
        self._range_seek(self._range_start)

    def _range_delete_selected(self, undo):
        selected = self.range_list.selection()
        if not selected:
            messagebox.showwarning('Nessuna annotazione', 'Seleziona un intervallo salvato nell’elenco.')
            return
        ann_id = int(selected[0])
        message = ('Annullare anche le etichette dei fotogrammi? Funziona soltanto '
                   'se non hai eseguito altre revisioni dopo questa annotazione.' if undo else
                   'Rimuovere l’annotazione dall’elenco? Le etichette apprese dai '
                   'fotogrammi rimarranno inalterate.')
        if not messagebox.askyesno('Rimozione intervallo', message):
            return
        try:
            self.learning_store.delete_interval(ann_id, session=self._range_session, undo_labels=undo)
            self._range_selected_id = None
            self.last_bulk_undo = None
            self._refresh_learning_history()
        except Exception as exc:
            messagebox.showerror('Impossibile annullare', str(exc))

    def _range_undo_saved(self):
        self._range_delete_selected(True)

    def _range_remove_saved(self):
        self._range_delete_selected(False)

    def _handle_learning_record(self, record):
        frame_id = int(record['frame_id'])
        self.active_session = record['session']
        # Never interrupt a person reviewing an earlier session or overwrite
        # a multi-selection every time a fresh screenshot is analyzed.
        selected = tuple(self.ai_history.selection())
        if not self.ai_session_var.get() or self.ai_session_var.get() == record['session']:
            self.ai_session_var.set(record['session'])
            self._refresh_learning_history()
            if (len(selected) <= 1 and self.review_follow_var.get() and
                    str(frame_id) in self.ai_history.get_children()):
                self.last_learning_id = frame_id
                self.ai_history.selection_set(str(frame_id))
                self.ai_history.see(str(frame_id))
                self._show_learning_record(self.history_ids.get(frame_id, record))
        self._log(f"[AI] #{frame_id} → {record['prediction']} "
                  f"({record['source']} {record['score']:.2f}, OCR={record['ocr_status']})" +
                  (f" · FASE {record['phase']} ({record['phase_reason']})" if record.get('phase') else ''))

    def _ai_timeline_select(self, _event=None):
        selected = self.ai_timeline.selection()
        if not selected:
            return
        frame_ids = self.timeline_ids.get(selected[0], [])
        if not frame_ids:
            return  # lifecycle/state markers do not imply review labels
        # A segment should be selectable in its entirety, independently of
        # the currently active filter.
        if self.review_filter_var.get() != 'Tutti':
            self.review_filter_var.set('Tutti')
        available = [str(number) for number in frame_ids if self.ai_history.exists(str(number))]
        if available:
            self.ai_history.selection_set(available)
            self.ai_history.see(available[0])
            self._ai_history_select()

    def _ai_select_all(self):
        ids = self.ai_history.get_children()
        if ids:
            self.ai_history.selection_set(ids)
            self._ai_history_select()

    def _ai_deselect(self):
        selected = self.ai_history.selection()
        if selected:
            self.ai_history.selection_remove(selected)
        self.review_selected_var.set('0 fotogrammi selezionati')

    def _ai_bulk_apply(self):
        selected = tuple(int(i) for i in self.ai_history.selection())
        if not selected:
            messagebox.showwarning('Selezione vuota', 'Seleziona prima almeno un fotogramma.')
            return
        session = self.ai_session_var.get()
        event = self.ai_label_var.get() if self.bulk_event_var.get() else None
        action = self.ai_action_var.get() if self.bulk_action_var.get() else None
        note = self.ai_note_var.get() if self.bulk_note_var.get() else None
        if event is None and action is None and note is None:
            messagebox.showwarning('Campi mancanti', 'Spunta almeno uno tra Evento, Azione e Nota.')
            return
        summary = ', '.join(part for part in (f'Evento: {event}' if event is not None else '',
                          f'Azione: {action}' if action is not None else '',
                          'Sostituisci nota' if note is not None else '') if part)
        if not messagebox.askyesno('Conferma modifica multipla',
                f'Modificare {len(selected)} fotogrammi della sessione {session}?\n\n{summary}\n\n'
                'La predizione originale del motore resterà invariata.'):
            return
        try:
            snapshots = self.learning_store.bulk_update(selected, session=session,
                             event=event, action=action, note=note)
            self.last_bulk_undo = (session, snapshots)
            self._refresh_learning_history()
            self._log(f'[Revisione] {len(snapshots)} fotogrammi modificati: {summary}')
            self.status_var.set(f'{len(snapshots)} etichette/azioni aggiornate nel dataset locale')
        except Exception as exc:
            messagebox.showerror('Modifica multipla fallita', str(exc))

    def _ai_bulk_undo(self):
        if not self.last_bulk_undo:
            messagebox.showinfo('Niente da annullare', 'Non hai ancora applicato modifiche multiple.')
            return
        session, snapshots = self.last_bulk_undo
        if session != self.ai_session_var.get():
            messagebox.showwarning('Sessione differente', 'Torna alla sessione modificata per annullare.')
            return
        if not messagebox.askyesno('Annulla modifiche',
                                   f'Ripristinare i {len(snapshots)} fotogrammi precedenti?'):
            return
        try:
            count = self.learning_store.restore_bulk(snapshots, session=session)
            self.last_bulk_undo = None
            self._refresh_learning_history()
            self._log(f'[Revisione] Ripristinati {count} fotogrammi')
        except Exception as exc:
            messagebox.showerror('Impossibile annullare', str(exc))

    def _show_learning_record(self, record):
        # Decode in a worker. Never block a drag event with JPEG disk I/O.
        image_index = self._video_index_map.nearest(
            elapsed_seconds(self._range_origin, record['captured_at'])) if (
                self._range_origin and record.get('captured_at')) else -1
        self._video_token = self._video_loader.request_preview(record, image_index, (750, 290))
        prediction = record.get('prediction', record.get('predicted_event', 'incerto'))
        score = float(record.get('score', record.get('predicted_score', 0)))
        source = record.get('source', record.get('predicted_source', ''))
        self.ai_event_var.set(f'Ipotesi AI: {prediction} · punteggio {score:.0%} (non probabilità calibrata)')
        estimate = self._temporal_estimates.get(record.get('id', record.get('frame_id')))
        if estimate:
            expected = ', '.join(NAMES[s] for s in NEXT[estimate.state]) or 'da stabilire'
            self.ai_temporal_var.set(
                f'Fase stimata: {estimate.label} · affidabilità indicativa {estimate.confidence:.0%} · '
                f'{estimate.reason} · sviluppi plausibili: {expected}' +
                (f" · attività: {self._activity_estimates[estimate.frame_id].label}"
                 if estimate.frame_id in self._activity_estimates and
                 self._activity_estimates[estimate.frame_id].activity != 'unknown' else '') +
                (' · DA RIVEDERE' if estimate.review_required else ''))
        elif record.get('phase'):
            self.ai_temporal_var.set('Fase stimata: ' + record['phase'] + ' · ' +
                                     record.get('phase_reason', 'stima automatica'))
        else:
            self.ai_temporal_var.set('Fase temporale: in attesa di analisi')
        try:
            controls = json.loads(record.get('observed_controls') or '[]')
        except (ValueError, TypeError):
            controls = []
        self.ai_source_var.set('Motore: ' + source +
                               (' · ' + record['description'] if record.get('description') else '') +
                               (f" · azione: {record['action_label']} ({record.get('action_source', 'manual')})"
                                if record.get('action_label') else '') +
                               (' · CONTROLLI SIMULTANEI: ' + ', '.join(controls) if controls else '') +
                               (' · DA CONFERMARE' if record.get('action_source') == 'demo' and
                                record.get('hud_scene') == 'unknown' and not record.get('demo_verified') else ''))
        self.ai_ocr_var.set('OCR: ' + record.get('ocr_status', '—') +
                            ' · acquisito ' + display_time(record.get('captured_at')))
        self.ai_change_var.set(f"Variazione pixel: {record.get('change', record.get('screen_change', 0)):.1%}")
        self.ai_ocr_text.delete('1.0', 'end')
        self.ai_ocr_text.insert('1.0', record.get('ocr_text', ''))
        self.ai_label_var.set(record.get('user_label') or prediction)
        self.ai_action_var.set(record.get('action_label') or 'wait')
        self.ai_note_var.set(record.get('user_note', ''))

    def _ai_history_select(self, event=None):
        selected = self.ai_history.selection()
        self.review_selected_var.set(f'{len(selected)} fotogrammi selezionati')
        if not selected:
            return
        # The first selection is the preview. Do not edit every selected row
        # unless the explicit bulk button is clicked.
        row = self.history_ids.get(int(selected[0]))
        if row:
            self.last_learning_id = row['id']
            self.active_session = row['session']
            if self._range_origin:
                self._video_pause()
                self._range_seek(elapsed_seconds(self._range_origin, row['captured_at']))
                # A table selection is an explicit edit target, unlike scrubbing.
                self.ai_label_var.set(row.get('user_label') or row.get('predicted_event', 'incerto'))
                self.ai_action_var.set(row.get('action_label') or 'wait')
                self.ai_note_var.set(row.get('user_note', ''))
            else:
                self._show_learning_record(row)

    def _ai_correct(self):
        if self.last_learning_id is None:
            messagebox.showwarning('Nessun fotogramma', 'Seleziona un fotogramma nella cronologia.')
            return
        try:
            self.learning_store.correct(self.last_learning_id, self.ai_label_var.get(), self.ai_note_var.get())
            self.last_bulk_undo = None
            self._refresh_learning_history()
            self._log(f"Esempio #{self.last_learning_id} etichettato: {self.ai_label_var.get()}")
            self.status_var.set('Correzione salvata: il modello userà il nuovo esempio dalla prossima analisi')
        except Exception as exc:
            messagebox.showerror('Correzione fallita', str(exc))

    def _ai_teach_action(self):
        if self.last_learning_id is None:
            messagebox.showwarning('Nessun fotogramma', 'Seleziona prima un fotogramma.')
            return
        try:
            row = next((r for r in self.learning_store.recent(10000)
                        if r['id'] == self.last_learning_id), None)
            if row and row['hud_scene'] != 'match':
                from observer.temporal import event_stage
                if event_stage(row.get('user_label')) != 'match':
                    if not messagebox.askyesno('Conferma schermata di gioco',
                            'Il riconoscimento HUD non ha identificato questo fotogramma come partita.\n\n'
                            'Confermi che qui STAVI GIOCANDO e vuoi insegnare questo comando? '
                            'L’evento verrà corretto in «partita in corso».'):
                        return
                    self.learning_store.correct(self.last_learning_id, 'partita in corso',
                                                row.get('user_note') or '')
            self.learning_store.teach_action(self.last_learning_id, self.ai_action_var.get())
            self.last_bulk_undo = None
            self._refresh_learning_history()
            self._log(f"Esempio #{self.last_learning_id} → azione {self.ai_action_var.get()}")
            self.status_var.set('Azione salvata per la policy rapida supervisionata')
        except Exception as exc:
            messagebox.showerror('Esempio non salvato', str(exc))

    def _ai_analyze_file(self):
        filename = filedialog.askopenfilename(title='Screenshot da analizzare',
                          filetypes=[('Immagini', '*.png *.jpg *.jpeg *.bmp')])
        if not filename:
            return
        cfg = self.cfg.copy()
        cfg.update(ocr_enabled=self.ocr_enabled_var.get(), ollama_enabled=self.ollama_enabled_var.get(),
                   ollama_model=self.ollama_model_var.get())
        self._log('Analisi locale avviata: ' + Path(filename).name)
        def task():
            try:
                image = cv2.imread(filename)
                if image is None:
                    raise ValueError('Immagine non leggibile')
                scene = Vision(ROOT / 'templates', cfg['template_threshold']).recognize(image).scene
                engine = LearningEngine(ROOT / 'learning', ocr_enabled=cfg['ocr_enabled'],
                           ocr_languages=cfg['ocr_languages'], max_saved_frames=int(cfg['max_saved_frames']),
                           ollama_enabled=cfg['ollama_enabled'], ollama_model=cfg['ollama_model'],
                           ollama_every=1, ollama_timeout=cfg['ollama_timeout_seconds'])
                result = engine.analyze(image, scene=scene, process='screenshot importato',
                                        session='import_' + time.strftime('%Y%m%d'))
                self.offline_results.put_nowait(result)
            except Exception as exc:
                self.offline_results.put_nowait(exc)
        threading.Thread(target=task, daemon=True, name='OfflineImageAnalysis').start()

    def _ai_save_narrative(self):
        session = self.ai_session_var.get()
        if not session:
            messagebox.showinfo('Sessione mancante', 'Acquisisci prima almeno uno screenshot.')
            return
        try:
            self.learning_store.annotate_session(session, self.ai_narrative.get('1.0', 'end'))
            if re.fullmatch(r'[A-Za-z0-9_-]{1,80}', session):
                folder = ROOT / 'reports'
                folder.mkdir(exist_ok=True)
                (folder / f'partita_{session}.txt').write_text(
                    self.learning_store.session_summary(session), encoding='utf-8')
            self._log('Resoconto di sessione e memoria salvati per ' + session)
        except Exception as exc:
            messagebox.showerror('Resoconto non salvato', str(exc))

    def _ai_summary(self):
        session = self.ai_session_var.get()
        if not session:
            messagebox.showinfo('Sessione mancante', 'Non ci sono ancora osservazioni.')
            return
        summary = self.learning_store.session_summary(session)
        popup = tk.Toplevel(self.root)
        popup.title('Cronologia della sessione ' + session)
        popup.geometry('760x650')
        text = tk.Text(popup, wrap='word', font=('Consolas', 10))
        text.pack(fill='both', expand=True, padx=8, pady=8)
        text.insert('1.0', summary)

    def _ai_export(self):
        session = self.ai_session_var.get()
        target = filedialog.asksaveasfilename(title='Esporta metadati e correzioni',
                   defaultextension='.jsonl', filetypes=[('JSON Lines', '*.jsonl')])
        if target:
            try:
                count = self.learning_store.export_jsonl(Path(target), session=session or None)
                self._log(f'Esportati {count} fotogrammi in JSONL')
            except Exception as exc:
                messagebox.showerror('Esportazione fallita', str(exc))

    def _close(self):
        self._video_pause()
        self._video_loader.close()
        self._stop()
        if self.runner:
            self.runner.join(timeout=4)
        if self.listener:
            self.listener.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use('clam')
    except tk.TclError:
        pass
    Studio(root)
    root.mainloop()


if __name__ == '__main__':
    main()
