"""
gate_clip_test.py — Automated gate clip tester with per-tick recording.

How it works (from 4 recorded clips):
  1. Player runs north and hits the gate wall (py freezes ~3495).
  2. Player stays pinned at the wall, re-issuing Player.Move northward every
     few ms to keep pressing.
  3. Eventually enemies arrive at the wall and the collision resolver fires,
     teleporting the player 40-85 units north.
  4. Player drifts into the success polygon a second or two later.

Each attempt writes a JSONL file (same schema as gate_clip_recorder.py) so
you can diagnose why a clip fired or failed.

Press START to begin an attempt.
"""

import collections
import datetime
import json
import math
import os
import time as _time

import PyImGui
from Py4GWCoreLib.Agent import Agent
from Py4GWCoreLib.AgentArray import AgentArray
from Py4GWCoreLib.Camera import Camera
from Py4GWCoreLib.Player import Player
from Py4GWCoreLib.UIManager import UIManager
from Py4GWCoreLib.enums_src.UI_enums import ControlAction
from Py4GWCoreLib.py4gwcorelib_src.Console import ConsoleLog, Console
from Py4GWCoreLib.py4gwcorelib_src.Utils import Utils

# ── Geometry ──────────────────────────────────────────────────────────────────
GATE_WALL_Y  = 3495.0
GATE_X_MIN   = -8973.0
GATE_X_MAX   = -8390.0

# From recordings: player freezes in the range 3483–3525 when pressed against gate
WALL_Y_LO    = 3480.0
WALL_Y_HI    = 3530.0

ENEMY_GATE_DY = 60.0
ENEMY_SCAN_R  = 600.0

SUCCESS_POLYGON = (
    (-8395.18, 3544.36),
    (-8967.01, 3573.01),
    (-9172.54, 4104.49),
    (-8361.22, 4591.23),
)
LOG_TAG = "GateClipTest"
_HERE = os.getcwd()


# ── Tunable parameters ────────────────────────────────────────────────────────

_param_approach_x  = -8639.05
_param_press_far_y = 4210.55
_param_reissue_ms  = 300.0
_param_timeout_ms  = 25_000.0
_param_auto_retry  = True
_param_min_enemies = 1

# Lateral sweep while AT_WALL: right herds enemies along the wall,
# left pushes into them to create the mutual collision
_param_sweep_enabled      = True
_param_sweep_right_ms     = 800.0
_param_sweep_left_ms      = 600.0
# X bounds: prevent player walking into the dip on the east side of the gate.
# From recordings: dip starts near X=-8363; safe east bound is ~-8530.
# West bound keeps the player near the enemy cluster (mobs lure to ~-8821).
_param_sweep_x_min        = -8800.0   # west (left) limit
_param_sweep_x_max        = -8530.0   # east (right) limit — stay clear of dip
# Enemy proximity gate: only strafe when an enemy is this close at the wall.
# If no enemies are nearby, just hold MoveForward and wait.
_param_sweep_trigger_dist = 100.0


# ── Phase ─────────────────────────────────────────────────────────────────────

class Phase:
    IDLE      = "IDLE"
    RUNNING   = "RUNNING"
    SUCCESS   = "SUCCESS"
    DESYNCED  = "DESYNCED"
    FAILED    = "FAILED"

_phase          = Phase.IDLE
_sub_state      = ""
_phase_start_ms = 0.0
_wall_arrive_ms = 0.0

_attempt_num  = 0
_attempt_log: list = []
_event_log: collections.deque = collections.deque(maxlen=25)

_prev_px: float | None = None
_prev_py: float | None = None
_prev_rot: float | None = None
_prev_yaw: float | None = None
_speed_history: collections.deque = collections.deque(maxlen=8)
_last_move_ms = 0.0

_sweep_dir       = "right"   # "right" | "left"
_sweep_switch_ms = 0.0        # absolute ms when direction flips next
_active_keys: set = set()     # keys currently held via UIManager.Keydown


# ── Enemy gate helpers ───────────────────────────────────────────────────────

