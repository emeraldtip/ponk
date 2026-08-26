"""
8-Player Pong
=============
An eight-player Pong variant for Windows 10 (also runs on macOS/Linux).
Four players control the paddles on the four sides. Four more players
tug the center rectangle around the arena, tug-of-war style, nudging it
into the ball's path to deflect shots. Supports up to 8 USB/Xbox/
PlayStation-style controllers, with keyboard fallback for any player
without a controller connected.

Run:
    python pong8p.py

Requires:
    pip install pygame

Controls (defaults, all rebindable to controllers in the in-game menu):
    Paddle players (default: keyboard):
      P1 (Top)          - Arrow keys (Left/Right/Up/Down)
      P2 (Right)        - W A S D
      P3 (Bottom)       - I J K L   (I=up, K=down, J=left, L=right)
      P4 (Left)         - T F G H  (T=up, G=down, F=left, H=right)

    Tug-of-war players (default: AI; hold key, or assign a controller):
      P5 (Top-Left)     - 1
      P6 (Top-Right)    - 2
      P7 (Bottom-Left)  - 3
      P8 (Bottom-Right) - 4

    In the menu, every one of the 8 player slots can be set independently
    to AI, Keyboard, or any connected controller (steering wheels included -
    tug strength follows how far the wheel is turned off-center).

    Menu / Pause      - ESC or Start/Options button on a controller
    Menu navigate     - Up/Down, change value Left/Right, click, or scroll
"""

import json
import math
import os
import random
import sys
from collections import deque
import pygame

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

FPS = 60

# The game is always fullscreen at the desktop's native resolution, so every
# size below is derived at runtime by configure_display() rather than fixed.
# The values here are placeholders (overwritten immediately) so the module
# is still importable/testable before a real display exists.
WIDTH, HEIGHT = 1920, 1080
CX, CY = WIDTH // 2, HEIGHT // 2

ARENA_W, ARENA_H = WIDTH, HEIGHT   # play field spans the full screen (minus a hairline margin)
CORNER_CUT = 70                    # size of the diagonal corner walls

X_L = CX - ARENA_W // 2
X_R = CX + ARENA_W // 2
Y_T = CY - ARENA_H // 2
Y_B = CY + ARENA_H // 2

PADDLE_THICK = 16
PADDLE_LENGTHS = {"small": 90, "large": 170}
# Paddles (and tug players) are positional, not speed-based: the control
# input (key held / stick or wheel position) maps directly to where the
# paddle sits along its track, like a wheel/lever rather than a throttle.

BALL_RADIUS = 10
BALL_START_SPEED = 380.0
BALL_MAX_SPEED = 900.0
BALL_SPEEDUP = 1.035                 # multiplier applied on every paddle hit

SCOREBOX_W, SCOREBOX_H = 190, 150   # smaller, physical center panel

# Ball trail & bounce-particle effect settings
TRAIL_MAX_CAP = 60                  # deque capacity; menu can select up to this many
PARTICLE_LEVELS = ["off", "low", "medium", "high"]
PARTICLE_LEVEL_COUNTS = {"off": 0, "low": 6, "medium": 14, "high": 24}

# Ball color presets, cyclable from the menu
BALL_COLOR_NAMES = ["White", "Red", "Orange", "Yellow", "Green", "Cyan", "Blue", "Magenta"]
BALL_COLORS = {
    "White":   (235, 235, 240),
    "Red":     (255, 99, 99),
    "Orange":  (255, 160, 80),
    "Yellow":  (255, 214, 99),
    "Green":   (140, 255, 140),
    "Cyan":    (120, 230, 230),
    "Blue":    (99, 190, 255),
    "Magenta": (230, 120, 230),
}

TUG_MAX_OFFSET = 110                 # baseline max distance the box can be pulled off-center

OOB_MARGIN = 260                     # baseline: how far past the arena edge the ball can go
                                      # before the game treats it as stuck/escaped and restarts

TOP, RIGHT, BOTTOM, LEFT = "TOP", "RIGHT", "BOTTOM", "LEFT"
SIDES = [TOP, RIGHT, BOTTOM, LEFT]

# Tug-of-war players: they don't control a paddle, they pull the center
# scorebox rectangle toward one of the four corners.
TL, TR, BL, BR = "TL", "TR", "BL", "BR"
TUG_SIDES = [TL, TR, BL, BR]
TUG_DIRECTIONS = {
    TL: (-0.7071067811865476, -0.7071067811865476),
    TR: (0.7071067811865476, -0.7071067811865476),
    BL: (-0.7071067811865476, 0.7071067811865476),
    BR: (0.7071067811865476, 0.7071067811865476),
}
TUG_LABELS = {TL: "P5", TR: "P6", BL: "P7", BR: "P8"}
TUG_NAMES = {TL: "Top-Left", TR: "Top-Right", BL: "Bottom-Left", BR: "Bottom-Right"}


def resource_path(filename):
    """Resolve a bundled asset (sound, etc.) next to this script, so it's
    found regardless of the working directory the game was launched from."""
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, filename)


class Sounds:
    """Loads and plays the game's sound effects. If the audio device or any
    sound file is unavailable, sound is silently disabled and the game
    keeps running without it."""

    def __init__(self):
        self.enabled = False
        self.goal_sound = None
        self.ping_sound = None
        self.pong_sound = None
        self._next_is_ping = True  # alternates ping/pong on each collision

        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init()
            self.goal_sound = pygame.mixer.Sound(resource_path("goal.mp3"))
            self.ping_sound = pygame.mixer.Sound(resource_path("ping.mp3"))
            self.pong_sound = pygame.mixer.Sound(resource_path("pong.mp3"))
            self.enabled = True
        except (pygame.error, FileNotFoundError, OSError) as e:
            print(f"[sound] disabled - couldn't load sound files: {e}")

    def play_goal(self):
        if self.enabled and self.goal_sound is not None:
            self.goal_sound.play()

    def play_collision(self):
        """Alternates between ping.mp3 and pong.mp3 on every call."""
        if not self.enabled:
            return
        snd = self.ping_sound if self._next_is_ping else self.pong_sound
        self._next_is_ping = not self._next_is_ping
        if snd is not None:
            snd.play()


def configure_display(width, height):
    """Recompute every size-dependent constant for the given screen
    resolution, so the arena always fills the entire display with no
    unused border area. Called once at import (placeholder 1920x1080)
    and again in main() with the real detected fullscreen resolution."""
    global WIDTH, HEIGHT, CX, CY, ARENA_W, ARENA_H, X_L, X_R, Y_T, Y_B
    global CORNER_CUT, PADDLE_THICK, PADDLE_LENGTHS
    global BALL_RADIUS, BALL_START_SPEED, BALL_MAX_SPEED
    global SCOREBOX_W, SCOREBOX_H
    global TUG_MAX_OFFSET, OOB_MARGIN

    WIDTH, HEIGHT = width, height
    CX, CY = WIDTH // 2, HEIGHT // 2

    margin = 12  # hairline breathing room only - practically edge-to-edge
    ARENA_W = WIDTH - margin * 2
    ARENA_H = HEIGHT - margin * 2
    X_L = CX - ARENA_W // 2
    X_R = CX + ARENA_W // 2
    Y_T = CY - ARENA_H // 2
    Y_B = CY + ARENA_H // 2

    # scale gameplay metrics relative to a 640px baseline (the original
    # design resolution) so proportions & feel stay consistent at any size
    s = min(ARENA_W, ARENA_H) / 640.0

    CORNER_CUT = max(36, round(70 * s))
    PADDLE_THICK = max(10, round(16 * s))
    PADDLE_LENGTHS = {"small": round(90 * s), "large": round(170 * s)}

    BALL_RADIUS = max(6, round(10 * s))
    BALL_START_SPEED = 380.0 * s
    BALL_MAX_SPEED = 900.0 * s

    SCOREBOX_W = round(190 * s)
    SCOREBOX_H = round(150 * s)

    TUG_MAX_OFFSET = round(110 * s)
    OOB_MARGIN = round(260 * s)


configure_display(WIDTH, HEIGHT)

# Colors
BG = (14, 16, 22)
ARENA_LINE = (60, 66, 82)
WHITE = (235, 235, 240)
GRAY = (140, 146, 160)
DARK_PANEL = (24, 27, 36)
ACCENT = (90, 200, 255)

