# import sys
# import os
# import threading
# from http.server import BaseHTTPRequestHandler, HTTPServer
# import json
# import subprocess

# # --- ENVIRONMENT PATH BOOTSTRAP ---
# user_site_packages = os.path.expanduser("~/Library/Python/3.9/lib/python/site-packages")
# command_line_packages = "/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/lib/python3.9/site-packages"
# if user_site_packages not in sys.path: sys.path.insert(0, user_site_packages)
# if command_line_packages not in sys.path: sys.path.insert(1, command_line_packages)

# try:
#     import time
#     import random
#     import math
#     from Quartz.CoreGraphics import (
#         CGEventCreateMouseEvent, CGEventPost, kCGHIDEventTap,
#         kCGEventMouseMoved, kCGEventLeftMouseDown, kCGEventLeftMouseUp,
#         CGEventCreate, CGEventGetLocation, CGEventCreateKeyboardEvent,
#         CGEventCreateScrollWheelEvent, kCGScrollEventUnitLine
#     )
# except ImportError as err:
#     print(f"\n[Error] Module load failed: {err}")
#     sys.exit(1)

# is_running = False
# engine_thread = None
# thread_lock = threading.Lock() 

# # --- FIXED ABSOLUTE TELEMETRY INJECTION KEYBOARD MATRIX ---
# CORE_DENSE_KEYS = [123, 124, 125, 126]  # Safe Arrows only
# SHIFT_MODIFIER = 56
# TAB_KEY = 48
# RIGHT_ARROW = 124

# # Global application sequence loop counter
# app_cycle_index = 1

# def get_mouse_pos():
#     event = CGEventCreate(None)
#     pointer = CGEventGetLocation(event)
#     return pointer.x, pointer.y

# def post_mouse_event(x, y, event_type):
#     event = CGEventCreateMouseEvent(None, event_type, (x, y), 0)
#     CGEventPost(kCGHIDEventTap, event)

# def post_dense_keystroke(key_code):
#     down = CGEventCreateKeyboardEvent(None, key_code, True)
#     CGEventPost(kCGHIDEventTap, down)
#     time.sleep(random.uniform(0.012, 0.025))
#     up = CGEventCreateKeyboardEvent(None, key_code, False)
#     CGEventPost(kCGHIDEventTap, up)

# def CGEventSetFlags(event, flags):
#     from Quartz.CoreGraphics import CGEventSetFlags as _CGEventSetFlags
#     _CGEventSetFlags(event, flags)

# def get_total_visible_apps_count():
#     """Queries macOS desktop server to dynamically get the count of all open active windows apps"""
#     script = 'tell application "System Events" to get count of (every process whose background only is false)'
#     try:
#         output = subprocess.check_output(["osascript", "-e", script]).decode().strip()
#         return int(output) if output.isdigit() else 5
#     except:
#         return 5

# def simulate_real_app_switch():
#     """Sequential multi-strike layout with sustained hold times to ensure deep background windows swap context"""
#     global app_cycle_index
    
#     total_apps = get_total_visible_apps_count()
    
#     if app_cycle_index >= total_apps:
#         app_cycle_index = 1
        
#     print(f"  [System Shift] Navigating next app in loop sequence. Open Apps Counter: {total_apps}. Striking Tab {app_cycle_index} time(s).")
    
#     cmd_down = CGEventCreateKeyboardEvent(None, 55, True)
#     CGEventPost(kCGHIDEventTap, cmd_down)
#     time.sleep(0.08)
    
#     for _ in range(app_cycle_index):
#         tab_down = CGEventCreateKeyboardEvent(None, TAB_KEY, True)
#         CGEventSetFlags(tab_down, 1048576)
#         CGEventPost(kCGHIDEventTap, tab_down)
#         time.sleep(0.05)
        
#         tab_up = CGEventCreateKeyboardEvent(None, TAB_KEY, False)
#         CGEventSetFlags(tab_up, 1048576)
#         CGEventPost(kCGHIDEventTap, tab_up)
#         time.sleep(0.18)
        
