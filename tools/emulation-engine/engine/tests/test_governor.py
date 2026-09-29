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


# WHY one shared sweep rather than one per test class: a three-hour run is the
# expensive thing here and the calibration, the variance floors and the quiet
# windows are all different views of the same runs. Measuring once and reading
# it three times keeps `make test` under ten seconds; measuring three times
# would triple it for no extra evidence.
_MEASUREMENT = {}


def measurement(trials, seed):
    key = (trials, seed)
    if key not in _MEASUREMENT:
        _MEASUREMENT[key] = test_metrics.measure(trials, seed)
    return _MEASUREMENT[key]


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

    # WHY 600 here and 2,000 in the simulator: this set runs on every `make
    # test` and has to stay quick, while the simulator is the instrument and
    # can afford to be thorough. Six hundred three-hour runs is 10,800 windows
    # and about six seconds — enough for the run-average distribution and for
    # the variance floors, and every literal asserted below was chosen from the
    # 2,000-run measurement with room to spare, so the smaller set here cannot
    # pass something the full one would fail.
    TRIALS = 600
    SEED = 20260929

    @classmethod
    def setUpClass(cls):
        cls.data = measurement(cls.TRIALS, cls.SEED)
        cls.averages = cls.data["averages"]
        cls.worst_windows = cls.data["worsts"]
        cls.worst_rolling = cls.data["rollings"]

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
        percentages, _, _ = test_metrics.simulate_run(rng)
        self.assertGreater(len(set(round(p) for p in percentages)), 5,
                           "every window in the run scored the same")
        self.assertGreater(max(percentages) - min(percentages), 10.0,
                           "the windows barely differ from one another")


class DealOrderTest(unittest.TestCase):
    """The spacing rules, checked on the budgets the bag actually deals.

    These are the structural half of the variance promise: they hold by
    construction on every draw, where the measured half (VariationTest) holds
    on the windows those draws produce. Both matter — the rules could be
    perfect and the loop still flatten them, or the measurement could pass on
    luck.
    """

    # WHY 2,000 draws: a little over 200 bags, so the rules are checked across
    # hundreds of seams between one bag and the next, which is where a rule
    # that only looked inside a bag would fail.
    DRAWS = 2000

    def draws(self, seed=3):
        gov = governor_at(Clock(), seed=seed)
        # begin() already dealt window 0's budget; include it.
        return [gov._target_blocks] + [gov._target_blocks_for(w)
                                       for w in range(1, self.DRAWS)]

    def test_no_two_consecutive_budgets_are_near_identical(self):
        """The requirement's own example: a deal of 27, 28, 27 would recreate
        the flagged pattern in miniature."""
        budgets = self.draws()
        closest = min(abs(a - b) for a, b in zip(budgets, budgets[1:]))
        self.assertGreaterEqual(
            closest, gov_module.MIN_ADJACENT_DELTA_BLOCKS,
            f"two consecutive windows were dealt budgets {closest} blocks apart")

    def test_no_three_consecutive_budgets_huddle_together(self):
        budgets = self.draws(seed=5)
        tightest = min(max(triple) - min(triple)
                       for triple in zip(budgets, budgets[1:], budgets[2:]))
        self.assertGreaterEqual(
            tightest, gov_module.MIN_TRIPLE_SPREAD_BLOCKS,
            f"three consecutive windows spanned only {tightest} blocks")

    def test_a_quiet_window_never_more_than_the_spacing_away(self):
        """This is what makes the 90-minute promise a guarantee rather than a
        measurement: if a quiet budget appears in every DEAL_SPACING draws,
        then every stretch of nine contains one."""
        budgets = self.draws(seed=7)
        span = gov_module.DEAL_SPACING
        for start in range(len(budgets) - span + 1):
            stretch = budgets[start:start + span]
            self.assertTrue(
                any(value <= gov_module.QUIET_MAX_BLOCKS for value in stretch),
                f"{span} windows in a row with no quiet one: {stretch}")

    def test_a_busy_window_never_more_than_the_spacing_away(self):
        budgets = self.draws(seed=11)
        span = gov_module.DEAL_SPACING
        for start in range(len(budgets) - span + 1):
            stretch = budgets[start:start + span]
            self.assertTrue(
                any(value >= gov_module.BUSY_MIN_BLOCKS for value in stretch),
                f"{span} windows in a row with no busy one: {stretch}")

    def test_the_rules_are_satisfiable_so_no_bag_falls_back(self):
        """A fallback deals the bag unconstrained, which would void every
        guarantee above without any other symptom. It has to be zero, not
        rare."""
        gov = governor_at(Clock(), seed=13)
        for window in range(1, self.DRAWS):
            gov._target_blocks_for(window)
        self.assertEqual(gov.bag_fallbacks, 0,
                         "the spacing rules could not be satisfied by a shuffle")

    def test_the_quiet_and_busy_groups_cannot_overlap_once_jittered(self):
        """A quiet budget that jittered above a busy one would let the rules
        above be satisfied by two windows that score the same."""
        highest_quiet = max(gov_module.WINDOW_TARGET_QUIET) + gov_module.WINDOW_TARGET_JITTER
        lowest_busy = min(gov_module.WINDOW_TARGET_BUSY) - gov_module.WINDOW_TARGET_JITTER
        self.assertLess(highest_quiet, lowest_busy)
        self.assertEqual(gov_module.QUIET_MAX_BLOCKS, highest_quiet)
        self.assertEqual(gov_module.BUSY_MIN_BLOCKS, lowest_busy)

    def test_the_bag_still_averages_what_it_was_designed_to(self):
        """The fixed sum is the whole reason wide windows do not cost a stable
        average. Order and jitter are both symmetric about the value, so a long
        run of draws must come back to the bag's own mean."""
        budgets = self.draws(seed=17)
        self.assertAlmostEqual(statistics.fmean(budgets),
                               statistics.fmean(gov_module.WINDOW_TARGET_BAG),
                               delta=0.2)

    def test_every_value_in_the_bag_actually_gets_dealt(self):
        """A constraint strict enough to make one value undealable would shift
        the average without any test noticing."""
        budgets = self.draws(seed=19)
        for value in gov_module.WINDOW_TARGET_BAG:
            self.assertTrue(
                any(abs(b - value) <= gov_module.WINDOW_TARGET_JITTER for b in budgets),
                f"the bag never dealt anything near {value}")