PLAYER_COLORS = {
    TOP: (255, 99, 99),      # red
    RIGHT: (99, 190, 255),   # blue
    BOTTOM: (255, 214, 99),  # yellow
    LEFT: (140, 255, 140),   # green
}

PLAYER_LABELS = {TOP: "P1", RIGHT: "P2", BOTTOM: "P3", LEFT: "P4"}

DEFAULT_KEYMAPS = {
    TOP:    {"left": pygame.K_LEFT,  "right": pygame.K_RIGHT, "up": pygame.K_UP,   "down": pygame.K_DOWN},
    RIGHT:  {"left": pygame.K_a,     "right": pygame.K_d,     "up": pygame.K_w,    "down": pygame.K_s},
    BOTTOM: {"left": pygame.K_j,     "right": pygame.K_l,     "up": pygame.K_i,    "down": pygame.K_k},
    LEFT:   {"left": pygame.K_f,     "right": pygame.K_h,     "up": pygame.K_t,    "down": pygame.K_g},
}
JOY_DEADZONE = 0.

TUG_COLORS = {
    TL: (200, 140, 255),   # purple
    TR: (255, 140, 220),   # pink
    BL: (140, 255, 220),   # teal
    BR: (255, 200, 120),   # amber
}

# Tug players only need a single "pull" button each (they push the box
# toward a fixed corner), so a single key per player is enough.
DEFAULT_TUG_KEYMAPS = {TL: pygame.K_1, TR: pygame.K_2, BL: pygame.K_3, BR: pygame.K_4}



# ----------------------------------------------------------------------------
# Small vector / geometry helpers
# ----------------------------------------------------------------------------

def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def reflect_circle_off_segment(px, py, vx, vy, r, x1, y1, x2, y2, seg_radius=0.0):
    """
    Capsule-vs-circle collision + reflection.
    Segment (x1,y1)-(x2,y2) has an optional 'radius' (thickness/2) of its own.
    Returns (hit, new_px, new_py, new_vx, new_vy).
    """
    dx, dy = x2 - x1, y2 - y1
    seg_len2 = dx * dx + dy * dy
    if seg_len2 == 0:
        seg_len2 = 1e-6
    t = ((px - x1) * dx + (py - y1) * dy) / seg_len2
    t = clamp(t, 0.0, 1.0)
    cx_, cy_ = x1 + t * dx, y1 + t * dy
    nx, ny = px - cx_, py - cy_
    dist = math.hypot(nx, ny)
    min_dist = r + seg_radius
    if dist >= min_dist or dist == 0:
        return False, px, py, vx, vy
    nx, ny = nx / dist, ny / dist
    # push the ball out of the capsule
    overlap = min_dist - dist
    npx, npy = px + nx * overlap, py + ny * overlap
    # reflect velocity about the normal
    dot = vx * nx + vy * ny
    if dot < 0:  # only reflect if moving into the surface
        nvx = vx - 2 * dot * nx
        nvy = vy - 2 * dot * ny
    else:
        nvx, nvy = vx, vy
    return True, npx, npy, nvx, nvy


def reflect_circle_off_rect(px, py, vx, vy, r, rect):
    """
    Circle-vs-AABB collision + reflection (used for the collidable center
    scorebox panel). Returns (hit, new_px, new_py, new_vx, new_vy).
    """
    cx_ = clamp(px, rect.left, rect.right)
    cy_ = clamp(py, rect.top, rect.bottom)
    dx, dy = px - cx_, py - cy_
    dist = math.hypot(dx, dy)
    if dist == 0:
        # Center is exactly on/degenerate case - nudge along the smallest axis.
        dx, dy = 0.0, -1.0
        dist = 1.0
    if dist >= r:
        return False, px, py, vx, vy
    nx, ny = dx / dist, dy / dist
    overlap = r - dist
    npx, npy = px + nx * overlap, py + ny * overlap
    dot = vx * nx + vy * ny
    if dot < 0:
        nvx = vx - 2 * dot * nx
        nvy = vy - 2 * dot * ny
    else:
        nvx, nvy = vx, vy
    return True, npx, npy, nvx, nvy


# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------

# Persisted next to the script itself, so settings (including which
# controller is assigned to which player) survive quitting and relaunching
# the program.
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pong8p_config.json")


class Settings:
    def __init__(self):
        self.points_to_win = 15
        self.autorestart = True
        self.paddle_size = "large"          # "small" | "large"
        self.trail_length = 15              # 0 (off) .. TRAIL_MAX_CAP, step 5
        self.particle_level = "medium"      # "off" | "low" | "medium" | "high"
        self.ball_speed_mult = 1.0          # 0.5x .. 2.5x, step 0.1
        self.ball_size_mult = 0.7           # 0.5x .. 2.5x, step 0.1
        self.ball_color_name = "White"      # one of BALL_COLOR_NAMES
        self.goal_size_mult = 1.0           # 0.2x .. 1.0x, step 0.1
        # Goal opening is the same absolute length on every side, regardless
        # of that side's own length (see Game.goal_bounds). 1.0x = as wide
        # as the shortest playable side (fully open, like classic pong).

        # Input assignment per player slot: "ai", "keyboard", or an integer
        # joystick id. Each of the 8 player slots is independent, so any
        # of your connected controllers can be assigned to any paddle side
        # or tug corner directly - no fixed "first N are human" ordering.
        self.inputs = {TOP: "keyboard", RIGHT: "keyboard", BOTTOM: "keyboard", LEFT: "keyboard"}
        self.tug_inputs = {TL: "ai", TR: "ai", BL: "ai", BR: "ai"}

    def paddle_length(self):
        return PADDLE_LENGTHS[self.paddle_size]

    def is_ai(self, side):
        return self.inputs[side] == "ai"

    def is_tug_ai(self, tside):
        return self.tug_inputs[tside] == "ai"

    def ball_start_speed(self):
        return BALL_START_SPEED * self.ball_speed_mult

    def ball_max_speed(self):
        return BALL_MAX_SPEED * self.ball_speed_mult

    def ball_color(self):
        return BALL_COLORS.get(self.ball_color_name, WHITE)

    # -- persistence -------------------------------------------------
    #
    # Controller assignments are saved by controller identity (GUID / name),
    # not by raw joystick index, since the index a controller gets depends
    # on USB connection order and isn't stable across relaunches. On load,
    # each saved controller identity is matched back up against whichever
    # controllers are currently connected.

    @staticmethod
    def _serialize_input(input_manager, value):
        if value in ("ai", "keyboard"):
            return value
        joy = input_manager.joysticks.get(value)
        if joy is None:
            return "keyboard"
        guid = input_manager.guid_by_id.get(value, f"name:{joy.get_name()}")
        # Multiple identical controllers (same make/model) share the same
        # GUID, so the GUID alone can't tell them apart. dup_index records
        # *which* of the same-GUID controllers this is (0 = first by
        # joystick index, 1 = second, ...), so on load we can match each
        # saved slot back up to the correct physical unit instead of every
        # slot collapsing onto whichever same-GUID controller happens to
        # enumerate first.
        same_guid = sorted(i for i, g in input_manager.guid_by_id.items() if g == guid)
        dup_index = same_guid.index(value) if value in same_guid else 0
        return {"controller_id": guid, "dup_index": dup_index, "name": joy.get_name()}

    @staticmethod
    def _resolve_input(input_manager, saved, default):
        if saved in ("ai", "keyboard"):
            return saved
        if isinstance(saved, dict):
            guid = saved.get("controller_id")
            dup_index = saved.get("dup_index", 0)
            same_guid = sorted(i for i, g in input_manager.guid_by_id.items() if g == guid)
            if dup_index < len(same_guid):
                return same_guid[dup_index]
        # Controller isn't connected (or wasn't recognized) - fall back to
        # the default for this slot rather than crashing or picking a
        # different, unrelated controller.
        return default

    def to_dict(self, input_manager):
        return {
            "points_to_win": self.points_to_win,
            "autorestart": self.autorestart,
            "paddle_size": self.paddle_size,
            "trail_length": self.trail_length,
            "particle_level": self.particle_level,
            "ball_speed_mult": self.ball_speed_mult,
            "ball_size_mult": self.ball_size_mult,
            "ball_color_name": self.ball_color_name,
            "goal_size_mult": self.goal_size_mult,
            "inputs": {side: self._serialize_input(input_manager, v)
                       for side, v in self.inputs.items()},
            "tug_inputs": {tside: self._serialize_input(input_manager, v)
                           for tside, v in self.tug_inputs.items()},
        }

    def save(self, input_manager):
        try:
            with open(CONFIG_PATH, "w") as f:
                json.dump(self.to_dict(input_manager), f, indent=2)
        except OSError:
            pass  # non-fatal - just means settings won't persist this run

    def load(self, input_manager):
        try:
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return

        self.points_to_win = data.get("points_to_win", self.points_to_win)
        self.autorestart = data.get("autorestart", self.autorestart)
        self.paddle_size = data.get("paddle_size", self.paddle_size)
        self.trail_length = data.get("trail_length", self.trail_length)
        self.particle_level = data.get("particle_level", self.particle_level)
        self.ball_speed_mult = data.get("ball_speed_mult", self.ball_speed_mult)
        self.ball_size_mult = data.get("ball_size_mult", self.ball_size_mult)
        self.ball_color_name = data.get("ball_color_name", self.ball_color_name)
        self.goal_size_mult = data.get("goal_size_mult", self.goal_size_mult)

        saved_inputs = data.get("inputs", {})
        for side in SIDES:
            if side in saved_inputs:
                self.inputs[side] = self._resolve_input(
                    input_manager, saved_inputs[side], self.inputs[side])

        saved_tug = data.get("tug_inputs", {})
        for tside in TUG_SIDES:
            if tside in saved_tug:
                self.tug_inputs[tside] = self._resolve_input(
                    input_manager, saved_tug[tside], self.tug_inputs[tside])