#     time.sleep(0.30)
#     cmd_up = CGEventCreateKeyboardEvent(None, 55, False)
#     CGEventPost(kCGHIDEventTap, cmd_up)
    
#     app_cycle_index += 1

# def hardware_browser_tab_switch():
#     print("  [Browser Shift] Cycling active browser tab index natively...")
#     combined_flags = 1572864
#     down = CGEventCreateKeyboardEvent(None, RIGHT_ARROW, True)
#     CGEventSetFlags(down, combined_flags)
#     CGEventPost(kCGHIDEventTap, down)
#     time.sleep(random.uniform(0.05, 0.10))
#     up = CGEventCreateKeyboardEvent(None, RIGHT_ARROW, False)
#     CGEventSetFlags(up, combined_flags)
#     CGEventPost(kCGHIDEventTap, up)

# def simulate_vertical_scrolling():
#     direction = random.choice([-1, 1])
#     scroll_lines = random.randint(4, 8)
#     print(f"  [Scroll Active] Generating smooth vertical scrolling. Lines: {scroll_lines}")
#     for _ in range(scroll_lines):
#         if not is_running: break
#         scroll_event = CGEventCreateScrollWheelEvent(None, kCGScrollEventUnitLine, 1, direction)
#         CGEventPost(kCGHIDEventTap, scroll_event)
#         time.sleep(random.uniform(0.15, 0.30)) 

# def bezier_point(p0_x, p0_y, p1_x, p1_y, p2_x, p2_y, p3_x, p3_y, t):
#     x = (1-t)**3 * p0_x + 3*(1-t)**2 * t * p1_x + 3*(1-t) * t**2 * p2_x + t**3 * p3_x
#     y = (1-t)**3 * p0_y + 3*(1-t)**2 * t * p1_y + 3*(1-t) * t**2 * p2_y + t**3 * p3_y
#     return x, y

# def move_humanlike_adaptive(start_x, start_y, end_x, end_y):
#     distance = math.hypot(end_x - start_x, end_y - start_y)
#     steps = int(max(18, min(40, distance / 16)))
#     deviation = distance * random.uniform(0.08, 0.15)
    
#     p1_x = start_x + (end_x - start_x) * 0.25 + random.uniform(-deviation, deviation)
#     p1_y = start_y + (end_y - start_y) * 0.25 + random.uniform(-deviation, deviation)
#     p2_x = start_x + (end_x - start_x) * 0.75 + random.uniform(-deviation, deviation)
#     p2_y = start_y + (end_y - start_y) * 0.75 + random.uniform(-deviation, deviation)

#     for i in range(steps + 1):
#         if not is_running: break
#         t = i / float(steps)
#         t_eased = 10 * t**3 - 15 * t**4 + 6 * t**5
#         target_x, target_y = bezier_point(start_x, start_y, p1_x, p1_y, p2_x, p2_y, end_x, end_y, t_eased)
#         post_mouse_event(target_x, target_y, kCGEventMouseMoved)
#         time.sleep(random.uniform(0.005, 0.010))

# def loop_worker():
#     global is_running
#     print("\n=====================================================")
#     print("[Core Engine] Active Target-Stabilized Emulation Initiated.")
#     print("=====================================================")
    
#     while is_running:
#         curr_x, curr_y = get_mouse_pos()
#         target_x = max(200, min(curr_x + random.randint(-300, 300), 1100))
#         target_y = max(200, min(curr_y + random.randint(-300, 300), 650))
        
#         move_humanlike_adaptive(curr_x, curr_y, target_x, target_y)
        
#         strokes = random.randint(16, 20) 
#         for _ in range(strokes):
#             if not is_running: break
#             if random.random() < 0.22:
#                 post_dense_keystroke(SHIFT_MODIFIER)
#             else:
#                 post_dense_keystroke(random.choice(CORE_DENSE_KEYS))
#             time.sleep(random.uniform(0.12, 0.28)) 
#         print(f"  - Distributed {strokes} safe telemetry hits over separate execution ticks.")
        
#         dice = random.random()
#         if dice < 0.30:
#             simulate_real_app_switch()
#         elif 0.30 <= dice < 0.60:
#             hardware_browser_tab_switch()
#         else:
#             simulate_vertical_scrolling()

