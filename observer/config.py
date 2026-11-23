"""Validated local configuration and calibration metadata."""
from __future__ import annotations
from copy import deepcopy
import json
from pathlib import Path
from .hud import HUD_ANCHORS
from .behavior import PROFILES

TEMPLATE_NAMES = (
    'hud_in_partita', 'gioca', 'lobby_indizio', 'continua', 'torna_lobby', 'gioca_di_nuovo', 'riscatta', 'reward_title',
    'caricamento', 'eliminato', 'spettatore_prossimo', 'spettatore_precedente',
    'interazione', 'cassa_visiva', 'bus_salto', 'discesa_apri',
) + HUD_ANCHORS
DEFAULTS = {
    'window_executables': [
        'FortniteClient-Win64-Shipping.exe',
        'FortniteClient-Win64-Shipping_EAC_EOS.exe',
    ],
    'scan_seconds': 0.15,
    'async_capture_enabled': True,
    'async_capture_interval_seconds': 0.10,
    'learning_enabled': True,
    'capture_seconds': 3.0,
    'max_saved_frames': 3600,
    'ocr_enabled': True,
    'ocr_languages': 'eng',
    'phase_ocr_interval_seconds': 1.4,
    'ollama_enabled': False,
    'ollama_model': 'qwen2.5vl:3b',
    'ollama_every_n_frames': 4,
    'ollama_timeout_seconds': 12.0,
    'exploration_vision_seconds': 12.0,
    'autonomous_enabled': False,
    'guided_explore_enabled': False,
    'guided_explore_harvest': True,
    'guided_smooth_enabled': True,
    'guided_activity_profile': 'equilibrato',
    'guided_camera_pixels': 150,
    'guided_solo_mode': True,
    'guided_jump_interval_seconds': 1.6,
    'guided_min_fall_seconds': 20.0,
    'guided_recovery_limit': 3,
    'guided_motion_threshold': 0.55,
    'guided_use_learning': True,
    'guided_timed_enabled': True,
    'single_match_jump_seconds': 120.0,
    'single_match_jump_interval_seconds': 1.8,
    'ignorante_loading_wait_seconds': 120.0,
    'ignorante_jump_min_seconds': 120.0,
    'ignorante_jump_interval_seconds': 0.8,
    'ignorante_jump_max_seconds': 150.0,
    'guided_timed_start_on_launch': True,
    'guided_timed_load_seconds': 90.0,
    'guided_timed_jump_window_seconds': 45.0,
    'guided_timed_fall_seconds': 40.0,
    'guided_timed_ground_seconds': 540.0,
    'guided_health_jump_enabled': True,
    'guided_health_threshold': 0.62,
    'guided_health_min_wait_seconds': 8.0,
    'guided_health_jump_window_seconds': 90.0,
    'guided_explore_manual': 'WASD per muoversi, mouse per guardare, piccone per raccogliere. Evita combattimenti.',
    'autonomous_decision_interval_seconds': 2.0,
    'fast_policy_enabled': True,
    'fast_policy_interval_seconds': 0.15,
    'fast_action_duration_seconds': 0.12,
    'fast_policy_min_confidence': 0.65,
    'demonstration_enabled': False,
    'demonstration_mouse_shoot': False,
    'autonomous_min_confidence': 0.55,
    'autonomous_max_stale_seconds': 8.0,
    'autonomous_stop_after_match': True,
    'template_threshold': 0.89,
    'motion_threshold': 1.20,
    'session_minutes': 120,
    'max_actions': 7500,
    'menu_retries': 2,
    'menu_cooldown_seconds': 12.0,
    'audio_threshold': 0.88,
    'audio_enabled': False,
    'input_backend': 'keyboard',
    'keys': {
        'forward': 'w', 'back': 's', 'left': 'a', 'right': 'd',
        'jump': 'space', 'interact': 'e', 'pickaxe': 'f',
        'slot_1': '1', 'slot_2': '2', 'reload': 'r', 'crouch': 'ctrl',
    },
}


