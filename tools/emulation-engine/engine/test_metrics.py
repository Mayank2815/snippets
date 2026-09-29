"""Three-hour activity simulator — the instrument the governor was tuned with.

    python3 engine/test_metrics.py                 # one run in detail, then 2,000 trials
    python3 engine/test_metrics.py --trials 500    # fewer trials
    python3 engine/test_metrics.py --seed 7        # a different (still fixed) seed
    python3 engine/test_metrics.py --quiet         # just the verdict
    python3 engine/test_metrics.py --flagcheck     # the one number, in plain words

An activity tracker scores a ten-minute window as 60 blocks of ten seconds and
counts a block as active if any input arrived in it. The requirement this tool
exists to meet is:

    * every 10-minute window is random,
    * no window is ever above 65 %,
    * the average across a 3-hour run sits between 38 % and 47 %,
    * over any 90 minutes the windows genuinely VARY — at least
      VARIANCE_MIN_RANGE_PERCENT of range and VARIANCE_MIN_STDEV_PERCENT of
      standard deviation across every nine consecutive windows,
    * genuinely quiet windows happen, not by accident,
    * and no two windows running look the same.

The last three exist because a real tracker flagged a real person for
"unusually consistent activity — activity rate varied 1-4 % for over 90
minutes". Their overall rate was 45 % and that was never the complaint. So the
number this file exists to produce is the one --flagcheck prints: across
thousands of simulated runs, how little did the tightest 90 minutes vary?
Anywhere near 1-4 and the engine would be flagged for the same reason.

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
# WHY 2,000: the variance promise is a statement about the WORST ninety minutes
# any run produces, and a worst case needs a lot of runs to be believed. Two
# hundred was enough for an average; it is not enough for a minimum. Two
# thousand three-hour runs is 36,000 windows and about twenty seconds of CPU.
DEFAULT_TRIALS = 2000
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
    """One three-hour run.

    Returns (window_percentages, active_block_set, bag_fallbacks) — the last
    being how many times the governor could not find a deal order satisfying
    its spacing rules and fell back on an unconstrained one. It should be zero
    and the report says so out loud, because a non-zero count would quietly
    turn the variance guarantee into a hope.

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

        mode, macro_pause = engine.choose_mode(rng, gov)
        if mode == "THINKING":
            # The pause comes back from choose_mode rather than being redrawn,
            # because the governor was asked whether THAT pause was affordable.
            clock.advance(macro_pause)
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
    return percentages, engine_blocks | human_blocks, gov.bag_fallbacks


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


def stretch_variation(percentages):
    """(range, standard deviation) for every VARIANCE_WINDOW_COUNT consecutive
    windows, as a list — one entry per 90-minute stretch in the run.

    WHY a sliding window and not a chopped-up one: the tracker's ninety minutes
    do not have to start where ours do. A run could pass with every aligned
    stretch varying nicely and still hold one flat stretch straddling two of
    them, which is the one that would get flagged.
    """
    span = gov_module.VARIANCE_WINDOW_COUNT
    out = []
    for start in range(len(percentages) - span + 1):
        stretch = percentages[start:start + span]
        out.append((max(stretch) - min(stretch), statistics.pstdev(stretch)))
    return out


# WHY an epsilon on a percentage comparison: both sides are whole numbers of
# blocks turned into percentages, and two blocks of sixty is 3.3333... either
# way — but 55.0 - 51.666... comes out a hair BELOW 2 / 60 * 100 in binary
# floating point, so an exact >= would report every legitimately-spaced pair as
# a violation. One part in a billion is far smaller than a block and far larger
# than the error.
FLOAT_SLACK = 1e-9


def adjacent_differences(percentages):
    """How far each window sits from the one before it, in points."""
    return [abs(a - b) for a, b in zip(percentages, percentages[1:])]


def below_adjacent_floor(differences):
    """How many neighbouring pairs came out closer than the deal rule allows.

    Never zero in a long sweep, and that is not the rule failing: see
    ADJACENT_BELOW_FLOOR_MAX_RATE in the governor.
    """
    floor = gov_module.MIN_ADJACENT_DELTA_PERCENT - FLOAT_SLACK
    return sum(1 for d in differences if d < floor)