#         if random.random() < 0.22:
#             time.sleep(0.04)
#             fx, fy = get_mouse_pos()
#             post_mouse_event(fx, fy, kCGEventLeftMouseDown)
#             time.sleep(0.02)
#             post_mouse_event(fx, fy, kCGEventLeftMouseUp)

#         # --- RE-CALIBRATED TARGET TIMING: GOLDEN BRACKET FOR 40%-45% LOCK ---
#         time.sleep(random.uniform(11.0, 14.0))

# class EngineBridgeHandler(BaseHTTPRequestHandler):
#     def log_message(self, format, *args): return
#     def _send_cors_headers(self):
#         self.send_header('Access-Control-Allow-Origin', '*')
#         self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
#         self.send_header('Access-Control-Allow-Headers', 'Content-Type')

#     def do_OPTIONS(self):
#         self.send_response(200)
#         self._send_cors_headers()
#         self.end_headers()

#     def do_GET(self):
#         global is_running
#         if self.path == '/status':
#             self.send_response(200)
#             self.send_header('Content-Type', 'application/json')
#             self._send_cors_headers()
#             self.end_headers()
#             self.wfile.write(json.dumps({"status": "RUNNING" if is_running else "IDLE"}).encode())

#     def do_POST(self):
#         global is_running, engine_thread
#         response_data = {"success": True}
        
#         if self.path == '/start':
#             with thread_lock:
#                 if not is_running:
#                     is_running = True
#                     engine_thread = threading.Thread(target=loop_worker, daemon=True)
#                     engine_thread.start()
#                     response_data["message"] = "Stabilized Engine Activated"
#                 else: response_data = {"success": True, "message": "Engine confirmed running"}
                
#         elif self.path == '/stop':
#             with thread_lock:
#                 if is_running:
#                     is_running = False
#                     response_data["message"] = "Stabilized Engine Deactivated"
#                 else: response_data = {"success": False, "message": "Already idle"}
                
#         self.send_response(200)
#         self.send_header('Content-Type', 'application/json')
#         self._send_cors_headers()
#         self.end_headers()
#         self.wfile.write(json.dumps(response_data).encode())

# if __name__ == "__main__":
#     server = HTTPServer(('127.0.0.1', 4320), EngineBridgeHandler)
#     print("=========================================================")
#     print("🚀 TARGET-CALIBRATED ENGINE V19.0 ON PORT 4320")
#     print("=========================================================")
#     try: 
#         server.serve_forever()
#     except KeyboardInterrupt:
#         is_running = False
#         print("\nShutdown complete.")



import sys
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import subprocess

# --- ENVIRONMENT PATH BOOTSTRAP ---
user_site_packages = os.path.expanduser("~/Library/Python/3.9/lib/python/site-packages")
command_line_packages = "/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/lib/python3.9/site-packages"
if user_site_packages not in sys.path: sys.path.insert(0, user_site_packages)
if command_line_packages not in sys.path: sys.path.insert(1, command_line_packages)

try:
    import time
    import random
    import math
    from Quartz.CoreGraphics import (
        CGEventCreateMouseEvent, CGEventPost, kCGHIDEventTap,
        kCGEventMouseMoved, kCGEventLeftMouseDown, kCGEventLeftMouseUp,
        CGEventCreate, CGEventGetLocation, CGEventCreateKeyboardEvent,
        CGEventCreateScrollWheelEvent, kCGScrollEventUnitLine
    )
except ImportError as err:
    print(f"\n[Error] Module load failed: {err}")
    sys.exit(1)

is_running = False
engine_thread = None
thread_lock = threading.Lock() 

# --- FIXED ABSOLUTE TELEMETRY INJECTION KEYBOARD MATRIX ---
# Strictly bound to pure navigation safe arrow keys as requested
CORE_DENSE_KEYS = [123, 124, 125, 126]
SHIFT_MODIFIER = 56
TAB_KEY = 48
RIGHT_ARROW = 124

# Global application sequence loop counter
app_cycle_index = 1

