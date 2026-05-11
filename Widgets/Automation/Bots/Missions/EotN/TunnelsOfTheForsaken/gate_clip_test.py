"""
gate_clip_test.py — TotF gate clip test (simplified).

Algorithm:
    1. Walk normally to APPROACH_POS.
    2. Wait for at least one hostile in MOB_WAIT_RADIUS.
    3. Set camera yaw to CLIP_CAMERA_YAW (face west for correct MoveBackward direction).
    4. Hold MoveBackward until within ARRIVAL_DIST of CLIP_POS_1.
    5. Release backward, hold Forward+StrafeRight for up to CLIP_DURATION_MS.
    6. If player Y > SUCCESS_Y → clip succeeded → navigate to DEST_POS.
    7. Else hold MoveBackward until within ARRIVAL_DIST of CLIP_POS_2, retry.
    8. If still failed → restart from step 1.
"""

import math

import PyImGui
from Py4GWCoreLib import *
from Py4GWCoreLib.GlobalCache import GLOBAL_CACHE
from Py4GWCoreLib.Player import Player
from Py4GWCoreLib.Agent import Agent
from Py4GWCoreLib.AgentArray import AgentArray
from Py4GWCoreLib.Camera import Camera
from Py4GWCoreLib.UIManager import UIManager
from Py4GWCoreLib.enums_src.UI_enums import ControlAction
from Py4GWCoreLib.Py4GWcorelib import Utils, ConsoleLog, Console

MODULE_NAME = "GateClip Test"
_LOG_SRC    = "GateClip"

# ── Skill IDs ──────────────────────────────────────────────────────────────────
SF_ID  = 826    # Shadow Form
SOD_ID = 1031   # Shroud of Distress
DP_ID  = 572    # Deadly Paradox

# ── Upkeep timing ──────────────────────────────────────────────────────────────
SF_RECAST_BUFFER_MS  = 3_000
SOD_RECAST_BUFFER_MS = 3_000
CAST_COOLDOWN_MS     = 250

# ── Positions ──────────────────────────────────────────────────────────────────
APPROACH_POS  = (-8835.32, 3495.77)  # walk normally here first
CLIP_POS_1    = (-8617.52, 3495.38)  # first clip attempt (walk backwards here)
CLIP_POS_2    = (-8529.51, 3495.23)  # second clip attempt (walk backwards here)
DEST_POS      = (-8585.0,  5367.0)   # destination after a successful clip

# ── Gate geometry ──────────────────────────────────────────────────────────────
SUCCESS_Y = 3515.0  # player Y > this = clipped through the gate

# ── Camera ─────────────────────────────────────────────────────────────────────
# GW yaw: 0 = east, π/2 = north, ±π = west, -π/2 = south (standard math radians).
# With camera facing west: MoveBackward drives east (+X) toward clip positions,
# and StrafeRight drives in the perpendicular direction for the clip attempt.
# Tune this if backward movement goes the wrong way.
WALK_CAMERA_YAW = math.pi  # 180° — used while walking backwards
CLIP_CAMERA_YAW = 3.022    # 173.1° — used during clip attempt

# ── Mob detection ──────────────────────────────────────────────────────────────
MOB_WAIT_RADIUS = 600.0  # wait for a hostile within this range before proceeding

# ── Tuning ─────────────────────────────────────────────────────────────────────
ARRIVAL_DIST        = 80.0   # close enough to a walk target
APPROACH_WAIT_MS    = 2_000  # wait at approach pos (camera set) before walking backwards
LURE_WAIT_MS        = 3_000  # wait at clip position for mobs to arrive before clipping
PRE_CLIP_WAIT_MS    = 2_000  # settle time between lure wait and pressing clip keys
CLIP_DURATION_MS    = 2_500  # how long to hold Forward+StrafeRight per attempt
APPROACH_REISSUE_MS = 500    # re-issue Player.Move if still not arrived

# ── Log throttle intervals ────────────────────────────────────────────────────
_TICK_LOG_MS   = 500
_UPKEEP_LOG_MS = 5_000

# ── Module state ───────────────────────────────────────────────────────────────
_running          = False
_state            = "idle"
_backward_held    = False
_clip_keys_held   = False
_last_cast_ms     = 0
_last_move_ms     = 0
_state_enter_ms   = 0
_retry_count      = 0

_last_log_ms: dict[str, int] = {}


# ── Logging ────────────────────────────────────────────────────────────────────

def _log(msg: str, level=Console.MessageType.Info):
    ConsoleLog(_LOG_SRC, msg, level, log=True)

def _log_warn(msg: str):
    ConsoleLog(_LOG_SRC, msg, Console.MessageType.Warning, log=True)

