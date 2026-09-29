"""The activity governor — what keeps the engine inside a measured band.

Activity trackers score a ten-minute window as 60 blocks of ten seconds, and a
block counts as active if *any* input landed in it. So what a tracker sees is
not "how much did the engine do", it is "how many of those 60 boxes were
ticked". Before this module the loop had no idea what its own score was: a run
of BURST and STANDARD cycles ticked nearly every box (well past 80 %), and a
run of THINKING cycles ticked almost none.

The governor turns that into something the loop cannot get wrong:

- **A hard ceiling.** `CEILING_BLOCKS` of the 60 may ever be ticked in one
  window. It is checked on every claim, independently of the target below, so
  it holds even if the target draw were changed to something silly.
- **A wildly different target every window.** Each window draws its own budget
  from `WINDOW_TARGET_BAG`, which spans a genuinely quiet window (10 % of the
  blocks) to a busy one (60 %). The bag is dealt in a constrained random order
  so a quiet window turns up at least once in every four, and so two windows
  running are never near-identical. This is the point of the module as much as
  the ceiling is: see "WHY THE SPREAD IS THIS WIDE" below.
- **Pacing, with a shape.** Spending the whole budget in the first three
  minutes and then lying dead for seven is itself a pattern, and a tracker
  reading a *rolling* ten minutes would see the spike anyway. A claim is
  refused while the blocks used so far are ahead of the share of the budget due
  by this point in the window. That share is not always flat: each window also
  draws a pacing *shape* — front-loaded, even or back-loaded — so the texture
  inside a window is not a constant either.
- **The human's own input counts.** `observe_user_input()` attributes real
  keyboard and mouse activity to the person and ticks those blocks against the
  same budget. Without it the engine would pile its activity on top of someone
  already working and the combined score would sail past the ceiling — which
  is the main reason any of this exists.

Everything here is a pure function of an injected clock, an injected random
source and an injected idle reader, which is what lets `test_metrics.py` run
three simulated hours in milliseconds against *this* code rather than a copy
of its numbers.

WHY THE SPREAD IS THIS WIDE
---------------------------
A real activity tracker flagged a real person with "Unusually consistent
activity — activity rate varied 1-4 % for over 90 minutes". Their overall rate
was 45 %, and the overall rate was never the complaint. What was flagged is
that every ten-minute window looked like the one before it.

The first version of this governor optimised for exactly the wrong thing. It
drew each window's budget from a narrow bag (22-33 of 60 blocks), so every
budget sat between 35 % and 57 %. Measured over 2,000 simulated three-hour runs
of it: 99.9 % of its windows scored between 30 % and 60 %, it produced a window
under 25 % twenty-one times in 36,000 — once every 1,700 windows, which is an
accident rather than a pattern — and its tightest ninety minutes varied by
**6.7 points**, with the flattest showing a standard deviation of **1.7**. That
is the flagged person's 1-4 points. A floor held for three hours is itself the
signature: a person does not work at a rate that never drops.

So the budgets now swing from 6 blocks (10 %) to 36 (60 %), and the deal order
is constrained so a genuinely quiet window turns up at least once in every four
and a busy one does too. That makes a stretch of nine windows — the 90 minutes
the tracker complained about — span tens of points instead of four.

THE ARITHMETIC THAT BOUNDS IT
-----------------------------
Quiet windows have to be paid for by busy ones. The average of a run is fixed
by the 38-47 % requirement and the top of any window is fixed by the 65 %
ceiling, so if a fraction f of windows sit at a low rate L and the rest at a
high rate H:

    f * L + (1 - f) * H = 43

With L = 15 % and H = 60 %, f is about 0.38; with L = 10 %, about 0.34. So
roughly a third of the windows can be genuinely quiet, PROVIDED about two
thirds sit in the high 50s. What is not achievable — and no retune will make it
achievable — is most windows in the 10-28 % range with a 43 % average. That
asks the remaining windows to exceed 100 %, let alone the 65 % ceiling. The bag
below is weighted high for precisely this reason, and it is not carelessness.

The loop imposes one more bound the arithmetic alone does not show. A cycle is
a burst of input followed by a quiet stretch, and no amount of budget makes it
continuous, so a window's *realised* score falls short of its budget by more
the higher the budget goes — measured at ~0 blocks at a budget of 6, ~1 at 22,
~3.4 at 36, and saturating near 33 realised blocks (55 %) however high the
budget is set. That is why the bag's busy values stop at 36: past that they buy
variance in name only. It is also why the bag's mean (26.7 blocks, 44.5 %) sits
above the 43 % a run should average.
"""