# ----------------------------------------------------------------------------
# Input handling
# ----------------------------------------------------------------------------

class InputManager:
    def __init__(self):
        pygame.joystick.init()
        self.joysticks = {}
        self.guid_by_id = {}
        self.rescan()

    def rescan(self):
        pygame.joystick.quit()
        pygame.joystick.init()
        self.joysticks = {}
        self.guid_by_id = {}
        for i in range(pygame.joystick.get_count()):
            j = pygame.joystick.Joystick(i)
            j.init()
            self.joysticks[i] = j
            self.guid_by_id[i] = self._joystick_identity(j)
            # Identical controllers (same make/model) share a GUID; see
            # Settings._serialize_input / _resolve_input for how saved
            # assignments still get matched back to the right physical
            # unit by joystick-index order among same-GUID controllers.

    @staticmethod
    def _joystick_identity(joy):
        """A best-effort stable identifier for a controller, used to
        remember which physical controller was assigned to which player
        across program restarts (raw joystick indices aren't stable -
        they depend on USB enumeration / connection order)."""
        try:
            guid = joy.get_guid()
            if guid:
                return guid
        except Exception:
            pass
        return f"name:{joy.get_name()}"
    
    #human slop here
    def get_device_index(self):
        assigning = True
        while assigning:
            i = 0
            for joy in self.joysticks:
                v = 0.0
                try:
                    if joy.get_numaxes() > axis_index:
                        v = joy.get_axis(axis_index)
                        if abs(v) < JOY_DEADZONE:
                            v = 0.0
                except Exception:
                    v = 0.0
                if v != 0:
                    assigning = False
                    return i
                i+=1
    #end human slop
    def device_label(self, assignment):
        if assignment == "ai":
            return "AI"
        if assignment == "keyboard":
            return "Keyboard"
        if assignment in self.joysticks:
            name = self.joysticks[assignment].get_name()
            return f"Controller {assignment}: {name[:22]}"
        return f"Controller {assignment} (not connected)"

    def available_assignments(self):
        opts = ["ai", "keyboard"] + list(self.joysticks.keys())
        return opts

    def get_axis_value(self, side, assignment, keys_down):
        """Returns a value in [-1, 1] along the side's active axis
        (left/right for TOP & BOTTOM, up/down for LEFT & RIGHT). For a
        controller/wheel this is the raw axis position - e.g. a steering
        wheel's rotation maps straight through - since paddles are
        positional, not speed-driven."""
        horizontal = 1
        # TOP and RIGHT are wired with their left/right sense flipped
        # relative to BOTTOM and LEFT, so "left" pushes the paddle right
        # and "right" pushes it left on those two sides.
        invert = side in (TOP, RIGHT)

        if assignment == "keyboard" or assignment not in self.joysticks:
            km = DEFAULT_KEYMAPS[side]
            if horizontal:
                v = 0.0
                if keys_down[km["left"]]:
                    v -= 1.0
                if keys_down[km["right"]]:
                    v += 1.0
            else:
                v = 0.0
                if keys_down[km["up"]]:
                    v -= 1.0
                if keys_down[km["down"]]:
                    v += 1.0
            return -v if invert else v

        joy = self.joysticks[assignment]
        axis_index = 0 if horizontal else 1
        v = 0.0
        try:
            if joy.get_numaxes() > axis_index:
                v = joy.get_axis(axis_index)
                if abs(v) < JOY_DEADZONE:
                    v = 0.0
        except Exception:
            v = 0.0
        # d-pad fallback / addition
        try:
            if joy.get_numhats() > 0:
                hx, hy = joy.get_hat(0)
                if horizontal and hx != 0:
                    v = float(hx)
                elif not horizontal and hy != 0:
                    v = float(-hy)  # hat y is inverted (up = 1)
        except Exception:
            pass
        if invert:
            v = -v
        return clamp(v, -1.0, 1.0)

    def get_tug_value(self, tside, assignment, keys_down):
        """'Wheel'-style analog pull amount in [0, 1] for a tug-of-war
        player. Keyboard is a simple on/off switch (1.0 while held). A
        connected controller - including a steering wheel - uses its main
        axis (axis 0, the same one used for steering) and reads how far
        it's turned off-center in EITHER direction as the pull strength,
        since the pull direction is already fixed by which corner this
        player is assigned to; a plain button also counts as a full pull
        for pads without a usable axis."""
        if assignment == "ai":
            return 0.0
        if assignment == "keyboard" or assignment not in self.joysticks:
            key = DEFAULT_TUG_KEYMAPS[tside]
            return 1.0 if keys_down[key] else 0.0

        joy = self.joysticks[assignment]
        value = 0.0
        try:
            if joy.get_numaxes() > 0:
                raw = joy.get_axis(0)
                if abs(raw) >= JOY_DEADZONE:
                    value = clamp(abs(raw), 0.0, 1.0)
        except Exception:
            pass
        try:
            if joy.get_numbuttons() > 0 and joy.get_button(0):
                value = max(value, 1.0)
        except Exception:
            pass
        return value


# ----------------------------------------------------------------------------
# Arena geometry (square field with cut corners -> octagon), goals per side
# ----------------------------------------------------------------------------

def corner_walls():
    """Returns list of ((x1,y1),(x2,y2)) diagonal segments cutting each corner."""
    c = CORNER_CUT
    return [
        ((X_L, Y_T + c), (X_L + c, Y_T)),   # top-left
        ((X_R - c, Y_T), (X_R, Y_T + c)),   # top-right
        ((X_R, Y_B - c), (X_R - c, Y_B)),   # bottom-right
        ((X_L + c, Y_B), (X_L, Y_B - c)),   # bottom-left
    ]


def arena_outline_points():
    c = CORNER_CUT
    return [
        (X_L + c, Y_T), (X_R - c, Y_T),
        (X_R, Y_T + c), (X_R, Y_B - c),
        (X_R - c, Y_B), (X_L + c, Y_B),
        (X_L, Y_B - c), (X_L, Y_T + c),
    ]


def scorebox_rect(offset=(0, 0)):
    """The center scorebox panel's rect, in world coords. Shared by drawing
    and collision so the ball always physically bounces off exactly what's
    drawn on screen. 'offset' is how far the tug-of-war players have
    dragged it from dead-center."""
    r = pygame.Rect(0, 0, SCOREBOX_W, SCOREBOX_H)
    r.center = (CX + offset[0], CY + offset[1])
    return r


def tug_anchor_points():
    """Fixed anchor point for each tug-of-war player's rope, tucked just
    inside each corner cut."""
    c = CORNER_CUT
    return {
        TL: (X_L + c * 0.5, Y_T + c * 0.5),
        TR: (X_R - c * 0.5, Y_T + c * 0.5),
        BL: (X_L + c * 0.5, Y_B - c * 0.5),
        BR: (X_R - c * 0.5, Y_B - c * 0.5),
    }


# ----------------------------------------------------------------------------
# Entities
# ----------------------------------------------------------------------------