def _enemy_near_gate(px: float, py: float) -> bool:
    """Return True if any live enemy is within sweep_trigger_dist of the player AND at the gate wall."""
    try:
        for aid in AgentArray.GetEnemyArray():
            if not Agent.IsAlive(aid):
                continue
            ax, ay = Agent.GetXY(aid)
            if abs(ay - GATE_WALL_Y) > ENEMY_GATE_DY:
                continue
            if Utils.Distance((px, py), (ax, ay)) <= _param_sweep_trigger_dist:
                return True
    except Exception:
        pass
    return False


# ── Key hold helpers ─────────────────────────────────────────────────────────

def _hold_key(action: int) -> None:
    if action not in _active_keys:
        UIManager.Keydown(action, 0)
        _active_keys.add(action)

def _release_key(action: int) -> None:
    if action in _active_keys:
        UIManager.Keyup(action, 0)
        _active_keys.discard(action)

def _release_all_keys() -> None:
    for action in list(_active_keys):
        UIManager.Keyup(action, 0)
    _active_keys.clear()


# ── Per-attempt recording ─────────────────────────────────────────────────────

_rec_file      = None
_rec_start_ms  = 0.0
_rec_frames    = 0
_rec_path      = ""


def _rec_open(attempt: int) -> None:
    global _rec_file, _rec_start_ms, _rec_frames, _rec_path
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    _rec_path  = os.path.join(_HERE, f"gct_attempt{attempt:03d}_{ts}.jsonl")
    _rec_file  = open(_rec_path, "w", encoding="utf-8")
    _rec_start_ms = _now_ms()
    _rec_frames   = 0


def _rec_close(result: str) -> None:
    global _rec_file
    if not _rec_file:
        return
    summary = {
        "summary":     True,
        "attempt":     _attempt_num,
        "frames":      _rec_frames,
        "duration_ms": round(_now_ms() - _rec_start_ms, 1),
        "result":      result,
    }
    _rec_file.write(json.dumps(summary) + "\n")
    _rec_file.close()
    _rec_file = None
    _log(f"Saved {_rec_frames}f → {os.path.basename(_rec_path)}")


def _rec_tick(px: float, py: float) -> None:
    global _rec_frames
    if not _rec_file:
        return

    player_id = Player.GetAgentID()
    vx, vy    = Agent.GetVelocityXY(player_id)
    speed     = math.hypot(vx, vy)
    moving    = Agent.IsMoving(player_id)
    rot       = Agent.GetRotationAngle(player_id)
    yaw       = Camera.GetYaw()
    cur_yaw   = Camera.GetCurrentYaw()

    travel_dir = math.atan2(vy, vx) if speed > 0.001 else 0.0
    if speed > 0.001:
        delta = travel_dir - rot
        strafe_angle_d = round(math.degrees(math.atan2(math.sin(delta), math.cos(delta))), 2)
    else:
        strafe_angle_d = 0.0

    dpx       = round(px - _prev_px, 3)  if _prev_px  is not None else 0.0
    dpy       = round(py - _prev_py, 3)  if _prev_py  is not None else 0.0
    yaw_delta = round(yaw - _prev_yaw, 4) if _prev_yaw is not None else 0.0
    rot_delta = round(rot - _prev_rot, 4) if _prev_rot is not None else 0.0

    try:
        kb_rot_ms    = int(Camera.GetTimeSinceLastKeyboardRotation() or 0)
        mouse_rot_ms = int(Camera.GetTimeSinceLastMouseRotation()    or 0)
        mouse_mv_ms  = int(Camera.GetTimeSinceLastMouseMove()        or 0)
    except Exception:
        kb_rot_ms = mouse_rot_ms = mouse_mv_ms = -1

    enemies = []
    try:
        for aid in AgentArray.GetEnemyArray():
            if not Agent.IsAlive(aid):
                continue
            ax, ay   = Agent.GetXY(aid)
            dist     = Utils.Distance((px, py), (ax, ay))
            if dist > ENEMY_SCAN_R:
                continue
            avx, avy = Agent.GetVelocityXY(aid)
            a_rot    = Agent.GetRotationAngle(aid)
            enemies.append({
                "id":      aid,
                "x":       round(ax,   2),
                "y":       round(ay,   2),
                "vx":      round(avx,  4),
                "vy":      round(avy,  4),
                "speed":   round(math.hypot(avx, avy), 4),
                "moving":  Agent.IsMoving(aid),
                "dist":    round(dist, 1),
                "dy_gate": round(ay - GATE_WALL_Y, 2),
                "rot":     round(a_rot, 4),
                "rot_d":   round(math.degrees(a_rot), 2),
            })
    except Exception:
        pass

    row = {
        "t":              round(_now_ms() - _rec_start_ms, 1),
        "phase":          _sub_state,
        "px":             round(px, 2),
        "py":             round(py, 2),
        "dpx":            dpx,
        "dpy":            dpy,
        "vx":             round(vx, 4),
        "vy":             round(vy, 4),
        "speed":          round(speed, 4),
        "travel_dir_d":   round(math.degrees(travel_dir), 2),
        "moving":         moving,
        "rot":            round(rot, 4),
        "rot_d":          round(math.degrees(rot), 2),
        "rot_delta":      rot_delta,
        "strafe_angle_d": strafe_angle_d,
        "yaw":            round(yaw, 4),
        "yaw_d":          round(math.degrees(yaw), 2),
        "cur_yaw":        round(cur_yaw, 4),
        "yaw_delta":      yaw_delta,
        "kb_rot_ms":      kb_rot_ms,
        "mouse_rot_ms":   mouse_rot_ms,
        "mouse_mv_ms":    mouse_mv_ms,
        "success":        _point_in_polygon(px, py),
        "enemies":        enemies,
    }
    _rec_file.write(json.dumps(row) + "\n")
    _rec_frames += 1


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_ms() -> float:
    return _time.monotonic() * 1_000.0