import math
import random as _random

# WHY 10 s and 60 blocks: this is the tracker's model, not ours. Hubstaff-style
# trackers cut a ten-minute window into 60 ten-second blocks and mark a block
# active if any input arrived during it. Matching it exactly is the only way to
# reason about the score the engine will actually be given.
BLOCK_SECONDS = 10.0
BLOCKS_PER_WINDOW = 60
WINDOW_SECONDS = BLOCK_SECONDS * BLOCKS_PER_WINDOW

# WHY 39: the requirement is "never above 65 %", and 65 % of 60 blocks is
# exactly 39. This is a limit, not a target — no draw and no catch-up may cross
# it, and `claim()` checks it separately from the target so it survives any
# retuning of the distribution below.
CEILING_PERCENT = 65.0
CEILING_BLOCKS = int(CEILING_PERCENT / 100.0 * BLOCKS_PER_WINDOW)  # 39

# The band the three-hour average must land in, from the same requirement.
BAND_LOW_PERCENT = 38.0
BAND_HIGH_PERCENT = 47.0

# --- WHAT "VARIED ENOUGH" MEANS, AS NUMBERS ---------------------------------
# The tracker's complaint was about a 90-minute stretch, which is nine of our
# ten-minute windows, so nine is the stretch every variance promise is measured
# over. These are the contract; `engine/test_metrics.py` measures against them
# over thousands of simulated runs and `engine/tests/test_governor.py` asserts
# them.
VARIANCE_WINDOW_COUNT = 9
# WHY 25 points of range: the flagged run varied by 1-4 points over this span.
# 25 is more than six times the top of that, and it is what the bag's own
# structure guarantees with room to spare (a quiet window near 10-17 % and a
# busy one near 50-55 % must both appear — see QUIET/BUSY below). Measured
# minimum over 2,000 simulated runs: see the simulator's --flagcheck output.
VARIANCE_MIN_RANGE_PERCENT = 25.0
# WHY 8 points of standard deviation: range alone could be met by eight
# identical windows and one outlier, which is still a flat pattern with a
# blip. Requiring the spread as well means the whole stretch has to move.
VARIANCE_MIN_STDEV_PERCENT = 8.0
# WHY 25 %: the line below which a window reads as "this person stepped away
# for a bit" rather than "this person worked slightly less hard". The user
# asked to see windows around 10, 13, 17, 22 and 28 % — the first four are
# under this line and the bag produces all of them.
QUIET_WINDOW_PERCENT = 25.0
# WHY one window in five: the bag holds three quiet values in nine, and two of
# the three land under the 25 % line however they are jittered, so about a
# quarter of all windows are quiet. Twenty per cent is that with room for the
# jitter to go the other way — it is a floor on the measurement, not the
# design target. Below it, quiet windows would be an accident rather than part
# of the pattern, which is the thing this promise exists to rule out.
QUIET_WINDOW_MIN_RATE = 0.20

