"""Simula le azioni senza inviare alcun input al computer."""
import argparse
from observer.behavior import PROFILES, activity_preview

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Anteprima OFFLINE delle routine Oye')
    parser.add_argument('--profilo', choices=PROFILES, default='equilibrato')
    parser.add_argument('--azioni', type=int, default=25)
    parser.add_argument('--senza-piccone', action='store_true')
    args = parser.parse_args()
    print(activity_preview(args.profilo, harvest=not args.senza_piccone, count=args.azioni))