class WindowShapeTest(unittest.TestCase):
    """The pacing shape — see SHAPE_MAX_SKEW."""

    def test_a_quiet_window_may_be_front_or_back_loaded(self):
        gov = governor_at(Clock(), seed=23)
        low = min(gov_module.WINDOW_TARGET_QUIET)
        shapes = [gov._shape_exponent_for(low) for _ in range(400)]
        self.assertLess(min(shapes), 0.85, "no window was ever front-loaded")
        self.assertGreater(max(shapes), 1.15, "no window was ever back-loaded")

    def test_a_window_near_the_ceiling_is_paced_almost_flat(self):
        """Back-loading a busy window spends into the half that abuts the next
        one, and the rolling ceiling would refuse those blocks — costing the
        average and buying nothing."""
        gov = governor_at(Clock(), seed=29)
        shapes = [gov._shape_exponent_for(gov_module.CEILING_BLOCKS - 1)
                  for _ in range(400)]
        self.assertLess(max(abs(s - 1.0) for s in shapes), 0.05)

    def test_the_first_block_of_a_window_is_always_claimable(self):
        """A back-loaded window whose allowance rounded to zero early on would
        sit dead for minutes and then have to catch up — which is the shape the
        pacing rule exists to prevent."""
        for exponent in (0.6, 1.0, 1.4):
            clock = Clock()
            gov = governor_at(clock, seed=31)
            gov._target_blocks = 4
            gov._shape_exponent = exponent
            gov._used = set()
            self.assertTrue(gov.claim(),
                            f"the window could not start with a shape of {exponent}")

    def test_shaping_never_lets_a_window_pass_the_ceiling(self):
        """The adversarial case again, with the most back-loaded shape the
        governor can draw forced on every window."""
        clock = Clock()
        gov = governor_at(clock, seed=37)
        worst = 0
        marked = []
        for block in range(120 * PER_WINDOW):
            gov._shape_exponent = 1.0 + gov_module.SHAPE_MAX_SKEW
            if gov.claim():
                marked.append(block)
            worst = max(worst, len(gov._used))
            clock.advance(BLOCK)
        self.assertLessEqual(worst, gov_module.CEILING_BLOCKS)
        self.assertLessEqual(test_metrics.rolling_max_percent(set(marked)),
                             gov_module.CEILING_PERCENT)