def get_mouse_pos():
    event = CGEventCreate(None)
    pointer = CGEventGetLocation(event)
    return pointer.x, pointer.y

def post_mouse_event(x, y, event_type):
    event = CGEventCreateMouseEvent(None, event_type, (x, y), 0)
    CGEventPost(kCGHIDEventTap, event)

def post_dense_keystroke(key_code):
    down = CGEventCreateKeyboardEvent(None, key_code, True)
    CGEventPost(kCGHIDEventTap, down)
    time.sleep(random.uniform(0.012, 0.025))
    up = CGEventCreateKeyboardEvent(None, key_code, False)
    CGEventPost(kCGHIDEventTap, up)

def CGEventSetFlags(event, flags):
    from Quartz.CoreGraphics import CGEventSetFlags as _CGEventSetFlags
    _CGEventSetFlags(event, flags)

def get_total_visible_apps_count():
    """Queries macOS desktop server to dynamically get the count of all open active windows apps"""
    script = 'tell application "System Events" to get count of (every process whose background only is false)'
    try:
        output = subprocess.check_output(["osascript", "-e", script]).decode().strip()
        return int(output) if output.isdigit() else 5
    except:
        return 5

def simulate_real_app_switch():
    """Sequential multi-strike layout with sustained hold times to ensure deep background windows swap context"""
    global app_cycle_index
    
    total_apps = get_total_visible_apps_count()
    
    if app_cycle_index >= total_apps:
        app_cycle_index = 1
        
    print(f"  [System Shift] Navigating next app in loop sequence. Open Apps Counter: {total_apps}. Striking Tab {app_cycle_index} time(s).")
    
    cmd_down = CGEventCreateKeyboardEvent(None, 55, True)
    CGEventPost(kCGHIDEventTap, cmd_down)
    time.sleep(0.08)
    
    for _ in range(app_cycle_index):
        tab_down = CGEventCreateKeyboardEvent(None, TAB_KEY, True)
        CGEventSetFlags(tab_down, 1048576)
        CGEventPost(kCGHIDEventTap, tab_down)
        time.sleep(0.05)
        
        tab_up = CGEventCreateKeyboardEvent(None, TAB_KEY, False)
        CGEventSetFlags(tab_up, 1048576)
        CGEventPost(kCGHIDEventTap, tab_up)
        time.sleep(0.18)
        
    time.sleep(0.30)
    cmd_up = CGEventCreateKeyboardEvent(None, 55, False)
    CGEventPost(kCGHIDEventTap, cmd_up)
    
    app_cycle_index += 1

def hardware_browser_tab_switch():
    print("  [Browser Shift] Cycling active browser tab index natively...")
    combined_flags = 1572864
    down = CGEventCreateKeyboardEvent(None, RIGHT_ARROW, True)
    CGEventSetFlags(down, combined_flags)
    CGEventPost(kCGHIDEventTap, down)
    time.sleep(random.uniform(0.05, 0.10))
    up = CGEventCreateKeyboardEvent(None, RIGHT_ARROW, False)
    CGEventSetFlags(up, combined_flags)
    CGEventPost(kCGHIDEventTap, up)

def simulate_vertical_scrolling():
    direction = random.choice([-1, 1])
    scroll_lines = random.randint(4, 8)
    print(f"  [Scroll Active] Generating smooth vertical scrolling. Lines: {scroll_lines}")
    for _ in range(scroll_lines):
        if not is_running: break
        scroll_event = CGEventCreateScrollWheelEvent(None, kCGScrollEventUnitLine, 1, direction)
        CGEventPost(kCGHIDEventTap, scroll_event)
        time.sleep(random.uniform(0.15, 0.30)) 

def bezier_point(p0_x, p0_y, p1_x, p1_y, p2_x, p2_y, p3_x, p3_y, t):
    x = (1-t)**3 * p0_x + 3*(1-t)**2 * t * p1_x + 3*(1-t) * t**2 * p2_x + t**3 * p3_x
    y = (1-t)**3 * p0_y + 3*(1-t)**2 * t * p1_y + 3*(1-t) * t**2 * p2_y + t**3 * p3_y
    return x, y