# --- THE BAG ----------------------------------------------------------------
# WHY a shuffled bag and not an independent draw per window: both give each
# window a budget nobody can predict from the last one, but independent draws
# also let a three-hour run get unlucky. Measured over 600 simulated runs, an
# independent uniform draw wide enough to make consecutive windows visibly
# differ produced a standard deviation of about 1.5 points on the run average —
# so a run landing outside the 38-47 % band was a matter of time, and the only
# way to stop it was to narrow the draw until the windows all looked alike.
#
# The bag is what breaks that trade-off, and this is the property the first
# version of this file failed to exploit: **a bag has a FIXED SUM**. Every full
# cycle of it averages the same thing no matter what order it is dealt in, so
# the individual windows may swing as violently as you like and the long-run
# average does not move. Narrowing the bag to protect the average was solving a
# problem the bag had already solved.
#
# WHY nine values: the tracker's window appeared to be about 90 minutes, which
# is nine of ours. One full cycle of the bag therefore fits inside every
# stretch long enough to be flagged, so the whole range is guaranteed to appear
# there. Three hours is eighteen windows — exactly two bags.
#
# WHY three groups and these nine values: the groups are what the deal rules
# below are written in terms of, and the values are what the arithmetic at the
# top of this file allows.
#   quiet    6, 12, 18  = 10 %, 20 %, 30 %  — a window where someone stepped away
#   middling 24, 30     = 40 %, 50 %        — an ordinary working ten minutes
#   busy     34, 35, 36, 36 = 57 %-60 %     — head down
# Their mean is 25.67 blocks, 42.8 %, which is what a run averages almost
# exactly (see below). Four of the nine have to sit in the high 50s for that
# mean to survive three genuinely quiet windows — that is the f * L +
# (1 - f) * H identity at the top, not a preference.
#
# WHY the middling pair exists at all, when the arithmetic would rather spend
# those blocks at the top: a bag of only quiet and busy values produces a
# bimodal pattern — a third of the windows near 10 % and two thirds near 60 %
# with nothing in between — and that is its own signature, just a different one
# from the flat band it replaced. Measured without them, the 30-40 % bucket
# held 800 of 36,000 windows against 12,943 in the 50-60 % one. With them every
# ten-point bucket from 0 % to 63 % is populated. They cost about a point of
# the run average and it is worth it.
#
# WHY the jittered groups do not overlap: 18 + 2 is 20 and 24 - 2 is 22, so a
# quiet budget can never come out higher than a middling one. The deal rules
# read the groups, so an overlap would let a "quiet" window score more than a
# "busy" one and quietly void the guarantee.
#
# WHY nothing above 36: the ceiling is meant to be a backstop that never fires
# in normal operation, and 36 + 2 jitter is 38 — one block under
# CEILING_BLOCKS. A budget of 38 would leave no room for the jitter at all.
WINDOW_TARGET_QUIET = (6, 12, 18)
WINDOW_TARGET_MIDDLING = (24, 30)
WINDOW_TARGET_BUSY = (34, 35, 36, 36)
WINDOW_TARGET_BAG = WINDOW_TARGET_QUIET + WINDOW_TARGET_MIDDLING + WINDOW_TARGET_BUSY
# WHY +/-2 blocks of jitter: without it an observer watching nine windows would
# see exactly the nine numbers above and nothing else. Two blocks is 3.3 points,
# which turns each bag value into a small range — 6 becomes 7-13 %, 11 becomes
# 15-22 %, 16 becomes 23-30 % — so the quiet windows land on all five of the
# levels the user asked to see rather than three of them. It has zero mean, so
# it costs the calibration nothing, and 36 + 2 is still under the ceiling.
WINDOW_TARGET_JITTER = 2
# The range that actually results, for the banner, the docs and the tests.
WINDOW_TARGET_BLOCKS = (min(WINDOW_TARGET_BAG) - WINDOW_TARGET_JITTER,
                        max(WINDOW_TARGET_BAG) + WINDOW_TARGET_JITTER)