class AffordablePauseTest(unittest.TestCase):
    """The long away-from-the-keyboard pause now asks the window first."""

    def setUp(self):
        self.clock = Clock()
        self.gov = governor_at(self.clock, seed=41)

    def test_a_quiet_window_can_afford_the_longest_pause(self):
        self.gov._target_blocks = min(gov_module.WINDOW_TARGET_QUIET)
        self.gov._used = set()
        self.assertTrue(self.gov.pause_is_affordable(max(engine.THINKING_PAUSE)))

    def test_a_busy_window_that_has_barely_started_cannot(self):
        """A budget of 36 in a 60-block window has 24 blocks of slack in total;
        a 70-second pause plus the blocks already gone eats it."""
        self.gov._target_blocks = max(gov_module.WINDOW_TARGET_BUSY)
        self.gov._used = set()
        # Two thirds of the way through, with two thirds of the budget still to
        # spend: there is no room at all.
        self.clock.advance(BLOCK * 40)
        self.gov._roll_to(self.gov._block_at(self.clock.now))
        self.gov._target_blocks = max(gov_module.WINDOW_TARGET_BUSY)
        self.gov._used = set()
        self.assertFalse(self.gov.pause_is_affordable(max(engine.THINKING_PAUSE)))

    def test_a_window_whose_budget_is_already_spent_can_always_pause(self):
        self.gov._target_blocks = 3
        self.gov._used = {0, 1, 2}
        self.assertTrue(self.gov.pause_is_affordable(max(engine.THINKING_PAUSE)))

    def test_choose_mode_swaps_thinking_out_when_it_cannot_be_afforded(self):
        """The loop must still do something on that cycle, not stall."""
        class NoPause:
            def pause_is_affordable(self, seconds):
                return False

        rng = random.Random(43)
        drawn = [engine.choose_mode(rng, NoPause())[0] for _ in range(400)]
        self.assertNotIn("THINKING", drawn)
        # And what it drew instead is a real working profile, not a placeholder.
        self.assertTrue(set(drawn) <= set(engine.MODE_PROFILES))

    def test_choose_mode_keeps_thinking_when_the_window_can_spare_it(self):
        class AllPause:
            def pause_is_affordable(self, seconds):
                return True

        rng = random.Random(47)
        drawn = [engine.choose_mode(rng, AllPause())[0] for _ in range(400)]
        self.assertIn("THINKING", drawn)

    def test_a_pool_with_nothing_else_in_it_still_returns_thinking(self):
        """The engine tests pin MODE_POOL to THINKING alone; there would be
        nothing to fall back to."""
        class NoPause:
            def pause_is_affordable(self, seconds):
                return False

        original = engine.MODE_POOL
        engine.MODE_POOL = ["THINKING"]
        try:
            mode, macro_pause = engine.choose_mode(random.Random(53), NoPause())
        finally:
            engine.MODE_POOL = original
        self.assertEqual(mode, "THINKING")
        self.assertIsNotNone(macro_pause)

    def test_the_pause_it_offers_is_the_pause_it_checked(self):
        """The simulator advances its clock by the returned pause. If
        choose_mode checked one length and reported another, every measurement
        would be against a loop that does not exist."""
        seen = []

        class Recording:
            def pause_is_affordable(self, seconds):
                seen.append(seconds)
                return True

        original = engine.MODE_POOL
        engine.MODE_POOL = ["THINKING"]
        try:
            _, macro_pause = engine.choose_mode(random.Random(59), Recording())
        finally:
            engine.MODE_POOL = original
        self.assertEqual(seen, [macro_pause])
        low, high = engine.THINKING_PAUSE
        self.assertTrue(low <= macro_pause <= high)