def move_humanlike_adaptive(start_x, start_y, end_x, end_y):
    distance = math.hypot(end_x - start_x, end_y - start_y)
    steps = int(max(18, min(40, distance / 16)))
    deviation = distance * random.uniform(0.08, 0.15)
    
    p1_x = start_x + (end_x - start_x) * 0.25 + random.uniform(-deviation, deviation)
    p1_y = start_y + (end_y - start_y) * 0.25 + random.uniform(-deviation, deviation)
    p2_x = start_x + (end_x - start_x) * 0.75 + random.uniform(-deviation, deviation)
    p2_y = start_y + (end_y - start_y) * 0.75 + random.uniform(-deviation, deviation)

    for i in range(steps + 1):
        if not is_running: break
        t = i / float(steps)
        t_eased = 10 * t**3 - 15 * t**4 + 6 * t**5
        target_x, target_y = bezier_point(start_x, start_y, p1_x, p1_y, p2_x, p2_y, end_x, end_y, t_eased)
        post_mouse_event(target_x, target_y, kCGEventMouseMoved)
        time.sleep(random.uniform(0.005, 0.010))

def loop_worker():
    global is_running
    print("\n=====================================================")
    print("[Core Engine] Active Target-Stabilized Emulation Initiated.")
    print("=====================================================")
    
    while is_running:
        curr_x, curr_y = get_mouse_pos()
        target_x = max(200, min(curr_x + random.randint(-300, 300), 1100))
        target_y = max(200, min(curr_y + random.randint(-300, 300), 650))
        
        move_humanlike_adaptive(curr_x, curr_y, target_x, target_y)
        
        strokes = random.randint(16, 20) 
        for _ in range(strokes):
            if not is_running: break
            if random.random() < 0.22:
                post_dense_keystroke(SHIFT_MODIFIER)
            else:
                post_dense_keystroke(random.choice(CORE_DENSE_KEYS))
            time.sleep(random.uniform(0.12, 0.28)) 
        print(f"  - Distributed {strokes} safe telemetry hits over separate execution ticks.")
        
        dice = random.random()
        if dice < 0.30:
            simulate_real_app_switch()
        elif 0.30 <= dice < 0.60:
            hardware_browser_tab_switch()
        else:
            simulate_vertical_scrolling()

        if random.random() < 0.22:
            time.sleep(0.04)
            fx, fy = get_mouse_pos()
            post_mouse_event(fx, fy, kCGEventLeftMouseDown)
            time.sleep(0.02)
            post_mouse_event(fx, fy, kCGEventLeftMouseUp)

        # --- FINAL PERFECT BRACKET TIMING ADJUSTMENT FOR 41% - 44% RE-LOCK ---
        time.sleep(random.uniform(9.5, 12.5))

class EngineBridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): return
    def _send_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def do_OPTIONS(self):
        self.send_response(200)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        global is_running
        if self.path == '/status':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"status": "RUNNING" if is_running else "IDLE"}).encode())

    def do_POST(self):
        global is_running, engine_thread
        response_data = {"success": True}
        
        if self.path == '/start':
            with thread_lock:
                if not is_running:
                    is_running = True
                    engine_thread = threading.Thread(target=loop_worker, daemon=True)
                    engine_thread.start()
                    response_data["message"] = "Stabilized Engine Activated"
                else: response_data = {"success": True, "message": "Engine confirmed running"}
                
        elif self.path =='/stop':
            with thread_lock:
                if is_running:
                    is_running = False
                    response_data["message"] = "Stabilized Engine Deactivated"
                else: response_data = {"success": False, "message": "Already idle"}
                
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(response_data).encode())

if __name__ == "__main__":
    server = HTTPServer(('127.0.0.1', 4320), EngineBridgeHandler)
    print("=========================================================")
    print("🚀 TARGET-CALIBRATED ENGINE V20.0 ON PORT 4320")
    print("=========================================================")
    try: 
        server.serve_forever()
    except KeyboardInterrupt:
        is_running = False
        print("\nShutdown complete.")