def first_half_shares(blocks, floor=6):
    """For each window, what share of its blocks landed in its first five
    minutes — the measurement that says whether the pacing SHAPE is real.

    A governor that paced every window flat would put this at 0.5 every time,
    and "every window is paced identically" is the same kind of constant as
    "every window scores the same". Windows with fewer than `floor` blocks are
    left out: one or two blocks cannot express a shape, and the quiet windows
    would otherwise swamp the answer with 0.0 and 1.0.
    """
    per_window = {}
    for block in blocks:
        window, offset = divmod(block, gov_module.BLOCKS_PER_WINDOW)
        used, early = per_window.get(window, (0, 0))
        per_window[window] = (used + 1,
                              early + (1 if offset < gov_module.BLOCKS_PER_WINDOW // 2 else 0))
    return [early / used for used, early in per_window.values() if used >= floor]


def bucket_histogram(percentages, width=10):
    """Counts per `width`-point bucket, as {bucket_start: count}."""
    counts = {}
    for pct in percentages:
        start = int(pct // width) * width
        counts[start] = counts.get(start, 0) + 1
    return counts


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
    variation = stretch_variation(percentages)
    if variation:
        tightest = min(variation)
        print(f"  tightest {gov_module.VARIANCE_WINDOW_COUNT * 10} minutes"
              f": {tightest[0]:.1f} points of range, {tightest[1]:.1f} sd   "
              f"(need {gov_module.VARIANCE_MIN_RANGE_PERCENT:g} and "
              f"{gov_module.VARIANCE_MIN_STDEV_PERCENT:g})")
    quiet_windows = [p for p in percentages if p < gov_module.QUIET_WINDOW_PERCENT]
    print(f"  quiet windows     : {len(quiet_windows)} of {len(percentages)} under "
          f"{gov_module.QUIET_WINDOW_PERCENT:g}%   "
          f"({', '.join(f'{p:.0f}%' for p in quiet_windows) if quiet_windows else 'none'})")
    return average, worst


def measure(trials, seed, human=False):
    """Run `trials` three-hour runs and collect everything the report needs.

    One pass rather than several, because a three-hour run is the expensive
    part and every figure below is a different view of the same runs.
    """
    result = dict(averages=[], worsts=[], rollings=[], ranges=[], stdevs=[],
                  adjacent=[], windows=[], shapes=[], fallbacks=0, tightest=None)
    for index in range(trials):
        # WHY seed + index: every trial is different, and the whole sweep is
        # reproducible from one number. A calibration figure that moved between
        # runs would be worthless as evidence.
        rng = random.Random(seed + index)
        percentages, blocks, fallbacks = simulate_run(rng, human=human)
        result["averages"].append(statistics.fmean(percentages))
        result["worsts"].append(max(percentages))
        result["rollings"].append(rolling_max_percent(blocks))
        result["adjacent"] += adjacent_differences(percentages)
        result["windows"] += percentages
        result["shapes"] += first_half_shares(blocks)
        result["fallbacks"] += fallbacks
        for span_range, span_sd in stretch_variation(percentages):
            result["ranges"].append(span_range)
            result["stdevs"].append(span_sd)
            if result["tightest"] is None or span_range < result["tightest"][0]:
                result["tightest"] = (span_range, span_sd, seed + index)
    return result


def report_trials(trials, seed, label, human=False):
    data = measure(trials, seed, human=human)
    averages, worsts, rollings = data["averages"], data["worsts"], data["rollings"]
    span = gov_module.VARIANCE_WINDOW_COUNT

    print(f"\n--- {trials} INDEPENDENT 3-HOUR RUNS ({label}) ---")
    print(f"  average of run averages : {statistics.fmean(averages):.2f}%")
    print(f"  run-average spread      : {min(averages):.2f}% .. {max(averages):.2f}%  "
          f"(sd {statistics.pstdev(averages):.2f})")
    print(f"  worst single window     : {max(worsts):.1f}%  "
          f"(ceiling {gov_module.CEILING_PERCENT:g}%)")
    print(f"  worst rolling 10-min    : {max(rollings):.1f}%")
    print(f"  runs outside {gov_module.BAND_LOW_PERCENT:g}-{gov_module.BAND_HIGH_PERCENT:g}% band : "
          f"{sum(1 for a in averages if not gov_module.BAND_LOW_PERCENT <= a <= gov_module.BAND_HIGH_PERCENT)}")
    print(f"  runs breaching ceiling  : {sum(1 for w in worsts if w > gov_module.CEILING_PERCENT)}")

    print(f"\n  VARIATION over every {span} consecutive windows ({span * 10} minutes), "
          f"{len(data['ranges'])} stretches:")
    print(f"    range  : worst {min(data['ranges']):.1f} pts, median "
          f"{statistics.median(data['ranges']):.1f}, best {max(data['ranges']):.1f}   "
          f"(floor {gov_module.VARIANCE_MIN_RANGE_PERCENT:g})")
    print(f"    std dev: worst {min(data['stdevs']):.1f} pts, median "
          f"{statistics.median(data['stdevs']):.1f}, best {max(data['stdevs']):.1f}   "
          f"(floor {gov_module.VARIANCE_MIN_STDEV_PERCENT:g})")
    print(f"    stretches under the range floor: "
          f"{sum(1 for r in data['ranges'] if r < gov_module.VARIANCE_MIN_RANGE_PERCENT)}")

    windows = data["windows"]
    quiet = sum(1 for p in windows if p < gov_module.QUIET_WINDOW_PERCENT)
    print(f"\n  WHERE THE {len(windows)} WINDOWS LANDED (10-point buckets):")
    buckets = bucket_histogram(windows)
    widest = max(buckets.values())
    for start in range(0, 100, 10):
        count = buckets.get(start, 0)
        bar = "#" * int(count / widest * 50) if count else ""
        print(f"    {start:>2}-{start + 9:<3}% {count:>7}  {bar}")
    print(f"    lowest window {min(windows):.1f}%, highest {max(windows):.1f}%")
    print(f"    under {gov_module.QUIET_WINDOW_PERCENT:g}% (a genuinely quiet window): "
          f"{quiet} ({quiet / len(windows) * 100:.1f}% of all windows, "
          f"floor {gov_module.QUIET_WINDOW_MIN_RATE * 100:g}%)")

    adjacent = sorted(data["adjacent"])
    print(f"\n  HOW FAR EACH WINDOW SAT FROM THE ONE BEFORE IT ({len(adjacent)} pairs):")
    print(f"    closest {adjacent[0]:.1f} pts, 1st percentile {adjacent[len(adjacent) // 100]:.1f}, "
          f"median {statistics.median(adjacent):.1f}, furthest {adjacent[-1]:.1f}")
    under = below_adjacent_floor(adjacent)
    print(f"    pairs closer than the {gov_module.MIN_ADJACENT_DELTA_PERCENT:.1f} pt deal rule: "
          f"{under} ({under / len(adjacent) * 100:.2f}%, allowed "
          f"{gov_module.ADJACENT_BELOW_FLOOR_MAX_RATE * 100:g}%) — these are windows that "
          f"scored under budget, not budgets dealt too close")
    shapes = sorted(data["shapes"])
    print(f"\n  THE SHAPE INSIDE A WINDOW — share of its blocks in the first five minutes:")
    print(f"    most front-loaded {shapes[0] * 100:.0f}%, 10th percentile "
          f"{shapes[len(shapes) // 10] * 100:.0f}%, median "
          f"{statistics.median(shapes) * 100:.0f}%, 90th "
          f"{shapes[len(shapes) * 9 // 10] * 100:.0f}%, most back-loaded "
          f"{shapes[-1] * 100:.0f}%")
    print(f"    windows paced within 2 points of dead even: "
          f"{sum(1 for s in shapes if abs(s - 0.5) <= 0.02) / len(shapes) * 100:.0f}%")

    if data["fallbacks"]:
        print(f"\n  bags dealt without the spacing rules (should be 0): {data['fallbacks']}")

    histogram(averages)
    return data


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


def flagcheck(trials, seed):
    """The one number, in the words the tracker used.

    A real tracker flagged a real person with "Activity rate varied 1-4 % for
    over 90 minutes". This prints the same measurement for the engine, taken
    over the worst ninety minutes in `trials` three-hour runs, so the headroom
    against being flagged for the same reason is a number and not a feeling.
    """
    span = gov_module.VARIANCE_WINDOW_COUNT
    data = measure(trials, seed)
    worst_range, worst_sd, worst_seed = data["tightest"]
    ranges = data["ranges"]
    print(f"What a tracker would have seen, across {trials} simulated three-hour runs")
    print(f"({len(ranges)} separate {span * 10}-minute stretches in all):\n")
    print(f"  The tightest {span * 10} minutes varied by {worst_range:.0f} points.")
    print(f"  A typical {span * 10} minutes varied by "
          f"{statistics.median(ranges):.0f} points.")
    print(f"  The person who got flagged varied by 1 to 4 points.\n")
    print(f"  So the engine's WORST stretch is about "
          f"{worst_range / 4:.0f} times as varied as the one that was flagged,")
    print(f"  and its typical stretch about {statistics.median(ranges) / 4:.0f} times.")
    print(f"\n  (worst stretch came from seed {worst_seed}; its standard deviation was "
          f"{worst_sd:.1f} points)")
    ok = worst_range >= gov_module.VARIANCE_MIN_RANGE_PERCENT
    print(f"\nSTATUS: {'PASS' if ok else 'FAIL'} — the floor is "
          f"{gov_module.VARIANCE_MIN_RANGE_PERCENT:g} points of range.")
    return 0 if ok else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    # WHY a fixed default seed: `make test` runs this, and a test that can fail
    # on an unlucky draw is worse than no test. Pass --seed to explore.
    parser.add_argument("--seed", type=int, default=20260929)
    parser.add_argument("--quiet", action="store_true", help="skip the per-window listing")
    parser.add_argument("--human", action="store_true",
                        help="also simulate a person using the machine alongside the engine")
    parser.add_argument("--flagcheck", action="store_true",
                        help="just the number the tracker would flag on, in plain words")
    args = parser.parse_args(argv)

    if args.flagcheck:
        return flagcheck(args.trials, args.seed)

    print("=== EMULATION ENGINE ACTIVITY SIMULATOR ===")
    print(f"  ceiling      : {gov_module.CEILING_BLOCKS}/{gov_module.BLOCKS_PER_WINDOW} blocks "
          f"({gov_module.CEILING_PERCENT:g}%) — never crossed")
    print(f"  window target: {gov_module.WINDOW_TARGET_BLOCKS[0]}-{gov_module.WINDOW_TARGET_BLOCKS[1]} blocks "
          f"({gov_module.WINDOW_TARGET_BLOCKS[0] / gov_module.BLOCKS_PER_WINDOW * 100:.0f}-"
          f"{gov_module.WINDOW_TARGET_BLOCKS[1] / gov_module.BLOCKS_PER_WINDOW * 100:.0f}%), "
          f"dealt fresh every 10 minutes")
    print(f"  bag          : {', '.join(str(v) for v in gov_module.WINDOW_TARGET_BAG)} "
          f"(+/-{gov_module.WINDOW_TARGET_JITTER}), a quiet one in every "
          f"{gov_module.DEAL_SPACING}")
    print(f"  profile pool : {', '.join(engine.MODE_POOL)}")
    print(f"  clicking     : {'on' if engine.ALLOW_CLICK else 'off'}")

    percentages, blocks, _ = simulate_run(random.Random(args.seed))
    report_single(percentages, blocks, quiet=args.quiet)

    data = report_trials(args.trials, args.seed, "engine alone")
    averages, worsts, rollings = data["averages"], data["worsts"], data["rollings"]

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
    span = gov_module.VARIANCE_WINDOW_COUNT
    quiet_rate = (sum(1 for p in data["windows"] if p < gov_module.QUIET_WINDOW_PERCENT)
                  / len(data["windows"]))
    checks = [
        (f"no window above {gov_module.CEILING_PERCENT:g}%",
         max(worsts) <= gov_module.CEILING_PERCENT,
         f"worst {max(worsts):.1f}%"),
        (f"no rolling window above {gov_module.CEILING_PERCENT:g}%",
         max(rollings) <= gov_module.CEILING_PERCENT,
         f"worst {max(rollings):.1f}%"),
        (f"every run inside {gov_module.BAND_LOW_PERCENT:g}-{gov_module.BAND_HIGH_PERCENT:g}%",
         all(gov_module.BAND_LOW_PERCENT <= a <= gov_module.BAND_HIGH_PERCENT for a in averages),
         f"{min(averages):.2f}% .. {max(averages):.2f}%, mean {overall:.2f}%"),
        (f"every {span * 10} min varies by >= {gov_module.VARIANCE_MIN_RANGE_PERCENT:g} pts",
         min(data["ranges"]) >= gov_module.VARIANCE_MIN_RANGE_PERCENT,
         f"worst {min(data['ranges']):.1f} pts"),
        (f"...and by >= {gov_module.VARIANCE_MIN_STDEV_PERCENT:g} pts of sd",
         min(data["stdevs"]) >= gov_module.VARIANCE_MIN_STDEV_PERCENT,
         f"worst {min(data['stdevs']):.1f} pts"),
        (f"quiet windows (<{gov_module.QUIET_WINDOW_PERCENT:g}%) are normal",
         quiet_rate >= gov_module.QUIET_WINDOW_MIN_RATE,
         f"{quiet_rate * 100:.1f}% of windows"),
        (f"neighbours differ by >= {gov_module.MIN_ADJACENT_DELTA_PERCENT:.1f} pts",
         (below_adjacent_floor(data["adjacent"]) / len(data["adjacent"])
          <= gov_module.ADJACENT_BELOW_FLOOR_MAX_RATE),
         f"{below_adjacent_floor(data['adjacent']) / len(data['adjacent']) * 100:.2f}% under, "
         f"median {statistics.median(data['adjacent']):.1f} pts"),
        ("every bag obeyed the spacing rules",
         data["fallbacks"] == 0,
         f"{data['fallbacks']} fell back"),
    ]

    print("\n--- VERDICT ---")
    width = max(len(name) for name, _, _ in checks)
    for name, ok, detail in checks:
        print(f"  {name:<{width}} : {'PASS' if ok else 'FAIL'}   ({detail})")
    passed = all(ok for _, ok, _ in checks)
    print(f"\nSTATUS: {'PASS' if passed else 'FAIL — retune WINDOW_TARGET_QUIET / _BUSY in engine/governor.py'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
