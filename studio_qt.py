"""Oye Studio: clean PySide6 control room using the existing observer engine.

LIVE is strictly opt-in per session. A minimized window never bypasses the
Runner's window-focus checks; OCR phase locks remain in the unchanged engine.
"""
from __future__ import annotations

import io
import json
import os
import re
import queue
import subprocess
import sys
import time
import threading
from collections import deque
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import Qt, QRect, QPoint, QTimer, Signal, QSize
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QFont, QIcon, QImage
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QStackedWidget, QScrollArea, QComboBox,
    QCheckBox, QDoubleSpinBox, QSpinBox, QFileDialog, QMessageBox, QTextEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QProgressBar,
    QSpacerItem, QSizePolicy, QLineEdit, QSlider,
)

from observer.config import load, save, TEMPLATE_NAMES
from observer.qt_frontend_logic import mode_flags, phase_display, save_calibration
from observer.runner import Runner
from observer.windows import snapshot, foreground_window, enable_dpi_awareness, close_capture
from observer.hud import HUD_CORE
from observer.learning import LearningStore, SCENE_EVENTS
from observer.autopilot import LIMITS
from observer.readiness import inspect_training
from observer.vision import Vision

ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent

C = {
    'bg': '#0B0F17', 'sidebar': '#111722', 'card': '#151D2A', 'card2': '#1A2434',
    'edge': '#2B3545', 'white': '#F1F4FF', 'muted': '#91A0B7',
    'accent': '#AA91FF', 'accent2': '#352951', 'green': '#76D7AD', 'yellow': '#F8CC7A',
    'red': '#F28C96',
}

QSS = '''
* { font-family: 'Segoe UI'; font-size: 12px; color: #F1F4FF; }
QMainWindow, QWidget#root, QScrollArea#scroll, QWidget#scrollBody { background: #0B0F17; }
QFrame#sidebar { background: #111722; border-right: 1px solid #2B3545; }
QFrame#card { background: #151D2A; border: 1px solid #2B3545; border-radius: 16px; }
QFrame#subcard { background: #1A2434; border: 1px solid #2B3545; border-radius: 11px; }
QFrame#image { background: #090D15; border-radius: 12px; border: 1px solid #344052; }
QLabel { background: transparent; }
QLabel#eyebrow { color: #AA91FF; font-size: 11px; font-weight: 700; }
QLabel#title { font-size: 29px; font-weight: 750; }
QLabel#section { font-size: 17px; font-weight: 650; }
QLabel#muted { color: #91A0B7; font-size: 12px; }
QLabel#metric { font-size: 18px; font-weight: 650; }
QLabel#status { color: #76D7AD; font-weight: 650; }
QPushButton { background: #263244; border: 1px solid #344052; border-radius: 9px;
    padding: 10px 14px; min-height: 17px; font-weight: 600; }
QPushButton:hover { background: #344057; border-color: #65748F; }
QPushButton:pressed { background: #415070; }
QPushButton:disabled { background: #202938; color: #667288; border-color: #273040; }
QPushButton#primary { background: #AA91FF; color: #0E0B1B; border: none; font-weight: 750; }
QPushButton#primary:hover { background: #C3B0FF; }
QPushButton#danger { background: #422630; border-color: #70404B; color: #FFC4C8; }
QPushButton#danger:hover { background: #58303C; }
QPushButton#nav { background: transparent; border: 0; border-radius: 9px; text-align: left;
    padding: 14px 16px; font-size: 13px; color: #9AA7BC; }
QPushButton#nav:hover { background: #1C2534; color: #F1F4FF; }
QPushButton#nav:checked { background: #302A47; color: #D6C9FF; }
QPushButton#mode { background: #151D2A; border: 1px solid #303A4A;
    border-radius: 12px; text-align: left; padding: 15px; font-size: 13px; font-weight: 700; min-height: 58px; }
QPushButton#mode:hover { background: #222B3C; }
QPushButton#mode:checked { background: #29233F; border: 2px solid #AA91FF; color: #E8DDFF; }
QComboBox, QDoubleSpinBox, QSpinBox { background: #1C2635; border: 1px solid #344052;
    border-radius: 8px; padding: 8px 10px; min-height: 22px; }
QComboBox QAbstractItemView { background: #1C2635; color: white; selection-background-color: #51457E; }
QCheckBox { color: #DFE5F1; spacing: 9px; padding: 4px 0; }
QCheckBox::indicator { width: 17px; height: 17px; border: 1px solid #637087;
    border-radius: 5px; background: #1B2533; }
QCheckBox::indicator:checked { background: #AA91FF; border-color: #AA91FF; }
QTextEdit, QTableWidget { background: #111722; border: 1px solid #2B3545;
    border-radius: 8px; color: #DCE5F3; padding: 8px; }
QHeaderView::section { background: #1D2735; padding: 10px; color: #9EACC1; border: none; }
QTableWidget::item { padding: 7px; border-bottom: 1px solid #283241; }
QTableWidget::item:selected { background: #41375C; }
QScrollBar:vertical { width: 8px; background: #0B0F17; }
QScrollBar::handle:vertical { background: #344057; border-radius: 4px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QProgressBar { background: #202939; border: none; border-radius: 4px; height: 8px; }
QProgressBar::chunk { background: #AA91FF; border-radius: 4px; }
'''


def label(text='', role=None, wrap=False):
    out = QLabel(str(text))
    if role:
        out.setObjectName(role)
    out.setWordWrap(wrap)
    return out


def card(layout=None, margin=20):
    outer = QFrame(); outer.setObjectName('card')
    inner = layout or QVBoxLayout()
    inner.setContentsMargins(margin, margin, margin, margin)
    inner.setSpacing(12)
    outer.setLayout(inner)
    return outer


def section_title(text, note=''):
    box = QVBoxLayout(); box.setSpacing(4)
    box.addWidget(label(text, 'section'))
    if note: box.addWidget(label(note, 'muted', True))
    return box


def vspace(height=8):
    s=QWidget(); s.setFixedHeight(height); return s