class Paddle:
    def __init__(self, side, length):
        self.side = side
        self.length = length
        self.thick = PADDLE_THICK
        self.pos = 0.0  # position of paddle center along its side, in world coords (x for T/B, y for L/R)
        self.reset_center()

    def reset_center(self):
        if self.side in (TOP, BOTTOM):
            self.pos = CX
        else:
            self.pos = CY

    def set_length(self, length):
        self.length = length
        self.clamp_to_bounds()

    def bounds(self):
        """Min/max allowed for self.pos (paddle center) so it stays clear of corner walls."""
        half = self.length / 2
        if self.side in (TOP, BOTTOM):
            lo = X_L + CORNER_CUT + half
            hi = X_R - CORNER_CUT - half
        else:
            lo = Y_T + CORNER_CUT + half
            hi = Y_B - CORNER_CUT - half
        if lo > hi:
            mid = (lo + hi) / 2
            lo = hi = mid
        return lo, hi

    def clamp_to_bounds(self):
        lo, hi = self.bounds()
        self.pos = clamp(self.pos, lo, hi)

    def move(self, axis_value):
        """Positional ('wheel') control: axis_value in [-1, 1] maps
        directly to where the paddle sits along its track - no speed or
        acceleration involved, exactly like a wheel/lever position."""
        lo, hi = self.bounds()
        mid = (lo + hi) / 2
        self.pos = mid + clamp(axis_value, -1.0, 1.0) * (hi - lo) / 2
        self.clamp_to_bounds()

    def rect(self):
        half = self.length / 2
        if self.side == TOP:
            return pygame.Rect(self.pos - half, Y_T, self.length, self.thick)
        if self.side == BOTTOM:
            return pygame.Rect(self.pos - half, Y_B - self.thick, self.length, self.thick)
        if self.side == LEFT:
            return pygame.Rect(X_L, self.pos - half, self.thick, self.length)
        if self.side == RIGHT:
            return pygame.Rect(X_R - self.thick, self.pos - half, self.thick, self.length)


class Ball:
    def __init__(self):
        self.radius = BALL_RADIUS
        self.trail = deque(maxlen=TRAIL_MAX_CAP)
        self.reset()

    def reset(self, toward=None, speed_override=None):
        self.x, self.y = float(CX), float(CY)
        angle = random.uniform(0, 2 * math.pi)
        # avoid near-axis-aligned launches (boring / can graze corners)
        while min(abs(math.cos(angle)), abs(math.sin(angle))) < 0.35:
            angle = random.uniform(0, 2 * math.pi)
        speed = speed_override if speed_override is not None else BALL_START_SPEED
        self.vx = math.cos(angle) * speed
        self.vy = math.sin(angle) * speed
        self.trail.clear()

    def speed(self):
        return math.hypot(self.vx, self.vy)

    def set_speed(self, s):
        cur = self.speed()
        if cur == 0:
            return
        f = s / cur
        self.vx *= f
        self.vy *= f

    def set_radius(self, r):
        self.radius = max(4, round(r))

    def update(self, dt):
        self.trail.append((self.x, self.y))
        self.x += self.vx * dt
        self.y += self.vy * dt


class Particle:
    """A single short-lived spark spawned wherever the ball bounces."""
    __slots__ = ("x", "y", "vx", "vy", "life", "max_life", "color", "size")

    def __init__(self, x, y, vx, vy, life, color, size):
        self.x, self.y = x, y
        self.vx, self.vy = vx, vy
        self.life = life
        self.max_life = life
        self.color = color
        self.size = size

    def update(self, dt):
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.vx *= 0.94
        self.vy *= 0.94
        self.life -= dt

    def alive(self):
        return self.life > 0


# ----------------------------------------------------------------------------
# Game states
# ----------------------------------------------------------------------------

STATE_TITLE = "TITLE"
STATE_PLAYING = "PLAYING"
STATE_CONFIG = "CONFIG"
STATE_GAMEOVER = "GAMEOVER"


