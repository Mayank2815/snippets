import time
import random
import math
import os
import sys

# Ensure engine module can be imported and isolated from persistent state
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["ENGINE_BACKEND"] = "fake"
os.environ["UNIT_TEST"] = "1"
from engine import CadenceController

print("=== HUBSTAFF ALGORITHM SIMULATOR (ADAPTIVE CADENCE ANALYSIS) ===")
print("Simulating 1-Hour (6 Windows) and 8-Hour Full Workday (48 Windows)...\n")

controller = CadenceController(target_overall=CadenceController.random_session_target())
controller.window_history.clear()
controller.hour_queue.clear()
res1 = controller.run_activity_verification(num_windows=6)

print(f"--- 1-HOUR (60-MINUTE) DETAILED TELEMETRY (Session Target: {controller.target_overall:.1f}%) ---")
for w in res1["windows"]:
    i = w["window"]
    suffix = "st" if i == 1 else "nd" if i == 2 else "rd" if i == 3 else "th"
    print(f"{i}{suffix}: 10 minutes reached : {w['score']:.1f}% (Target: {w['target']:.1f}% | Active: {w['activeSeconds']}s/600s)")

print(f"\n1-Hour Average : {res1['average']:.2f}% | Range: {res1['minScore']:.1f}% - {res1['maxScore']:.1f}%")
assert res1["minScore"] <= 28.5, f"Expected 1-hour minimum score <= 28.5%, got {res1['minScore']:.1f}%"
assert not res1["hasDuplicates"], "Found duplicate numbers in 1-hour window sequence!"

# 8-Hour Full Workday Simulation (48 Windows)
sim8 = CadenceController(target_overall=CadenceController.random_session_target())
sim8.window_history.clear()
sim8.hour_queue.clear()

print(f"\n--- 8-HOUR FULL SESSION SIMULATION (48 WINDOWS | Session Target: {sim8.target_overall:.1f}%) ---")
hourly_avgs = []
hours_without_low = 0
for h in range(1, 9):
    h_scores = []
    for _ in range(6):
        t = sim8._generate_next_target()
        tl = sim8.generate_window_timeline(t, 600)
        act = sum(dur for typ, dur in tl if typ == 'ACTIVE')
        tot = sum(dur for _, dur in tl)
        sc = round((act / tot) * 100.0, 1)
        sim8.window_history.append(sc)
        h_scores.append(sc)
    h_avg = round(sum(h_scores) / 6.0, 2)
    hourly_avgs.append(h_avg)
    cum_avg = round(sum(sim8.window_history) / len(sim8.window_history), 2)
    win_str = ', '.join([f'{s:.1f}%' for s in h_scores])
    if min(h_scores) > 18.0:
        hours_without_low += 1
    print(f"Hour {h}: [{win_str}] -> Hourly: {h_avg:.2f}% | Running Avg: {cum_avg:.2f}%")

final_8h_avg = round(sum(sim8.window_history) / 48.0, 2)
print(f"\nFINAL 8-HOUR CUMULATIVE AVERAGE : {final_8h_avg:.2f}% (Target Band: 35.0% - 45.0%)")
print(f"Hourly Averages Range: {min(hourly_avgs):.1f}% - {max(hourly_avgs):.1f}%")
print(f"8-Hour Global Min Window: {min(sim8.window_history):.1f}% | Global Max Window: {max(sim8.window_history):.1f}%")
print(f"Hours in Steady Flow (min in 20s): {hours_without_low} / 8 (Expected ~1-2 hours/day)")
assert 0 <= hours_without_low <= 4, f"Found {hours_without_low} hour(s) missing lower range windows <= 18% (expected 0-4)!"
assert min(sim8.window_history) <= 18.0, "8-hour session missing low percentage window!"
assert max(sim8.window_history) >= 55.0, "8-hour session missing high peak burst!"

if 35.0 <= final_8h_avg <= 45.0:
    print("STATUS: ✅ PERFECTLY OPTIMIZED. NATURAL HUMAN VARIANCE WITH 35%-45% AVERAGE.")
