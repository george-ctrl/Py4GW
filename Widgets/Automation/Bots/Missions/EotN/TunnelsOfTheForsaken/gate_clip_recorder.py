"""
gate_clip_recorder.py — Record full game state while performing the gate clip manually.

Usage:
    1. Load this widget.
    2. Walk to the gate area.
    3. Press START — recording begins.
    4. Perform the clip manually.
    5. Press STOP (or let it auto-stop on success detection).
    6. The log is written to gate_clip_YYYYMMDD_HHMMSS.jsonl next to this file.

Each tick one JSON line is appended:
    {
      "t":            ms since recording started,

      -- Position --
      "px":           player x,
      "py":           player y,
      "dpx":          x delta since last frame,
      "dpy":          y delta since last frame,

      -- Velocity / movement --
      "vx":           player velocity x,
      "vy":           player velocity y,
      "speed":        |velocity| (magnitude),
      "travel_dir_d": direction of travel in degrees (atan2 of velocity),
      "moving":       Agent.IsMoving bool,

      -- Player body facing --
      "rot":          body rotation angle (radians),
      "rot_d":        body rotation (degrees),
      "rot_delta":    change in body rotation since last frame (radians),
      "strafe_angle_d": angle between body facing and travel direction (degrees);
                        0 = running straight, ±90 = pure strafe, 180 = walking backward,

      -- Camera / input proxies --
      "yaw":          Camera.GetYaw() — radians,
      "yaw_d":        camera yaw in degrees,
      "cur_yaw":      Camera.GetCurrentYaw() — actual rendered yaw,
      "yaw_delta":    change in camera yaw since last frame (radians),
      "kb_rot_ms":    ms since last keyboard camera-rotation event (0 = key held now),
      "mouse_rot_ms": ms since last mouse camera-rotation event,
      "mouse_mv_ms":  ms since last any mouse move,

      "success":      true if player is inside SUCCESS_POLYGON this tick,

      "enemies": [
        {
          "id":      agent id,
          "x":       x,
          "y":       y,
          "vx":      velocity x,
          "vy":      velocity y,
          "speed":   |velocity|,
          "moving":  bool,
          "dist":    distance to player,
          "dy_gate": enemy.y - GATE_WALL_Y  (positive = north of gate),
          "rot":     body rotation angle (radians),
          "rot_d":   body rotation (degrees)
        },
        ...
      ]
    }

A final summary line is appended when recording stops:
    {"summary": true, "frames": N, "duration_ms": T, "clipped": bool}
"""

import json
import math
import os
import time as _time
import datetime

import PyImGui
from Py4GWCoreLib.Agent import Agent
from Py4GWCoreLib.AgentArray import AgentArray
from Py4GWCoreLib.Camera import Camera
from Py4GWCoreLib.Player import Player
from Py4GWCoreLib.py4gwcorelib_src.Utils import Utils

# ── Gate geometry (must match constants.py) ───────────────────────────────────
GATE_WALL_Y = 3495.0
SUCCESS_POLYGON = (
    (-8395.18, 3544.36),
    (-8967.01, 3573.01),
    (-9172.54, 4104.49),
    (-8361.22, 4591.23),
)
SCAN_RADIUS = 800.0   # record enemies within this distance

_HERE = os.getcwd()

# ── Module state ──────────────────────────────────────────────────────────────
_recording   = False
_frames      = 0
_start_ms    = 0.0
_clipped     = False
_log_path    = ""
_log_file    = None
_status_msg  = "Idle"

# Per-frame previous values for delta computation
_prev_px:  float | None = None
_prev_py:  float | None = None
_prev_yaw: float | None = None
_prev_rot: float | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_ms() -> float:
    return _time.monotonic() * 1_000.0


