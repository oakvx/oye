"""Slow asynchronous OCR must not be mistaken for a missing button."""
import unittest
from observer.runner import _cached_play_match
from observer.menu_supervisor import MenuSupervisor
from observer.models import Match


class SlowOCRTests(unittest.TestCase):
    def test_distinct_ocr_readings_confirm_with_intervening_video_frames(self):
        supervisor = MenuSupervisor()
        region = {}
        offers = []
        for frame in (1., 1.2, 1.4, 2.5, 2.7, 4., 4.2):
            if frame in (1., 2.5, 4.):
                region['gioca'] = (frame, {'score': .9, 'center': (20, 30)})
            at, match = _cached_play_match(frame, 'lobby', region, None, 100, 200)
            offers.append(supervisor.offer(now=frame, frame_at=frame, stage='joining',
                scene='lobby', matches={'gioca': match}, repeat=True,
                evidence_times={'gioca': at}))
        self.assertTrue(all(o.action.name == 'wait' for o in offers[:5]))
        self.assertEqual(offers[5].action.name, 'menu_click')
        self.assertEqual(offers[5].action.point, (120, 230))
        self.assertEqual(offers[6].action.name, 'wait')  # same OCR is not another confirmation

    def test_cached_ocr_expires_and_never_overrides_other_scenes(self):
        scanner = (1., Match('gioca', .9, (4, 5)))
        self.assertEqual(_cached_play_match(2, 'lobby', {}, scanner, 0, 0), scanner)
        self.assertIsNone(_cached_play_match(8.5, 'lobby', {}, scanner, 0, 0))
        self.assertIsNone(_cached_play_match(2, 'results', {}, scanner, 0, 0))
        self.assertIsNone(_cached_play_match(.5, 'lobby', {}, scanner, 0, 0))

if __name__ == '__main__':
    unittest.main()

class SlowResultOCRTests(unittest.TestCase):
    def test_result_buttons_keep_independent_timestamps(self):
        from observer.runner import _cached_result_menu_matches
        for name in ('continua', 'torna_lobby', 'gioca_di_nuovo'):
            supervisor = MenuSupervisor()
            region = {}
            for frame in (1., 1.2, 2.5, 2.7, 4.):
                if frame in (1., 2.5, 4.):
                    region[name] = (frame, {'score': .9, 'center': (20, 30)})
                at, match = _cached_result_menu_matches(frame, 'results', region, 100, 200)[name]
                p = supervisor.offer(now=frame, frame_at=frame, stage='ending',
                    scene='results', matches={name: match}, repeat=True,
                    evidence_times={name: at})
                self.assertEqual(p.action.name, 'menu_click' if frame == 4. else 'wait')
            self.assertEqual(p.action.point, (120, 230))

    def test_cache_never_authorizes_reward_or_incompatible_scene(self):
        from observer.runner import _cached_result_menu_matches
        region = {name: (1., {'score': .9, 'center': (20, 30)})
                  for name in ('gioca_di_nuovo', 'riscatta')}
        self.assertEqual(set(_cached_result_menu_matches(2, 'results', region, 0, 0)), {'gioca_di_nuovo'})
        self.assertEqual(_cached_result_menu_matches(2, 'lobby', region, 0, 0), {})
        self.assertEqual(_cached_result_menu_matches(9, 'results', region, 0, 0), {})

class RealisticOCRCadenceTests(unittest.TestCase):
    def test_gaps_do_not_turn_cached_readings_into_clicks(self):
        from observer.runner import _cached_result_menu_matches
        for name, stage, scene in (('gioca', 'joining', 'lobby'),
                                   ('gioca_di_nuovo', 'ending', 'results')):
            supervisor = MenuSupervisor()
            region = {}
            proposed = []
            # 1.4 s submit cadence, alternate modular and legacy turns,
            # preferred ROI every other legacy slot => one read per 5.6 s.
            for i in range(57):
                t = round(i * .2, 1)
                if i in (0, 28, 56):
                    region[name] = (t, {'score': .9, 'center': (20, 30)})
                if name == 'gioca':
                    evidence = _cached_play_match(t, scene, region, None, 100, 200)
                else:
                    evidence = _cached_result_menu_matches(t, scene, region, 100, 200).get(name)
                matches = {name: evidence[1]} if evidence else {}
                times = {name: evidence[0]} if evidence else {}
                offer = supervisor.offer(now=t, frame_at=t, stage=stage, scene=scene,
                    matches=matches, repeat=True, evidence_times=times)
                if offer.action.name == 'menu_click':
                    proposed.append(t)
            self.assertEqual(proposed, [11.2], name)

    def test_button_evidence_logging_does_not_invent_fresh_samples(self):
        from observer.runner import Runner
        runner = object.__new__(Runner)
        runner._button_positive_at = {}
        runner._button_logged_at = {}
        events = []
        runner._emit = lambda kind, message, info: events.append((kind, info))
        runner._record_button_evidence('gioca', 'ocr_zona', 1., .9, 'present', 2.)
        runner._record_button_evidence('gioca', 'ocr_zona', 1., .9, 'present', 3.)
        runner._record_button_evidence('gioca', 'ocr_zona', 6.6, .8, 'present', 7.)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1][1]['since_previous_positive_seconds'], 5.6)
        self.assertEqual(events[1][1]['age_seconds'], .4)