class Game:
    def __init__(self, settings, input_manager):
        self.settings = settings
        self.input = input_manager
        self.state = STATE_TITLE
        self.scores = {TOP: 0, RIGHT: 0, BOTTOM: 0, LEFT: 0}
        self.paddles = {side: Paddle(side, settings.paddle_length()) for side in SIDES}
        self.ball = Ball()
        self.particles = []
        self.sounds = Sounds()
        self.winner = None
        self.gameover_timer = 0.0
        self.flash_side = None
        self.flash_timer = 0.0

        # Tug-of-war center box state
        self.box_offset = [0.0, 0.0]
        self.tug_pulling = {t: False for t in TUG_SIDES}
        self._ai_tug_state = {t: False for t in TUG_SIDES}
        self._ai_tug_timer = {t: 0.0 for t in TUG_SIDES}

        # Predictive-AI tracking error state (per paddle side)
        self._ai_err = {side: 0.0 for side in SIDES}
        self._ai_err_timer = {side: 0.0 for side in SIDES}

        self.apply_ball_size()

    # -- setup / reset ---------------------------------------------------

    def start_new_game(self):
        self.scores = {TOP: 0, RIGHT: 0, BOTTOM: 0, LEFT: 0}
        for side in SIDES:
            self.paddles[side] = Paddle(side, self.settings.paddle_length())
        self.box_offset = [0.0, 0.0]
        self.tug_pulling = {t: False for t in TUG_SIDES}
        self._ai_tug_state = {t: False for t in TUG_SIDES}
        self._ai_tug_timer = {t: 0.0 for t in TUG_SIDES}
        self._ai_err = {side: 0.0 for side in SIDES}
        self._ai_err_timer = {side: 0.0 for side in SIDES}
        self.apply_ball_size()
        self.ball.reset(speed_override=self.settings.ball_start_speed())
        self.particles = []
        self.winner = None
        self.state = STATE_PLAYING

    def apply_paddle_size(self):
        for p in self.paddles.values():
            p.set_length(self.settings.paddle_length())

    def apply_ball_size(self):
        self.ball.set_radius(BALL_RADIUS * self.settings.ball_size_mult)

    def goal_bounds(self):
        """Returns {side: (lo, hi)} - the world-coord extent of the scoring
        opening on each side. All four goals share the same absolute length
        (derived from the shortest playable side), so the goal is the same
        size for every player no matter which side they defend."""
        avail_w = (X_R - CORNER_CUT) - (X_L + CORNER_CUT)
        avail_h = (Y_B - CORNER_CUT) - (Y_T + CORNER_CUT)
        full_length = min(avail_w, avail_h)
        length = clamp(full_length * self.settings.goal_size_mult, 40, full_length)
        half = length / 2
        return {
            TOP: (CX - half, CX + half),
            BOTTOM: (CX - half, CX + half),
            LEFT: (CY - half, CY + half),
            RIGHT: (CY - half, CY + half),
        }

    def side_walls(self):
        """Wall segments covering the part of each side that is NOT the
        goal opening (but still inside the corner cuts). The ball bounces
        off these instead of scoring, exactly like the corner walls."""
        bounds = self.goal_bounds()
        walls = []

        h_lo, h_hi = X_L + CORNER_CUT, X_R - CORNER_CUT
        for side, y in ((TOP, Y_T), (BOTTOM, Y_B)):
            lo, hi = bounds[side]
            if lo > h_lo:
                walls.append(((h_lo, y), (lo, y)))
            if hi < h_hi:
                walls.append(((hi, y), (h_hi, y)))

        v_lo, v_hi = Y_T + CORNER_CUT, Y_B - CORNER_CUT
        for side, x in ((LEFT, X_L), (RIGHT, X_R)):
            lo, hi = bounds[side]
            if lo > v_lo:
                walls.append(((x, v_lo), (x, lo)))
            if hi < v_hi:
                walls.append(((x, hi), (x, v_hi)))

        return walls

    @staticmethod
    def _reflect_into_range(v, lo, hi):
        """Fold a coordinate back into [lo, hi] as if it had bounced off
        both ends (triangle-wave reflection) - a cheap way to predict
        where a straight-line shot ends up after wall bounces."""
        span = hi - lo
        if span <= 0:
            return (lo + hi) / 2
        m = (v - lo) % (2 * span)
        if m < 0:
            m += 2 * span
        if m > span:
            m = 2 * span - m
        return lo + m

    def _predict_intercept(self, side):
        """Predict where the ball will cross this paddle's line, folding
        for bounces off the perpendicular walls. Returns None if the ball
        isn't currently headed toward this side."""
        b = self.ball
        if side in (TOP, BOTTOM):
            line_y = Y_T + PADDLE_THICK if side == TOP else Y_B - PADDLE_THICK
            vy = b.vy
            heading_here = (side == TOP and vy < 0) or (side == BOTTOM and vy > 0)
            if not heading_here:
                return None
            t = (line_y - b.y) / vy
            if t < 0:
                return None
            raw = b.x + b.vx * t
            lo, hi = X_L + CORNER_CUT, X_R - CORNER_CUT
        else:
            line_x = X_L + PADDLE_THICK if side == LEFT else X_R - PADDLE_THICK
            vx = b.vx
            heading_here = (side == LEFT and vx < 0) or (side == RIGHT and vx > 0)
            if not heading_here:
                return None
            t = (line_x - b.x) / vx
            if t < 0:
                return None
            raw = b.y + b.vy * t
            lo, hi = Y_T + CORNER_CUT, Y_B - CORNER_CUT
        return self._reflect_into_range(raw, lo, hi)

    def _ai_axis(self, side, dt):
        """AI control: predicts where the ball will cross this paddle's
        line (accounting for wall bounces), drifts back toward center to
        stay ready when the ball is headed elsewhere, and adds a small,
        periodically-refreshed tracking error so it doesn't play like a
        perfect wall. Returns a normalized [-1, 1] 'wheel' position, since
        paddles are positional rather than speed-driven."""
        paddle = self.paddles[side]
        target = self._predict_intercept(side)
        if target is None:
            target = CX if side in (TOP, BOTTOM) else CY

        self._ai_err_timer[side] -= dt
        if self._ai_err_timer[side] <= 0:
            span = paddle.length * 0.45
            self._ai_err[side] = random.uniform(-span, span)
            self._ai_err_timer[side] = random.uniform(0.3, 0.8)
        target += self._ai_err[side]

        lo, hi = paddle.bounds()
        if hi <= lo:
            return 0.0
        axis = 2 * (target - lo) / (hi - lo) - 1
        return clamp(axis, -1.0, 1.0)

    def _update_center_box(self, dt, keys_down):
        """Four extra players tug the center scorebox panel toward their
        corner. Like the paddles, this is positional/'wheel'-style rather
        than speed-based: the box sits wherever the net pull currently
        points it, with no momentum, so it snaps back to center the
        instant everyone lets go."""
        fx, fy = 0.0, 0.0
        for tside in TUG_SIDES:
            if self.settings.is_tug_ai(tside):
                value = self._ai_tug_value(tside, dt)
            else:
                value = self.input.get_tug_value(tside, self.settings.tug_inputs[tside], keys_down)
            self.tug_pulling[tside] = value > 0.05
            dx, dy = TUG_DIRECTIONS[tside]
            fx += dx * value
            fy += dy * value

        self.box_offset[0] = clamp(fx, -1.0, 1.0) * TUG_MAX_OFFSET
        self.box_offset[1] = clamp(fy, -1.0, 1.0) * TUG_MAX_OFFSET

    def _ai_tug_value(self, tside, dt):
        """AI tug players pull in short random bursts, at a random
        strength, so the box stays lightly in motion instead of sitting
        dead in the center."""
        self._ai_tug_timer[tside] -= dt
        if self._ai_tug_timer[tside] <= 0:
            self._ai_tug_state[tside] = random.uniform(0.0, 1.0) if random.random() < 0.45 else 0.0
            self._ai_tug_timer[tside] = random.uniform(0.5, 1.6)
        return self._ai_tug_state[tside]

    def spawn_particles(self, x, y, color):
        scale = min(ARENA_W, ARENA_H) / 640.0
        count = PARTICLE_LEVEL_COUNTS.get(self.settings.particle_level, 0)
        for _ in range(count):
            angle = random.uniform(0, 2 * math.pi)
            speed = random.uniform(60, 260) * scale
            life = random.uniform(0.22, 0.5)
            size = random.uniform(1.5, 3.5) * scale
            self.particles.append(Particle(
                x, y, math.cos(angle) * speed, math.sin(angle) * speed, life, color, size
            ))

    # -- per-frame update --------------------------------------------------

    def update(self, dt, keys_down):
        if self.state != STATE_PLAYING:
            return

        for side in SIDES:
            if self.settings.is_ai(side):
                axis = self._ai_axis(side, dt)
            else:
                axis = self.input.get_axis_value(side, self.settings.inputs[side], keys_down)
            self.paddles[side].move(axis)

        self._update_center_box(dt, keys_down)

        self.ball.update(dt)
        self._handle_collisions()
        self._handle_goals()

        # Safety net: if the ball somehow ends up well outside the arena
        # (e.g. a corner-geometry edge case letting it tunnel through
        # without being caught as a goal), don't let it get stuck off
        # screen forever - just restart the game.
        b = self.ball
        if (b.x < X_L - OOB_MARGIN or b.x > X_R + OOB_MARGIN or
                b.y < Y_T - OOB_MARGIN or b.y > Y_B + OOB_MARGIN):
            self.start_new_game()
            return

        for p in self.particles:
            p.update(dt)
        self.particles = [p for p in self.particles if p.alive()]

        if self.flash_timer > 0:
            self.flash_timer -= dt
            if self.flash_timer <= 0:
                self.flash_side = None

    def _handle_collisions(self):
        b = self.ball
        max_speed = self.settings.ball_max_speed()

        # paddles
        for side, paddle in self.paddles.items():
            rect = paddle.rect()
            # closest point on rect to ball center
            cx_ = clamp(b.x, rect.left, rect.right)
            cy_ = clamp(b.y, rect.top, rect.bottom)
            dx, dy = b.x - cx_, b.y - cy_
            dist = math.hypot(dx, dy)
            if dist < b.radius:
                if side in (TOP, BOTTOM):
                    # reflect vertical component, add angle based on hit offset
                    offset = (b.x - paddle.pos) / (paddle.length / 2)
                    offset = clamp(offset, -1, 1)
                    speed = min(b.speed() * BALL_SPEEDUP, max_speed)
                    direction = 1 if side == TOP else -1
                    angle = offset * math.radians(60)
                    b.vx = math.sin(angle) * speed
                    b.vy = direction * abs(math.cos(angle)) * speed
                    b.y = rect.bottom + b.radius + 0.5 if side == TOP else rect.top - b.radius - 0.5
                else:
                    offset = (b.y - paddle.pos) / (paddle.length / 2)
                    offset = clamp(offset, -1, 1)
                    speed = min(b.speed() * BALL_SPEEDUP, max_speed)
                    direction = 1 if side == LEFT else -1
                    angle = offset * math.radians(60)
                    b.vy = math.sin(angle) * speed
                    b.vx = direction * abs(math.cos(angle)) * speed
                    b.x = rect.right + b.radius + 0.5 if side == LEFT else rect.left - b.radius - 0.5
                self.spawn_particles(cx_, cy_, PLAYER_COLORS[side])
                self.sounds.play_collision()

        # corner walls
        for (x1, y1), (x2, y2) in corner_walls():
            hit, nx, ny, nvx, nvy = reflect_circle_off_segment(b.x, b.y, b.vx, b.vy, b.radius, x1, y1, x2, y2)
            if hit:
                b.x, b.y, b.vx, b.vy = nx, ny, nvx, nvy
                self.spawn_particles(nx, ny, ACCENT)
                self.sounds.play_collision()

        # side walls (the non-goal portion of each side, when goal_size < 100%)
        for (x1, y1), (x2, y2) in self.side_walls():
            hit, nx, ny, nvx, nvy = reflect_circle_off_segment(b.x, b.y, b.vx, b.vy, b.radius, x1, y1, x2, y2)
            if hit:
                b.x, b.y, b.vx, b.vy = nx, ny, nvx, nvy
                self.spawn_particles(nx, ny, ACCENT)
                self.sounds.play_collision()

        # collidable center scorebox panel (moves as tug players drag it)
        hit, nx, ny, nvx, nvy = reflect_circle_off_rect(b.x, b.y, b.vx, b.vy, b.radius, scorebox_rect(self.box_offset))
        if hit:
            b.x, b.y, b.vx, b.vy = nx, ny, nvx, nvy
            speed = min(b.speed() * 1.02, max_speed)
            b.set_speed(speed)
            self.spawn_particles(nx, ny, ACCENT)
            self.sounds.play_collision()

    def _handle_goals(self):
        b = self.ball
        conceded = None
        margin = b.radius + 4
        bounds = self.goal_bounds()
        if b.x < X_L - margin and bounds[LEFT][0] <= b.y <= bounds[LEFT][1]:
            conceded = LEFT
        elif b.x > X_R + margin and bounds[RIGHT][0] <= b.y <= bounds[RIGHT][1]:
            conceded = RIGHT
        elif b.y < Y_T - margin and bounds[TOP][0] <= b.x <= bounds[TOP][1]:
            conceded = TOP
        elif b.y > Y_B + margin and bounds[BOTTOM][0] <= b.x <= bounds[BOTTOM][1]:
            conceded = BOTTOM

        if conceded:
            for side in SIDES:
                if side != conceded:
                    self.scores[side] += 1
            self.flash_side = conceded
            self.flash_timer = 0.6
            self.sounds.play_goal()
            self.ball.reset(speed_override=self.settings.ball_start_speed())

            winner = None
            for side, score in self.scores.items():
                if score >= self.settings.points_to_win:
                    winner = side
                    break
            if winner:
                self.winner = winner
                self.state = STATE_GAMEOVER
                self.gameover_timer = 3.0 if self.settings.autorestart else 0.0

    def update_gameover(self, dt):
        if self.settings.autorestart:
            self.gameover_timer -= dt
            if self.gameover_timer <= 0:
                self.start_new_game()