class VariationTest(unittest.TestCase):
    """The new headline promise: 90 minutes of this must not look flat.

    A real tracker flagged a real person for "activity rate varied 1-4 % for
    over 90 minutes" at an overall rate of 45 %. The overall rate was never the
    problem, so neither the band test nor the ceiling test above would have
    caught it. This is the class that would.
    """

    TRIALS = CalibrationTest.TRIALS
    SEED = CalibrationTest.SEED

    @classmethod
    def setUpClass(cls):
        cls.data = measurement(cls.TRIALS, cls.SEED)

    def test_every_ninety_minutes_spans_at_least_the_required_range(self):
        worst = min(self.data["ranges"])
        self.assertGreaterEqual(
            worst, gov_module.VARIANCE_MIN_RANGE_PERCENT,
            f"the tightest {gov_module.VARIANCE_WINDOW_COUNT}-window stretch in "
            f"{self.TRIALS} runs varied by only {worst:.1f} points")

    def test_every_ninety_minutes_has_at_least_the_required_spread(self):
        """Range alone could be met by eight identical windows and one outlier.
        Standard deviation says the whole stretch moved."""
        worst = min(self.data["stdevs"])
        self.assertGreaterEqual(worst, gov_module.VARIANCE_MIN_STDEV_PERCENT,
                                f"the flattest stretch had a spread of {worst:.1f} points")

    def test_the_typical_stretch_is_far_past_the_floor(self):
        """A promise that only just holds has nothing left for the variance a
        real run will have."""
        median = statistics.median(self.data["ranges"])
        self.assertGreater(median, gov_module.VARIANCE_MIN_RANGE_PERCENT * 1.5,
                           f"the median stretch varied by only {median:.1f} points")

    def test_genuinely_quiet_windows_are_a_normal_part_of_the_pattern(self):
        windows = self.data["windows"]
        quiet = sum(1 for p in windows if p < gov_module.QUIET_WINDOW_PERCENT)
        rate = quiet / len(windows)
        self.assertGreaterEqual(
            rate, gov_module.QUIET_WINDOW_MIN_RATE,
            f"only {rate * 100:.1f}% of windows were under "
            f"{gov_module.QUIET_WINDOW_PERCENT:g}%")

    def test_the_really_deep_quiet_windows_happen_too(self):
        """Not just "under 25 %" — the user asked to see windows around 10 %,
        and a bag that only ever produced 24 % would pass the test above."""
        windows = self.data["windows"]
        deep = sum(1 for p in windows if p <= 13.0)
        self.assertGreater(deep / len(windows), 0.03,
                           "windows near 10% essentially never happened")
        self.assertLess(min(windows), 10.0)

    def test_busy_windows_happen_too(self):
        """The other end: without them the average could only be reached by
        never being quiet."""
        windows = self.data["windows"]
        busy = sum(1 for p in windows if p >= 55.0)
        self.assertGreater(busy / len(windows), 0.20)

    def test_the_whole_range_is_populated_not_just_the_two_ends(self):
        """Bimodal is its own signature. Every ten-point bucket from the
        quietest window to the busiest must hold real windows."""
        windows = self.data["windows"]
        buckets = test_metrics.bucket_histogram(windows)
        top = int(max(windows) // 10) * 10
        for start in range(0, top + 1, 10):
            share = buckets.get(start, 0) / len(windows)
            self.assertGreater(share, 0.01,
                               f"the {start}-{start + 9}% band held only "
                               f"{buckets.get(start, 0)} of {len(windows)} windows")

    def test_neighbouring_windows_hardly_ever_score_alike(self):
        """The deal rule guarantees the BUDGETS differ; a window can still
        score a block or two under its budget and land level with its
        neighbour. That has to stay rare."""
        adjacent = self.data["adjacent"]
        under = test_metrics.below_adjacent_floor(adjacent)
        self.assertLessEqual(
            under / len(adjacent), gov_module.ADJACENT_BELOW_FLOOR_MAX_RATE,
            f"{under} of {len(adjacent)} neighbouring pairs scored closer than "
            f"{gov_module.MIN_ADJACENT_DELTA_PERCENT:.1f} points apart")
        self.assertGreater(statistics.median(adjacent), 15.0,
                           "a typical pair of neighbours barely differed")

    def test_the_pacing_shape_is_not_the_same_every_window(self):
        """"Every window is paced identically" is the same kind of constant as
        "every window scores the same"."""
        shapes = sorted(self.data["shapes"])
        self.assertLess(shapes[len(shapes) // 10], 0.42, "no window front-loaded")
        self.assertGreater(shapes[len(shapes) * 9 // 10], 0.53, "no window back-loaded")

    def test_no_bag_in_the_whole_sweep_fell_back(self):
        self.assertEqual(self.data["fallbacks"], 0)


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


class RequirementTestCase(unittest.TestCase):
    """The numbers the user actually asked for, written down as literals.

    WHY this exists on top of the behavioural tests: every other test compares
    the governor against its own constants, so raising CEILING_PERCENT to 100
    left all 114 of them green while the rolling activity climbed to 66.7 %.
    Self-consistent is not the same as correct. These assertions are the
    contract; a change that means to move them has to say so here.
    """

    # From the request, 2026-09-29: "3hrs mein har 10 min ki window mein
    # activity random ho max 65 tk takki overall average 38-47 hi rhe".
    REQUIRED_CEILING_PERCENT = 65.0
    REQUIRED_BAND = (38.0, 47.0)

    # Added 2026-09-29 after a real tracker flagged a real person with
    # "Unusually consistent activity — Activity rate varied 1-4 % for over 90
    # minutes" at an overall rate of 45 %. The level was never the complaint;
    # the sameness was. 90 minutes is nine of our ten-minute windows.
    REQUIRED_VARIANCE_WINDOWS = 9
    REQUIRED_MIN_RANGE_PERCENT = 25.0
    REQUIRED_MIN_STDEV_PERCENT = 8.0
    # "the user explicitly wants to see windows around 10, 13, 17, 22, 28
    # percent", and wants them to be normal rather than accidental.
    REQUIRED_QUIET_LEVELS_PERCENT = (10.0, 13.0, 17.0, 22.0, 28.0)
    REQUIRED_QUIET_WINDOW_PERCENT = 25.0
    # WHY 1.0 point of tolerance on the levels: a window is a whole number of
    # sixty blocks, so 10 % is reachable exactly but 13 % is 7.8 blocks and the
    # nearest reachable value is 13.33 %. Anything under a point is "around".
    QUIET_LEVEL_TOLERANCE_PERCENT = 1.0

    # WHY these two are pinned here as well as measured: the measured tests run
    # the simulator, and a measurement can only ever say what happened. These
    # say what was asked for, so a future retune that quietly lowers the bar
    # has to come here and change the literal in front of a reviewer.
    def test_the_variance_floors_are_the_ones_that_were_asked_for(self):
        self.assertEqual(gov_module.VARIANCE_WINDOW_COUNT,
                         self.REQUIRED_VARIANCE_WINDOWS)
        self.assertGreaterEqual(gov_module.VARIANCE_MIN_RANGE_PERCENT,
                                self.REQUIRED_MIN_RANGE_PERCENT)
        self.assertGreaterEqual(gov_module.VARIANCE_MIN_STDEV_PERCENT,
                                self.REQUIRED_MIN_STDEV_PERCENT)

    def test_ninety_minutes_is_what_nine_windows_actually_covers(self):
        """The floors above are stated over 90 minutes; if a window stopped
        being ten minutes the count would have to change with it."""
        minutes = gov_module.VARIANCE_WINDOW_COUNT * gov_module.WINDOW_SECONDS / 60.0
        self.assertEqual(minutes, 90.0)

    def test_the_quiet_line_is_where_it_was_asked_to_be(self):
        self.assertEqual(gov_module.QUIET_WINDOW_PERCENT,
                         self.REQUIRED_QUIET_WINDOW_PERCENT)

    def test_every_quiet_level_the_user_named_is_reachable(self):
        """Not "the bag can go low" — these five specific levels."""
        reachable = sorted(
            {max(1, min(gov_module.CEILING_BLOCKS, value + nudge))
             / gov_module.BLOCKS_PER_WINDOW * 100.0
             for value in gov_module.WINDOW_TARGET_BAG
             for nudge in range(-gov_module.WINDOW_TARGET_JITTER,
                                gov_module.WINDOW_TARGET_JITTER + 1)})
        for level in self.REQUIRED_QUIET_LEVELS_PERCENT:
            nearest = min(reachable, key=lambda p: abs(p - level))
            self.assertLessEqual(
                abs(nearest - level), self.QUIET_LEVEL_TOLERANCE_PERCENT,
                f"no window budget lands near {level:g}%; nearest is {nearest:.1f}%")

    def test_a_genuinely_quiet_window_is_in_the_bag_at_all(self):
        """The failure this pins is the one that prompted the whole change: the
        bag before it had a floor of 35 % and never produced a quiet window."""
        lowest = ((min(gov_module.WINDOW_TARGET_BAG) - gov_module.WINDOW_TARGET_JITTER)
                  / gov_module.BLOCKS_PER_WINDOW * 100.0)
        self.assertLessEqual(lowest, 12.0,
                             f"the quietest window the bag can produce is {lowest:.1f}%")

    def test_the_bag_can_span_the_required_range_on_its_own(self):
        """Before any measurement: the drawn budgets alone have to be capable
        of the required range, or no amount of shuffling could produce it."""
        low, high = gov_module.WINDOW_TARGET_BLOCKS
        span = (high - low) / gov_module.BLOCKS_PER_WINDOW * 100.0
        self.assertGreaterEqual(span, self.REQUIRED_MIN_RANGE_PERCENT)

    def test_the_deal_rules_force_a_quiet_and_a_busy_window_close_together(self):
        """The structural half of the 90-minute promise. If a quiet budget and
        a busy one both appear in every DEAL_SPACING draws, and nine is more
        than twice DEAL_SPACING, then every 90 minutes holds both."""
        self.assertLessEqual(gov_module.DEAL_SPACING * 2,
                             self.REQUIRED_VARIANCE_WINDOWS)
        forced = ((gov_module.BUSY_MIN_BLOCKS - gov_module.QUIET_MAX_BLOCKS)
                  / gov_module.BLOCKS_PER_WINDOW * 100.0)
        # WHY this is allowed to be less than the required range while the
        # measured test demands the full 25: this is the worst case the rules
        # ALONE force — a quiet window at the very top of the quiet group
        # beside a busy one at the very bottom of the busy group. In practice
        # the quiet windows land well below their own ceiling, which is why the
        # measured worst over 2,000 runs is 28.3 points against the 20 forced
        # here. Both numbers are worth having: this one cannot be luck.
        self.assertGreaterEqual(forced, 15.0,
                                f"the deal rules only force {forced:.1f} points apart")

    def test_the_ceiling_is_the_one_that_was_asked_for(self):
        self.assertEqual(gov_module.CEILING_PERCENT, self.REQUIRED_CEILING_PERCENT)
        # The block count it derives must not round the limit upwards.
        self.assertLessEqual(
            gov_module.CEILING_BLOCKS / gov_module.BLOCKS_PER_WINDOW * 100.0,
            self.REQUIRED_CEILING_PERCENT,
        )

    def test_the_band_is_the_one_that_was_asked_for(self):
        self.assertEqual(
            (gov_module.BAND_LOW_PERCENT, gov_module.BAND_HIGH_PERCENT),
            self.REQUIRED_BAND,
        )

    def test_no_window_budget_can_reach_the_required_ceiling(self):
        """However the distribution is retuned, no draw may reach 65 %."""
        highest = max(gov_module.WINDOW_TARGET_BLOCKS)
        self.assertLess(
            highest / gov_module.BLOCKS_PER_WINDOW * 100.0,
            self.REQUIRED_CEILING_PERCENT,
            "the largest window budget reaches the ceiling",
        )

    def test_the_bag_averages_inside_the_required_band(self):
        """The bag is deliberately a few points high (the loop never spends it
        all), but it must not be so high that the band is unreachable."""
        mean_blocks = sum(gov_module.WINDOW_TARGET_BAG) / len(gov_module.WINDOW_TARGET_BAG)
        mean_percent = mean_blocks / gov_module.BLOCKS_PER_WINDOW * 100.0
        self.assertLess(mean_percent, self.REQUIRED_CEILING_PERCENT)
        # Measured: the loop lands about 3 points under the bag's mean.
        self.assertGreater(mean_percent, self.REQUIRED_BAND[0])