def validate(cfg: dict) -> dict:
    if not isinstance(cfg, dict):
        raise ValueError('Il file di configurazione deve contenere un oggetto JSON')
    result = deepcopy(DEFAULTS)
    extra = set(cfg) - set(result)
    if extra:
        raise ValueError('Parametri sconosciuti: ' + ', '.join(sorted(extra)))
    for key in result:
        if key in cfg:
            result[key] = cfg[key]
    for k, lo, hi in (
        ('phase_ocr_interval_seconds', .6, 8.0),
        ('scan_seconds', .10, 4), ('async_capture_interval_seconds', .05, 1.0), ('capture_seconds', 1., 60.),
        ('autonomous_decision_interval_seconds', .5, 60.),
        ('fast_policy_interval_seconds', .10, 5.),
        ('fast_action_duration_seconds', .10, .50),
        ('fast_policy_min_confidence', .5, .99),
        ('exploration_vision_seconds', 3., 120.),
        ('guided_jump_interval_seconds', .75, 5.),
        ('guided_min_fall_seconds', 8., 60.),
        ('guided_camera_pixels', 20, 320),
        ('guided_timed_load_seconds', 15., 240.),
        ('single_match_jump_seconds', 30., 180.),
        ('single_match_jump_interval_seconds', .75, 5.),
        ('ignorante_loading_wait_seconds', 15., 360.),
        ('ignorante_jump_min_seconds', 30., 300.),
        ('ignorante_jump_interval_seconds', .5, 5.),
        ('ignorante_jump_max_seconds', 30., 300.),
        ('guided_timed_jump_window_seconds', 10., 120.),
        ('guided_timed_fall_seconds', 15., 120.),
        ('guided_timed_ground_seconds', 60., 3600.),
        ('guided_health_threshold', .45, .90),
        ('guided_health_min_wait_seconds', 0., 60.),
        ('guided_health_jump_window_seconds', 10., 120.),
        ('guided_recovery_limit', 1, 8), ('guided_motion_threshold', .1, 5.),
        ('autonomous_min_confidence', .1, 1.),
        ('autonomous_max_stale_seconds', 2., 120.),
        ('max_saved_frames', 50, 10000), ('ollama_every_n_frames', 1, 100),
        ('ollama_timeout_seconds', 3., 120.), ('template_threshold', .75, .995),
        ('motion_threshold', .25, 12), ('session_minutes', 1, 120),
        ('max_actions', 10, 10000), ('menu_retries', 0, 5),
        ('menu_cooldown_seconds', 5, 120), ('audio_threshold', .5, .99),
    ):
        val = result[k]
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not lo <= val <= hi:
            raise ValueError(f'Valore non valido per {k}: {val!r} (range {lo}-{hi})')
    if result['ignorante_jump_min_seconds'] > result['ignorante_jump_max_seconds']:
        raise ValueError('Durata salti Ignorante: minimo superiore al massimo')
    if result['autonomous_enabled'] and not (result['guided_explore_enabled'] or result['ollama_enabled'] or result['fast_policy_enabled']):
        raise ValueError('Per la guida autonoma abilita la policy rapida o Ollama Vision')
    if (result['autonomous_enabled'] and not result['guided_explore_enabled'] and
            result['fast_policy_enabled'] and not result['ollama_enabled'] and not result['learning_enabled']):
        raise ValueError('La modalità FAST-ONLY richiede il dataset di apprendimento attivo')
    if result['guided_activity_profile'] not in PROFILES or not isinstance(result['guided_activity_profile'], str):
        raise ValueError('Profilo di esplorazione non valido')
    if result['input_backend'] not in ('keyboard', 'gamepad'):
        raise ValueError('input_backend deve essere keyboard oppure gamepad')
    for flag in ('learning_enabled', 'async_capture_enabled', 'ocr_enabled', 'ollama_enabled',
                 'autonomous_enabled', 'autonomous_stop_after_match', 'fast_policy_enabled',
                 'demonstration_enabled', 'demonstration_mouse_shoot',
                 'guided_explore_enabled', 'guided_explore_harvest', 'guided_smooth_enabled',
                 'guided_solo_mode', 'guided_use_learning',
                 'guided_timed_enabled', 'guided_timed_start_on_launch', 'guided_health_jump_enabled'):
        if type(result[flag]) is not bool:
            raise ValueError(f'{flag} deve essere true/false')
    if not isinstance(result['ocr_languages'], str) or not __import__('re').fullmatch(r'[a-z]{3}(\+[a-z]{3}){0,3}', result['ocr_languages']):
        raise ValueError('ocr_languages: usa codici Tesseract come eng o ita+eng')
    if (not isinstance(result['guided_explore_manual'], str) or
            len(result['guided_explore_manual']) > 1600):
        raise ValueError('Il manuale di esplorazione deve contenere massimo 1600 caratteri')
    if not isinstance(result['ollama_model'], str) or not result['ollama_model'].strip() or len(result['ollama_model']) > 100:
        raise ValueError('ollama_model non valido')
    if type(result['audio_enabled']) is not bool:
        raise ValueError('audio_enabled deve essere true o false')
    names = result['window_executables']
    if not isinstance(names, list) or not names or any(
        not isinstance(n, str) or not n.lower().endswith('.exe') or '/' in n or '\\' in n
        for n in names
    ):
        raise ValueError('window_executables: elenco di nomi EXE, senza percorsi')
    if not isinstance(result['keys'], dict) or (set(result['keys']) - set(DEFAULTS['keys'])):
        raise ValueError('keys: associazioni richieste non valide')
    # Migrate v6/v7 custom bindings.
    result['keys'] = {**DEFAULTS['keys'], **result['keys']}
    if any(not isinstance(v, str) or not v.strip() or len(v) > 24 for v in result['keys'].values()):
        raise ValueError('keys: tasti non validi')
    return result


def load(path: Path) -> dict:
    if not path.exists():
        return validate({})
    return validate(json.loads(path.read_text(encoding='utf-8')))


def save(path: Path, cfg: dict) -> None:
    clean = validate(cfg)
    path.write_text(json.dumps(clean, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def validate_roi(roi) -> tuple[float, float, float, float]:
    if not isinstance(roi, (list, tuple)) or len(roi) != 4:
        raise ValueError('ROI deve essere [x1, y1, x2, y2]')
    vals = tuple(float(v) for v in roi)
    if not (0 <= vals[0] < vals[2] <= 1 and 0 <= vals[1] < vals[3] <= 1):
        raise ValueError('ROI normalizzata non valida')
    return vals


def load_rois(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or any(name not in TEMPLATE_NAMES for name in data):
        raise ValueError('Metadati ROI non validi')
    return {name: validate_roi(roi) for name, roi in data.items()}
