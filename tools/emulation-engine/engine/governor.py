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
- **A random target per window.** Each window draws its own budget from
  `WINDOW_TARGET_BLOCKS`, so two windows in a row genuinely differ. The draw
  was tuned against `engine/test_metrics.py` (hundreds of simulated three-hour
  runs) so the long-run average lands in the middle of the 38-47 % band the
  tool promises, not against its edges.
- **Pacing.** Spending the whole budget in the first three minutes and then
  lying dead for seven is itself a pattern, and a tracker reading a *rolling*
  ten minutes would see the spike anyway. A claim is refused while the blocks
  used so far are ahead of the pro-rata share of the budget for the part of
  the window that has elapsed.
- **The human's own input counts.** `observe_user_input()` attributes real
  keyboard and mouse activity to the person and ticks those blocks against the
  same budget. Without it the engine would pile its activity on top of someone
  already working and the combined score would sail past the ceiling — which
  is the main reason any of this exists.

Everything here is a pure function of an injected clock, an injected random
source and an injected idle reader, which is what lets `test_metrics.py` run
three simulated hours in milliseconds against *this* code rather than a copy
of its numbers.
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

# WHY a shuffled bag and not an independent draw per window: both give each
# window a budget nobody can predict from the last one, but independent draws
# also let a three-hour run get unlucky. Measured over 600 simulated runs, an
# independent uniform draw wide enough to make consecutive windows visibly
# differ produced a standard deviation of about 1.5 points on the run average —
# so a run landing outside the 38-47 % band was a matter of time, and the only
# way to stop it was to narrow the draw until the windows all looked alike.
#
# A bag fixes that without giving anything up. The six budgets below are dealt
# in a random order; when the bag runs out it is refilled and reshuffled. Any
# six consecutive windows therefore average the middle of the range exactly,
# while the *next* window is still a surprise. Three hours is eighteen windows,
# i.e. three whole bags, which is why the run average now varies only by the
# small amount the loop itself loses. Measured over 2,000 simulated three-hour
# runs: average 43.11 %, every run between 39.81 % and 45.46 %, sd 0.83 — so
# not one left the 38-47 % band, and the nearest any came to an edge was 1.5
# points clear of it.
#
# WHY these six values: 22-33 of 60 blocks is 36.7 %-55 %, a spread wide enough
# that one window plainly differs from its neighbour. Their mean, 27.5 blocks
# (45.8 %), is deliberately about three points ABOVE the 43 % a run should
# average, because the loop never quite spends its whole budget — a THINKING
# pause, or a cycle that ends near a window boundary, leaves a little on the
# table every time. Three points is what that costs, measured.
# WHY every value stays well under CEILING_BLOCKS (39): the ceiling is meant to
# be a backstop that never fires in normal operation, not the working
# mechanism. The highest budget here plus the jitter is 34.
WINDOW_TARGET_BAG = (22, 25, 27, 28, 30, 33)
# WHY +/-1 block of jitter: without it an observer watching six windows would
# see exactly the six numbers above and nothing else. It has zero mean, so it
# costs the calibration nothing.
WINDOW_TARGET_JITTER = 1
# The range that actually results, for the banner, the docs and the tests.
WINDOW_TARGET_BLOCKS = (min(WINDOW_TARGET_BAG) - WINDOW_TARGET_JITTER,
                        max(WINDOW_TARGET_BAG) + WINDOW_TARGET_JITTER)

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
        # Block indices (absolute, not window-relative) ticked in the current
        # window. A set rather than a counter because human input can be
        # noticed late and must tick the block it actually happened in, which
        # may not be the current one.
        self._used = set()
        # Budgets not yet dealt from the current bag (see WINDOW_TARGET_BAG).
        self._bag = []
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

    def _target_blocks_for(self, window):
        """Deal the next budget from the shuffled bag, refilling it when empty.

        Never above the hard ceiling, and never below one — a window with a
        budget of zero could not start at all, and the bag makes that
        impossible anyway.
        """
        if not self._bag:
            self._bag = list(WINDOW_TARGET_BAG)
            self._rng.shuffle(self._bag)
        drawn = self._bag.pop() + self._rng.randint(-WINDOW_TARGET_JITTER, WINDOW_TARGET_JITTER)
        return max(1, min(CEILING_BLOCKS, drawn))

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
        # Pacing: at block `elapsed` of 60, no more than the pro-rata share of
        # the budget may have been spent. Ceil rather than floor so the first
        # block of a window is always available — otherwise the allowance is 0
        # and the engine could never start.
        elapsed = (block % BLOCKS_PER_WINDOW) + 1
        allowance = math.ceil(self._target_blocks * elapsed / BLOCKS_PER_WINDOW)
        if used >= allowance:
            return False            # ahead of pace; go quiet and let time pass
        self._mark(block)
        self._engine_last_input_at = now
        return True

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
