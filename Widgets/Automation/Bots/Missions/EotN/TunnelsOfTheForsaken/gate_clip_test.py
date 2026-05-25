"""
gate_clip_test.py — TotF gate clip test.

Algorithm:
    1. Walk to GATE_POS and set camera for backwards walk.
    2. Wait for at least one hostile within MOB_WAIT_RADIUS; record their IDs.
    3. Hold MoveBackward toward CLIP_POS.
    4. Each tick: if any recorded hostile closes distance by > MOV_THRESHOLD → clip.
    5. Fallback: arrived at CLIP_POS without trigger → clip anyway.
    6. Clip success: player Y > SUCCESS_Y → navigate to DEST_POS.
    7. Clip fail: restart from step 1.
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
SF_ID  = 826
SOD_ID = 1031
DP_ID  = 572

# ── Upkeep timing ──────────────────────────────────────────────────────────────
SF_RECAST_BUFFER_MS  = 3_000
SOD_RECAST_BUFFER_MS = 3_000
CAST_COOLDOWN_MS     = 250

# ── Positions ──────────────────────────────────────────────────────────────────
GATE_POS = (-8821.39, 3495.75)  # stand here to lure mobs
CLIP_POS = (-8639.05, 3495.43)  # walk backwards to here
DEST_POS = (-8645.88, 4210.55)  # destination after successful clip

# ── Gate geometry ──────────────────────────────────────────────────────────────
SUCCESS_Y     = 3515.0   # player Y > this = clipped through the gate
SUCCESS_MIN_X = -8900.0  # player X must be > this to exclude the west alcove

# ── Camera ─────────────────────────────────────────────────────────────────────
WALK_CAMERA_YAW = math.pi  # 180° — MoveBackward drives east toward CLIP_POS
CLIP_CAMERA_YAW = 3.022    # 173.1° — used during clip attempt

# ── Mob detection ──────────────────────────────────────────────────────────────
MOB_WAIT_RADIUS = 600.0   # detect any hostile within this range before starting lure wait
MELEE_RANGE     = 160.0   # enemies must be within this distance before walk_back starts

# ── Tuning ─────────────────────────────────────────────────────────────────────
LURE_WAIT_MS         = 3_000   # wait at gate after enemies arrive before walking back
WALK_BACK_TIMEOUT_MS = 8_000   # give up waiting for enemy movement and retry lure
ARRIVAL_DIST         = 80.0
CLIP_DURATION_MS     = 6_000   # total clip attempt window
CLIP_PRESS_MS        = 200     # hold Forward+StrafeRight for this many ms
CLIP_RELEASE_MS      = 50      # release between presses for this many ms
CLIP_JIGGLE_RAD      = math.radians(5.0)  # camera jiggle ±5° before each press
APPROACH_REISSUE_MS  = 500

# ── Log throttle ───────────────────────────────────────────────────────────────
_TICK_LOG_MS   = 500
_UPKEEP_LOG_MS = 5_000

# ── Module state ───────────────────────────────────────────────────────────────
_running        = False
_state          = "idle"
_backward_held  = False
_clip_keys_held = False
_last_cast_ms        = 0
_last_move_ms        = 0
_state_enter_ms      = 0
_retry_count         = 0
_clip_phase: str       = "idle"   # "pressing" | "releasing"
_clip_phase_ms: int    = 0
_clip_jiggle_sign: int = 1
_clip_camera_offset: float = 0.0

_tracked_enemies: set[int] = set()

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
    if now - _last_log_ms.get(key, 0) >= _TICK_LOG_MS:
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

def _release_clip_keys():
    global _clip_keys_held, _clip_phase, _clip_camera_offset
    if _clip_keys_held:
        UIManager.Keyup(ControlAction.ControlAction_MoveForward.value, 0)
        UIManager.Keyup(ControlAction.ControlAction_StrafeRight.value, 0)
        _clip_keys_held = False
        _log("Keyup: MoveForward + StrafeRight")
    _clip_phase         = "idle"
    _clip_camera_offset = 0.0

def _release_all_keys():
    _release_backward()
    _release_clip_keys()


# ── State machine ──────────────────────────────────────────────────────────────

def _transition(new_state: str, msg: str = "", level=Console.MessageType.Info):
    global _state, _state_enter_ms
    prev            = _state
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
    dist   = Utils.Distance((px, py), GATE_POS)

    _log_throttled("approach", f"Walking to gate — dist={dist:.1f}  pos=({px:.1f}, {py:.1f})")

    if dist < ARRIVAL_DIST:
        _transition("wait_for_mobs", f"arrived dist={dist:.1f}")
        return

    now = _now_ms()
    if now - _last_move_ms > APPROACH_REISSUE_MS:
        Player.Move(*GATE_POS)
        _last_move_ms = now
        _log(f"[approach] Player.Move issued — dist={dist:.1f}", Console.MessageType.Debug)


_IGNORED_NAMES = {"wavering echo"}

def _nearby_hostiles(radius: float = MOB_WAIT_RADIUS) -> list[int]:
    px, py = Player.GetXY()
    result = []
    try:
        for agent_id in AgentArray.GetEnemyArray():
            if not Agent.IsAlive(agent_id):
                continue
            name = Agent.GetNameByID(agent_id).strip().lower()
            if name in _IGNORED_NAMES:
                continue
            ax, ay = Agent.GetXY(agent_id)
            if Utils.Distance((px, py), (ax, ay)) < radius:
                result.append(agent_id)
    except Exception as exc:
        _log_warn(f"AgentArray query failed: {exc}")
    return result


def _tick_wait_for_mobs():
    hostiles = _nearby_hostiles()
    count    = len(hostiles)
    _log_throttled("wait_mobs", f"Waiting for mobs — {count} in range (r={MOB_WAIT_RADIUS:.0f})")
    if count > 0:
        _transition("lure_wait", f"{count} hostile(s) detected")


def _tick_lure_wait():
    global _tracked_enemies
    elapsed = _elapsed()
    if elapsed < LURE_WAIT_MS:
        _log_throttled("lure_wait", f"Lure wait — {elapsed}ms / {LURE_WAIT_MS}ms")
        return
    px, py = Player.GetXY()
    hostiles = _nearby_hostiles()
    moving   = [aid for aid in hostiles if Agent.IsMoving(aid)]
    far      = [aid for aid in hostiles if Utils.Distance((px, py), Agent.GetXY(aid)) > MELEE_RANGE]
    status   = "  ".join(
        f"[{aid}] {'MOVING' if Agent.IsMoving(aid) else 'idle'} d={Utils.Distance((px, py), Agent.GetXY(aid)):.0f}"
        for aid in hostiles
    )
    _log_throttled("lure_settle", f"[lure_wait] settling — {status or 'no hostiles'}", Console.MessageType.Debug)
    if moving:
        _log_throttled("lure_moving", f"[lure_wait] {len(moving)} enemy(s) still moving — waiting", Console.MessageType.Debug)
        return
    if far:
        _log_throttled("lure_far", f"[lure_wait] {len(far)} enemy(s) not in melee range yet — waiting", Console.MessageType.Debug)
        return
    _tracked_enemies = set(hostiles)
    _transition("walk_back", f"{len(_tracked_enemies)} enemies idle and in melee range — starting walk back")


def _tick_walk_back():
    _hold_backward()
    px, py = Player.GetXY()

    for agent_id in list(_tracked_enemies):
        try:
            if not Agent.IsAlive(agent_id):
                _log_throttled(f"wb_{agent_id}", f"[walk_back] [{agent_id}] dead — skipping", Console.MessageType.Debug)
                continue
            moving = Agent.IsMoving(agent_id)
            _log_throttled(f"wb_{agent_id}", f"[walk_back] [{agent_id}] {'MOVING — triggering clip' if moving else 'idle'}", Console.MessageType.Debug)
            if moving:
                _release_backward()
                _transition("clip", f"enemy {agent_id} started moving")
                return
        except Exception as exc:
            _log_warn(f"enemy {agent_id} query failed: {exc}")

    elapsed = _elapsed()
    _log_throttled(
        "walk_back",
        f"[walk_back] pos=({px:.1f}, {py:.1f})  elapsed={elapsed}ms / {WALK_BACK_TIMEOUT_MS}ms",
    )

    if elapsed >= WALK_BACK_TIMEOUT_MS:
        _release_backward()
        _log_warn(f"[walk_back] timeout — no enemy movement detected, retrying lure")
        _transition("lure_wait", "walk_back timeout")


def _start_clip_press(now: int):
    global _clip_phase, _clip_phase_ms, _clip_jiggle_sign, _clip_camera_offset, _clip_keys_held
    _clip_jiggle_sign   = -_clip_jiggle_sign
    _clip_camera_offset = _clip_jiggle_sign * CLIP_JIGGLE_RAD
    UIManager.Keydown(ControlAction.ControlAction_MoveForward.value, 0)
    UIManager.Keydown(ControlAction.ControlAction_StrafeRight.value, 0)
    _clip_keys_held = True
    _clip_phase     = "pressing"
    _clip_phase_ms  = now


def _tick_clip():
    global _clip_phase, _clip_phase_ms, _clip_keys_held, _retry_count
    now = _now_ms()

    if _clip_phase == "idle":
        _start_clip_press(now)
    elif _clip_phase == "pressing":
        if now - _clip_phase_ms >= CLIP_PRESS_MS:
            UIManager.Keyup(ControlAction.ControlAction_MoveForward.value, 0)
            UIManager.Keyup(ControlAction.ControlAction_StrafeRight.value, 0)
            _clip_keys_held = False
            _clip_phase     = "releasing"
            _clip_phase_ms  = now
    elif _clip_phase == "releasing":
        if now - _clip_phase_ms >= CLIP_RELEASE_MS:
            _start_clip_press(now)

    px, py = Player.GetXY()
    _log_throttled("clip", f"Clipping — pos=({px:.1f}, {py:.2f})  phase={_clip_phase}  offset={math.degrees(_clip_camera_offset):.1f}°  elapsed={_elapsed()}ms")

    if py > SUCCESS_Y and px > SUCCESS_MIN_X:
        _release_all_keys()
        _log_ok(f"CLIP SUCCESS — pos=({px:.1f}, {py:.2f})  elapsed={_elapsed()}ms")
        Player.Move(*DEST_POS)
        _transition("success", f"pos=({px:.1f}, {py:.2f})")
        return

    if _elapsed() >= CLIP_DURATION_MS:
        _release_all_keys()
        _log_warn(f"Clip timed out after {CLIP_DURATION_MS}ms — Y={py:.2f} (need >{SUCCESS_Y})")
        _transition("approach", "retry")
        _retry_count += 1


def _tick_state():
    if _state == "approach":
        _tick_approach()
    elif _state == "wait_for_mobs":
        _tick_wait_for_mobs()
    elif _state == "lure_wait":
        _tick_lure_wait()
    elif _state == "walk_back":
        _tick_walk_back()
    elif _state == "clip":
        _tick_clip()


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
    if _state == "clip":
        Camera.SetYaw(CLIP_CAMERA_YAW + _clip_camera_offset)
    else:
        Camera.SetYaw(WALK_CAMERA_YAW)

def _upkeep():
    _upkeep_sf()
    _upkeep_sod()
    if _state in ("wait_for_mobs", "walk_back", "clip"):
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
                _log(f"  GATE_POS     = {GATE_POS}")
                _log(f"  CLIP_POS     = {CLIP_POS}")
                _log(f"  DEST         = {DEST_POS}")
                _log(f"  SUCCESS_Y    = {SUCCESS_Y}")
                _log(f"  MOB_RADIUS   = {MOB_WAIT_RADIUS}")
                _log(f"  CLIP_DUR     = {CLIP_DURATION_MS}ms")
                _log(f"  WALK_YAW     = {WALK_CAMERA_YAW:.4f} ({math.degrees(WALK_CAMERA_YAW):.1f}°)")
                _log(f"  CLIP_YAW     = {CLIP_CAMERA_YAW:.4f} ({math.degrees(CLIP_CAMERA_YAW):.1f}°)")
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
        target_yaw = CLIP_CAMERA_YAW if _state == "clip" else WALK_CAMERA_YAW
        PyImGui.text(f"Camera  yaw={yaw:.3f} ({math.degrees(yaw):.1f}°)  target={math.degrees(target_yaw):.1f}°")

        hostiles = _nearby_hostiles()
        PyImGui.text(f"Mobs    {len(hostiles)} in range (r={MOB_WAIT_RADIUS:.0f})")
        PyImGui.text(f"Tracked {len(_tracked_enemies)} enemies")
        if _tracked_enemies:
            for eid in list(_tracked_enemies)[:4]:
                try:
                    moving = Agent.IsMoving(eid)
                    ax, ay = Agent.GetXY(eid)
                    dist   = Utils.Distance((px, py), (ax, ay))
                    PyImGui.text(f"  [{eid}] {'MOVING' if moving else 'idle'} d={dist:.0f}")
                except Exception:
                    PyImGui.text(f"  [{eid}] (error)")

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
    if not _running or _state in ("idle", "success"):
        return
    _upkeep()
    _tick_state()


if __name__ == "__main__":
    main()