def _log_ok(msg: str):
    ConsoleLog(_LOG_SRC, msg, Console.MessageType.Success, log=True)

def _log_throttled(key: str, msg: str, level=Console.MessageType.Info):
    now = _now_ms()
    last = _last_log_ms.get(key)
    if last is None or now - last >= _TICK_LOG_MS:
        ConsoleLog(_LOG_SRC, msg, level, log=True)
        _last_log_ms[key] = now

def _log_upkeep(key: str, msg: str):
    now = _now_ms()
    if now - _last_log_ms.get(key, 0) >= _UPKEEP_LOG_MS:
        ConsoleLog(_LOG_SRC, msg, Console.MessageType.Debug, log=True)
        _last_log_ms[key] = now


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now_ms() -> int:
    return Utils.GetBaseTimestamp()

def _elapsed() -> int:
    return _now_ms() - _state_enter_ms

def _player_id() -> int:
    return Player.GetAgentID()

def _has_effect(skill_id: int) -> bool:
    return GLOBAL_CACHE.Effects.HasEffect(_player_id(), skill_id)

def _effect_remaining_ms(skill_id: int) -> float:
    return GLOBAL_CACHE.Effects.GetEffectTimeRemaining(_player_id(), skill_id)

def _slot_for(skill_id: int) -> int:
    for slot in range(1, 9):
        if GLOBAL_CACHE.SkillBar.GetSkillIDBySlot(slot) == skill_id:
            return slot
    return 0

def _try_cast(skill_id: int, label: str) -> bool:
    global _last_cast_ms
    now = _now_ms()
    if now - _last_cast_ms < CAST_COOLDOWN_MS:
        return False
    slot = _slot_for(skill_id)
    if not slot:
        _log_warn(f"[{label}] not on skill bar (ID={skill_id})")
        return False
    GLOBAL_CACHE.SkillBar.UseSkill(slot)
    _last_cast_ms = now
    return True


# ── Key management ─────────────────────────────────────────────────────────────

def _hold_backward():
    global _backward_held
    if not _backward_held:
        UIManager.Keydown(ControlAction.ControlAction_MoveBackward.value, 0)
        _backward_held = True
        _log("Keydown: MoveBackward")

def _release_backward():
    global _backward_held
    if _backward_held:
        UIManager.Keyup(ControlAction.ControlAction_MoveBackward.value, 0)
        _backward_held = False
        _log("Keyup: MoveBackward")

def _hold_clip_keys():
    global _clip_keys_held
    if not _clip_keys_held:
        UIManager.Keydown(ControlAction.ControlAction_MoveForward.value, 0)
        UIManager.Keydown(ControlAction.ControlAction_StrafeRight.value, 0)
        _clip_keys_held = True
        _log("Keydown: MoveForward + StrafeRight")

def _release_clip_keys():
    global _clip_keys_held
    if _clip_keys_held:
        UIManager.Keyup(ControlAction.ControlAction_MoveForward.value, 0)
        UIManager.Keyup(ControlAction.ControlAction_StrafeRight.value, 0)
        _clip_keys_held = False
        _log("Keyup: MoveForward + StrafeRight")

def _release_all_keys():
    _release_backward()
    _release_clip_keys()


# ── State machine ──────────────────────────────────────────────────────────────

def _transition(new_state: str, msg: str = "", level=Console.MessageType.Info):
    global _state, _state_enter_ms
    prev = _state
    _state          = new_state
    _state_enter_ms = _now_ms()
    px, py = Player.GetXY()
    detail = f" — {msg}" if msg else ""
    ConsoleLog(
        _LOG_SRC,
        f"[{prev}] -> [{new_state}]{detail}  pos=({px:.1f}, {py:.1f})",
        level,
        log=True,
    )


def _tick_approach():
    global _last_move_ms
    px, py = Player.GetXY()
    dist   = Utils.Distance((px, py), APPROACH_POS)

    _log_throttled(
        "approach",
        f"Walking to approach — dist={dist:.1f}  pos=({px:.1f}, {py:.1f})",
    )

    if dist < ARRIVAL_DIST:
        _transition("wait_for_mobs", f"arrived dist={dist:.1f}")
        return

    now = _now_ms()
    if now - _last_move_ms > APPROACH_REISSUE_MS:
        Player.Move(*APPROACH_POS)
        _last_move_ms = now


def _nearby_hostiles() -> list[int]:
    px, py = Player.GetXY()
    result = []
    try:
        for agent_id in AgentArray.GetEnemyArray():
            if not Agent.IsAlive(agent_id):
                continue
            ax, ay = Agent.GetXY(agent_id)
            if Utils.Distance((px, py), (ax, ay)) < MOB_WAIT_RADIUS:
                result.append(agent_id)
    except Exception as exc:
        _log_warn(f"AgentArray query failed: {exc}")
    return result


