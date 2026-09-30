"""Three-hour activity simulator — the instrument the governor was tuned with.

    python3 engine/test_metrics.py                 # one run in detail, then 200 trials
    python3 engine/test_metrics.py --trials 500    # more trials
    python3 engine/test_metrics.py --seed 7        # a different (still fixed) seed
    python3 engine/test_metrics.py --quiet         # just the verdict

An activity tracker scores a ten-minute window as 60 blocks of ten seconds and
counts a block as active if any input arrived in it. The requirement this tool
exists to meet is:

    * every 10-minute window is random,
    * no window is ever above 65 %,
    * and the average across a 3-hour run sits between 38 % and 47 %.

This file steps a virtual clock — nothing sleeps, nothing is generated, nothing
is touched — through three simulated hours of the real loop, driving the **real
governor** from `engine/governor.py` with the **real profile and timing
constants** from `engine/engine.py`. It imports every number it uses. That is
the whole point of rewriting it: the version this replaced wrote its own
timings down (2-2.5 s of action, 14-24 s of sleep), matched no cadence the
engine ever had, and then printed "PERFECTLY OPTIMIZED. SAFE FOR PRODUCTION
WORK." about a model of nothing.

It exits 0 when the run passes and 1 when it does not, so `make test` fails on
a miscalibration rather than printing a tick and moving on.
"""

import argparse
import os
import random
import statistics
import sys

# The backend is picked at import time, and this file must never generate
# input on the machine it is run on. ENGINE_FAST is deliberately NOT set: the
# simulator uses its own virtual clock and never calls pause().
os.environ.setdefault("ENGINE_BACKEND", "fake")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import engine  # noqa: E402
import governor as gov_module  # noqa: E402

# WHY three hours: the span the requirement is stated over, and therefore the
# span the average has to be judged across. A shorter run would flatter the
# numbers by hiding the variance between windows.
RUN_SECONDS = 3 * 3600.0
# WHY 200: enough that the spread of the run average is a real distribution
# rather than a lucky draw, and still a few seconds of CPU. The requirement
# asks for at least this many.
DEFAULT_TRIALS = 200
# WHY 20 % of blocks: the "someone is actually working" scenario in the second
# report. A person at a keyboard does not tick every block either — they read,
# they think, they go to meetings — and a fifth of the blocks is a plausible
# background level to check the engine shares its budget with rather than
# stacking on top of.
HUMAN_BLOCK_FRACTION = 0.20


