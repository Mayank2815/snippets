"""The activity governor, and the calibration it promises.

    python3 -m unittest discover -s engine/tests -v

Everything here runs on a virtual clock and a seeded random source. Nothing
sleeps, nothing is generated, nothing on the machine is touched, and no test
can fail on an unlucky draw — a flaky calibration test would be worse than no
calibration test, because the number it guards is the whole point of the
feature and nobody trusts an assertion that goes red on its own.
"""

import os
import random
import statistics
import sys
import unittest

os.environ.setdefault("ENGINE_BACKEND", "fake")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import governor as gov_module  # noqa: E402
from governor import ActivityGovernor  # noqa: E402
import engine  # noqa: E402
import test_metrics  # noqa: E402

BLOCK = gov_module.BLOCK_SECONDS
PER_WINDOW = gov_module.BLOCKS_PER_WINDOW


class Clock:
    """A clock the test moves by hand."""

    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def governor_at(clock, seed=1, read_idle=None):
    gov = ActivityGovernor(clock, read_idle=read_idle, rng=random.Random(seed))
    gov.begin()
    return gov


class HardCeilingTest(unittest.TestCase):
    """The ceiling is a limit the loop cannot cross, not a target it aims at."""

    def test_an_engine_that_never_stops_asking_still_cannot_pass_the_ceiling(self):
        """The adversarial case: claim() on every single block, forever. This
        is the assertion the whole feature rests on."""
        clock = Clock()
        gov = governor_at(clock, seed=7)
        windows = 200
        used_per_window = []
        used = 0
        for block in range(windows * PER_WINDOW):
            # Ask several times inside the block, as a real burst does.
            for _ in range(5):
                if gov.claim():
                    pass
                clock.advance(BLOCK / 5.0)
            if (block + 1) % PER_WINDOW == 0:
                used_per_window.append(len(gov._used))
        used_per_window.append(len(gov._used))
        self.assertTrue(used_per_window)
        worst = max(used_per_window)
        self.assertLessEqual(
            worst, gov_module.CEILING_BLOCKS,
            f"a window used {worst} blocks, above the ceiling of {gov_module.CEILING_BLOCKS}")
        # And the percentage form of the same statement, which is how the
        # requirement is written.
        self.assertLessEqual(worst / PER_WINDOW * 100.0, gov_module.CEILING_PERCENT)

    def test_the_ceiling_holds_for_rolling_windows_too(self):
        """A tracker does not have to line its windows up with ours.

        The shape that breaks a windows-only ceiling: stay quiet through the
        first half of every OTHER window, and ask on every block otherwise.
        Each fixed window still passes — pro-rata pacing only limits running
        AHEAD, so a window that has fallen behind is allowed to catch up — but
        that catch-up sits in the second half of one window, directly against
        the ordinary first half of the next, and the ten minutes spanning the
        two is far over the limit. Measured at 75.0 % in a whole simulated run
        before the rolling check existed, and higher than that under this
        deliberately adversarial pattern.
        """
        clock = Clock()
        gov = governor_at(clock, seed=11)
        marked = []
        for block in range(200 * PER_WINDOW):
            window, offset = divmod(block, PER_WINDOW)
            quiet = window % 2 == 0 and offset < PER_WINDOW // 2
            if not quiet and gov.claim():
                marked.append(block)
            clock.advance(BLOCK)
        worst = test_metrics.rolling_max_percent(set(marked))
        self.assertLessEqual(worst, gov_module.CEILING_PERCENT,
                             f"a rolling 10-minute window reached {worst:.1f}%")

    def test_no_drawn_target_ever_reaches_the_ceiling(self):
        """The ceiling should be a backstop, not the working mechanism: in
        normal operation the window target is what limits things."""
        gov = governor_at(Clock(), seed=3)
        targets = [gov._target_blocks_for(w) for w in range(600)]
        self.assertLess(max(targets), gov_module.CEILING_BLOCKS)
        self.assertGreater(min(targets), 0)


class RandomTargetTest(unittest.TestCase):
    def test_consecutive_windows_draw_different_targets(self):
        """"Random per window" has to mean the next window is genuinely not the
        last one — a constant budget would satisfy every other test here."""
        gov = governor_at(Clock(), seed=5)
        targets = [gov._target_blocks_for(w) for w in range(200)]
        pairs = list(zip(targets, targets[1:]))
        different = sum(1 for a, b in pairs if a != b)
        self.assertGreater(different / len(pairs), 0.8,
                           "consecutive windows mostly drew the same budget")
        # And the spread is wide enough to see, not a jitter around one value.
        self.assertGreaterEqual(max(targets) - min(targets), 8)

    def test_the_bag_keeps_the_long_run_mean_where_it_was_tuned(self):
        """The shuffle bag is what makes the three-hour average reliable. Any
        whole number of bags must average the bag's own mean exactly, jitter
        aside."""
        gov = governor_at(Clock(), seed=9)
        bag = gov_module.WINDOW_TARGET_BAG
        drawn = [gov._target_blocks_for(w) for w in range(len(bag) * 400)]
        self.assertAlmostEqual(statistics.fmean(drawn), statistics.fmean(bag), delta=0.1)

    def test_each_bag_deals_every_budget_once(self):
        """One bag is one of each value, in a random order, each nudged by at
        most the jitter — so its total is the bag's total give or take that."""
        gov = governor_at(Clock(), seed=13)
        bag = gov_module.WINDOW_TARGET_BAG
        slack = len(bag) * gov_module.WINDOW_TARGET_JITTER
        # begin() already dealt the first window's budget, so drain what is
        # left of that bag before counting whole ones.
        while gov._bag:
            gov._target_blocks_for(0)
        for round_index in range(20):
            dealt = [gov._target_blocks_for(w) for w in range(len(bag))]
            self.assertAlmostEqual(sum(dealt), sum(bag), delta=slack,
                                   msg=f"bag {round_index} dealt {sorted(dealt)}")