def _tick_wait_for_mobs():
    hostiles = _nearby_hostiles()
    count    = len(hostiles)
    _log_throttled(
        "wait_mobs",
        f"Waiting for mobs — {count} hostile(s) in range (radius={MOB_WAIT_RADIUS})",
    )
    if count > 0:
        _transition("approach_wait", f"{count} hostile(s) detected")



def _tick_walk_back(target: tuple[float, float], next_state: str):
    """Hold MoveBackward until player is within ARRIVAL_DIST of target."""
    _hold_backward()
    px, py = Player.GetXY()
    dist   = Utils.Distance((px, py), target)

    _log_throttled(
        "walk_back",
        f"Walking back to ({target[0]:.1f}, {target[1]:.1f}) — "
        f"dist={dist:.1f}  pos=({px:.1f}, {py:.1f})",
    )

    if dist < ARRIVAL_DIST:
        _release_backward()
        _transition(next_state, f"arrived dist={dist:.1f}")


def _tick_clip(fail_state: str):
    """Hold Forward+StrafeRight, check success, transition on timeout."""
    _hold_clip_keys()
    _, py = Player.GetXY()

    _log_throttled(
        "clip",
        f"Clipping — Y={py:.2f}  target Y>{SUCCESS_Y}  elapsed={_elapsed()}ms",
    )

    if py > SUCCESS_Y:
        _release_all_keys()
        _log_ok(f"CLIP SUCCESS — Y={py:.2f}  elapsed={_elapsed()}ms")
        Player.Move(*DEST_POS)
        _transition("success", f"Y={py:.2f}")
        return

    if _elapsed() >= CLIP_DURATION_MS:
        _release_all_keys()
        _log_warn(
            f"Clip timed out after {CLIP_DURATION_MS}ms — "
            f"Y={py:.2f} (need >{SUCCESS_Y})"
        )
        _transition(fail_state, "timeout")


# Maps timed-wait states to (duration_ms, next_state, log_label).
_WAIT_STATES: dict[str, tuple[int, str, str]] = {
    "approach_wait": (APPROACH_WAIT_MS, "walk_back_1", "Approach wait"),
    "lure_wait_1":   (LURE_WAIT_MS,     "pre_clip_1",  "Lure wait 1"),
    "pre_clip_1":    (PRE_CLIP_WAIT_MS, "clip_1",      "Pre-clip settle 1"),
    "lure_wait_2":   (LURE_WAIT_MS,     "pre_clip_2",  "Lure wait 2"),
    "pre_clip_2":    (PRE_CLIP_WAIT_MS, "clip_2",      "Pre-clip settle 2"),
}


def _tick_wait(duration_ms: int, next_state: str, label: str):
    elapsed = _elapsed()
    key = f"wait_{next_state}"
    if key not in _last_log_ms:
        _last_log_ms[key] = _now_ms()  # seed so first log fires after one throttle interval
    _log_throttled(key, f"{label} — {elapsed}ms / {duration_ms}ms")
    if elapsed >= duration_ms:
        _transition(next_state, f"{label} done")


def _tick_state():
    global _retry_count

    if _state in _WAIT_STATES:
        duration_ms, next_state, label = _WAIT_STATES[_state]
        _tick_wait(duration_ms, next_state, label)
        return

    if _state == "approach":
        _tick_approach()
    elif _state == "wait_for_mobs":
        _tick_wait_for_mobs()
    elif _state == "walk_back_1":
        _tick_walk_back(CLIP_POS_1, "lure_wait_1")
    elif _state == "clip_1":
        _tick_clip(fail_state="walk_back_2")
    elif _state == "walk_back_2":
        _tick_walk_back(CLIP_POS_2, "lure_wait_2")
    elif _state == "clip_2":
        _tick_clip(fail_state="approach")
        if _state == "approach":   # transitioned = both attempts failed
            _retry_count += 1


# ── Upkeep ─────────────────────────────────────────────────────────────────────

def _upkeep_sf():
    sf_active    = _has_effect(SF_ID)
    sf_remaining = _effect_remaining_ms(SF_ID) if sf_active else 0.0
    dp_active    = _has_effect(DP_ID)

    if not sf_active:
        _try_cast(SF_ID, "ShadowForm")
        return

    if sf_remaining < SF_RECAST_BUFFER_MS:
        if not dp_active:
            _try_cast(DP_ID, "DeadlyParadox")
        else:
            _log_upkeep("dp_wait", f"SF low ({sf_remaining/1000:.1f}s), DP active — waiting for SF to drop")