class VirtualClock:
    """A clock that only moves when the simulation says so."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _block_of(clock, started_at):
    return int((clock.now - started_at) // gov_module.BLOCK_SECONDS)


def simulate_run(rng, human=False):
    """One three-hour run. Returns (window_percentages, active_block_set).

    The loop below mirrors `engine.loop_worker` step for step: draw a profile,
    return early for THINKING, ask the governor, and on a refusal wait one poll
    interval and ask again. Cycle durations come from `engine.cycle_seconds`,
    which is composed from the loop's own constants.
    """
    clock = VirtualClock()

    human_blocks = set()
    if human:
        # A person whose input lands in a fifth of the blocks, chosen up front
        # so the pattern is reproducible from the seed. The governor sees them
        # through read_idle exactly as it sees a real person.
        total_blocks = int(RUN_SECONDS // gov_module.BLOCK_SECONDS)
        for block in range(total_blocks):
            if rng.random() < HUMAN_BLOCK_FRACTION:
                human_blocks.add(block)

    def read_idle():
        """Seconds since the last input of any kind, as the OS would report.

        The engine's own input is not modelled here — the governor's engine
        stamp already covers it, and adding a second source would only make
        this agree with itself. What it does model is a person typing at some
        point inside each of their blocks.
        """
        if not human:
            return None
        block = int(clock.now // gov_module.BLOCK_SECONDS)
        for candidate in range(block, max(-1, block - 10), -1):
            if candidate in human_blocks:
                # Treat the person as having typed at the start of that block.
                return max(0.0, clock.now - candidate * gov_module.BLOCK_SECONDS)
        return 1e9  # a long time ago

    gov = gov_module.ActivityGovernor(clock, read_idle=read_idle if human else None, rng=rng)
    gov.begin()
    started_at = clock.now

    engine_blocks = set()

    while clock.now < RUN_SECONDS:
        gov.observe_user_input()
        if gov.user_is_active():
            clock.advance(engine.GOVERNOR_POLL_SECONDS)
            continue

        mode = rng.choice(engine.MODE_POOL)
        if mode == "THINKING":
            _, quiet = engine.cycle_seconds(mode, rng)
            clock.advance(quiet)
            continue

        if not gov.claim():
            clock.advance(engine.GOVERNOR_POLL_SECONDS)
            continue
        engine_blocks.add(_block_of(clock, started_at))

        active, quiet = engine.cycle_seconds(mode, rng)
        # The active phase generates input continuously, so every block it
        # crosses is claimed — and a refusal at a block boundary truncates the
        # cycle, exactly as `engine.act` makes the real loop do.
        end = clock.now + active
        while True:
            block = _block_of(clock, started_at)
            next_boundary = started_at + (block + 1) * gov_module.BLOCK_SECONDS
            if next_boundary >= end:
                clock.now = end
                break
            clock.now = next_boundary
            if not gov.claim():
                break
            engine_blocks.add(_block_of(clock, started_at))
        clock.advance(quiet)

    # The governor's own per-window record is the authority on the score; the
    # simulator does not recount it.
    windows = [used for used, _ in gov.history] + [len(gov._used)]
    # WHY the truncation: the loop stops at the first check AFTER the three
    # hours are up, so a cycle straddling the end opens a 19th window holding a
    # few seconds and a block or two. That sliver is an artefact of where the
    # simulation stops, not part of what a tracker would score across three
    # hours, and leaving it in dragged the reported average down by about a
    # point and made the per-window listing end on a meaningless 1.7 %.
    windows = windows[:int(RUN_SECONDS // gov_module.WINDOW_SECONDS)]
    percentages = [used / gov_module.BLOCKS_PER_WINDOW * 100.0 for used in windows]
    return percentages, engine_blocks | human_blocks


def rolling_max_percent(blocks):
    """The worst any *rolling* ten-minute window scores, not just the fixed
    ones the governor lines itself up with.

    WHY this is checked separately: a tracker does not have to align its
    windows with the engine's. Two fixed windows that each pass can still hide
    a spike that straddles their boundary — which is precisely what the pacing
    rule exists to prevent, so this is the number that proves the pacing works.
    """
    if not blocks:
        return 0.0
    span = gov_module.BLOCKS_PER_WINDOW
    ordered = sorted(blocks)
    worst = 0
    head = 0
    for tail, start in enumerate(ordered):
        while ordered[head] < start - span + 1:
            head += 1
        worst = max(worst, tail - head + 1)
    return worst / span * 100.0


def report_single(percentages, blocks, quiet=False):
    average = statistics.fmean(percentages)
    worst = max(percentages)
    if not quiet:
        print("\n--- ONE 3-HOUR RUN, WINDOW BY WINDOW ---")
        for index, pct in enumerate(percentages):
            used = round(pct / 100.0 * gov_module.BLOCKS_PER_WINDOW)
            bar = "#" * int(pct / 2)
            flag = "  <-- OVER CEILING" if pct > gov_module.CEILING_PERCENT else ""
            print(f"  window {index + 1:>2}  {used:>2}/{gov_module.BLOCKS_PER_WINDOW} blocks  "
                  f"{pct:5.1f}%  {bar}{flag}")
    print(f"\n  windows           : {len(percentages)}")
    print(f"  highest window    : {worst:.1f}%   (ceiling {gov_module.CEILING_PERCENT:g}%)")
    print(f"  lowest window     : {min(percentages):.1f}%")
    print(f"  rolling 10-min max: {rolling_max_percent(blocks):.1f}%   "
          f"(any 60 consecutive blocks, not just the aligned ones)")
    print(f"  3-hour average    : {average:.1f}%   "
          f"(band {gov_module.BAND_LOW_PERCENT:g}-{gov_module.BAND_HIGH_PERCENT:g}%)")
    return average, worst


def report_trials(trials, seed, label, human=False):
    averages, worsts, rollings = [], [], []
    for index in range(trials):
        # WHY seed + index: every trial is different, and the whole sweep is
        # reproducible from one number. A calibration figure that moved between
        # runs would be worthless as evidence.
        rng = random.Random(seed + index)
        percentages, blocks = simulate_run(rng, human=human)
        averages.append(statistics.fmean(percentages))
        worsts.append(max(percentages))
        rollings.append(rolling_max_percent(blocks))

    average = statistics.fmean(averages)
    print(f"\n--- {trials} INDEPENDENT 3-HOUR RUNS ({label}) ---")
    print(f"  average of run averages : {average:.2f}%")
    print(f"  run-average spread      : {min(averages):.2f}% .. {max(averages):.2f}%  "
          f"(sd {statistics.pstdev(averages):.2f})")
    print(f"  worst single window     : {max(worsts):.1f}%  "
          f"(ceiling {gov_module.CEILING_PERCENT:g}%)")
    print(f"  worst rolling 10-min    : {max(rollings):.1f}%")
    print(f"  runs outside {gov_module.BAND_LOW_PERCENT:g}-{gov_module.BAND_HIGH_PERCENT:g}% band : "
          f"{sum(1 for a in averages if not gov_module.BAND_LOW_PERCENT <= a <= gov_module.BAND_HIGH_PERCENT)}")
    print(f"  runs breaching ceiling  : {sum(1 for w in worsts if w > gov_module.CEILING_PERCENT)}")

    histogram(averages)
    return averages, worsts, rollings


def histogram(averages, buckets=10):
    low, high = min(averages), max(averages)
    if high - low < 1e-9:
        return
    width = (high - low) / buckets
    print("\n  distribution of the 3-hour average:")
    for bucket in range(buckets):
        start = low + bucket * width
        end = start + width
        count = sum(1 for a in averages if start <= a < end or (bucket == buckets - 1 and a == high))
        print(f"    {start:5.2f}% - {end:5.2f}%  {'#' * count}{'' if count else ''} ({count})")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    # WHY a fixed default seed: `make test` runs this, and a test that can fail
    # on an unlucky draw is worse than no test. Pass --seed to explore.
    parser.add_argument("--seed", type=int, default=20260929)
    parser.add_argument("--quiet", action="store_true", help="skip the per-window listing")
    parser.add_argument("--human", action="store_true",
                        help="also simulate a person using the machine alongside the engine")
    args = parser.parse_args(argv)

    print("=== EMULATION ENGINE ACTIVITY SIMULATOR ===")
    print(f"  ceiling      : {gov_module.CEILING_BLOCKS}/{gov_module.BLOCKS_PER_WINDOW} blocks "
          f"({gov_module.CEILING_PERCENT:g}%) — never crossed")
    print(f"  window target: {gov_module.WINDOW_TARGET_BLOCKS[0]}-{gov_module.WINDOW_TARGET_BLOCKS[1]} blocks, "
          f"drawn fresh every 10 minutes")
    print(f"  profile pool : {', '.join(engine.MODE_POOL)}")
    print(f"  clicking     : {'on' if engine.ALLOW_CLICK else 'off'}")

    percentages, blocks = simulate_run(random.Random(args.seed))
    report_single(percentages, blocks, quiet=args.quiet)

    averages, worsts, rollings = report_trials(args.trials, args.seed, "engine alone")

    if args.human:
        report_trials(args.trials, args.seed + 100000,
                      f"engine plus a person active in {HUMAN_BLOCK_FRACTION:.0%} of blocks",
                      human=True)
        print("\n  Read that second table with one caveat. The fixed-window and average\n"
              "  figures are the governor's own accounting and it does hold the line —\n"
              "  the engine stands down for about 40 % of such a run and shares its\n"
              "  budget with the person. The ROLLING figure above is measured against\n"
              "  every block the person actually touched, including the ones the engine\n"
              "  could not attribute to them: input arriving within\n"
              f"  {gov_module.ENGINE_INPUT_ATTRIBUTION_MARGIN} s of one of the engine's own events is\n"
              "  indistinguishable from its own, and about one human block in ten falls\n"
              "  there. The engine cannot count what it cannot see, and it cannot make\n"
              "  someone else type less — so with a person working flat out alongside it\n"
              "  the combined score can pass the ceiling by a few points. Its own\n"
              "  contribution stays inside it.")

    overall = statistics.fmean(averages)
    ceiling_ok = max(worsts) <= gov_module.CEILING_PERCENT
    rolling_ok = max(rollings) <= gov_module.CEILING_PERCENT
    band_ok = all(gov_module.BAND_LOW_PERCENT <= a <= gov_module.BAND_HIGH_PERCENT for a in averages)

    print("\n--- VERDICT ---")
    print(f"  no window above {gov_module.CEILING_PERCENT:g}%            : {'PASS' if ceiling_ok else 'FAIL'}")
    print(f"  no rolling window above {gov_module.CEILING_PERCENT:g}%    : {'PASS' if rolling_ok else 'FAIL'}")
    print(f"  every run inside {gov_module.BAND_LOW_PERCENT:g}-{gov_module.BAND_HIGH_PERCENT:g}%         : "
          f"{'PASS' if band_ok else 'FAIL'}")
    print(f"  overall average {overall:.2f}%")
    passed = ceiling_ok and rolling_ok and band_ok
    print(f"\nSTATUS: {'PASS' if passed else 'FAIL — recalibrate WINDOW_TARGET_BLOCKS in engine/governor.py'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
