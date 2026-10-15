import pytest

from blueshare.pointer import PIN_COUNTS, SETTLE_S, Calibration, EdgePush, Exit, PointerModel, Side

CAL = Calibration(width=2000, height=1000)


def test_enter_from_pc_below_pins_bottom_left_then_moves_inside():
    model = PointerModel(CAL, Side.TOP)
    moves = model.enter(0.25)
    assert moves == [(-PIN_COUNTS, PIN_COUNTS, SETTLE_S), (500, -20, SETTLE_S)]
    assert (model.x, model.y) == (500, 980)


def test_return_needs_push_beyond_margin():
    model = PointerModel(CAL, Side.TOP, return_margin=0.1)
    model.enter(0.5)
    assert not model.move(0, 20, now=1.0)        # reaches the bottom edge
    assert not model.move(0, 60, now=1.1)        # 60 past the edge: within the 100 margin
    assert model.move(0, 50, now=1.2)            # 110 past the edge


def test_overshoot_resets_after_a_pause():
    model = PointerModel(CAL, Side.TOP, return_margin=0.1, overshoot_reset_s=0.4)
    model.enter(0.5)
    model.move(0, 20, now=1.0)
    assert not model.move(0, 90, now=1.1)
    assert not model.move(0, 90, now=2.0)        # pause > 0.4 s: start counting again
    assert model.move(0, 20, now=2.1)


def test_moving_back_up_clears_overshoot():
    model = PointerModel(CAL, Side.TOP, return_margin=0.1)
    model.enter(0.5)
    model.move(0, 89, now=1.0)                   # 69 past the edge
    model.move(0, -5, now=1.1)                   # counted 3x: may have been accelerated
    assert model.overshoot == 0 and model.y == 985
    assert not model.move(0, 60, now=1.2)


def test_moves_away_from_the_return_edge_are_pessimistic():
    model = PointerModel(CAL, Side.TOP, return_margin=0.1, away_factor=3.0)
    model.enter(0.5)                             # y = 980
    model.move(0, -100, now=1.0)                 # estimated 300 up
    assert model.y == 680
    assert not model.move(0, 100 + 20 + 101, now=1.1)   # the real cursor is past the edge, the estimate is not
    assert not model.move(0, 199, now=1.2)       # estimate reaches the edge, 100 beyond: not past the margin
    assert model.move(0, 1, now=1.3)


def test_without_margin_or_pessimism_returns_right_at_the_edge():
    model = PointerModel(CAL, Side.TOP, return_margin=0.0, away_factor=1.0)
    model.enter(0.5)                             # y = 980
    model.move(0, -100, now=1.0)
    assert model.y == 880
    assert not model.move(0, 121, now=1.1)       # 1 count past the edge: the minimum margin
    assert model.move(0, 1, now=1.2)


def test_negative_margin_returns_before_the_edge_only_when_heading_there():
    model = PointerModel(CAL, Side.TOP, return_margin=-0.03, away_factor=1.0)  # lead: 30 counts
    model.enter(0.5)                             # y = 980, 20 from the edge
    assert not model.move(-50, 0, now=1.0)       # moving along the edge
    assert not model.move(0, -100, now=1.1)      # y = 880
    assert not model.move(0, 60, now=1.2)        # y = 940: 60 from the edge
    assert model.move(0, 31, now=1.3)            # y = 971: within 30 and moving down


def test_side_exit_needs_the_margin_and_is_pessimistic():
    model = PointerModel(CAL, Side.TOP, return_margin=0.1, away_factor=3.0)  # side margin: 200
    model.exit_before = True
    model.enter(0.5)                             # x = 1000
    model.move(100, 0, now=1.0)                  # for the left edge this counts 300: estimate 1300
    assert model.move(-1300, 0, now=1.1) is Exit.NONE    # reaches the left edge
    assert model.move(-200, 0, now=1.2) is Exit.NONE     # within the margin
    assert model.move(-1, 0, now=1.3) is Exit.BEFORE
    assert model.move(5000, 0, now=1.4) is Exit.NONE     # right exit not enabled


def test_side_edges_have_their_own_settings():
    model = PointerModel(CAL, Side.TOP, return_margin=0.1, away_factor=3.0,
                         side_margin=0.0, side_away_factor=1.0)       # side: no margin, no pessimism
    model.exit_after = True
    model.enter(0.5)                                                  # x = 1000
    model.move(-100, 0, now=1.0)                                      # not scaled for the right edge
    assert model.move(1100, 0, now=1.1) is Exit.NONE                  # reaches the right edge
    assert model.move(2, 0, now=1.2) is Exit.AFTER                    # minimum margin: 1 count
    assert model.margin_counts == 100                                 # the return keeps its own margin


def test_moves_along_the_edge_are_not_scaled():
    model = PointerModel(CAL, Side.TOP, away_factor=3.0)
    model.enter(0.5)
    model.move(-100, 0, now=1.0)
    assert model.x == 900


def test_cursor_is_clamped_on_other_edges():
    model = PointerModel(CAL, Side.TOP)
    model.enter(0.5)
    model.move(-5000, -5000, now=1.0)
    assert (model.x, model.y) == (0, 0)
    assert not model.move(0, 1050, now=1.1)      # needs margin beyond the bottom edge
    assert model.fraction_along_edge() == 0.0


@pytest.mark.parametrize("side,moves,pos", [
    (Side.BOTTOM, [(-PIN_COUNTS, -PIN_COUNTS, SETTLE_S), (1000, 20, SETTLE_S)], (1000, 20)),
    (Side.LEFT, [(PIN_COUNTS, -PIN_COUNTS, SETTLE_S), (-40, 500, SETTLE_S)], (1960, 500)),
    (Side.RIGHT, [(-PIN_COUNTS, -PIN_COUNTS, SETTLE_S), (40, 500, SETTLE_S)], (40, 500)),
])
def test_entry_for_other_sides(side, moves, pos):
    model = PointerModel(CAL, side)
    assert model.enter(0.5) == moves
    assert (model.x, model.y) == pos


def test_return_for_left_side_tablet():
    model = PointerModel(CAL, Side.LEFT, return_margin=0.1)
    model.enter(0.5)
    assert not model.move(40, 0, now=1.0)
    assert model.move(201, 0, now=1.1)


def test_edge_push_requires_continuous_push():
    push = EdgePush(threshold_counts=100, reset_s=0.3)
    assert not push.feed(True, 60, now=1.0)
    assert not push.feed(True, 0, now=1.05)      # sliding along the edge keeps the count
    assert push.feed(True, 40, now=1.1)
    push.reset()
    assert not push.feed(True, 60, now=2.0)
    assert not push.feed(False, 60, now=2.1)     # left the edge
    assert not push.feed(True, 60, now=2.2)
    assert not push.feed(True, 60, now=3.0)      # too slow: restarted


def test_edge_push_tolerates_jitter_but_not_leaving_the_edge():
    push = EdgePush(threshold_counts=100)
    push.feed(True, 90, now=1.0)
    assert not push.feed(True, -1, now=1.05)     # jitter away from the edge: count kept
    assert push.feed(True, 10, now=1.1)
    push.reset()
    push.feed(True, 90, now=2.0)
    assert not push.feed(False, -5, now=2.05)    # really left the edge
    assert not push.feed(True, 20, now=2.1)