def _upkeep_sod():
    sod_active    = _has_effect(SOD_ID)
    sod_remaining = _effect_remaining_ms(SOD_ID) if sod_active else 0.0

    if not sod_active:
        _log_warn("SoD dropped!")
        _try_cast(SOD_ID, "ShroudOfDistress")
        return

    if sod_remaining < SOD_RECAST_BUFFER_MS:
        _try_cast(SOD_ID, "ShroudOfDistress")
        return

    _log_upkeep("sod", f"SoD {sod_remaining/1000:.1f}s remaining")

def _maintain_camera():
    """Force camera yaw every tick — the game resets it otherwise."""
    if _state in ("clip_1", "clip_2", "pre_clip_1", "pre_clip_2"):
        Camera.SetYaw(CLIP_CAMERA_YAW)
    else:
        Camera.SetYaw(WALK_CAMERA_YAW)

def _upkeep():
    _upkeep_sf()
    _upkeep_sod()
    if _state in ("approach_wait", "walk_back_1", "clip_1", "walk_back_2", "clip_2"):
        _maintain_camera()


# ── ImGui UI ───────────────────────────────────────────────────────────────────

def _draw_window():
    global _running, _retry_count

    if PyImGui.begin(MODULE_NAME):

        if PyImGui.button("Stop" if _running else "Start"):
            if _running:
                _release_all_keys()
                _log("Stopped by user")
                _transition("idle", "user stopped")
                _running = False
            else:
                _log("=" * 60)
                _log("Starting gate clip test")
                _log(f"  APPROACH     = {APPROACH_POS}")
                _log(f"  CLIP_1       = {CLIP_POS_1}")
                _log(f"  CLIP_2       = {CLIP_POS_2}")
                _log(f"  DEST         = {DEST_POS}")
                _log(f"  SUCCESS_Y    = {SUCCESS_Y}")
                _log(f"  LURE_WAIT    = {LURE_WAIT_MS}ms")
                _log(f"  PRE_CLIP     = {PRE_CLIP_WAIT_MS}ms")
                _log(f"  CLIP_DUR     = {CLIP_DURATION_MS}ms")
                _log(f"  WALK_YAW     = {WALK_CAMERA_YAW:.4f} ({math.degrees(WALK_CAMERA_YAW):.1f}°)")
                _log(f"  CLIP_YAW     = {CLIP_CAMERA_YAW:.4f} ({math.degrees(CLIP_CAMERA_YAW):.1f}°)")
                _log(f"  MOB_RADIUS   = {MOB_WAIT_RADIUS}")
                _log("=" * 60)
                _retry_count = 0
                _running     = True
                _transition("approach", "user started")

        PyImGui.separator()

        px, py = Player.GetXY()
        PyImGui.text(f"Pos   X={px:.1f}  Y={py:.2f}")
        PyImGui.text(f"Gate  Y={3495.0:.1f}  dist={abs(py - 3495.0):.2f}")

        PyImGui.separator()

        sf_up  = _has_effect(SF_ID)
        sod_up = _has_effect(SOD_ID)
        dp_up  = _has_effect(DP_ID)
        PyImGui.text(
            f"SF {'UP' if sf_up else 'DN'}  "
            f"SoD {'UP' if sod_up else 'DN'}  "
            f"DP {'UP' if dp_up else '--'}"
        )
        if sf_up:
            PyImGui.text(f"SF  {_effect_remaining_ms(SF_ID)/1000:.1f}s")
        if sod_up:
            PyImGui.text(f"SoD {_effect_remaining_ms(SOD_ID)/1000:.1f}s")

        PyImGui.separator()

        yaw = Camera.GetYaw()
        target_yaw = CLIP_CAMERA_YAW if _state in ("clip_1", "clip_2", "pre_clip_1", "pre_clip_2") else WALK_CAMERA_YAW
        PyImGui.text(f"Camera  yaw={yaw:.3f} ({math.degrees(yaw):.1f}°)  target={math.degrees(target_yaw):.1f}°")

        hostiles = _nearby_hostiles()
        PyImGui.text(f"Mobs    {len(hostiles)} in range (r={MOB_WAIT_RADIUS:.0f})")

        PyImGui.separator()

        PyImGui.text(f"State:   {_state}  ({_elapsed()}ms)")
        PyImGui.text(f"Retries: {_retry_count}")
        PyImGui.text(
            f"Keys: back={'HELD' if _backward_held else 'off'}  "
            f"clip={'HELD' if _clip_keys_held else 'off'}"
        )

    PyImGui.end()


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    _draw_window()
    if not _running or _state in ("idle", "success", "failed"):
        return
    _upkeep()
    _tick_state()


if __name__ == "__main__":
    main()