# ----------------------------------------------------------------------------
# Config Menu
# ----------------------------------------------------------------------------

class ConfigMenu:
    """List-based menu, navigable by keyboard or mouse. Hovering the mouse
    never changes the selected row by itself - only clicking a row (or
    using the keyboard) does that - and the list scrolls with the mouse
    wheel or keyboard navigation once it's taller than the panel."""

    MAX_VISIBLE_ROWS = 12

    def __init__(self, settings, input_manager):
        self.settings = settings
        self.input = input_manager
        self.selected = 0
        self.scroll = 0
        self.rows = []  # filled in build()
        self.row_rects = []  # (row_index, rect) for currently visible rows only
        self.build()

    def _clamp_scroll(self):
        max_scroll = max(0, len(self.rows) - self.MAX_VISIBLE_ROWS)
        self.scroll = clamp(self.scroll, 0, max_scroll)

    def ensure_selected_visible(self):
        if self.selected < self.scroll:
            self.scroll = self.selected
        elif self.selected >= self.scroll + self.MAX_VISIBLE_ROWS:
            self.scroll = self.selected - self.MAX_VISIBLE_ROWS + 1
        self._clamp_scroll()

    def scroll_by(self, delta_rows):
        self.scroll += delta_rows
        self._clamp_scroll()

    def build(self):
        s = self.settings

        def input_label(side):
            if s.is_ai(side):
                return "AI (follows ball)"
            return self.input.device_label(s.inputs[side])

        def tug_input_label(tside):
            if s.is_tug_ai(tside):
                return "AI (auto-tug)"
            return self.input.device_label(s.tug_inputs[tside])

        self.rows = [
            ("points", f"Points to win: {s.points_to_win}"),
            ("autorestart", f"Auto-restart: {'On' if s.autorestart else 'Off'}"),
            ("paddle_size", f"Paddle size: {s.paddle_size.capitalize()}"),
            ("goal_size", f"Goal size: {round(s.goal_size_mult * 100)}%  (same for all)"),
            ("ball_speed", f"Ball speed: {round(s.ball_speed_mult * 100)}%"),
            ("ball_size", f"Ball size: {round(s.ball_size_mult * 100)}%"),
            ("ball_color", f"Ball color: {s.ball_color_name}"),
            ("trail", f"Ball trail length: {s.trail_length if s.trail_length else 'Off'}"),
            ("particles", f"Bounce particles: {s.particle_level.capitalize()}"),
            ("input_top", f"P1 (Top) input: {input_label(TOP)}"),
            ("input_right", f"P2 (Right) input: {input_label(RIGHT)}"),
            ("input_bottom", f"P3 (Bottom) input: {input_label(BOTTOM)}"),
            ("input_left", f"P4 (Left) input: {input_label(LEFT)}"),
            ("input_tl", f"P5 (Top-Left tug) input: {tug_input_label(TL)}"),
            ("input_tr", f"P6 (Top-Right tug) input: {tug_input_label(TR)}"),
            ("input_bl", f"P7 (Bottom-Left tug) input: {tug_input_label(BL)}"),
            ("input_br", f"P8 (Bottom-Right tug) input: {tug_input_label(BR)}"),
            ("rescan", "Rescan controllers"),
            ("resume", "Resume / Start Game"),
            ("restart", "Restart Game"),
            ("exit", "Exit Game"),
        ]
        self.selected = clamp(self.selected, 0, len(self.rows) - 1)
        self._clamp_scroll()

    def change(self, key, direction):
        s = self.settings
        if key == "points":
            s.points_to_win = clamp(s.points_to_win + direction * 5, 5, 50)
        elif key == "autorestart":
            s.autorestart = not s.autorestart
        elif key == "paddle_size":
            s.paddle_size = "small" if s.paddle_size == "large" else "large"
        elif key == "goal_size":
            s.goal_size_mult = clamp(round(s.goal_size_mult + direction * 0.1, 2), 0.2, 1.0)
        elif key == "ball_speed":
            s.ball_speed_mult = clamp(round(s.ball_speed_mult + direction * 0.1, 2), 0.5, 2.5)
        elif key == "ball_size":
            s.ball_size_mult = clamp(round(s.ball_size_mult + direction * 0.1, 2), 0.5, 2.5)
        elif key == "ball_color":
            idx = BALL_COLOR_NAMES.index(s.ball_color_name) if s.ball_color_name in BALL_COLOR_NAMES else 0
            idx = (idx + direction) % len(BALL_COLOR_NAMES)
            s.ball_color_name = BALL_COLOR_NAMES[idx]
        elif key == "trail":
            s.trail_length = clamp(s.trail_length + direction * 5, 0, TRAIL_MAX_CAP)
        elif key == "particles":
            idx = PARTICLE_LEVELS.index(s.particle_level) if s.particle_level in PARTICLE_LEVELS else 0
            idx = (idx + direction) % len(PARTICLE_LEVELS)
            s.particle_level = PARTICLE_LEVELS[idx]
        elif key in ("input_top", "input_right", "input_bottom", "input_left"):
            side = {"input_top": TOP, "input_right": RIGHT,
                    "input_bottom": BOTTOM, "input_left": LEFT}[key]
            opts = self.input.available_assignments()
            cur = s.inputs[side]
            idx = opts.index(cur) if cur in opts else 0
            idx = (idx + direction) % len(opts)
            s.inputs[side] = opts[idx]
        elif key in ("input_tl", "input_tr", "input_bl", "input_br"):
            tside = {"input_tl": TL, "input_tr": TR,
                     "input_bl": BL, "input_br": BR}[key]
            opts = self.input.available_assignments()
            cur = s.tug_inputs[tside]
            idx = opts.index(cur) if cur in opts else 0
            idx = (idx + direction) % len(opts)
            s.tug_inputs[tside] = opts[idx]
        elif key == "rescan":
            self.input.rescan()
        self.settings.save(self.input)
        self.build()

    def activate(self, key, game):
        if key == "resume":
            game.apply_paddle_size()
            game.apply_ball_size()
            if game.state == STATE_TITLE or game.winner is not None:
                game.start_new_game()
            else:
                game.state = STATE_PLAYING
        elif key == "restart":
            game.apply_paddle_size()
            game.apply_ball_size()
            game.start_new_game()
        elif key == "rescan":
            self.input.rescan()
            self.settings.save(self.input)
            self.build()
        elif key in ("autorestart", "paddle_size", "particles", "ball_color"):
            self.change(key, 1)
        elif key.startswith("input_"):
            #unslop here
            idx = self.input.get_device_index()
            if key in ("input_top", "input_right", "input_bottom", "input_left"):
                side = {"input_top": TOP, "input_right": RIGHT,
                        "input_bottom": BOTTOM, "input_left": LEFT}[key]
                opts = self.input.available_assignments()
                s.inputs[side] = opts[idx]
            if key in ("input_tl", "input_tr", "input_bl", "input_br"):
                tside = {"input_tl": TL, "input_tr": TR,
                         "input_bl": BL, "input_br": BR}[key]
                opts = self.input.available_assignments()
                s.tug_inputs[tside] = opts[idx]
        elif key == "exit":
            self.settings.save(self.input)
            pygame.quit()
            sys.exit(0)


