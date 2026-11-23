"""Conservative seed profile derived from the supplied 2026-11-22 screenshots.

Single match, not three independently validated sessions.  All ROI positions are
normalized from the user's 1920x1080 borderless capture. Only stable, visibly
identified menu/text elements are enabled. No claim of HUD generalization.
"""
from __future__ import annotations
from pathlib import Path

VERSION='fortnite-1920x1080-screenshots-v42'
MARKER='Profilo screenshot v42: '  # conservative: do not overwrite user edits
REFERENCE_DIR=Path(__file__).resolve().parent.parent/'assets'/'fortnite_1920x1080_v42'

def normalized(pixel_roi):
    x1,y1,x2,y2=pixel_roi
    return [x1/1920,y1/1080,x2/1920,y2/1080]

# Scanners are independent and use one frame. Location never includes the
# navigation tab reading GIOCA at the top, which persists during matchmaking.
SEEDS={
    'lobby':{
        'name':'GIOCA · pulsante sinistro', 'method':'template',
        'roi':normalized((65,830,460,990)), 'expected_text':'GIOCA',
        'threshold':.85,'state':'LOBBY','weight':1.9,'interval':1.0,
        'options':{'absence_threshold':.66,'explicit_absence':True},
        'description':MARKER+'il GIOCA operativo è nel pannello sinistro, NON nella barra superiore'},
    'matchmaking':{
        'name':'ANNULLA · matchmaking', 'method':'template',
        'roi':normalized((65,830,460,990)), 'expected_text':'ANNULLA',
        'threshold':.88,'state':'MATCHMAKING','weight':1.9,'interval':1.2,
        'options':{},'description':MARKER+'ricerca server: ANNULLA al posto di GIOCA'},
    'spectating':{
        'name':'SPETTATORE · testata', 'method':'template',
        'roi':normalized((730,0,1280,115)), 'expected_text':'SPETTATORE',
        'threshold':.85,'state':'SPECTATING','weight':2.0,'interval':1.0,
        'options':{},'description':MARKER+'scritta testata; può sparire durante osservazione compagni'},
    'reboot_wait':{
        'name':'IN ATTESA DEL RIAVVIO · spettatore', 'method':'template',
        'roi':normalized((730,540,1300,730)), 'expected_text':'IN ATTESA DEL RIAVVIO',
        'threshold':.76,'state':'SPECTATING','weight':2.1,'interval':1.0,
        'options':{},'description':MARKER+'schermata dopo eliminazione, anche senza etichetta SPETTATORE'},
    'elimination':{
        'name':'POSIZIONE FINALE · risultati', 'method':'template',
        'roi':normalized((680,0,1250,143)), 'expected_text':'POSIZIONE FINALE',
        'threshold':.90,'state':'RETURNING_TO_LOBBY','weight':2.1,'interval':1.0,
        'options':{},'description':MARKER+'risultati; cifra del piazzamento esclusa dal template'},
    'return_to_lobby':{
        'name':'RITORNA ALLA LOBBY · risultato', 'method':'template',
        'roi':normalized((1230,380,1890,690)), 'expected_text':'RITORNA ALLA LOBBY',
        'threshold':.90,'state':'RETURNING_TO_LOBBY','weight':2.1,'interval':1.0,
        'options':{},'description':MARKER+'pulsante risultati; non premere GIOCA DI NUOVO'},
}

# A fixed-pixel screenshot template must not silently move to another aspect ratio.
for _spec in SEEDS.values():
    _spec['options']['resolution']=[1920,1080]

def packaged_reference(scanner_id):
    file=REFERENCE_DIR/(scanner_id+'.png')
    return file if scanner_id in SEEDS and file.is_file() else None
