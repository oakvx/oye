"""Import only missing calibration rules; never replace the user's database.

Run without --apply for a dry run. The selected profile must already be the
same resolution and mode as the seed. Templates are intentionally not copied.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import sqlite3
import sys

SEED = Path(__file__).resolve().parents[1] / 'assets/calibration/profilo_preliminare_1920x1080.sqlite3'


def import_rules(database: Path, seed: Path = SEED, *, apply: bool = False):
    database, seed = Path(database), Path(seed)
    if not database.is_file() or not seed.is_file():
        raise ValueError('Database esistente o profilo di esempio non trovato: nessuna modifica')
    if database.resolve() == seed.resolve():
        raise ValueError('Il database di destinazione non può essere il profilo di esempio')
    source = sqlite3.connect(seed.resolve().as_uri() + '?mode=ro', uri=True)
    target = sqlite3.connect(database.resolve().as_uri() + '?mode=rw', uri=True, timeout=5)
    try:
        source.row_factory = target.row_factory = sqlite3.Row
        if apply:
            target.execute('BEGIN IMMEDIATE')
        source_profile = source.execute('SELECT * FROM oye_resolution_profiles WHERE selected=1').fetchall()
        selected = target.execute('SELECT * FROM oye_resolution_profiles WHERE selected=1').fetchall()
        if len(source_profile) != 1 or len(selected) != 1:
            raise ValueError('Seleziona un unico profilo di risoluzione prima di importare')
        src, dst = source_profile[0], selected[0]
        if (src['width'], src['height'], src['mode']) != (dst['width'], dst['height'], dst['mode']):
            raise ValueError('Il profilo selezionato non corrisponde a 1920x1080 borderless: nessuna modifica')
        columns = ('target', 'state', 'action_description', 'user_explanation', 'region_json', 'expected_text', 'updated_at')
        rows = source.execute('SELECT ' + ','.join(columns) + ' FROM oye_screen_rules WHERE profile_id=?', (src['id'],)).fetchall()
        existing = {r[0] for r in target.execute('SELECT target FROM oye_screen_rules WHERE profile_id=?', (dst['id'],))}
        added = [r['target'] for r in rows if r['target'] not in existing]
        skipped = [r['target'] for r in rows if r['target'] in existing]
        if apply:
            for row in rows:
                if row['target'] not in existing:
                    target.execute('INSERT INTO oye_screen_rules (profile_id,' + ','.join(columns) + ') VALUES (' + ','.join('?' for _ in range(len(columns)+1)) + ')',
                                   (dst['id'], *(row[k] for k in columns)))
            target.commit()
        return added, skipped
    finally:
        target.close()
        source.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Importazione non distruttiva delle ROI preliminari')
    parser.add_argument('--root', type=Path, default=Path('.'), help='Cartella dati dell’installazione Oye')
    parser.add_argument('--apply', action='store_true', help='Applica; senza questa opzione mostra solo l’anteprima')
    args = parser.parse_args(argv)
    try:
        added, skipped = import_rules(args.root / 'learning' / 'observer.sqlite3', apply=args.apply)
    except (ValueError, sqlite3.Error) as exc:
        print('Importazione interrotta:', exc, file=sys.stderr)
        return 1
    print(('Inserite' if args.apply else 'Da inserire') + ': ' + (', '.join(added) or 'nessuna'))
    print('Già esistenti, non modificate: ' + (', '.join(skipped) or 'nessuna'))
    print('Template e screenshot non inclusi. Riavvia Oye; OCR e clic non sono ancora verificati.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