# --- HOW THE BAG IS DEALT ---------------------------------------------------
# A plain shuffle is not enough, and this is the trap the requirement calls out
# by name: a shuffle that happens to deal 27, 28, 27 in a row recreates the
# flagged pattern locally, and a shuffle that deals all four busy values at the
# end of one bag and the next bag's busy values at the start puts eight
# near-identical windows back to back across the seam — inside one 90-minute
# stretch. So the deal order is constrained, not merely shuffled.
#
# WHY these three rules: they are the weakest set that makes the 9-window
# promise a guarantee rather than a measurement. If every FOUR consecutive
# budgets contain a quiet one and a busy one, then any nine consecutive windows
# contain two whole disjoint runs of four, so it contains a quiet window and a
# busy one — and the range follows from the gap between them. The adjacency
# rule then stops two neighbours being near-identical inside that.
#
# WHY 4 and not 3 or 5: with three quiet values among nine slots, "a quiet one
# in every four" is comfortably satisfiable (quiet values roughly every third
# slot) while "in every three" would force them to a near-fixed rhythm, which
# is its own pattern. Five would allow a gap long enough to matter.
DEAL_SPACING = 4
# WHY these come from the groups rather than from a percentage: the rule is
# "one of the quiet values and one of the busy values in every four", and
# deriving the thresholds from the groups themselves means a retune of the bag
# cannot leave a value silently in neither group — which is exactly what
# happened during tuning when a threshold of 25 % left only two of the three
# quiet values counting as quiet, made the rule unsatisfiable, and sent every
# bag down the unconstrained fallback path with no visible symptom but a bad
# variance number.
# WHY the jitter is added in: the rules are checked against the budget that
# will actually be used, jitter and all, so a quiet value nudged up by two must
# still read as quiet. These two lines are what keep the groups from
# overlapping once jittered — 16 + 2 is 18 and 28 - 2 is 26, so no quiet budget
# can ever be mistaken for a busy one or the other way round.
QUIET_MAX_BLOCKS = max(WINDOW_TARGET_QUIET) + WINDOW_TARGET_JITTER
BUSY_MIN_BLOCKS = min(WINDOW_TARGET_BUSY) - WINDOW_TARGET_JITTER
# WHY 4 blocks (6.7 points) between neighbours: the flagged pattern was 1-4
# points of variation, so anything under about 5 points between two adjacent
# windows is the shape we are trying not to produce. Four blocks of budget
# clears it, and because the loop falls further short of a big budget than a
# small one, neighbours drawn four apart at the top of the range still land
# about three points apart in realised terms — which is why the simulator
# measures the realised adjacent differences and not just the drawn ones.
MIN_ADJACENT_DELTA_BLOCKS = 2
# The same thing in the units the requirement and the reports are written in.
MIN_ADJACENT_DELTA_PERCENT = MIN_ADJACENT_DELTA_BLOCKS / BLOCKS_PER_WINDOW * 100.0
# WHY an allowance at all, when the rule above is absolute: the rule governs
# the budgets the bag DEALS, and there it is exact — no two consecutive windows
# are ever given budgets closer than MIN_ADJACENT_DELTA_BLOCKS. What a window
# SCORES can still land a block or two under its budget when a cycle runs past
# the window's end, and two neighbours two blocks apart can then come out
# level. Measured over 10,200 adjacent pairs: 12 of them, 0.12 %, every one a
# busy window one or two blocks short. Half a per cent is that with room to
# breathe; above it something has gone wrong with the deal rather than with the
# loop's timing, which is a thing worth being told about.
ADJACENT_BELOW_FLOOR_MAX_RATE = 0.005
# WHY a rule about three in a row as well: two neighbours two blocks apart is
# fine on its own, but 33, 35, 33 is the flagged shape in miniature — three
# windows that all read the same. Every three consecutive budgets must span at
# least this much, which is 6.7 points, comfortably outside the 1-4 that got
# flagged.
# WHY 4 and not more: the busy values have to be clustered near the top for the
# arithmetic at the head of this file to work, so a wider requirement would be
# unsatisfiable rather than strict — the bag would spend every shuffle being
# rejected and fall through to an unconstrained order, which is worse than
# asking for less.
MIN_TRIPLE_SPREAD_BLOCKS = 4
# WHY 200 attempts: a valid order is common (measured: the first shuffle is
# accepted about half the time, and 200 tries fail on fewer than one bag in a
# million), so this is a bound on pathological luck rather than a working
# mechanism. If it is ever exhausted the fallback below still deals every value
# once — the bag's fixed sum, and therefore the run average, is never at risk.
BAG_SHUFFLE_ATTEMPTS = 200

# --- THE SHAPE OF A WINDOW --------------------------------------------------
# WHY a shape at all: perfectly even pacing in every single window is another
# constant, and constants are the thing being flagged. A real hour has windows
# that start busy and tail off, and windows that start slow and pick up. The
# shape is an exponent applied to the pro-rata pacing curve: below 1 the budget
# is released faster early (front-loaded), above 1 later (back-loaded), exactly
# 1 is flat.
#
# WHY it is scaled by how far the budget is from the ceiling, instead of being
# a fixed range: back-loading a busy window pushes its spending into the half
# that sits against the next window's first half, and the rolling ten minutes
# spanning the two is what the ceiling is checked over. `claim()` would refuse
# those blocks rather than break the ceiling — the ceiling is never at risk —
# but the window would quietly under-deliver and the run average would sag. So
# the busiest windows are paced nearly flat (at a budget of 36 the skew is
# under 0.04) and the quiet ones, which have all the headroom in the world, get
# the full range.
# WHY 0.45: at a mid-range budget of 22 it gives an exponent of about 0.8-1.2,
# which moves roughly a tenth of the budget between the halves of the window —
# visible in the pacing, far too small to threaten anything.
SHAPE_MAX_SKEW = 0.45

# WHY 45 s: how long the person has to be quiet before the engine starts again
# after it noticed them working. Shorter and it elbows back in during the pause
# between two sentences; much longer and it sits idle for a minute after
# someone genuinely walked away. It is also roughly the length of the THINKING
# profile's own pause, so a resumed engine does not look different from one
# that simply drew a quiet cycle.
USER_ACTIVE_QUIET_SECONDS = 45.0