class ImagePanel(QFrame):
    """Aspect-fit image with an unobtrusive placeholder."""
    def __init__(self, placeholder='Anteprima disponibile quando avvii la sessione'):
        super().__init__(); self.setObjectName('image')
        self._pixmap = QPixmap(); self._placeholder = placeholder
        self.setMinimumHeight(280)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_jpeg(self, image: bytes):
        pix = QPixmap()
        if pix.loadFromData(image):
            self._pixmap = pix; self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        area = self.rect().adjusted(9,9,-9,-9)
        if not self._pixmap.isNull():
            scaled = self._pixmap.scaled(area.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x=area.left()+(area.width()-scaled.width())//2
            y=area.top()+(area.height()-scaled.height())//2
            p.drawPixmap(x,y,scaled)
        else:
            p.setPen(QPen(QColor('#293447'),1))
            for x in range(30,self.width(),32): p.drawLine(x,0,x,self.height())
            for y in range(30,self.height(),32): p.drawLine(0,y,self.width(),y)
            cx,cy=self.width()//2,self.height()//2-16
            p.setPen(QPen(QColor('#6C618A'),2))
            p.drawEllipse(QPoint(cx,cy),26,26)
            p.drawLine(cx-38,cy,cx-14,cy); p.drawLine(cx+14,cy,cx+38,cy)
            p.drawLine(cx,cy-38,cx,cy-14); p.drawLine(cx,cy+14,cx,cy+38)
            p.setPen(QColor('#9BA7BE'))
            p.drawText(QRect(20,cy+50,self.width()-40,54),Qt.AlignHCenter|Qt.AlignTop,self._placeholder)
        p.end()


class CalibrationCanvas(ImagePanel):
    """Mouse selection is mapped into original pixels (not scaled QWidget pixels)."""
    def __init__(self):
        super().__init__('Apri o cattura uno screenshot per iniziare')
        self.frame = None
        self.selection = None
        self.anchor = None

    def set_frame(self, frame):
        self.frame = frame.copy()
        self.selection = None
        self.anchor = None
        image = cv2.cvtColor(frame, cv2.COLOR_BGRA2RGB if frame.shape[2] == 4 else cv2.COLOR_BGR2RGB)
        h,w = image.shape[:2]
        qimg=QImage(image.data,w,h,image.strides[0],QImage.Format_RGB888).copy()
        self._pixmap=QPixmap.fromImage(qimg); self.update()

    def _image_box(self):
        if self._pixmap.isNull(): return QRect()
        area=self.rect().adjusted(9,9,-9,-9)
        scaled=self._pixmap.size().scaled(area.size(),Qt.KeepAspectRatio)
        x=area.left()+(area.width()-scaled.width())//2
        y=area.top()+(area.height()-scaled.height())//2
        return QRect(x,y,scaled.width(),scaled.height())

    def _image_coords(self, pt):
        box=self._image_box()
        if box.isEmpty() or self.frame is None: return None
        x=round((pt.x()-box.x())*self.frame.shape[1]/box.width())
        y=round((pt.y()-box.y())*self.frame.shape[0]/box.height())
        return max(0,min(self.frame.shape[1],x)),max(0,min(self.frame.shape[0],y))

    def mousePressEvent(self,event):
        if event.button()==Qt.LeftButton and self.frame is not None:
            self.anchor=self._image_coords(event.position().toPoint()); self.selection=None

    def mouseMoveEvent(self,event):
        if self.anchor is not None:
            p=self._image_coords(event.position().toPoint())
            if p:
                self.selection=(min(self.anchor[0],p[0]),min(self.anchor[1],p[1]),
                                max(self.anchor[0],p[0]),max(self.anchor[1],p[1]));self.update()

    def mouseReleaseEvent(self,event):
        if self.anchor is not None:
            self.mouseMoveEvent(event); self.anchor=None

    def paintEvent(self,event):
        super().paintEvent(event)
        if self.selection is not None and self.frame is not None:
            x1,y1,x2,y2=self.selection
            box=self._image_box()
            a=box.x()+round(x1*box.width()/self.frame.shape[1]); b=box.y()+round(y1*box.height()/self.frame.shape[0])
            c=box.x()+round(x2*box.width()/self.frame.shape[1]); d=box.y()+round(y2*box.height()/self.frame.shape[0])
            p=QPainter(self)
            p.setPen(QPen(QColor('#D7BBFF'),2)); p.setBrush(QColor(170,145,255,36))
            p.drawRect(QRect(QPoint(a,b),QPoint(c,d))); p.end()


class OyeStudio(QMainWindow):
    hotkey = Signal(str)
    camera_result = Signal(str)

    MODES = [
        ('observe','Osserva','Solo analisi · nessun input'),
        ('train','Allenamento','Tu giochi · Oye registra'),
        ('single_match','Una partita','Gioca → rientra → stop'),
        ('guided','Esplora e piccona','Oye si muove · routine'),
        ('autonomous','Pilota AI','Policy appresa · sperimentale'),
    ]

    def __init__(self):
        super().__init__()
        self.cfg = load(ROOT / 'config.json')
        self.runner = None
        self.listener = None
        self.down_keys = set()
        self.events=deque(maxlen=160)
        self.training_rows=[]
        self.selected_frame_id=None
        self.mode='observe'
        self.latest={'scene':'—','phase':'—','action':'—','ocr_phase':'normal','camera_feedback':'Non verificata'}
        self.learning_store = LearningStore(ROOT/'learning',max_saved_frames=int(self.cfg['max_saved_frames']))
        self.setWindowTitle('Oye · Studio 23')
        self.resize(1350,860); self.setMinimumSize(960,670)
        self._setup_ui()
        self._refresh_calibration()
        self._refresh_training()
        self.hotkey.connect(self._handle_hotkey)
        self.camera_result.connect(self._camera_done)
        self.timer=QTimer(self);self.timer.timeout.connect(self._poll);self.timer.start(200)

    def _setup_ui(self):
        main=QWidget();main.setObjectName('root');self.setCentralWidget(main)
        row=QHBoxLayout(main);row.setContentsMargins(0,0,0,0);row.setSpacing(0)
        sidebar=QFrame();sidebar.setObjectName('sidebar');sidebar.setFixedWidth(225)
        nav=QVBoxLayout(sidebar);nav.setContentsMargins(18,25,18,18);nav.setSpacing(9)
        brand=QHBoxLayout()
        logo=label('◈');logo.setStyleSheet('font-size:31px;color:#AA91FF;font-weight:800;');brand.addWidget(logo)
        bn=QVBoxLayout();bn.setSpacing(0)
        bn.addWidget(label('OYE','section'));bn.addWidget(label('OBSERVER STUDIO','eyebrow'))
        brand.addLayout(bn);brand.addStretch();nav.addLayout(brand)
        nav.addWidget(vspace(27));nav.addWidget(label('WORKSPACE','eyebrow'))
        self.navs=[]
        for i,(name,text) in enumerate([('⌂','Panoramica'),('⊞','Calibrazione'),('▤','Partite e dati'),('⚙','Impostazioni')]):
            b=QPushButton(f'{name}   {text}');b.setObjectName('nav');b.setCheckable(True)
            b.clicked.connect(lambda _checked,idx=i: self._navigate(idx))
            nav.addWidget(b);self.navs.append(b)
        nav.addStretch()
        foot=card(margin=13);fl=foot.layout()
        fl.addWidget(label('MOTORE LOCALE','eyebrow'))
        fl.addWidget(label('Visione + OCR + input', 'muted', True))
        fl.addWidget(label('I dati restano sul PC.','muted'))
        nav.addWidget(foot)
        row.addWidget(sidebar)
        content=QWidget();cl=QVBoxLayout(content);cl.setContentsMargins(26,22,26,17);cl.setSpacing(15)
        top=QHBoxLayout()
        self.header_section=label('PANORAMICA','eyebrow');top.addWidget(self.header_section)
        top.addStretch()
        self.session_badge=label('●  NON ATTIVO','status');top.addWidget(self.session_badge)
        cl.addLayout(top)
        self.pages=QStackedWidget();cl.addWidget(self.pages,1)
        self.pages.addWidget(self._dashboard())
        self.pages.addWidget(self._calibration_page())
        self.pages.addWidget(self._training_page())
        self.pages.addWidget(self._settings_page())
        row.addWidget(content,1)
        self._navigate(0)
        self._set_running(False)

    def _page(self):
        scroll=QScrollArea();scroll.setObjectName('scroll');scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame);scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body=QWidget();body.setObjectName('scrollBody')
        lay=QVBoxLayout(body);lay.setContentsMargins(0,0,9,8);lay.setSpacing(17)
        scroll.setWidget(body)
        return scroll,lay

    def _dashboard(self):
        page,lay=self._page()
        title=label('Oye, pronto a osservare.','title');lay.addWidget(title)
        self.subheading=label('Scegli cosa deve fare. Il resto rimane nascosto finché non ti serve.','muted')
        lay.addWidget(self.subheading)
        lay.addWidget(vspace(3));lay.addWidget(label('MODALITÀ DI SESSIONE','eyebrow'))
        modes=QHBoxLayout();modes.setSpacing(10);self.mode_buttons={}
        for key,name,description in self.MODES:
            btn=QPushButton(f'{name}\n{description}');btn.setObjectName('mode');btn.setCheckable(True)
            btn.clicked.connect(lambda checked,mode=key:self._select_mode(mode))
            modes.addWidget(btn,1);self.mode_buttons[key]=btn
        lay.addLayout(modes);self._select_mode('observe')
        ctrls=QHBoxLayout();ctrls.setSpacing(10)
        self.start_button=QPushButton('▶   Avvia sessione');self.start_button.setObjectName('primary');self.start_button.clicked.connect(self.start_session)
        self.pause_button=QPushButton('Ⅱ   Pausa · F8');self.pause_button.clicked.connect(self.pause_session)
        self.stop_button=QPushButton('■   Arresta · F9');self.stop_button.setObjectName('danger');self.stop_button.clicked.connect(self.stop_session)
        ctrls.addWidget(self.start_button);ctrls.addWidget(self.pause_button);ctrls.addWidget(self.stop_button)
        ctrls.addStretch()
        self.profile_choice=QComboBox();self.profile_choice.addItems(['equilibrato','esploratore','raccoglitore'])
        self.profile_choice.setCurrentText(self.cfg['guided_activity_profile'])
        self.profile_choice.setMinimumWidth(145);self.profile_choice.setToolTip('Stile della routine: applicato all’avvio')
        ctrls.addWidget(label('Stile','muted'));ctrls.addWidget(self.profile_choice)
        lay.addLayout(ctrls)
        viewport=QHBoxLayout();viewport.setSpacing(14)
        left=card();ll=left.layout()
        preview_title=QHBoxLayout();preview_title.addWidget(label('SCHERMO DEL GIOCO','eyebrow'));preview_title.addStretch()
        self.frame_tag=label('Nessun frame','muted');preview_title.addWidget(self.frame_tag)
        ll.addLayout(preview_title)
        self.preview=ImagePanel();ll.addWidget(self.preview,1)
        self.frame_details=label('Il monitor si attiva quando Fortnite è visibile.','muted',True);ll.addWidget(self.frame_details)
        viewport.addWidget(left,7)
        right=card();rr=right.layout();rr.addWidget(label('STATO DELLA PARTITA','eyebrow'))
        self.status_values={}
        for key,text in [('phase','Fase'),('ocr_phase','Riconoscimento OCR'),('action','Ultima azione'),('activity','Attività'),('camera_feedback','Telecamera')]:
            rr.addWidget(label(text.upper(),'muted'))
            value=label('—' if key!='ocr_phase' else 'In attesa','metric',True)
            value.setMaximumHeight(52)
            rr.addWidget(value);self.status_values[key]=value
        rr.addStretch()
        self.safety_note=label('Nessun comando inviato.','muted',True);rr.addWidget(self.safety_note)
        viewport.addWidget(right,4);lay.addLayout(viewport,1)
        bottom=card(margin=16);bl=bottom.layout()
        bar=QHBoxLayout();bar.addWidget(label('ATTIVITÀ RECENTI','eyebrow'));bar.addStretch()
        self.toggle_log=QPushButton('Apri registro');self.toggle_log.clicked.connect(self._toggle_log)
        bar.addWidget(self.toggle_log);bl.addLayout(bar)
        self.event_summary=label('Nessuna attività registrata','muted',True);bl.addWidget(self.event_summary)
        self.log=QTextEdit();self.log.setReadOnly(True);self.log.setMaximumHeight(170);self.log.hide();bl.addWidget(self.log)
        lay.addWidget(bottom)
        return page

    def _calibration_page(self):
        page,lay=self._page()
        lay.addWidget(label('Calibrazione visiva','title'))
        lay.addWidget(label('Tre passaggi. Apri un frame, seleziona il riferimento e salvalo. Niente tabelle infinite.','muted'))
        tools=card();tl=tools.layout()
        head=QHBoxLayout();head.addWidget(label('01  CARICA FOTOGRAMMA','eyebrow'));head.addStretch()
        open_btn=QPushButton('Apri immagine');open_btn.clicked.connect(self._open_screenshot)
        grab_btn=QPushButton('Cattura da Fortnite · 5 s');grab_btn.clicked.connect(self._capture_delayed)
        head.addWidget(open_btn);head.addWidget(grab_btn);tl.addLayout(head)
        self.calcanvas=CalibrationCanvas();self.calcanvas.setMinimumHeight(365);tl.addWidget(self.calcanvas,1)
        helper=label('Suggerimento: HUD = bordi stabili, non oggetti o numeri. Seleziona con il mouse direttamente sull’immagine.','muted',True)
        tl.addWidget(helper)
        lay.addWidget(tools,1)
        c=card();cc=c.layout();cc.addWidget(label('02  SELEZIONA E SALVA','eyebrow'))
        line=QHBoxLayout();self.template_choice=QComboBox()
        labels={
            'hud_salute':'HUD · cornice della salute', 'hud_minimappa':'HUD · bordo della minimappa',
            'hud_inventario':'HUD · contorno inventario', 'gioca':'Menu · pulsante GIOCA',
            'continua':'Fine partita · CONTINUA', 'torna_lobby':'Fine partita · TORNA ALLA LOBBY',
            'spettatore_prossimo':'Spettatore · prossimo', 'spettatore_precedente':'Spettatore · precedente',
            'bus_salto':'Bus · prompt SALTA',
        }
        for name in ('hud_salute','hud_minimappa','hud_inventario','gioca','continua','torna_lobby',
                     'spettatore_prossimo','spettatore_precedente','bus_salto',
                     *[n for n in TEMPLATE_NAMES if n not in labels]):
            self.template_choice.addItem(labels.get(name,name.replace('_',' ').title()),name)
        save_btn=QPushButton('Salva riferimento');save_btn.setObjectName('primary');save_btn.clicked.connect(self._save_reference)
        verify_btn=QPushButton('Verifica frame');verify_btn.clicked.connect(self._verify_reference)
        line.addWidget(self.template_choice,1);line.addWidget(save_btn);line.addWidget(verify_btn)
        cc.addLayout(line)
        self.cal_status=label('Ancora nessun riferimento selezionato.','muted',True)
        cc.addWidget(self.cal_status);lay.addWidget(c)
        return page

    def _training_page(self):
        page,lay=self._page()
        lay.addWidget(label('Partite e apprendimento','title'))
        lay.addWidget(label('Qui trovi le registrazioni delle partite giocate da te. Le correzioni avanzate rimangono nell’editor timeline.','muted',True))
        stats=QHBoxLayout();self.training_labels=[]
        for name in ['Fotogrammi','Eventi corretti','Azioni insegnate']:
            part=card(margin=16);p=part.layout();p.addWidget(label(name.upper(),'eyebrow'))
            v=label('0','title');p.addWidget(v);stats.addWidget(part);self.training_labels.append(v)
        lay.addLayout(stats)
        history=card();hl=history.layout()
        toolbar=QHBoxLayout();toolbar.addWidget(label('ULTIMI FOTOGRAMMI','eyebrow'));toolbar.addStretch()
        reload_btn=QPushButton('Aggiorna');reload_btn.clicked.connect(self._refresh_training)
        open_btn=QPushButton('Apri editor timeline');open_btn.setObjectName('primary');open_btn.clicked.connect(self._open_legacy_editor)
        toolbar.addWidget(reload_btn);toolbar.addWidget(open_btn);hl.addLayout(toolbar)
        self.training_table=QTableWidget(0,4);self.training_table.setHorizontalHeaderLabels(['Data','Sessione','Scena','Annotazione'])
        self.training_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.training_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.training_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.training_table.itemSelectionChanged.connect(self._select_training_frame)
        self.training_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.training_table.verticalHeader().hide();self.training_table.setMinimumHeight(300)
        hl.addWidget(self.training_table);lay.addWidget(history,1)
        editor=card();el=editor.layout()
        el.addWidget(label('CORREGGI UN FOTOGRAMMA','eyebrow'))
        el.addWidget(label('Seleziona un fotogramma sopra, poi scegli evento o azione. Le correzioni sono salvate localmente.','muted',True))
        editrow=QHBoxLayout()
        self.training_preview=ImagePanel('Seleziona un fotogramma dalla tabella')
        self.training_preview.setMinimumHeight(250)
        editrow.addWidget(self.training_preview,3)
        fields=QVBoxLayout()
        self.selected_frame_label=label('Nessun fotogramma selezionato','muted',True)
        fields.addWidget(self.selected_frame_label)
        fields.addWidget(label('Evento','muted'))
        self.event_choice=QComboBox();self.event_choice.setEditable(True)
        self.event_choice.addItems(sorted(set(SCENE_EVENTS.values())|{'in volo','sul bus','eliminato'}))
        fields.addWidget(self.event_choice)
        fields.addWidget(label('Azione osservata','muted'))
        self.action_choice=QComboBox();self.action_choice.addItems([x for x in LIMITS if x!='aim'])
        fields.addWidget(self.action_choice)
        save_event=QPushButton('Salva evento');save_event.clicked.connect(self._correct_training_event)
        save_action=QPushButton('Insegna azione');save_action.clicked.connect(self._teach_training_action)
        fields.addWidget(save_event);fields.addWidget(save_action)
        fields.addStretch()
        editrow.addLayout(fields,2)
        el.addLayout(editrow)
        interval=QHBoxLayout();interval.addWidget(label('Seleziona due o più righe per etichettare anche l’intervallo fra i relativi orari.','muted',True),1)
        interval_btn=QPushButton('Etichetta intervallo');interval_btn.clicked.connect(self._annotate_training_interval)
        interval.addWidget(interval_btn);el.addLayout(interval)
        lay.addWidget(editor)
        footer=card(margin=16);ff=footer.layout();ff.addWidget(label('PRONTO PER UNA PROVA AI?','eyebrow'))
        self.readiness_label=label('Controlla gli esempi acquisiti prima di provare il pilota.', 'muted',True)
        ff.addWidget(self.readiness_label)
        actions=QHBoxLayout();inspect_btn=QPushButton('Controlla apprendimento');inspect_btn.clicked.connect(self._check_readiness)
        export_btn=QPushButton('Esporta metadati JSONL');export_btn.clicked.connect(self._export_learning)
        actions.addWidget(inspect_btn);actions.addWidget(export_btn);actions.addStretch()
        ff.addLayout(actions);lay.addWidget(footer)
        return page

    def _settings_page(self):
        page,lay=self._page()
        lay.addWidget(label('Impostazioni','title'))
        lay.addWidget(label('Solo i controlli utili nell’uso quotidiano. Le opzioni di sviluppo restano nel file config.json.','muted'))
        cap=card();cv=cap.layout();cv.addLayout(section_title('Riconoscimento','OCR, visione locale e acquisizione'))
        self.ocr_check=QCheckBox('Riconoscimento testi con Tesseract');self.ocr_check.setChecked(self.cfg['ocr_enabled']);cv.addWidget(self.ocr_check)
        self.ollama_check=QCheckBox('Abilita Ollama Vision per il pilota AI');self.ollama_check.setChecked(self.cfg['ollama_enabled']);cv.addWidget(self.ollama_check)
        self.demo_check=QCheckBox('Registra i tasti quando giochi tu (allenamento)');self.demo_check.setChecked(True);cv.addWidget(self.demo_check)
        line=QHBoxLayout();line.addWidget(label('Intervallo screenshot (s)','muted'))
        self.capture_spin=QDoubleSpinBox();self.capture_spin.setRange(1,60);self.capture_spin.setSingleStep(.5);self.capture_spin.setValue(self.cfg['capture_seconds'])
        line.addWidget(self.capture_spin);line.addStretch();cv.addLayout(line);lay.addWidget(cap)
        mov=card();ml=mov.layout();ml.addLayout(section_title('Movimento','Profilo Esplora e piccona'))
        self.timed_check=QCheckBox('Routine a tempo (senza obbligo di HUD calibrato)');self.timed_check.setChecked(self.cfg['guided_timed_enabled']);ml.addWidget(self.timed_check)
        self.harvest_check=QCheckBox('Usa il piccone durante l’esplorazione');self.harvest_check.setChecked(self.cfg['guided_explore_harvest']);ml.addWidget(self.harvest_check)
        self.smooth_check=QCheckBox('Movimenti fluidi');self.smooth_check.setChecked(self.cfg['guided_smooth_enabled']);ml.addWidget(self.smooth_check)
        turn=QHBoxLayout();turn.addWidget(label('Ampiezza rotazione (pixel)','muted'))
        self.turn_spin=QSpinBox();self.turn_spin.setRange(20,320);self.turn_spin.setValue(self.cfg['guided_camera_pixels'])
        turn.addWidget(self.turn_spin);turn.addStretch();ml.addLayout(turn)
        once=QHBoxLayout()
        once.addWidget(label('Una partita · durata salti iniziali (secondi)','muted'))
        self.single_jump_spin=QSpinBox()
        self.single_jump_spin.setRange(30,180)
        self.single_jump_spin.setSingleStep(10)
        self.single_jump_spin.setValue(int(self.cfg['single_match_jump_seconds']))
        self.single_jump_spin.setToolTip('Dopo ingresso verificato: saltelli + spostamenti, poi solo esplorazione')
        once.addWidget(self.single_jump_spin)
        once.addStretch()
        ml.addLayout(once)
        ml.addWidget(label('Per Una partita occorre calibrare GIOCA, HUD, controlli spettatore e ritorno alla lobby. Non basta la scomparsa di GIOCA.','muted',True))
        lay.addWidget(mov)
        adv=card();al=adv.layout();al.addLayout(section_title('Input e compatibilità','Le modifiche entrano in vigore dalla prossima sessione'))
        b=QHBoxLayout();b.addWidget(label('Dispositivo input','muted'))
        self.backend_choice=QComboBox();self.backend_choice.addItem('Tastiera e mouse','keyboard');self.backend_choice.addItem('Gamepad (sperimentale)','gamepad')
        self.backend_choice.setCurrentIndex(0 if self.cfg['input_backend']=='keyboard' else 1)
        b.addWidget(self.backend_choice);b.addStretch();al.addLayout(b)
        al.addWidget(label('F8 sospende · F9 arresta · input LIVE solo dopo conferma esplicita.','muted',True))
        lay.addWidget(adv)
        diag=card();dl=diag.layout()
        dl.addLayout(section_title('Test dei comandi','Un test alla volta, solo dopo conferma. Premi F9 per fermare eventuali sessioni.'))
        buttons=QHBoxLayout()
        wbtn=QPushButton('Verifica movimento W');wbtn.clicked.connect(lambda:self._run_input_test('forward'))
        cbtn=QPushButton('Verifica telecamera');cbtn.clicked.connect(lambda:self._run_input_test('camera'))
        buttons.addWidget(wbtn);buttons.addWidget(cbtn);buttons.addStretch();dl.addLayout(buttons)
        lay.addWidget(diag)
        actions=QHBoxLayout();save_btn=QPushButton('Salva impostazioni');save_btn.setObjectName('primary');save_btn.clicked.connect(self.save_settings)
        actions.addWidget(save_btn);actions.addStretch();lay.addLayout(actions);lay.addStretch()
        return page

    def _navigate(self, index):
        self.pages.setCurrentIndex(index)
        for i,btn in enumerate(self.navs):btn.setChecked(i==index)
        self.header_section.setText(('PANORAMICA','CALIBRAZIONE','PARTITE E DATI','IMPOSTAZIONI')[index])
        if index==2:self._refresh_training()
        elif index==1:self._refresh_calibration()

    def _select_mode(self, mode):
        if self.runner and self.runner.thread and self.runner.thread.is_alive():return
        self.mode=mode
        for key,btn in self.mode_buttons.items():btn.setChecked(key==mode)
        messages={'observe':'Analizza lo schermo senza inviare alcun comando.',
                  'train':'Tu giochi: Oye osserva e registra la sessione.',
                  'guided':'Oye prova a esplorare: richiede input LIVE autorizzati.',
                  'single_match':'Oye preme Gioca, salta e si muove per 2 minuti, esplora, torna in lobby e si ferma.',
                  'autonomous':'Oye usa il dataset e la policy AI: richiede esempi e input LIVE.'}
        self.subheading.setText(messages[mode])

    def save_settings(self):
        if self.runner and self.runner.thread and self.runner.thread.is_alive():
            QMessageBox.warning(self,'Sessione attiva','Ferma la sessione prima di modificare le impostazioni.');return False
        try:
            c=load(ROOT/'config.json')
            c.update(ocr_enabled=self.ocr_check.isChecked(),ollama_enabled=self.ollama_check.isChecked(),
                     capture_seconds=self.capture_spin.value(),guided_timed_enabled=self.timed_check.isChecked(),
                     guided_explore_harvest=self.harvest_check.isChecked(),guided_smooth_enabled=self.smooth_check.isChecked(),
                     guided_camera_pixels=self.turn_spin.value(),input_backend=self.backend_choice.currentData(),
                     single_match_jump_seconds=float(self.single_jump_spin.value()),
                     guided_activity_profile=self.profile_choice.currentText())
            # These flags represent the NEXT session, not persisted authorization.
            c['autonomous_enabled']=False;c['guided_explore_enabled']=False;c['demonstration_enabled']=False
            save(ROOT/'config.json',c);self.cfg=c
            self._record('Impostazioni salvate');return True
        except Exception as exc:
            QMessageBox.critical(self,'Impostazioni non salvate',str(exc));return False

    def start_session(self):
        if self.runner and self.runner.thread and self.runner.thread.is_alive():
            QMessageBox.information(self,'Sessione in corso','Arresta la sessione corrente prima di avviarne una nuova.');return
        if not self.save_settings():return
        flags=mode_flags(self.mode)
        if flags['live']:
            extra=('La routine a tempo può agire senza riconoscere tutte le fasi del gioco.\n\n'
                   if self.mode=='guided' and self.cfg['guided_timed_enabled'] else
                   'Una partita non agisce durante i caricamenti: richiede HUD e pulsanti calibrati; si ferma dopo la lobby.\n\n'
                   if self.mode=='single_match' else '')
            question=('Questa modalità invia comandi a Fortnite. Potrebbe violarne le regole e comportare sanzioni.\n\n'
                      +extra+'Non aggira anti-cheat o controlli AFK. F8 = pausa, F9 = arresta.\n\nVuoi attivare gli input LIVE?')
            if QMessageBox.question(self,'Conferma input LIVE',question,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:
                return
        if self.mode=='train' and self.cfg['input_backend']=='gamepad' and self.demo_check.isChecked():
            QMessageBox.information(self,'Registrazione controller',
                'La registrazione automatica delle dimostrazioni riconosce la tastiera, non i pulsanti del gamepad.\n'
                'Gli screenshot verranno comunque acquisiti e potrai correggerli nella pagina Partite e dati.')
        if self.mode=='autonomous' and self.cfg['ollama_enabled']:
            try:
                from observer.ollama_vision import LocalVisionModel
                LocalVisionModel(self.cfg['ollama_model'],self.cfg['ollama_timeout_seconds']).check_ready()
            except Exception as exc:
                QMessageBox.warning(self,'Ollama non pronto',str(exc));return
        if self.mode=='autonomous' and self.cfg['fast_policy_enabled'] and not self.cfg['ollama_enabled']:
            try:
                report=inspect_training(self.learning_store)
                if report.action_examples<3:
                    QMessageBox.warning(self,'Prima serve allenamento','Registra alcune azioni giocando tu o annotandole nell’editor timeline.');return
                if not report.trial_ready:
                    if QMessageBox.question(self,'Dataset limitato',
                        f'{report.matches} partite · {report.action_examples} azioni.\n'
                        'L’apprendimento non è ancora verificato: tentare comunque una prova sorvegliata?',
                        QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
            except Exception as exc:
                QMessageBox.critical(self,'Errore verifica dataset',str(exc));return
        try:
            # The engine loads this file at startup; persist only the mode flags,
            # never carry LIVE arming over to the next application launch.
            c=dict(self.cfg);c.update({k:v for k,v in flags.items() if k not in ('live','exploration')})
            if self.mode=='train' and not self.demo_check.isChecked():c['demonstration_enabled']=False
            c['learning_enabled']=True
            save(ROOT/'config.json',c)
            self.runner=Runner(root=ROOT,live=flags['live'],exploration=flags['exploration'],
                               auto_requeue=self.mode in ('guided','single_match'),shoot=False,
                               single_match=self.mode=='single_match')
            if flags['live'] and not self._install_hotkeys():
                self.runner=None
                # Roll back any mode settings if live hotkeys cannot be installed.
                self._reset_autonomous_flags()
                raise RuntimeError('Tasti F8/F9 globali non disponibili: modalità LIVE annullata')
            self.runner.start();self._install_hotkeys()
            self._record('Sessione avviata: '+dict((m,t) for m,t,_ in self.MODES)[self.mode])
            self._set_running(True)
            if flags['live']:self.showMinimized()
        except Exception as exc:
            self._reset_autonomous_flags()
            self._record('Errore avvio: '+str(exc))
            QMessageBox.critical(self,'Avvio non riuscito',str(exc))

    def _reset_autonomous_flags(self):
        try:
            c=load(ROOT/'config.json')
            c['autonomous_enabled']=False;c['guided_explore_enabled']=False;c['demonstration_enabled']=False
            save(ROOT/'config.json',c)
        except Exception as exc: self._record('Impossibile ripristinare i flag: '+str(exc))

    def _install_hotkeys(self):
        if self.listener and self.listener.is_alive():return True
        try:
            from pynput import keyboard
            def down(key):
                if key not in (keyboard.Key.f8,keyboard.Key.f9) or key in self.down_keys:return
                self.down_keys.add(key);self.hotkey.emit('pause' if key==keyboard.Key.f8 else 'stop')
            def up(key):self.down_keys.discard(key)
            self.listener=keyboard.Listener(on_press=down,on_release=up)
            self.listener.daemon=True;self.listener.start();return True
        except Exception as exc:
            self._record('Hotkey non disponibili: '+str(exc));return False

    def _handle_hotkey(self,action):
        if action=='pause':self.pause_session()
        else:self.stop_session()

    def pause_session(self):
        if not self.runner or not self.runner.thread or not self.runner.thread.is_alive():return
        paused=self.runner.pause_toggle()
        self.session_badge.setText('Ⅱ  IN PAUSA' if paused else '●  SESSIONE ATTIVA')
        self.pause_button.setText('▶   Riprendi · F8' if paused else 'Ⅱ   Pausa · F8')

    def stop_session(self):
        if self.runner and self.runner.thread and self.runner.thread.is_alive():
            self.runner.stop();self.session_badge.setText('◉  ARRESTO IN CORSO')
        else:self._set_running(False)

    def _set_running(self,running):
        self.start_button.setEnabled(not running)
        self.pause_button.setEnabled(running);self.stop_button.setEnabled(running)
        self.profile_choice.setEnabled(not running)
        for btn in self.mode_buttons.values():btn.setEnabled(not running)
        if not running:
            self.session_badge.setText('○  NON ATTIVO');self.pause_button.setText('Ⅱ   Pausa · F8')
            self.safety_note.setText('Nessun comando inviato. Prima dell’avvio scegli la modalità.')
        else:
            self.session_badge.setText('●  SESSIONE ATTIVA')
            self.safety_note.setText('F8 mette in pausa · F9 arresta. Input soggetti al focus del gioco.')

    def _record(self,message):
        stamp=time.strftime('%H:%M:%S')
        line=f'{stamp}  {message}'
        self.events.appendleft(line)
        self.event_summary.setText(line[:180])
        self.log.append(line)
        if len(self.events)>120:
            self.log.setPlainText('\n'.join(reversed(list(self.events))))

    def _toggle_log(self):
        show=self.log.isHidden();self.log.setVisible(show)
        self.toggle_log.setText('Nascondi registro' if show else 'Apri registro')

    def _poll(self):
        if not self.runner:return
        for _ in range(100):
            try:rec=self.runner.messages.get_nowait()
            except queue.Empty:break
            typ=rec.get('event','')
            if typ=='preview':
                if isinstance(rec.get('jpeg'),bytes):
                    self.preview.set_jpeg(rec['jpeg']);self.frame_tag.setText(f"Frame {rec.get('frame_count','—')}")
                    self.frame_details.setText(f"Scena: {rec.get('scene','—')}  ·  OCR: {phase_display(rec.get('ocr_phase','normal'))}"
                        + f"  ·  HUD: {rec.get('hud_core_votes',0)}/{rec.get('hud_core_configured',0)}")
            elif typ=='telemetry':
                for key,target in self.status_values.items():
                    value=rec.get(key)
                    if value is not None:
                        if key=='ocr_phase':value=phase_display(value)
                        target.setText(str(value)[:64]);target.setToolTip(str(value));self.latest[key]=value
            elif typ=='learning_frame':
                pass
            else:
                if typ=='phase_ocr':self.status_values['ocr_phase'].setText(phase_display(rec.get('phase',rec.get('message',''))))
                if typ=='camera_feedback':self.status_values['camera_feedback'].setText(rec.get('message','')[:90])
                if typ not in ('runtime_ocr','capture_metrics'):
                    self._record(f"{typ}: {rec.get('message','')}"[:240])
        if self.runner.thread and not self.runner.thread.is_alive():
            self._set_running(False)
            self.runner=None
            self._reset_autonomous_flags()
            self._refresh_training()
            self._record('Sessione terminata')

    def _open_screenshot(self):
        filename,_=QFileDialog.getOpenFileName(self,'Apri screenshot',str(ROOT),
                                                'Immagini (*.png *.jpg *.jpeg *.bmp)')
        if filename:
            frame=cv2.imread(filename,cv2.IMREAD_COLOR)
            if frame is None:QMessageBox.warning(self,'Immagine non valida','Impossibile aprire lo screenshot.');return
            self.calcanvas.set_frame(frame);self.cal_status.setText('Ora trascina sul riferimento e premi Salva.')

    def _capture_delayed(self):
        if self.runner and self.runner.thread and self.runner.thread.is_alive():
            QMessageBox.warning(self,'Sessione attiva','Arresta Oye prima della calibrazione.');return
        self.cal_status.setText('Torna a Fortnite: cattura tra 5 secondi…');self.showMinimized()
        QTimer.singleShot(5000,self._capture_now)

    def _capture_now(self):
        try:
            enable_dpi_awareness()
            win=foreground_window(self.cfg['window_executables'])
            if win is None:raise RuntimeError('Fortnite deve essere in primo piano durante la cattura.')
            frame=snapshot(win);self.calcanvas.set_frame(frame)
            folder=ROOT/'captures';folder.mkdir(exist_ok=True)
            cv2.imwrite(str(folder/f'cattura_{time.strftime("%Y%m%d_%H%M%S")}.png'),frame)
            self.cal_status.setText('Screenshot acquisito. Trascina un rettangolo sull’elemento da salvare.')
        except Exception as exc:
            self.cal_status.setText(str(exc));QMessageBox.warning(self,'Cattura non riuscita',str(exc))
        finally:
            close_capture();self.showNormal();self.activateWindow()

    def _save_reference(self):
        try:
            out=save_calibration(self.calcanvas.frame,self.calcanvas.selection,
                                 self.template_choice.currentData(),ROOT/'templates')
            self.cal_status.setText(f'Riferimento salvato: {out.name}. Valido dalla prossima sessione.')
            self._refresh_calibration()
        except Exception as exc:
            QMessageBox.warning(self,'Riferimento non salvato',str(exc))

    def _verify_reference(self):
        if self.calcanvas.frame is None:
            QMessageBox.information(self,'Verifica','Apri prima uno screenshot.');return
        try:
            v=Vision(ROOT/'templates',self.cfg['template_threshold'])
            result=v.recognize(self.calcanvas.frame)
            matched=', '.join(result.best.keys()) if result.best else 'nessuno'
            if result.hud:
                matched+=f'\nHUD: {result.hud.core_votes}/{result.hud.core_configured}\n{result.hud.reason}'
            QMessageBox.information(self,'Riconoscimento sul frame',
                f'Scena: {result.scene}\nIndizi: {matched}\n\nLa verifica sullo stesso screenshot non dimostra la generalizzazione.')
        except Exception as exc:QMessageBox.warning(self,'Verifica fallita',str(exc))

    def _refresh_calibration(self):
        if not hasattr(self,'cal_status'):return
        present={p.stem for p in (ROOT/'templates').glob('*.png')}
        n=len(set(HUD_CORE).intersection(present))
        self.cal_status.setText(f'HUD strutturali: {n}/3 disponibili (2 richiesti dalla modalità intelligente).  '
                                f'Regolazione menu: {len(present)} riferimenti salvati.')

    def _refresh_training(self):
        if not hasattr(self,'training_labels'):return
        try:
            data=self.learning_store.stats()
            for target,key in zip(self.training_labels,('frames','labeled','actions')):target.setText(str(data[key]))
            rows=self.learning_store.recent(limit=80)
            self.training_rows=rows
            self.training_table.blockSignals(True)
            self.training_table.setRowCount(len(rows))
            for i,rec in enumerate(rows):
                vals=(str(rec.get('captured_at') or '')[:19],str(rec.get('session') or ''),
                      str(rec.get('hud_scene') or ''),str(rec.get('user_label') or rec.get('predicted_event') or '—'))
                for j,value in enumerate(vals):self.training_table.setItem(i,j,QTableWidgetItem(value[:90]))
            self.training_table.blockSignals(False)
        except Exception as exc:
            self.training_table.blockSignals(False)
            self.readiness_label.setText('Impossibile leggere l’archivio: '+str(exc))

    def _select_training_frame(self):
        positions=self.training_table.selectionModel().selectedRows()
        if not positions:return
        idx=positions[0].row()
        if idx>=len(self.training_rows):return
        rec=self.training_rows[idx]
        self.selected_frame_id=rec['id']
        self.selected_frame_label.setText(f"Frame #{rec['id']} · {rec.get('captured_at','')}\n{rec.get('ocr_status') or 'OCR non disponibile'}")
        self.event_choice.setCurrentText(rec.get('user_label') or rec.get('predicted_event') or 'incerto')
        self.action_choice.setCurrentText(rec.get('action_label') or 'wait')
        filename=rec.get('image_file') or ''
        if re.fullmatch(r'[a-f0-9]{32}\.jpg',filename):
            file=self.learning_store.images/filename
            if file.is_file():
                try:self.training_preview.set_jpeg(file.read_bytes());return
                except OSError:pass
        self.training_preview._pixmap=QPixmap();self.training_preview.update()

    def _selected_training_record(self):
        if self.selected_frame_id is None:
            QMessageBox.information(self,'Nessuna selezione','Seleziona un fotogramma.');return None
        return next((rec for rec in self.training_rows if rec.get('id')==self.selected_frame_id),None)

    def _correct_training_event(self):
        rec=self._selected_training_record()
        if not rec:return
        try:
            event=self.event_choice.currentText().strip()
            self.learning_store.correct(rec['id'],event,rec.get('user_note') or '')
            self._record(f"Etichetta salvata: frame #{rec['id']} → {event}")
            self._refresh_training()
        except Exception as exc:QMessageBox.warning(self,'Correzione fallita',str(exc))

    def _teach_training_action(self):
        rec=self._selected_training_record()
        if not rec:return
        action=self.action_choice.currentText()
        if action not in LIMITS or action=='aim':return
        try:
            if rec.get('hud_scene')!='match' and (rec.get('user_label') or '')!='partita in corso':
                if QMessageBox.question(self,'Controllo scena',
                    'L’HUD non conferma che eri in partita. Vuoi etichettare questo frame come partita in corso e insegnare l’azione?',
                    QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
                self.learning_store.correct(rec['id'],'partita in corso',rec.get('user_note') or '')
            self.learning_store.teach_action(rec['id'],action)
            self._record(f"Azione insegnata: frame #{rec['id']} → {action}")
            self._refresh_training()
        except Exception as exc:QMessageBox.warning(self,'Azione non salvata',str(exc))

    def _annotate_training_interval(self):
        indices=sorted(i.row() for i in self.training_table.selectionModel().selectedRows())
        if len(indices)<2:
            QMessageBox.information(self,'Seleziona intervallo','Seleziona almeno due fotogrammi della stessa partita (Ctrl + clic).');return
        records=[self.training_rows[i] for i in indices]
        sessions={r['session'] for r in records}
        if len(sessions)!=1:
            QMessageBox.warning(self,'Intervallo non valido','Seleziona fotogrammi della stessa sessione.');return
        start=min(r['captured_at'] for r in records);end=max(r['captured_at'] for r in records)
        event=self.event_choice.currentText().strip()
        if QMessageBox.question(self,'Conferma intervallo',
            f'Etichettare come «{event}» i fotogrammi tra\n{start}\ne\n{end}?',
            QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        try:
            info=self.learning_store.annotate_interval(session=records[0]['session'],start_at=start,end_at=end,event=event)
            self._record(f"Intervallo salvato: {info['frames_labeled']} fotogrammi · {event}")
            self._refresh_training()
        except Exception as exc:QMessageBox.warning(self,'Intervallo non salvato',str(exc))

    def _check_readiness(self):
        try:
            info=inspect_training(self.learning_store)
            self.readiness_label.setText(f'{info.matches} partite · {info.action_examples} esempi · '
                f'{info.action_types} azioni · '+('Prova limitata possibile' if info.trial_ready else 'Dataset da ampliare'))
            QMessageBox.information(self,'Analisi apprendimento',info.describe())
        except Exception as exc:QMessageBox.warning(self,'Analisi fallita',str(exc))

    def _export_learning(self):
        filename,_=QFileDialog.getSaveFileName(self,'Esporta metadati',str(ROOT/'partite.jsonl'),'JSONL (*.jsonl)')
        if filename:
            try:
                n=self.learning_store.export_jsonl(Path(filename));self._record(f'Esportati {n} fotogrammi')
            except Exception as exc:QMessageBox.warning(self,'Esportazione fallita',str(exc))

    def _open_legacy_editor(self):
        try:
            if self.runner and self.runner.thread and self.runner.thread.is_alive():
                QMessageBox.warning(self,'Sessione attiva','Termina la sessione prima di aprire l’editor avanzato.');return
            subprocess.Popen([sys.executable,str(ROOT/'studio_legacy.py')],cwd=str(ROOT))
        except Exception as exc:QMessageBox.warning(self,'Editor non disponibile',str(exc))

    def _run_input_test(self,which):
        if self.runner and self.runner.thread and self.runner.thread.is_alive():
            QMessageBox.warning(self,'Sessione in corso','Ferma la sessione prima di testare i comandi.');return
        if not self.save_settings():return
        name='rotazione mouse' if which=='camera' else 'pressione W'
        if QMessageBox.question(self,'Diagnostica input',
            f'Test {name}: vengono inviati comandi al gioco, SOLO se Fortnite è in primo piano.\n\n'
            'Avrai 3 secondi per tornare al gioco.\nIl risultato visivo può essere inconcludente.\n\nConfermi?',
            QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        self.showMinimized()
        config=dict(self.cfg)
        def work():
            time.sleep(3)
            try:
                from observer.input_probe import probe_camera_visual,probe_forward_visual
                _sent,message=(probe_camera_visual(config) if which=='camera' else probe_forward_visual(config))
            except Exception as exc: message=f'{type(exc).__name__}: {exc}'
            self.camera_result.emit(message)
        threading.Thread(target=work,name='OyeInputProbe',daemon=True).start()

    def _camera_done(self,message):
        self.showNormal();self.activateWindow()
        self._record('Test telecamera: '+message)
        QMessageBox.information(self,'Verifica telecamera',message)

    def closeEvent(self,event):
        if self.runner and self.runner.thread and self.runner.thread.is_alive():
            result=QMessageBox.question(self,'Chiudere Oye?','La sessione è attiva. Arrestarla e chiudere?',
                 QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
            if result!=QMessageBox.Yes:event.ignore();return
            self.runner.stop();self.runner.join(timeout=4)
        if self.listener:
            try:self.listener.stop()
            except Exception:pass
        self._reset_autonomous_flags()
        event.accept()


def main():
    app=QApplication(sys.argv)
    app.setStyle('Fusion');app.setStyleSheet(QSS)
    w=OyeStudio();w.show()
    return app.exec()

if __name__=='__main__':
    raise SystemExit(main())