else:
    print(f"STATUS: ⚠️ AVERAGE DRIFT ({final_8h_avg:.2f}%). CHECK PARAMETERS.")

# Multi-Day Variance & Zero-Pattern Verification (10 Simulated Workdays)
print("\n--- MULTI-DAY STATISTICAL VERIFICATION (10 WORKDAYS / 480 WINDOWS) ---")
all_10_days_windows = []
hourly_dupe_count = 0
day_averages = []
total_hours_without_low = 0

for d in range(1, 11):
    day_ctrl = CadenceController(target_overall=CadenceController.random_session_target())
    day_ctrl.window_history.clear()
    day_ctrl.hour_queue.clear()
    for h in range(8):
        h_scores = []
        for _ in range(6):
            t = day_ctrl._generate_next_target()
            tl = day_ctrl.generate_window_timeline(t, 600)
            act = sum(dur for typ, dur in tl if typ == 'ACTIVE')
            tot = sum(dur for _, dur in tl)
            sc = round((act / tot) * 100.0, 1)
            day_ctrl.window_history.append(sc)
            h_scores.append(sc)
        rounded_h = [round(s) for s in h_scores]
        if len(rounded_h) != len(set(rounded_h)):
            hourly_dupe_count += 1
        if min(h_scores) > 18.0:
            total_hours_without_low += 1
        if h_scores[0] <= 22.0:
            total_hours_starting_low = getattr(day_ctrl, '_total_start_low', 0) + 1
            setattr(day_ctrl, '_total_start_low', total_hours_starting_low)
    d_avg = round(sum(day_ctrl.window_history) / 48.0, 2)
    day_averages.append(d_avg)
    all_10_days_windows.extend(day_ctrl.window_history)
    print(f"Workday #{d:2d}: Target {day_ctrl.target_overall:.1f}% -> 8-Hour Cumulative Total: {d_avg:.2f}% (Windows: {min(day_ctrl.window_history):.1f}% - {max(day_ctrl.window_history):.1f}%)")

total_start_low = sum(1 for i in range(0, len(all_10_days_windows), 6) if all_10_days_windows[i] <= 22.0)
print("\n--- 10-DAY PATTERN AUDIT ---")
print(f"Hours with duplicate rounded integer scores: {hourly_dupe_count} / 80 (Target: 0)")
print(f"Hours starting with low window (<= 22.0%): {total_start_low} / 80 ({total_start_low/80*100:.1f}%) [Expected ~20-30 hrs]")
print(f"Hours with Steady Flow (min in 20s): {total_hours_without_low} / 80 ({total_hours_without_low/80*100:.1f}%) [Expected ~10-25 hrs]")
print(f"Hours with Deep Low (<= 18.0%): {80 - total_hours_without_low} / 80 ({(80 - total_hours_without_low)/80*100:.1f}%) [Expected ~55-70 hrs]")
low_count = sum(1 for a in day_averages if a <= 40.0)
high_count = sum(1 for a in day_averages if a > 42.0)
print(f"Workdays in lower band (<= 40.0%): {low_count} / 10 ({low_count*10}%) [Expected ~80%]")
print(f"Workdays in sprint band (> 42.0%): {high_count} / 10 ({high_count*10}%) [Expected ~20%]")
assert hourly_dupe_count == 0, f"Detected {hourly_dupe_count} duplicate hours!"
assert 10 <= total_start_low <= 40, f"Unexpected count of hours starting low: {total_start_low}"
assert 7 <= total_hours_without_low <= 32, f"Unexpected steady flow count: {total_hours_without_low}"
assert all(35.0 <= a <= 45.0 for a in day_averages), "Day averages out of [35.0%, 45.0%] bounds!"
print("STATUS: ✅ 10-DAY AUDIT PASSED: ZERO PATTERNS, STRICT UNIQUENESS, NATURAL DISTRIBUTION.")