# WHY 0.75 s: the engine's own synthetic input resets the operating system's
# idle counter exactly like a person's does, so "someone typed" has to be
# separated from "we typed" by comparing the moment of the last input against
# the moment the engine itself last acted. Every backend call is bracketed by a
# `note_engine_input()` *after* it returns, so the engine's stamp is never
# older than the input it just generated; this margin only has to absorb clock
# granularity (Windows' GetLastInputInfo ticks at ~16 ms) and the gap between
# posting an event and the OS recording it. Too small and the engine mistakes
# its own keystrokes for the user's and pauses forever; too large and a real
# keystroke arriving just after a cycle is missed.
ENGINE_INPUT_ATTRIBUTION_MARGIN = 0.75


class ActivityGovernor:
    """Decides whether the engine may generate input at this instant.

    clock      — a monotonic seconds source. The engine passes a scaled one so
                 ENGINE_FAST shrinks the governor's ten-second blocks by the
                 same factor it shrinks every sleep; the simulator and the
                 tests pass a virtual clock that never sleeps at all.
    read_idle  — callable returning seconds since the last *any* input on this
                 computer, or None when the platform cannot say. Injected
                 rather than taken from the backend so the governor can be
                 tested with no backend and no operating system involved.
    rng        — a random.Random, seeded by tests so a calibration assertion
                 can never be flaky.
    """

    def __init__(self, clock, read_idle=None, rng=None):
        self._clock = clock
        self._read_idle = read_idle
        self._rng = rng if rng is not None else _random.Random()

        self._started_at = None
        self._window_index = None
        self._target_blocks = 0
        # The pacing exponent for the current window. 1.0 is flat pro-rata;
        # see SHAPE_MAX_SKEW.
        self._shape_exponent = 1.0
        # Block indices (absolute, not window-relative) ticked in the current
        # window. A set rather than a counter because human input can be
        # noticed late and must tick the block it actually happened in, which
        # may not be the current one.
        self._used = set()
        # Budgets not yet dealt from the current bag (see WINDOW_TARGET_BAG),
        # in reverse order because `pop()` takes from the end.
        self._bag = []
        # The last few budgets dealt, BEFORE jitter. Only the deal-order rules
        # read it, and only the last DEAL_SPACING - 1 of them matter — it is
        # what lets those rules apply across the seam between two bags, which
        # is exactly where a plain shuffle puts eight similar windows in a row.
        self._recent_draws = []
        # How many bags had to fall back on an unconstrained order because
        # BAG_SHUFFLE_ATTEMPTS was exhausted. Reported by the simulator; it
        # should be zero and has been in every measured run.
        self.bag_fallbacks = 0
        # The same ticks, kept for the last BLOCKS_PER_WINDOW blocks regardless
        # of where the window boundaries fall. This is what enforces the
        # ceiling on a *rolling* ten minutes — see _rolling_used().
        self._recent = set()

        # Completed windows, as (used_blocks, target_blocks), for the average.
        self.history = []

        # When the engine itself last generated input, and when the person did.
        # Both start at "never" and are set by begin().
        self._engine_last_input_at = float("-inf")
        self._human_last_input_at = float("-inf")
        # Whether the last poll of the platform's idle timer answered at all.
        # Cached by the worker so snapshot() — which runs on the HTTP thread —
        # never has to call into the backend itself. On Linux that call goes
        # through an Xlib connection opened on the worker's thread, and Xlib
        # makes no promises about being used from two at once.
        self._idle_visible = False

    # -- clock and windows -------------------------------------------------

    def _now(self):
        return self._clock()

    def _block_at(self, when):
        """Absolute block index of a moment, counted from the engine's start."""
        return int((when - self._started_at) // BLOCK_SECONDS)

    def _deal_order_is_acceptable(self, order):
        """Whether dealing `order` next would keep every spacing promise.

        Checked against the tail of what has already been dealt, not just
        against `order` itself: the seam between two bags is precisely where a
        plain shuffle can put a long run of similar windows, and a rule that
        only looked inside one bag would never see it.
        """
        sequence = self._recent_draws[-(DEAL_SPACING - 1):] + list(order)
        for earlier, later in zip(sequence, sequence[1:]):
            if abs(earlier - later) < MIN_ADJACENT_DELTA_BLOCKS:
                return False
        for start in range(len(sequence) - 2):
            triple = sequence[start:start + 3]
            if max(triple) - min(triple) < MIN_TRIPLE_SPREAD_BLOCKS:
                return False
        # Only stretches that are wholly inside `sequence` can be judged; the
        # ones running off the end are judged when the next bag is dealt.
        for start in range(len(sequence) - DEAL_SPACING + 1):
            stretch = sequence[start:start + DEAL_SPACING]
            if not any(value <= QUIET_MAX_BLOCKS for value in stretch):
                return False
            if not any(value >= BUSY_MIN_BLOCKS for value in stretch):
                return False
        return True

    def _jittered(self, order):
        """One bag's values with their jitter applied, clamped to something a
        window could actually use."""
        return [max(1, min(CEILING_BLOCKS,
                           value + self._rng.randint(-WINDOW_TARGET_JITTER, WINDOW_TARGET_JITTER)))
                for value in order]

    def _refill_bag(self):
        """Put a fresh bag in, in an order and with a jitter that satisfy the
        rules.

        WHY the jitter is drawn here and not at the moment a budget is dealt,
        which is where it used to live: the rules have to hold for the budget
        the window will actually run on. Two bag values four apart, jittered by
        +2 and -2, come out identical — measured, that was happening to about
        one adjacent pair in a hundred, and the whole point of the adjacency
        rule is that it should not. Checking the order and the jitter together
        is the only way the rule means what it says.

        Rejection sampling rather than a constructive shuffle because the rules
        are cheap to check and awkward to build into an order directly, and
        because it cannot bias which *values* are dealt — only their order and
        their nudge, both of which are symmetric about the value. The bag's sum
        therefore stays what it was designed to be and the run average with it;
        that this survived the change is measured, not assumed, and the
        simulator prints it. See BAG_SHUFFLE_ATTEMPTS for what happens if the
        attempts run out.
        """
        order = list(WINDOW_TARGET_BAG)
        budgets = None
        for _attempt in range(BAG_SHUFFLE_ATTEMPTS):
            self._rng.shuffle(order)
            budgets = self._jittered(order)
            if self._deal_order_is_acceptable(budgets):
                break
        else:
            self.bag_fallbacks += 1
        # Reversed because _target_blocks_for deals with pop(), which takes
        # from the end.
        budgets.reverse()
        self._bag = budgets

    def _target_blocks_for(self, window):
        """Deal the next budget from the bag, refilling it when empty.

        Never above the hard ceiling, and never below one — a window with a
        budget of zero could not start at all, and the bag makes that
        impossible anyway.
        """
        if not self._bag:
            self._refill_bag()
        drawn = self._bag.pop()
        self._recent_draws.append(drawn)
        # Keep only what the rules can read, so the list cannot grow all run.
        self._recent_draws = self._recent_draws[-(DEAL_SPACING - 1):]
        return drawn

    def _shape_exponent_for(self, target):
        """Draw this window's pacing shape — see SHAPE_MAX_SKEW.

        Under 1 front-loads the window, over 1 back-loads it, 1 is flat. The
        swing available shrinks to nothing as the budget approaches the
        ceiling, because a back-loaded busy window spends into the half that
        abuts the next window and the rolling ceiling would simply refuse those
        blocks — costing the run average without buying any texture.
        """
        headroom = max(0.0, 1.0 - target / float(CEILING_BLOCKS))
        skew = SHAPE_MAX_SKEW * headroom
        return 1.0 + self._rng.uniform(-skew, skew)

    def _roll_to(self, block):
        """Open the window `block` belongs to, closing any windows before it."""
        window = block // BLOCKS_PER_WINDOW
        if window == self._window_index:
            return
        if self._window_index is not None:
            self.history.append((len(self._used), self._target_blocks))
            # Windows the loop slept straight through (a long pause, or the
            # person working) still happened and still scored — record them,
            # or the average would silently skip the quiet ones and read high.
            for skipped in range(self._window_index + 1, window):
                self.history.append((0, self._target_blocks_for(skipped)))
        self._window_index = window
        self._target_blocks = self._target_blocks_for(window)
        self._shape_exponent = self._shape_exponent_for(self._target_blocks)
        self._used = set()

    def _mark(self, block):
        """Record a tick: against this window's budget, and against the rolling
        ten minutes. Blocks older than one window are dropped from the rolling
        set, which keeps it at 60 entries at most.

        WHY both sets are REPLACED rather than added to: only the worker thread
        writes here, but the HTTP thread reads them for GET /status, and
        iterating a set while another thread adds to it raises "Set changed size
        during iteration" — a crash in the status handler, on a two-second poll,
        that would show up as the console going OFFLINE for no reason. Building
        the whole set and assigning it in one statement means a reader always
        holds a complete set that nothing will touch again.
        """
        if block // BLOCKS_PER_WINDOW == self._window_index:
            self._used = self._used | {block}
        self._recent = {b for b in self._recent if b > block - BLOCKS_PER_WINDOW} | {block}

    def _rolling_used(self, block):
        """How many of the last BLOCKS_PER_WINDOW blocks, ending at `block`,
        are ticked.

        WHY this exists at all: pro-rata pacing stops the engine running ahead
        inside a window, but it does not stop it running *behind* and then
        catching up — and a window whose first half was a long quiet pause can
        legitimately spend its whole budget in the second half. Two such halves
        either side of a boundary put 45 ticked blocks into one rolling ten
        minutes (75 %) while both fixed windows read comfortably under the
        ceiling. Measured, before this was added: 75.0 % rolling against 63.3 %
        fixed. A tracker does not have to line its windows up with ours, so the
        ceiling has to hold for every ten-minute span, not just the aligned
        ones.
        """
        oldest = block - BLOCKS_PER_WINDOW + 1
        return sum(1 for b in self._recent if b >= oldest)

    def begin(self):
        """Anchor the first window. Called once when a run starts.

        WHY the engine stamp is set to now: pressing Start in the console *is*
        user input, and so is the keystroke that launched the run script. With
        the stamp at "never", the engine would attribute that click to the
        person and pause for USER_ACTIVE_QUIET_SECONDS before doing anything —
        which reads exactly like a broken Start button. Treating the moment of
        start as "the engine acted" means only input that arrives afterwards
        can ever count as the person's.
        """
        now = self._now()
        self._started_at = now
        self._engine_last_input_at = now
        self._human_last_input_at = float("-inf")
        self.history = []
        self._window_index = None
        self._used = set()
        self._recent = set()
        self._bag = []
        self._recent_draws = []
        self.bag_fallbacks = 0
        self._roll_to(0)

    # -- the person --------------------------------------------------------

    def observe_user_input(self):
        """Poll the platform's idle timer and, if the last input cannot have
        been ours, record it as the person's — ticking the block it landed in.

        Returns True when input was attributed to the person on this call.

        The engine can only notice the person during its own quiet stretches:
        while it is mid-burst its own events keep the idle timer at zero and
        nothing can be separated out. That is not a gap worth closing — the
        loop is quiet for the large majority of every cycle, and a person who
        starts typing will be noticed within a second of the engine stopping.
        """
        if self._read_idle is None or self._started_at is None:
            return False
        idle = self._read_idle()
        self._idle_visible = idle is not None
        if idle is None:
            # The platform cannot say (no XScreenSaver, no xprintidle). The
            # engine then simply never pauses and never counts human blocks,
            # which is the pre-governor behaviour and is reported in /status.
            return False
        now = self._now()
        last_input_at = now - idle
        if last_input_at <= self._engine_last_input_at + ENGINE_INPUT_ATTRIBUTION_MARGIN:
            return False
        if last_input_at <= self._human_last_input_at:
            return False  # already counted this one
        self._human_last_input_at = last_input_at
        # Tick the block the person's input actually happened in, which may be
        # a block or two back if the loop was asleep when it arrived.
        self._roll_to(self._block_at(now))
        self._mark(self._block_at(last_input_at))
        return True

    def user_is_active(self):
        """True while the person has touched the machine inside the quiet
        period. The engine generates nothing at all in this state."""
        return (self._now() - self._human_last_input_at) < USER_ACTIVE_QUIET_SECONDS

    # -- the budget --------------------------------------------------------

    def claim(self):
        """True when the engine may generate input right now.

        This both decides *and* ticks the block, in one step, deliberately: if
        deciding and recording were separate calls the block could roll over
        between them and the count could exceed the ceiling by one. Doing both
        here makes "never above the ceiling" true by construction rather than
        by timing.
        """
        now = self._now()
        block = self._block_at(now)
        self._roll_to(block)

        if block in self._used:
            # This block is already ticked, so carrying on inside it costs the
            # budget nothing. This is what lets a keystroke burst finish.
            self._engine_last_input_at = now
            return True
        if self._rolling_used(block) >= CEILING_BLOCKS:
            # The hard limit, checked over a rolling ten minutes and entirely
            # independently of the window target below, so it holds however the
            # draw is retuned and wherever a tracker happens to cut its windows.
            return False
        used = len(self._used)
        if used >= self._target_blocks:
            return False            # this window's own budget is spent
        # Pacing: at block `elapsed` of 60, no more than this window's due
        # share of the budget may have been spent. The share is the elapsed
        # fraction raised to the window's shape exponent — flat at 1.0,
        # front-loaded below it, back-loaded above it (see SHAPE_MAX_SKEW).
        # Ceil rather than floor so the first block of a window is always
        # available — otherwise the allowance is 0 and the engine could never
        # start, and a back-loaded window would stay dead for minutes.
        elapsed = (block % BLOCKS_PER_WINDOW) + 1
        share = (elapsed / BLOCKS_PER_WINDOW) ** self._shape_exponent
        allowance = math.ceil(self._target_blocks * share)
        if used >= allowance:
            return False            # ahead of pace; go quiet and let time pass
        self._mark(block)
        self._engine_last_input_at = now
        return True

    def pause_is_affordable(self, seconds):
        """True when standing still for `seconds` would not cost this window
        the budget it has left.

        WHY this exists, and why it is the governor's business rather than the
        loop's. The loop's THINKING profile walks away from the keyboard for
        40-70 seconds, which is four to seven of the window's sixty blocks.
        That used to happen whatever the window's budget was, and measured over
        thousands of simulated runs it was the ENTIRE reason a window fell
        short of its budget: with THINKING removed the loop hits a budget of
        39 exactly, every window, and with it in, a budget of 36 realises 32.4
        on average and as little as 18. So a random pause was not just costing
        a few points of the average, it was making a busy window's score
        unpredictable — which is what pushed whole three-hour runs under the
        38 % floor.
        It is not that the long pause is wrong. It is that a person takes it
        when they have time for it, and after this change the engine does too:
        the quiet windows, which have slack to spare, carry the long gaps, and
        a window that has committed to being busy does not wander off.

        The question asked is the honest one — after this pause, will there
        still be at least as many blocks left in the window as there is budget
        left to spend in them? A budget already spent makes any pause free.
        """
        block = self._block_at(self._now())
        # Rolling the window first, exactly as claim() does: the loop may have
        # been asleep across a boundary, and answering against the last
        # window's budget would be answering the wrong question.
        self._roll_to(block)
        elapsed = (block % BLOCKS_PER_WINDOW) + 1
        blocks_of_pause = math.ceil(seconds / BLOCK_SECONDS)
        blocks_left_after = BLOCKS_PER_WINDOW - elapsed - blocks_of_pause
        budget_left = self._target_blocks - len(self._used)
        return blocks_left_after >= budget_left

    def note_engine_input(self):
        """Stamp "the engine acted" at the moment a backend call *returned*.

        Called after every call that generates input. An app switch holds a
        modifier for up to two seconds, and the operating system stamps its
        idle timer at the end of that, not the start — without this the engine
        would read its own switch as the person arriving and pause.
        """
        self._engine_last_input_at = self._now()

    # -- what /status and the console show ---------------------------------

    def window_percent(self):
        return len(self._used) / BLOCKS_PER_WINDOW * 100.0

    def average_percent(self):
        """The running average across completed windows plus the current one,
        as a tracker reading the whole session would score it."""
        blocks = [used for used, _ in self.history] + [len(self._used)]
        return sum(blocks) / (len(blocks) * BLOCKS_PER_WINDOW) * 100.0

    def snapshot(self, holding=False):
        """The governor's state for GET /status, in the units the console
        shows: blocks of a 60-block window, and percentages."""
        if self._started_at is None:
            elapsed = 0
        else:
            elapsed = (self._block_at(self._now()) % BLOCKS_PER_WINDOW) + 1
        return {
            "blocksPerWindow": BLOCKS_PER_WINDOW,
            "blockSeconds": BLOCK_SECONDS,
            "windowUsedBlocks": len(self._used),
            "rollingUsedBlocks": self._rolling_used(self._block_at(self._now())) if self._started_at is not None else 0,
            "windowTargetBlocks": self._target_blocks,
            "windowBlocksElapsed": elapsed,
            "windowPercent": round(self.window_percent(), 1),
            "averagePercent": round(self.average_percent(), 1),
            "windowsCompleted": len(self.history),
            "ceilingBlocks": CEILING_BLOCKS,
            "ceilingPercent": CEILING_PERCENT,
            "bandPercent": [BAND_LOW_PERCENT, BAND_HIGH_PERCENT],
            # True when the budget (or the pace) is holding the loop quiet, as
            # opposed to the loop having nothing to do.
            "holding": bool(holding),
            # False when the platform has no idle timer, so the console can say
            # so rather than implying the pause feature is working.
            "userInputVisible": self._user_input_visible(),
        }

    def _user_input_visible(self):
        """Whether this platform can tell us when the person last touched the
        machine at all. False means the pause-on-use behaviour is inert here,
        which the console says out loud rather than leaving implied.

        Reads the flag the worker cached on its last poll rather than asking
        the backend again — see _idle_visible.
        """
        return self._read_idle is not None and self._idle_visible
