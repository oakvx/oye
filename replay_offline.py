"""Offline phase/action scenario replay (NEVER sends inputs)."""
from __future__ import annotations
import argparse
from pathlib import Path
from observer.replay import run_file


def main():
    parser = argparse.ArgumentParser(description='Prova la logica di Oye senza aprire Fortnite')
    parser.add_argument('scenario', type=Path, help='JSON con array frames')
    parser.add_argument('--output', type=Path, help='Salva i risultati in JSON')
    args = parser.parse_args()
    results = run_file(args.scenario, args.output)
    for step in results:
        print(f'{step.at:>7.2f}s  {step.scene:<12}  {step.stage:<11}  {step.action:<18}  {step.reason}')
    print(f'Frame analizzati: {len(results)}. Nessun input inviato.')

if __name__ == '__main__':
    main()