def _point_in_polygon(px: float, py: float, polygon: tuple) -> bool:
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _start_recording() -> None:
    global _recording, _frames, _start_ms, _clipped, _log_path, _log_file, _status_msg
    global _prev_px, _prev_py, _prev_yaw, _prev_rot
    ts        = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    _log_path = os.path.join(_HERE, f"gate_clip_{ts}.jsonl")
    _log_file = open(_log_path, "w", encoding="utf-8")
    _frames   = 0
    _start_ms = _now_ms()
    _clipped  = False
    _prev_px  = _prev_py = _prev_yaw = _prev_rot = None
    _recording = True
    _status_msg = f"Recording → {os.path.basename(_log_path)}"


def _stop_recording(reason: str = "user") -> None:
    global _recording, _log_file, _status_msg
    if not _recording:
        return
    _recording = False
    duration   = _now_ms() - _start_ms
    summary    = {
        "summary":     True,
        "frames":      _frames,
        "duration_ms": round(duration, 1),
        "clipped":     _clipped,
        "stop_reason": reason,
    }
    if _log_file:
        _log_file.write(json.dumps(summary) + "\n")
        _log_file.close()
        _log_file = None
    _status_msg = (
        f"Saved {_frames} frames ({duration/1000:.1f}s) — "
        f"{'CLIPPED' if _clipped else 'no clip'} — {reason}"
    )


def _record_tick() -> None:
    global _frames, _clipped, _prev_px, _prev_py, _prev_yaw, _prev_rot

    player_id = Player.GetAgentID()
    px, py    = Player.GetXY()
    vx, vy    = Agent.GetVelocityXY(player_id)
    moving    = Agent.IsMoving(player_id)
    rot       = Agent.GetRotationAngle(player_id)
    yaw       = Camera.GetYaw()
    cur_yaw   = Camera.GetCurrentYaw()
    success   = _point_in_polygon(px, py, SUCCESS_POLYGON)

    # ── derived movement quantities ───────────────────────────────────────
    speed      = math.hypot(vx, vy)
    travel_dir = math.atan2(vy, vx) if speed > 0.001 else 0.0

    # Strafe angle: signed angle between body facing and travel direction.
    # 0° = running forward, 180°/−180° = walking backward, ±90° = pure strafe.
    if speed > 0.001:
        delta = travel_dir - rot
        strafe_angle_d = round(math.degrees(math.atan2(math.sin(delta), math.cos(delta))), 2)
    else:
        strafe_angle_d = 0.0

    # ── per-frame deltas ──────────────────────────────────────────────────
    dpx       = round(px - _prev_px, 3)  if _prev_px  is not None else 0.0
    dpy       = round(py - _prev_py, 3)  if _prev_py  is not None else 0.0
    yaw_delta = round(yaw - _prev_yaw, 4) if _prev_yaw is not None else 0.0
    rot_delta = round(rot - _prev_rot, 4) if _prev_rot is not None else 0.0

    _prev_px, _prev_py, _prev_yaw, _prev_rot = px, py, yaw, rot

    # ── camera / input-proxy timings ──────────────────────────────────────
    try:
        kb_rot_ms    = int(Camera.GetTimeSinceLastKeyboardRotation() or 0)
        mouse_rot_ms = int(Camera.GetTimeSinceLastMouseRotation()    or 0)
        mouse_mv_ms  = int(Camera.GetTimeSinceLastMouseMove()        or 0)
    except Exception:
        kb_rot_ms = mouse_rot_ms = mouse_mv_ms = -1

    if success and not _clipped:
        _clipped = True

    # ── enemy scan ────────────────────────────────────────────────────────
    enemies = []
    try:
        for aid in AgentArray.GetEnemyArray():
            if not Agent.IsAlive(aid):
                continue
            ax, ay  = Agent.GetXY(aid)
            dist    = Utils.Distance((px, py), (ax, ay))
            if dist > SCAN_RADIUS:
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
        "t":             round(_now_ms() - _start_ms, 1),
        # position
        "px":            round(px, 2),
        "py":            round(py, 2),
        "dpx":           dpx,
        "dpy":           dpy,
        # velocity / movement
        "vx":            round(vx, 4),
        "vy":            round(vy, 4),
        "speed":         round(speed, 4),
        "travel_dir_d":  round(math.degrees(travel_dir), 2),
        "moving":        moving,
        # player body facing
        "rot":           round(rot, 4),
        "rot_d":         round(math.degrees(rot), 2),
        "rot_delta":     rot_delta,
        "strafe_angle_d": strafe_angle_d,
        # camera / input proxies
        "yaw":           round(yaw, 4),
        "yaw_d":         round(math.degrees(yaw), 2),
        "cur_yaw":       round(cur_yaw, 4),
        "yaw_delta":     yaw_delta,
        "kb_rot_ms":     kb_rot_ms,
        "mouse_rot_ms":  mouse_rot_ms,
        "mouse_mv_ms":   mouse_mv_ms,
        # outcome
        "success":       success,
        "enemies":       enemies,
    }

    _log_file.write(json.dumps(row) + "\n")
    _frames += 1