# ----------------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------------

def draw_trail(surf, ball, trail_length, ball_color):
    """Fading, shrinking dots behind the ball. trail_length is how many of
    the most recent recorded positions to show (0 disables the trail)."""
    if trail_length <= 0:
        return
    pts = list(ball.trail)[-trail_length:]
    n = len(pts)
    if n == 0:
        return
    for i, (tx, ty) in enumerate(pts):
        t = (i + 1) / n  # 0 (oldest) .. 1 (newest, right before the ball)
        radius = max(1, round(ball.radius * (0.15 + 0.75 * t)))
        color = (
            round(BG[0] + (ball_color[0] - BG[0]) * t * 0.65),
            round(BG[1] + (ball_color[1] - BG[1]) * t * 0.65),
            round(BG[2] + (ball_color[2] - BG[2]) * t * 0.65),
        )
        pygame.draw.circle(surf, color, (round(tx), round(ty)), radius)


def draw_particles(surf, particles):
    """Bounce-spark particles, faded from their spawn color down to the
    background color as they age (no per-particle alpha surface needed)."""
    for p in particles:
        t = clamp(p.life / p.max_life, 0.0, 1.0)
        color = (
            round(BG[0] + (p.color[0] - BG[0]) * t),
            round(BG[1] + (p.color[1] - BG[1]) * t),
            round(BG[2] + (p.color[2] - BG[2]) * t),
        )
        radius = max(1, round(p.size * t))
        pygame.draw.circle(surf, color, (round(p.x), round(p.y)), radius)


