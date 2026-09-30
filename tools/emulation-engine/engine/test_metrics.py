import time
import random
import math

print("=== HUBSTAFF ALGORITHM SIMULATOR (10-MINUTE ANALYSIS) ===")
print("Analyzing script configuration without engaging live tracker...")

total_blocks = 60  # 10 minutes contain 60 blocks of 10 seconds each
active_blocks = 0

# Simulate the tracker's 10-minute (600-second) time frame
current_time = 0
next_action_time = 0

for block in range(total_blocks):
    block_start = block * 10
    block_end = block_start + 10

    # Check whether one of the script's actions lands inside this 10-second block
    block_triggered = False

    while current_time < block_end:
        if current_time >= next_action_time:
            block_triggered = True
            # Script timing model: one action burst, then a sleep before the next one
            action_duration = random.uniform(2.0, 2.5)
            sleep_duration = random.uniform(14.0, 24.0)

            next_action_time = current_time + action_duration + sleep_duration

        current_time += 0.1 # Advance the simulated clock by 0.1 s

    if block_triggered:
        active_blocks += 1

# Final score calculation
calculated_percentage = (active_blocks / total_blocks) * 100

print("\n--- SIMULATION RESULTS ---")
print(f"Total Monitored Windows: {total_blocks} slots (10 minutes)")
print(f"Captured Active Slots  : {active_blocks} slots")
print(f"Predicted Activity Score: {calculated_percentage:.2f}%")

if 40 <= calculated_percentage <= 55:
    print("STATUS: ✅ PERFECTLY OPTIMIZED. SAFE FOR PRODUCTION WORK.")
else:
    print("STATUS: ⚠️ OUT OF BOUNDS. RE-CALIBRATION REQUIRED.")