def _point_in_polygon(px: float, py: float) -> bool:
    inside = False
    n = len(SUCCESS_POLYGON)
    j = n - 1
    for i in range(n):
        xi, yi = SUCCESS_POLYGON[i]
        xj, yj = SUCCESS_POLYGON[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _log(msg: str) -> None:
    ConsoleLog(LOG_TAG, msg, Console.MessageType.Info)
    _event_log.appendleft((_now_ms(), msg))


def _set_phase(new_phase: str, sub: str = "") -> None:
    global _phase, _sub_state, _phase_start_ms
    _log(f"{_phase}/{_sub_state} → {new_phase}/{sub}")
    _phase          = new_phase
    _sub_state      = sub
    _phase_start_ms = _now_ms()


def _start() -> None:
    global _attempt_num, _wall_arrive_ms, _prev_px, _prev_py, _prev_rot, _prev_yaw, _last_move_ms
    global _sweep_dir, _sweep_switch_ms
    _release_all_keys()
    _attempt_num   += 1
    _wall_arrive_ms = 0.0
    _prev_px = _prev_py = _prev_rot = _prev_yaw = None
    _last_move_ms = 0.0
    _sweep_dir = "right"
    _sweep_switch_ms = 0.0
    _speed_history.clear()
    _rec_open(_attempt_num)
    _set_phase(Phase.RUNNING, "APPROACHING")
    _log(f"Attempt #{_attempt_num} started")


def _stop() -> None:
    _release_all_keys()
    _rec_close("user_stop")
    _set_phase(Phase.IDLE)
    _log("Stopped by user")


def _record_attempt(result: str) -> None:
    _release_all_keys()
    _attempt_log.append((_attempt_num, result))
    _log(f"Attempt #{_attempt_num}: {result}")
    _rec_close(result)


# ── Per-tick update ───────────────────────────────────────────────────────────

def _update() -> None:
    global _prev_px, _prev_py, _prev_rot, _prev_yaw, _wall_arrive_ms, _last_move_ms
    global _sweep_dir, _sweep_switch_ms

    px, py = Player.GetXY()

    if _phase not in (Phase.RUNNING,):
        _prev_px, _prev_py = px, py
        return

    vx, vy = Agent.GetVelocityXY(Player.GetAgentID())
    speed  = math.hypot(vx, vy)

    dpx  = px - _prev_px if _prev_px is not None else 0.0
    dpy  = py - _prev_py if _prev_py is not None else 0.0
    jump = math.hypot(dpx, dpy)

    _speed_history.append(speed)

    rot = Agent.GetRotationAngle(Player.GetAgentID())
    yaw = Camera.GetYaw()

    _rec_tick(px, py)

    _prev_px, _prev_py = px, py
    _prev_rot, _prev_yaw = rot, yaw

    now = _now_ms()

    # ── success polygon check ──────────────────────────────────────────────
    if _point_in_polygon(px, py):
        avg = sum(_speed_history) / len(_speed_history) if _speed_history else 0.0
        # Keyboard-movement clips produce large jumps (300+ units), so jump alone
        # is not a reliable discriminator. A real clip always has speed > 0 at
        # the teleport frame; a server desync while standing still has speed ≈ 0.
        if speed < 5.0 and avg < 5.0:
            _set_phase(Phase.DESYNCED)
            _record_attempt(f"DESYNC  jump={jump:.1f} speed={speed:.0f}")
        else:
            _set_phase(Phase.SUCCESS)
            _record_attempt(f"CLIPPED  jump={jump:.1f} speed={speed:.0f} wall_time={(now - _wall_arrive_ms)/1000:.1f}s")
        return

    # ── sub-state transitions ──────────────────────────────────────────────
    if _sub_state == "APPROACHING":
        if WALL_Y_LO <= py <= WALL_Y_HI:
            _wall_arrive_ms = now
            _sweep_dir = "right"
            _sweep_switch_ms = now + _param_sweep_right_ms
            if _param_sweep_enabled:
                _hold_key(ControlAction.ControlAction_MoveForward.value)
            _set_phase(Phase.RUNNING, "AT_WALL")

    elif _sub_state == "AT_WALL":
        if _param_sweep_enabled:
            # Keep forward held throughout
            _hold_key(ControlAction.ControlAction_MoveForward.value)

            # Advance sweep timer
            if now >= _sweep_switch_ms:
                if _sweep_dir == "right":
                    _sweep_dir = "left"
                    _sweep_switch_ms = now + _param_sweep_left_ms
                else:
                    _sweep_dir = "right"
                    _sweep_switch_ms = now + _param_sweep_right_ms

            # Only strafe when an enemy is at the gate nearby AND we're within X bounds
            enemy_near     = _enemy_near_gate(px, py)
            at_right_bound = px >= _param_sweep_x_max
            at_left_bound  = px <= _param_sweep_x_min

            if enemy_near and _sweep_dir == "right" and not at_right_bound:
                _hold_key(ControlAction.ControlAction_StrafeRight.value)
                _release_key(ControlAction.ControlAction_StrafeLeft.value)
            elif enemy_near and _sweep_dir == "left" and not at_left_bound:
                _hold_key(ControlAction.ControlAction_StrafeLeft.value)
                _release_key(ControlAction.ControlAction_StrafeRight.value)
            else:
                # No enemies nearby or at boundary — stop strafing, just press forward
                _release_key(ControlAction.ControlAction_StrafeRight.value)
                _release_key(ControlAction.ControlAction_StrafeLeft.value)

        if py < WALL_Y_LO - 50.0:
            _release_all_keys()
            _wall_arrive_ms = 0.0
            _set_phase(Phase.RUNNING, "APPROACHING")

        elif now - _phase_start_ms >= _param_timeout_ms:
            _set_phase(Phase.FAILED)
            _record_attempt(f"TIMEOUT  {_param_timeout_ms/1000:.0f}s  wall_time={(now - _wall_arrive_ms)/1000:.1f}s")
            if _param_auto_retry:
                _log("Auto-retry…")
                _start()
            return

    # ── issue Player.Move (only when not using held keys) ─────────────────
    if _sub_state == "APPROACHING" or not _param_sweep_enabled:
        if now - _last_move_ms >= _param_reissue_ms:
            try:
                Player.Move(_param_approach_x, _param_press_far_y)
            except Exception as exc:
                _log(f"Player.Move error: {exc}")
            _last_move_ms = now


# ── Enemy scan ────────────────────────────────────────────────────────────────

def _scan_enemies(px: float, py: float) -> tuple[list, int]:
    nearby = []
    try:
        for aid in AgentArray.GetEnemyArray():
            if not Agent.IsAlive(aid):
                continue
            ax, ay = Agent.GetXY(aid)
            dist = Utils.Distance((px, py), (ax, ay))
            if dist > ENEMY_SCAN_R:
                continue
            avx, avy = Agent.GetVelocityXY(aid)
            at_gate = (GATE_WALL_Y - ENEMY_GATE_DY) <= ay <= (GATE_WALL_Y + 10.0)
            nearby.append({
                "id":      aid,
                "x":       ax,
                "y":       ay,
                "speed":   math.hypot(avx, avy),
                "dist":    dist,
                "dy":      ay - GATE_WALL_Y,
                "at_gate": at_gate,
            })
    except Exception:
        pass
    nearby.sort(key=lambda e: e["dist"])
    at_gate = sum(1 for e in nearby if e["at_gate"])
    return nearby, at_gate


# ── ImGui window ──────────────────────────────────────────────────────────────

_PHASE_COL = {
    Phase.IDLE:     (0.6, 0.6, 0.6, 1.0),
    Phase.RUNNING:  (0.3, 0.8, 1.0, 1.0),
    Phase.SUCCESS:  (0.2, 1.0, 0.3, 1.0),
    Phase.DESYNCED: (1.0, 0.2, 0.2, 1.0),
    Phase.FAILED:   (1.0, 0.5, 0.1, 1.0),
}

_SUB_COL = {
    "APPROACHING": (0.6, 0.8, 1.0, 1.0),
    "AT_WALL":     (1.0, 0.85, 0.0, 1.0),
}


def _draw_window() -> None:
    global _param_approach_x, _param_press_far_y, _param_reissue_ms
    global _param_timeout_ms, _param_auto_retry, _param_min_enemies
    global _param_sweep_enabled, _param_sweep_right_ms, _param_sweep_left_ms
    global _param_sweep_x_min, _param_sweep_x_max, _param_sweep_trigger_dist

    if not PyImGui.begin("Gate Clip Test"):
        PyImGui.end()
        return

    px, py = Player.GetXY()
    vx, vy = Agent.GetVelocityXY(Player.GetAgentID())
    speed  = math.hypot(vx, vy)
    dpx    = (px - _prev_px) if _prev_px is not None else 0.0
    dpy    = (py - _prev_py) if _prev_py is not None else 0.0
    jump   = math.hypot(dpx, dpy)

    nearby, at_gate_count = _scan_enemies(px, py)

    now     = _now_ms()
    elapsed = (now - _phase_start_ms) / 1000.0

    # ── Phase / control row ───────────────────────────────────────────────
    col = _PHASE_COL.get(_phase, (1, 1, 1, 1))
    PyImGui.push_style_color(PyImGui.ImGuiCol.Text, col)
    PyImGui.text(f"Phase: {_phase}")
    PyImGui.pop_style_color(1)

    if _sub_state:
        sc = _SUB_COL.get(_sub_state, (1, 1, 1, 1))
        PyImGui.push_style_color(PyImGui.ImGuiCol.Text, sc)
        PyImGui.same_line(0, -1)
        PyImGui.text(f"/ {_sub_state}  ({elapsed:.1f}s)")
        PyImGui.pop_style_color(1)
    else:
        PyImGui.same_line(0, -1)
        PyImGui.text(f"  ({elapsed:.1f}s)")

    if _phase == Phase.IDLE:
        if PyImGui.button("START"):
            _start()
    else:
        if PyImGui.button("STOP"):
            _stop()

    PyImGui.same_line(0, -1)
    if PyImGui.button("RESET ALL"):
        global _attempt_num, _attempt_log
        _attempt_num = 0
        _attempt_log.clear()
        _event_log.clear()
        _stop()

    PyImGui.separator()

    # ── Player metrics ────────────────────────────────────────────────────
    PyImGui.text(f"X={px:9.2f}   Y={py:9.2f}   dY={dpy:+6.2f}   jump={jump:.2f}")
    PyImGui.text(f"speed={speed:6.1f}   vY={vy:+7.2f}   dY_gate={py - GATE_WALL_Y:+.2f}")
    avg_spd = sum(_speed_history) / len(_speed_history) if _speed_history else 0.0
    PyImGui.text(f"avg_speed(8f)={avg_spd:.1f}   in_polygon={'YES' if _point_in_polygon(px, py) else 'no'}")

    if _sub_state == "AT_WALL" and _wall_arrive_ms > 0.0:
        wt = (now - _wall_arrive_ms) / 1000.0
        PyImGui.text(f"Time at wall: {wt:.1f}s")

    if _rec_file:
        PyImGui.text(f"Recording: {_rec_frames}f  {os.path.basename(_rec_path)}")

    PyImGui.separator()

    # ── Enemy table ───────────────────────────────────────────────────────
    ready_col = (0.2, 1.0, 0.3, 1.0) if at_gate_count >= _param_min_enemies else (1.0, 0.5, 0.1, 1.0)
    PyImGui.push_style_color(PyImGui.ImGuiCol.Text, ready_col)
    PyImGui.text(f"Enemies at gate: {at_gate_count} / {len(nearby)} nearby   (need {_param_min_enemies})")
    PyImGui.pop_style_color(1)
    for e in nearby[:8]:
        tag = "[G]" if e["at_gate"] else "   "
        mv  = "v" if e["speed"] > 5 else "."
        PyImGui.text(f"  {tag}[{e['id']}] d={e['dist']:5.0f}  dY={e['dy']:+6.1f}  spd={e['speed']:5.0f} {mv}")

    PyImGui.separator()

    # ── Parameters ───────────────────────────────────────────────────────
    PyImGui.text("Parameters")
    _param_approach_x  = PyImGui.slider_float("approach X##gc",  _param_approach_x,  GATE_X_MIN, GATE_X_MAX)
    _param_press_far_y = PyImGui.slider_float("press far Y##gc", _param_press_far_y, 3550.0, 12000.0)
    _param_reissue_ms  = PyImGui.slider_float("reissue ms##gc",  _param_reissue_ms,  50.0, 1000.0)
    _param_timeout_ms  = PyImGui.slider_float("timeout ms##gc",  _param_timeout_ms,  5000.0, 60000.0)
    _param_min_enemies = PyImGui.slider_int(  "min enemies##gc", _param_min_enemies, 1, 8)
    _param_auto_retry  = PyImGui.checkbox(    "auto-retry##gc",  _param_auto_retry)

    PyImGui.text("Lateral sweep (AT_WALL only)")
    _param_sweep_enabled      = PyImGui.checkbox(    "sweep enabled##gc",      _param_sweep_enabled)
    _param_sweep_right_ms     = PyImGui.slider_float("sweep right ms##gc",     _param_sweep_right_ms,     100.0, 3000.0)
    _param_sweep_left_ms      = PyImGui.slider_float("sweep left ms##gc",      _param_sweep_left_ms,      100.0, 3000.0)
    _param_sweep_x_min        = PyImGui.slider_float("sweep X min (W)##gc",    _param_sweep_x_min,        GATE_X_MIN, GATE_X_MAX)
    _param_sweep_x_max        = PyImGui.slider_float("sweep X max (E)##gc",    _param_sweep_x_max,        GATE_X_MIN, GATE_X_MAX)
    _param_sweep_trigger_dist = PyImGui.slider_float("enemy trigger dist##gc", _param_sweep_trigger_dist, 20.0, 300.0)
    if _sub_state == "AT_WALL" and _param_sweep_enabled:
        remaining = max(0.0, (_sweep_switch_ms - _now_ms()) / 1000.0)
        has_enemy = _enemy_near_gate(px, py)
        enemy_tag = "ENEMY NEAR" if has_enemy else "waiting for enemy"
        PyImGui.text(f"  {_sweep_dir.upper()}  switch in {remaining:.1f}s  [{enemy_tag}]")

    PyImGui.separator()

    # ── Attempt history ───────────────────────────────────────────────────
    PyImGui.text(f"Attempts: {_attempt_num}")
    for attempt, result in reversed(_attempt_log[-8:]):
        c = (0.2, 1.0, 0.3, 1.0) if "CLIPPED" in result else (
            (1.0, 0.2, 0.2, 1.0) if "DESYNC"  in result else (1.0, 0.5, 0.1, 1.0)
        )
        PyImGui.push_style_color(PyImGui.ImGuiCol.Text, c)
        PyImGui.text(f"  #{attempt}: {result}")
        PyImGui.pop_style_color(1)

    PyImGui.separator()

    # ── Event log ─────────────────────────────────────────────────────────
    PyImGui.text("Events:")
    for t, msg in list(_event_log)[:12]:
        age = (now - t) / 1000.0
        PyImGui.text(f"  [{age:5.1f}s] {msg}")

    PyImGui.end()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    _update()
    _draw_window()


if __name__ == "__main__":
    main()