def draw_arena(surf, fonts, game, flash_side):
    pygame.draw.polygon(surf, (20, 23, 30), arena_outline_points())
    pygame.draw.polygon(surf, ARENA_LINE, arena_outline_points(), 3)

    # center dashed cross (visual only)
    for i in range(-ARENA_W // 2, ARENA_W // 2, 24):
        pygame.draw.circle(surf, (34, 38, 48), (CX + i, CY), 2)

    # highlight the actual goal opening on each side in that player's color,
    # so it's visually clear how much of the side is scoreable vs. a wall
    goal_bounds = game.goal_bounds()
    goal_line_w = max(3, round(5 * (min(ARENA_W, ARENA_H) / 640.0)))
    for side in SIDES:
        lo, hi = goal_bounds[side]
        color = PLAYER_COLORS[side]
        if side == TOP:
            pygame.draw.line(surf, color, (lo, Y_T), (hi, Y_T), goal_line_w)
        elif side == BOTTOM:
            pygame.draw.line(surf, color, (lo, Y_B), (hi, Y_B), goal_line_w)
        elif side == LEFT:
            pygame.draw.line(surf, color, (X_L, lo), (X_L, hi), goal_line_w)
        elif side == RIGHT:
            pygame.draw.line(surf, color, (X_R, lo), (X_R, hi), goal_line_w)

    for side in SIDES:
        color = PLAYER_COLORS[side]
        rect = game.paddles[side].rect()
        pygame.draw.rect(surf, color, rect, border_radius=4)

    if flash_side:
        color = PLAYER_COLORS[flash_side]
        pad = 40
        flash_surf = pygame.Surface((ARENA_W + pad, ARENA_H + pad), pygame.SRCALPHA)
        pygame.draw.polygon(
            flash_surf, (*color, 60),
            [(x - X_L + pad // 2, y - Y_T + pad // 2) for x, y in arena_outline_points()]
        )
        surf.blit(flash_surf, (X_L - pad // 2, Y_T - pad // 2))

    # tug-of-war ropes: one per corner player, running to the (possibly
    # dragged-off-center) scorebox panel; brighter/thicker while pulling
    scale = min(ARENA_W, ARENA_H) / 640.0
    box_center = (CX + game.box_offset[0], CY + game.box_offset[1])
    anchors = tug_anchor_points()
    for tside in TUG_SIDES:
        ax, ay = anchors[tside]
        color = TUG_COLORS[tside]
        active = game.tug_pulling.get(tside, False)
        line_color = color if active else tuple(c // 3 for c in color)
        line_w = max(2, round((4 if active else 2) * scale))
        pygame.draw.line(surf, line_color, (ax, ay), box_center, line_w)
        r = max(5, round(9 * scale))
        pygame.draw.circle(surf, color, (round(ax), round(ay)), r)
        pygame.draw.circle(surf, BG, (round(ax), round(ay)), max(2, r - 3))
        lbl = fonts["tiny"].render(TUG_LABELS[tside], True, color)
        surf.blit(lbl, lbl.get_rect(center=(ax, ay - r - round(10 * scale))))

    ball_color = game.settings.ball_color()
    draw_trail(surf, game.ball, game.settings.trail_length, ball_color)
    draw_particles(surf, game.particles)
    pygame.draw.circle(surf, ball_color, (int(game.ball.x), int(game.ball.y)), game.ball.radius)


def draw_scorebox(surf, fonts, game, menu_hover):
    scale = min(ARENA_W, ARENA_H) / 640.0

    rect = scorebox_rect(game.box_offset)
    panel = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
    pygame.draw.rect(panel, (*DARK_PANEL, 235), panel.get_rect(), border_radius=12)
    pygame.draw.rect(panel, ARENA_LINE, panel.get_rect(), 2, border_radius=12)
    surf.blit(panel, rect.topleft)

    # paddings scale off the panel's own size so text stays well-contained
    # now that the panel itself is smaller
    edge_pad = round(rect.h * 0.17)   # distance of the TOP/BOTTOM score from that edge
    side_pad = round(rect.w * 0.19)   # distance of the LEFT/RIGHT score from that edge
    positions = {
        TOP: (rect.centerx, rect.top + edge_pad),
        RIGHT: (rect.right - side_pad, rect.centery),
        BOTTOM: (rect.centerx, rect.bottom - edge_pad),
        LEFT: (rect.left + side_pad, rect.centery),
    }
    lbl_off = round(rect.h * 0.11)
    label_offsets = {TOP: (0, lbl_off), BOTTOM: (0, -lbl_off), RIGHT: (0, lbl_off), LEFT: (0, lbl_off)}
    for side in SIDES:
        txt = fonts["score"].render(str(game.scores[side]), True, PLAYER_COLORS[side])
        tr = txt.get_rect(center=positions[side])
        surf.blit(txt, tr)
        lbl = fonts["tiny"].render(PLAYER_LABELS[side], True, GRAY)
        ox, oy = label_offsets[side]
        lr = lbl.get_rect(center=(positions[side][0] + ox, positions[side][1] + oy))
        surf.blit(lbl, lr)
    # menu button
    btn_w, btn_h = round(94 * scale), round(24 * scale)
    btn_rect = pygame.Rect(0, 0, btn_w, btn_h)
    btn_rect.center = (rect.centerx, rect.centery)
    color = ACCENT if menu_hover else (60, 66, 82)
    pygame.draw.rect(surf, color, btn_rect, border_radius=max(4, round(6 * scale)))
    lbl = fonts["tiny"].render("MENU (Esc)", True, (10, 12, 16) if menu_hover else WHITE)
    surf.blit(lbl, lbl.get_rect(center=btn_rect.center))
    return btn_rect


def draw_title(surf, fonts):
    pass


def draw_gameover(surf, fonts, game):
    color = PLAYER_COLORS[game.winner]
    txt = fonts["title"].render(f"{PLAYER_LABELS[game.winner]} WINS!", True, color)
    surf.blit(txt, txt.get_rect(center=(CX, HEIGHT * 0.16)))
    if game.settings.autorestart:
        sub = fonts["small"].render(f"Restarting in {game.gameover_timer:0.1f}s...", True, GRAY)
    else:
        sub = fonts["small"].render("Press ENTER / A to play again, or MENU to configure", True, GRAY)
    surf.blit(sub, sub.get_rect(center=(CX, HEIGHT * 0.16 + 44)))


def draw_menu(surf, fonts, menu, screen_state):
    overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
    overlay.fill((6, 7, 10, 210))
    surf.blit(overlay, (0, 0))

    scale = min(ARENA_W, ARENA_H) / 640.0
    row_h = round(32 * scale)
    header_h = round(64 * scale)
    footer_h = round(40 * scale)
    panel_w = round(660 * scale)
    visible_count = min(len(menu.rows), menu.MAX_VISIBLE_ROWS)
    panel_h = header_h + visible_count * row_h + footer_h
    panel = pygame.Rect(0, 0, panel_w, panel_h)
    panel.center = (CX, CY)
    pygame.draw.rect(surf, DARK_PANEL, panel, border_radius=16)
    pygame.draw.rect(surf, ARENA_LINE, panel, 2, border_radius=16)

    title = fonts["med"].render("GAME SETTINGS", True, WHITE)
    surf.blit(title, title.get_rect(center=(panel.centerx, panel.top + round(28 * scale))))

    scrollable = len(menu.rows) > menu.MAX_VISIBLE_ROWS
    side_margin = round(30 * scale)
    scrollbar_w = round(6 * scale) if scrollable else 0
    row_w = panel.w - side_margin * 2 - scrollbar_w
    menu.row_rects = []
    start_y = panel.top + header_h
    last_visible = min(len(menu.rows), menu.scroll + menu.MAX_VISIBLE_ROWS)
    for slot, i in enumerate(range(menu.scroll, last_visible)):
        key, label = menu.rows[i]
        r = pygame.Rect(panel.left + side_margin, start_y + slot * row_h, row_w, row_h - 6)
        menu.row_rects.append((i, r))
        selected = (i == menu.selected)
        if selected:
            pygame.draw.rect(surf, (36, 60, 76), r, border_radius=8)
        color = ACCENT if selected else WHITE
        if key == "resume":
            color = (140, 255, 140) if selected else (110, 210, 110)
        elif key == "restart":
            color = (255, 214, 99) if selected else (215, 175, 90)
        elif key == "exit":
            color = (255, 140, 140) if selected else (215, 105, 105)
        txt = fonts["small"].render(label, True, color)
        surf.blit(txt, (r.left + 14, r.centery - txt.get_height() // 2))

    if scrollable:
        track = pygame.Rect(panel.right - side_margin - scrollbar_w, start_y,
                             scrollbar_w, visible_count * row_h)
        pygame.draw.rect(surf, (40, 44, 56), track, border_radius=scrollbar_w // 2)
        frac_h = visible_count / len(menu.rows)
        frac_y = menu.scroll / max(1, len(menu.rows) - visible_count)
        thumb_h = max(round(18 * scale), round(track.h * frac_h))
        thumb_y = track.top + round((track.h - thumb_h) * frac_y)
        thumb = pygame.Rect(track.left, thumb_y, scrollbar_w, thumb_h)
        pygame.draw.rect(surf, ACCENT, thumb, border_radius=scrollbar_w // 2)

    hint = fonts["tiny"].render(
        "Up/Down: navigate   Left/Right: change   Enter: select   Scroll/wheel: more   Esc: close",
        True, GRAY,
    )
    surf.blit(hint, hint.get_rect(center=(panel.centerx, panel.bottom - round(18 * scale))))


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main():
    pygame.init()
    pygame.display.set_caption("4-Player Pong")
    pygame.mouse.set_visible(True)

    # Always fullscreen, at the desktop's own native resolution - (0, 0) tells
    # SDL to use the current display mode, so there's no letterboxing or any
    # unused screen area regardless of the monitor's aspect ratio/size.
    screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    actual_w, actual_h = screen.get_size()
    configure_display(actual_w, actual_h)

    clock = pygame.time.Clock()

    # Scale fonts to the same factor used for gameplay metrics, so text
    # stays proportionate on very small or very large/4K displays.
    font_scale = min(ARENA_W, ARENA_H) / 640.0
    fonts = {
        "title": pygame.font.SysFont("arial", max(24, round(44 * font_scale)), bold=True),
        "med": pygame.font.SysFont("arial", max(15, round(24 * font_scale)), bold=True),
        "score": pygame.font.SysFont("consolas", max(13, round(22 * font_scale)), bold=True),
        "small": pygame.font.SysFont("arial", max(10, round(16 * font_scale))),
        "tiny": pygame.font.SysFont("arial", max(8, round(11 * font_scale))),
    }

    settings = Settings()
    input_manager = InputManager()
    settings.load(input_manager)
    game = Game(settings, input_manager)
    menu = ConfigMenu(settings, input_manager)

    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0
        dt = min(dt, 1 / 20)  # avoid huge steps if window was dragged/minimized
        events = pygame.event.get()
        keys_down = pygame.key.get_pressed()

        mouse_pos = pygame.mouse.get_pos()
        mouse_clicked = False

        for e in events:
            if e.type == pygame.QUIT:
                settings.save(input_manager)
                running = False
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                mouse_clicked = True
            elif e.type == pygame.MOUSEWHEEL and game.state == STATE_CONFIG:
                menu.scroll_by(-e.y)
            elif e.type == pygame.KEYDOWN:
                if e.key == pygame.K_ESCAPE:
                    if game.state == STATE_CONFIG:
                        if game.winner is not None or game.state == STATE_TITLE:
                            pass
                        game.state = STATE_PLAYING if game.scores != {TOP:0,RIGHT:0,BOTTOM:0,LEFT:0} or game.winner else STATE_TITLE
                        # If a real game is in progress, resume it; otherwise go to title
                        if any(v > 0 for v in game.scores.values()) and game.winner is None:
                            game.state = STATE_PLAYING
                        elif game.winner is not None:
                            game.state = STATE_GAMEOVER
                        else:
                            game.state = STATE_TITLE
                    elif game.state in (STATE_PLAYING, STATE_TITLE, STATE_GAMEOVER):
                        game.state = STATE_CONFIG
                elif game.state == STATE_CONFIG:
                    if e.key in (pygame.K_UP, pygame.K_w):
                        menu.selected = (menu.selected - 1) % len(menu.rows)
                        menu.ensure_selected_visible()
                    elif e.key in (pygame.K_DOWN, pygame.K_s):
                        menu.selected = (menu.selected + 1) % len(menu.rows)
                        menu.ensure_selected_visible()
                    elif e.key in (pygame.K_LEFT, pygame.K_a):
                        menu.change(menu.rows[menu.selected][0], -1)
                    elif e.key in (pygame.K_RIGHT, pygame.K_d):
                        menu.change(menu.rows[menu.selected][0], 1)
                    elif e.key in (pygame.K_RETURN, pygame.K_SPACE):
                        menu.activate(menu.rows[menu.selected][0], game)
                elif game.state == STATE_TITLE and e.key in (pygame.K_RETURN, pygame.K_SPACE):
                    game.start_new_game()
                elif game.state == STATE_GAMEOVER and not settings.autorestart and e.key in (pygame.K_RETURN, pygame.K_SPACE):
                    game.start_new_game()
            # Controllers are movement-only (left/right along each player's
            # side): no controller button opens the menu, selects menu rows,
            # or starts/restarts the game. The menu can only be opened with
            # the ESC key on the keyboard.

        # ---- update ----
        if game.state == STATE_PLAYING:
            game.update(dt, keys_down)
        elif game.state == STATE_GAMEOVER:
            game.update_gameover(dt)

        # ---- draw ----
        screen.fill(BG)
        draw_arena(screen, fonts, game, game.flash_side)
        btn_rect = draw_scorebox(screen, fonts, game, False)

        menu_hover = btn_rect.collidepoint(mouse_pos)
        if menu_hover:
            btn_rect = draw_scorebox(screen, fonts, game, True)
        if menu_hover and mouse_clicked and game.state in (STATE_PLAYING, STATE_TITLE, STATE_GAMEOVER):
            game.state = STATE_CONFIG

        if game.state == STATE_TITLE:
            draw_title(screen, fonts)
        elif game.state == STATE_GAMEOVER:
            draw_gameover(screen, fonts, game)

        if game.state == STATE_CONFIG:
            draw_menu(screen, fonts, menu, game.state)
            if mouse_clicked:
                for i, r in menu.row_rects:
                    if r.collidepoint(mouse_pos):
                        menu.selected = i
                        key = menu.rows[i][0]
                        if key in ("points", "trail", "ball_speed", "ball_size", "goal_size"):
                            # click right half = increase, left half = decrease
                            direction = 1 if mouse_pos[0] > r.centerx else -1
                            menu.change(key, direction)
                        else:
                            menu.activate(key, game)
                        break

        pygame.display.flip()

    pygame.quit()
    sys.exit(0)


if __name__ == "__main__":
    main()