# ── ImGui window ──────────────────────────────────────────────────────────────

def _draw_window() -> None:
    if not PyImGui.begin("Gate Clip Recorder"):
        PyImGui.end()
        return

    px, py = Player.GetXY()
    yaw    = Camera.GetYaw()
    rot    = Agent.GetRotationAngle(Player.GetAgentID())

    # Start / Stop button
    if _recording:
        if PyImGui.button("STOP"):
            _stop_recording("user")
    else:
        if PyImGui.button("START"):
            _start_recording()

    PyImGui.separator()
    PyImGui.text(_status_msg)
    if _recording:
        elapsed = (_now_ms() - _start_ms) / 1_000.0
        PyImGui.text(f"Frames: {_frames}   Elapsed: {elapsed:.1f}s")

    PyImGui.separator()
    PyImGui.text(f"Pos    X={px:.1f}  Y={py:.2f}")
    PyImGui.text(f"dY gate  {py - GATE_WALL_Y:+.2f}")
    PyImGui.text(f"Yaw    {math.degrees(yaw):.1f}°   Body rot {math.degrees(rot):.1f}°")
    try:
        kb_ms    = int(Camera.GetTimeSinceLastKeyboardRotation() or 0)
        mouse_ms = int(Camera.GetTimeSinceLastMouseRotation()    or 0)
        PyImGui.text(f"Kbd rot  {kb_ms}ms ago   Mouse rot  {mouse_ms}ms ago")
    except Exception:
        pass
    in_poly = _point_in_polygon(px, py, SUCCESS_POLYGON)
    PyImGui.text(f"In success polygon: {'YES' if in_poly else 'no'}")

    PyImGui.separator()
    try:
        hostiles = []
        for aid in AgentArray.GetEnemyArray():
            if not Agent.IsAlive(aid):
                continue
            ax, ay = Agent.GetXY(aid)
            dist   = Utils.Distance((px, py), (ax, ay))
            if dist < SCAN_RADIUS:
                hostiles.append((dist, aid, ax, ay))
        hostiles.sort()
        PyImGui.text(f"Nearby enemies: {len(hostiles)}")
        for dist, aid, ax, ay in hostiles[:6]:
            mvg  = "MOV" if Agent.IsMoving(aid) else "idl"
            a_rot = Agent.GetRotationAngle(aid)
            PyImGui.text(
                f"  [{aid}] {mvg}  d={dist:.0f}  dY={ay-GATE_WALL_Y:+.1f}"
                f"  rot={math.degrees(a_rot):.0f}°"
            )
    except Exception:
        pass

    PyImGui.end()


# ── Auto-stop after clip settles ──────────────────────────────────────────────

_clip_landed_ms: float = 0.0
_AUTO_STOP_AFTER_MS = 2_000


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    global _clip_landed_ms

    _draw_window()

    if not _recording:
        return

    _record_tick()

    # Mark when the clip first lands so we can auto-stop shortly after
    if _clipped and _clip_landed_ms == 0.0:
        _clip_landed_ms = _now_ms()

    if _clip_landed_ms > 0.0 and _now_ms() - _clip_landed_ms >= _AUTO_STOP_AFTER_MS:
        _stop_recording("auto — clip detected")
        _clip_landed_ms = 0.0


if __name__ == "__main__":
    main()