class PacingTest(unittest.TestCase):
    def test_the_budget_cannot_be_spent_in_the_first_few_minutes(self):
        """Front-loading is its own pattern, and it would also fail a rolling
        read. An engine asking on every block must still have blocks left
        halfway through the window."""
        clock = Clock()
        gov = governor_at(clock, seed=17)
        target = gov._target_blocks
        for _ in range(PER_WINDOW // 2):
            gov.claim()
            clock.advance(BLOCK)
        halfway = len(gov._used)
        self.assertLessEqual(halfway, target // 2 + 1,
                             "more than half the budget went in the first half of the window")

    def test_both_halves_of_a_window_carry_activity(self):
        """The complement of the test above: paced does not mean dead later."""
        clock = Clock()
        gov = governor_at(clock, seed=19)
        first_half, second_half = 0, 0
        for block in range(PER_WINDOW):
            if gov.claim():
                if block < PER_WINDOW // 2:
                    first_half += 1
                else:
                    second_half += 1
            clock.advance(BLOCK)
        self.assertGreater(first_half, 0, "nothing happened in the first half of the window")
        self.assertGreater(second_half, 0, "nothing happened in the second half of the window")
        # Neither half should carry more than about two thirds of the total.
        total = first_half + second_half
        self.assertLess(max(first_half, second_half) / total, 0.7,
                        f"activity was lopsided: {first_half} then {second_half}")


class HumanInputTest(unittest.TestCase):
    """The engine has to notice the person, count them, and get out of the way."""

    def setUp(self):
        self.clock = Clock()
        self.idle = [1e9]          # seconds since the last input of any kind
        self.gov = governor_at(self.clock, seed=23, read_idle=lambda: self.idle[0])

    def _human_types_now(self):
        self.idle[0] = 0.0

    def _nobody_has_touched_it_for(self, seconds):
        self.idle[0] = seconds

    def test_the_engine_does_not_mistake_its_own_input_for_the_user(self):
        """The failure this guards is an engine that pauses forever: every
        synthetic keystroke resets the same idle counter a real one does."""
        for _ in range(200):
            self.gov.claim()
            self.gov.note_engine_input()
            self.idle[0] = 0.0     # the OS sees OUR input, zero seconds ago
            self.clock.advance(0.05)
            self.gov.observe_user_input()
        self.assertFalse(self.gov.user_is_active(),
                         "the engine read its own input as the user's and would pause forever")

    def test_a_keystroke_while_the_engine_is_quiet_is_seen_as_the_user(self):
        self.clock.advance(30.0)        # the engine has done nothing for a while
        self._human_types_now()
        self.assertTrue(self.gov.observe_user_input())
        self.assertTrue(self.gov.user_is_active())

    def test_the_users_own_blocks_count_against_the_same_budget(self):
        """The main reason the governor exists: without this the engine piles
        its activity on top of someone already working."""
        self.clock.advance(5.0)
        self.assertEqual(len(self.gov._used), 0)
        self._human_types_now()
        self.gov.observe_user_input()
        self.assertEqual(len(self.gov._used), 1, "the user's block was not counted")

        # And it is the same budget, not a second one: with the window's whole
        # budget spent by the person, the engine gets nothing. A small budget is
        # forced here so the refusal is unambiguously the target doing it and
        # not the ceiling.
        self.gov._target_blocks = 5
        for block in range(1, 5):
            self.clock.now = self.gov._started_at + block * BLOCK + 1.0
            self._human_types_now()
            self.gov.observe_user_input()
        self.assertEqual(len(self.gov._used), 5)
        # A block the person has NOT touched, late enough that pacing is not
        # the thing refusing either.
        self.clock.now = self.gov._started_at + 40 * BLOCK
        self._nobody_has_touched_it_for(1e9)
        self.assertFalse(self.gov.claim(), "the engine claimed a block the person had already spent")

    def test_activity_resumes_after_the_quiet_period(self):
        self.clock.advance(30.0)
        self._human_types_now()
        self.gov.observe_user_input()
        self.assertTrue(self.gov.user_is_active())

        # Just short of the quiet period: still the user's machine.
        self.clock.advance(gov_module.USER_ACTIVE_QUIET_SECONDS - 1.0)
        self._nobody_has_touched_it_for(gov_module.USER_ACTIVE_QUIET_SECONDS - 1.0)
        self.gov.observe_user_input()
        self.assertTrue(self.gov.user_is_active())

        # Past it: the engine may carry on.
        self.clock.advance(2.0)
        self._nobody_has_touched_it_for(gov_module.USER_ACTIVE_QUIET_SECONDS + 1.0)
        self.gov.observe_user_input()
        self.assertFalse(self.gov.user_is_active())

    def test_a_platform_that_cannot_answer_never_pauses_the_engine(self):
        """None means "this box has no idle timer" — Linux without XScreenSaver
        and without xprintidle. Guessing there would pause at random."""
        gov = governor_at(Clock(), seed=29, read_idle=lambda: None)
        self.assertFalse(gov.observe_user_input())
        self.assertFalse(gov.user_is_active())
        self.assertFalse(gov.snapshot()["userInputVisible"])


class CalibrationTest(unittest.TestCase):
    """The headline promise, measured — not asserted about one lucky run.

    This drives the same simulator `make test` runs, which drives the real
    governor and the real loop constants. If someone retunes a profile or the
    target bag without re-measuring, this is what goes red.
    """

    # WHY 200: the number the requirement asks for, and enough that the spread
    # of the run average is a distribution rather than a coin toss. Each run is
    # three simulated hours and the whole set takes about a second.
    TRIALS = 200
    SEED = 20260929

    @classmethod
    def setUpClass(cls):
        cls.averages, cls.worst_windows, cls.worst_rolling = [], [], []
        for index in range(cls.TRIALS):
            rng = random.Random(cls.SEED + index)
            percentages, blocks = test_metrics.simulate_run(rng)
            cls.averages.append(statistics.fmean(percentages))
            cls.worst_windows.append(max(percentages))
            cls.worst_rolling.append(test_metrics.rolling_max_percent(blocks))

    def test_no_simulated_window_ever_passed_the_ceiling(self):
        self.assertLessEqual(max(self.worst_windows), gov_module.CEILING_PERCENT)

    def test_no_simulated_rolling_window_ever_passed_the_ceiling(self):
        self.assertLessEqual(max(self.worst_rolling), gov_module.CEILING_PERCENT)

    def test_every_three_hour_run_averaged_inside_the_band(self):
        outside = [a for a in self.averages
                   if not gov_module.BAND_LOW_PERCENT <= a <= gov_module.BAND_HIGH_PERCENT]
        self.assertEqual(
            outside, [],
            f"{len(outside)} of {self.TRIALS} runs fell outside "
            f"{gov_module.BAND_LOW_PERCENT}-{gov_module.BAND_HIGH_PERCENT}%: "
            f"{[round(a, 2) for a in outside[:5]]}")

    def test_the_average_sits_near_the_middle_of_the_band_not_against_an_edge(self):
        """Tuned to the middle on purpose: a calibration that only just fits
        has no room for the variance a real run will have."""
        overall = statistics.fmean(self.averages)
        self.assertGreater(overall, 41.5, f"overall average {overall:.2f}% is drifting low")
        self.assertLess(overall, 44.5, f"overall average {overall:.2f}% is drifting high")
        # And the worst run of the set still has room on both sides.
        self.assertGreater(min(self.averages), gov_module.BAND_LOW_PERCENT + 0.5)
        self.assertLess(max(self.averages), gov_module.BAND_HIGH_PERCENT - 0.5)

    def test_the_windows_within_a_run_are_not_all_the_same(self):
        rng = random.Random(self.SEED)
        percentages, _ = test_metrics.simulate_run(rng)
        self.assertGreater(len(set(round(p) for p in percentages)), 5,
                           "every window in the run scored the same")
        self.assertGreater(max(percentages) - min(percentages), 10.0,
                           "the windows barely differ from one another")


class SnapshotTest(unittest.TestCase):
    def test_the_snapshot_carries_what_the_console_prints(self):
        clock = Clock()
        gov = governor_at(clock, seed=31)
        gov.claim()
        snap = gov.snapshot(holding=True)
        for field in ("windowUsedBlocks", "windowTargetBlocks", "windowBlocksElapsed",
                      "windowPercent", "averagePercent", "windowsCompleted",
                      "ceilingBlocks", "ceilingPercent", "bandPercent", "holding",
                      "blocksPerWindow", "rollingUsedBlocks", "userInputVisible"):
            self.assertIn(field, snap)
        self.assertEqual(snap["windowUsedBlocks"], 1)
        self.assertTrue(snap["holding"])
        self.assertEqual(snap["blocksPerWindow"], PER_WINDOW)

    def test_windows_slept_straight_through_still_count_towards_the_average(self):
        """A window the person owned end to end scored whatever they scored, not
        nothing — skipping it would make the running average read high."""
        clock = Clock()
        gov = governor_at(clock, seed=37)
        gov.claim()
        clock.advance(gov_module.WINDOW_SECONDS * 4)
        gov.claim()
        self.assertEqual(len(gov.history), 4)
        self.assertLess(gov.average_percent(), 5.0)


if __name__ == "__main__":
    unittest.main()
