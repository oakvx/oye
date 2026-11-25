from observer.ignorante import IgnoranteController, MatchFrame
from observer.models import Match


def sample(cycle, at, *signals, valid=True, playable=False, blocked=False):
    buttons = {name: Match(name, .99, (200, 800)) for name in signals
               if name in ('gioca', 'gioca_di_nuovo', 'torna_lobby')}
    offer = cycle.observe(MatchFrame(at, valid, frozenset(signals), buttons,
                                     playable=playable, blocked=blocked), now=at)
    if offer.name:
        cycle.on_clicked(offer.name, at)
    return offer


def test_three_matches_with_direct_restarts_and_lobby_fallback():
    cycle = IgnoranteController(0)
    assert sample(cycle, 1, 'gioca').name is None
    assert sample(cycle, 2, 'gioca').name == 'gioca'
    for at in (3, 4, 26):
        assert sample(cycle, at, 'annulla').action.name == 'wait'
    assert sample(cycle, 27).action.name == 'wait'
    assert sample(cycle, 28, 'annulla').action.name == 'wait'
    assert sample(cycle, 29).action.name == 'wait'
    assert sample(cycle, 29.5, valid=False).action.name == 'wait'
    for at in (30, 30.5):
        assert sample(cycle, at).action.name == 'wait'
    assert sample(cycle, 31).move

    cycle.interrupt(31.1, 'Pausa')
    for at in (32, 32.5):
        assert not sample(cycle, at).move
    assert sample(cycle, 33).move

    assert not sample(cycle, 34, 'posizione_finale', playable=True).move
    assert cycle.matches_completed == 0
    assert sample(cycle, 35, 'posizione_finale', 'gioca_di_nuovo').name is None
    assert sample(cycle, 36, 'gioca_di_nuovo', 'torna_lobby').name == 'gioca_di_nuovo'
    assert cycle.matches_completed == 1
    assert not sample(cycle, 37).move
    assert not sample(cycle, 38, playable=True).move
    assert sample(cycle, 39, playable=True).move

    assert sample(cycle, 40, 'posizione_finale', 'torna_lobby').name is None
    assert sample(cycle, 41, 'torna_lobby').name is None
    for at in (44, 47, 50, 53, 55):
        assert sample(cycle, at, 'torna_lobby').name is None
    assert sample(cycle, 56, 'torna_lobby').name == 'torna_lobby'
    assert cycle.matches_completed == 2
    for at in (57, 58):
        assert sample(cycle, at, 'gioca').name is None
    cycle.lobby_ready(59)  # Existing reward workflow has completed.
    assert sample(cycle, 60, 'gioca').name is None
    assert sample(cycle, 61, 'gioca').name == 'gioca'
    for at in (62, 63):
        assert not sample(cycle, at, 'annulla').move
    for at in (64, 64.5):
        assert not sample(cycle, at).move
    assert sample(cycle, 65).move
    for at in (66, 67, 68):
        assert not sample(cycle, at, 'posizione_finale').move
    assert cycle.matches_completed == 3


def test_ineffective_click_is_retried_only_on_fresh_confirmations_then_suspends():
    cycle = IgnoranteController(0)
    assert sample(cycle, 1, 'gioca').name is None
    assert sample(cycle, 1, 'gioca').name is None
    assert sample(cycle, 2, 'gioca').name == 'gioca'
    assert sample(cycle, 13, 'gioca').name is None
    assert sample(cycle, 14, valid=False).name is None
    assert sample(cycle, 15, 'gioca').name is None
    assert sample(cycle, 16, 'gioca').name == 'gioca'
    assert sample(cycle, 27, 'gioca').name is None
    assert sample(cycle, 28, 'gioca').name == 'gioca'
    for at in (39, 40, 240, 241):
        assert sample(cycle, at, 'gioca').name is None
    assert cycle.stage == 'error'
    assert 'quattro minuti' in cycle.reason


def test_direct_restart_cannot_count_one_modular_hud_detection_twice():
    cycle = IgnoranteController(0)
    sample(cycle, 1, 'gioca')
    sample(cycle, 2, 'gioca')
    sample(cycle, 3, 'annulla')
    sample(cycle, 4, 'annulla')
    for at in (5, 5.5, 6):
        sample(cycle, at)
    sample(cycle, 7, 'gioca_di_nuovo')
    assert sample(cycle, 8, 'gioca_di_nuovo').name == 'gioca_di_nuovo'
    for at, detected_at in ((9, 7.5), (10, 9), (10.5, 9), (11, 9)):
        observation = MatchFrame(at, True, playable=True, playable_at=detected_at)
        assert not cycle.observe(observation, now=at).move
    assert cycle.observe(MatchFrame(12, True, playable=True, playable_at=11.5), now=12).move


def test_manual_start_can_join_matchmaking_without_automatic_play_click():
    cycle = IgnoranteController(0)
    assert not sample(cycle, 1, 'annulla').move
    assert not sample(cycle, 2, 'annulla').move
    assert cycle.stage == 'searching'
    for at in (3, 3.5):
        assert not sample(cycle, at).move
    assert sample(cycle, 4).move


def test_start_in_match_or_missed_cancel_requires_two_playable_captures():
    for clicked in (False, True):
        cycle = IgnoranteController(0)
        if clicked:
            cycle.on_clicked('gioca', 0)
        for at in (1, 2, 3):
            assert not sample(cycle, at).move
        assert not sample(cycle, 4, playable=True).move
        assert sample(cycle, 5, playable=True).move
        assert cycle.stage == 'playing'


def test_brief_cancel_does_not_block_later_gameplay_confirmation():
    cycle = IgnoranteController(0)
    assert not sample(cycle, 1, 'annulla').move
    assert not sample(cycle, 2, playable=True).move
    assert sample(cycle, 3, playable=True).move


def test_playable_hint_cannot_override_reward_or_menu():
    for signals, blocked in [((), True), (('gioca',), False),
                             (('posizione_finale',), False)]:
        cycle = IgnoranteController(0)
        for at in (1, 2, 3):
            assert not sample(cycle, at, *signals, playable=True, blocked=blocked).move
