"""
_gate_clip.py — GateClipper: reusable mob-collision gate-clip state machine.

Usage in dasher.py:

    from ._gate_clip import GateClipper

    def _build_gate_clip_node() -> BehaviorTree:
        clipper = GateClipper(log_fn=log)
        def _tick(_):
            set_watchdog_paused(clipper.at_wall)
            return clipper.update()
        return BehaviorTree(BehaviorTree.ActionNode(_tick, name="GateClip"))
"""

from __future__ import annotations

import math
import time
from typing import Callable

from Py4GWCoreLib.Agent import Agent
from Py4GWCoreLib.AgentArray import AgentArray
from Py4GWCoreLib.Camera import Camera
from Py4GWCoreLib.Player import Player
from Py4GWCoreLib.UIManager import UIManager
from Py4GWCoreLib.enums_src.UI_enums import ControlAction
from Py4GWCoreLib.py4gwcorelib_src.BehaviorTree import BehaviorTree
from Py4GWCoreLib.py4gwcorelib_src.Utils import Utils

from ..constants import GateClip

_NS = BehaviorTree.NodeState


def _mono_ms() -> float:
    return time.monotonic() * 1_000.0


def _poly_contains(px: float, py: float, polygon: tuple) -> bool:
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


class GateClipper:
    """
    Mob-collision gate clip.

    Sub-state sequence:
        APPROACHING  walk to settle_pos
        SETTLING     lock camera yaw, wait settle_ms for enemies to stabilise
        TO_WALL      walk north into gate wall
        WAIT_MOB     hold MoveForward, wait for enemies at wall
        SWEEP_LEFT   MoveForward + StrafeLeft toward clip corner (x ≈ −8950)
        SWEEP_RIGHT  MoveForward + StrafeRight back to reset_x
        (repeat SWEEP_LEFT / SWEEP_RIGHT until clip fires)

    Returns NodeState.SUCCESS once inside SUCCESS_POLYGON,
            NodeState.FAILURE after max_retries wall-timeouts.
    """

    _WALL_Y_LO     = 3480.0
    _WALL_Y_HI     = 3530.0
    _GATE_WALL_Y   = GateClip.GATE_WALL_Y
    _ENEMY_GATE_DY = 60.0
    _SUCCESS_POLY  = GateClip.SUCCESS_POLYGON

    def __init__(
        self,
        *,
        settle_pos: tuple[float, float]          = (-8739.04, 3192.48),
        settle_ms: float                         = 1000.0,
        settle_radius: float                     = 150.0,
        camera_yaw_deg: float                    = 90.0,
        approach_x: float                        = -8639.05,
        press_far_y: float                       = GateClip.DEST_POS[1],
        clip_corner_x: float                     = -8950.0,
        reset_x: float                           = -8690.0,
        min_enemies: int                         = 1,
        timeout_ms: float                        = 25_000.0,
        max_retries: int                         = 10,
        reissue_ms: float                        = 300.0,
        dc_fn: Callable[[], None] | None         = None,
        stuck_ms: float                          = 3_000.0,
        log_fn: Callable[[str], None] | None     = None,
    ) -> None:
        self._settle_pos    = settle_pos
        self._settle_ms     = settle_ms
        self._settle_radius = settle_radius
        self._camera_yaw    = math.radians(camera_yaw_deg)
        self._approach_x    = approach_x
        self._press_far_y   = press_far_y
        self._clip_corner_x = clip_corner_x
        self._reset_x       = reset_x
        self._min_enemies   = min_enemies
        self._timeout_ms    = timeout_ms
        self._max_retries   = max_retries
        self._reissue_ms    = reissue_ms
        self._dc_fn         = dc_fn
        self._stuck_ms      = stuck_ms
        self._log           = log_fn or (lambda _: None)

        self._sub            = "APPROACHING"
        self._phase_ms       = _mono_ms()
        self._wall_arrive_ms = 0.0
        self._last_move_ms   = 0.0
        self._retries        = 0
        self._active_keys: set[int] = set()
        self._last_pos       = (0.0, 0.0)
        self._last_moved_ms  = _mono_ms()

    # ── public ────────────────────────────────────────────────────────────────

    @property
    def at_wall(self) -> bool:
        """True while the player is in any wall sub-state (for watchdog pausing)."""
        return self._sub in ("WAIT_MOB", "SWEEP_LEFT", "SWEEP_RIGHT")

    def reset(self) -> None:
        self._release_all()
        self._sub            = "APPROACHING"
        self._phase_ms       = _mono_ms()
        self._wall_arrive_ms = 0.0
        self._last_move_ms   = 0.0
        self._retries        = 0
        self._last_pos       = (0.0, 0.0)
        self._last_moved_ms  = _mono_ms()

    def update(self) -> BehaviorTree.NodeState:
        px, py = Player.GetXY()
        now    = _mono_ms()

        if Utils.Distance((px, py), self._last_pos) > 5.0:
            self._last_pos      = (px, py)
            self._last_moved_ms = now

        if _poly_contains(px, py, self._SUCCESS_POLY):
            self._release_all()
            self._log("GateClip: SUCCESS")
            return _NS.SUCCESS

        if self._sub == "APPROACHING":
            if Utils.Distance((px, py), self._settle_pos) <= self._settle_radius:
                self._go("SETTLING")
            elif now - self._last_move_ms >= self._reissue_ms:
                Player.Move(*self._settle_pos)
                self._last_move_ms = now
            return _NS.RUNNING

        if self._sub == "SETTLING":
            Camera.SetYaw(self._camera_yaw)
            if now - self._phase_ms >= self._settle_ms:
                self._go("TO_WALL")
            return _NS.RUNNING

        if self._sub == "TO_WALL":
            if self._WALL_Y_LO <= py <= self._WALL_Y_HI:
                self._wall_arrive_ms = now
                self._hold(ControlAction.ControlAction_MoveForward.value)
                self._go("WAIT_MOB")
            elif now - self._last_move_ms >= self._reissue_ms:
                Player.Move(self._approach_x, self._press_far_y)
                self._last_move_ms = now
            return _NS.RUNNING

        # ── wall sub-states ───────────────────────────────────────────────────
        self._hold(ControlAction.ControlAction_MoveForward.value)

        if py < self._WALL_Y_LO - 50.0:
            self._release_all()
            self._wall_arrive_ms = 0.0
            self._go("APPROACHING")
            return _NS.RUNNING

        if self._wall_arrive_ms > 0 and (now - self._wall_arrive_ms) >= self._timeout_ms:
            self._retries += 1
            self._log(f"GateClip: timeout — retry {self._retries}/{self._max_retries}")
            if self._retries >= self._max_retries:
                self._release_all()
                return _NS.FAILURE
            self._release_all()
            self._wall_arrive_ms = 0.0
            self._go("APPROACHING")
            return _NS.RUNNING

        if self._dc_fn and (now - self._last_moved_ms) >= self._stuck_ms:
            self._log("GateClip: stuck — DC fallback")
            self._release_all()
            self._dc_fn()
            self._wall_arrive_ms = 0.0
            self._last_moved_ms  = now
            self._go("APPROACHING")
            return _NS.RUNNING

        if self._sub == "WAIT_MOB":
            self._release(ControlAction.ControlAction_StrafeLeft.value)
            self._release(ControlAction.ControlAction_StrafeRight.value)
            if self._enemies_at_gate() >= self._min_enemies:
                self._go("SWEEP_LEFT")

        elif self._sub == "SWEEP_LEFT":
            self._hold(ControlAction.ControlAction_StrafeLeft.value)
            self._release(ControlAction.ControlAction_StrafeRight.value)
            if px <= self._clip_corner_x:
                self._release(ControlAction.ControlAction_StrafeLeft.value)
                self._go("SWEEP_RIGHT")

        elif self._sub == "SWEEP_RIGHT":
            self._hold(ControlAction.ControlAction_StrafeRight.value)
            self._release(ControlAction.ControlAction_StrafeLeft.value)
            if px >= self._reset_x:
                self._release(ControlAction.ControlAction_StrafeRight.value)
                self._go("SWEEP_LEFT")

        return _NS.RUNNING

    # ── private ───────────────────────────────────────────────────────────────

    def _go(self, sub: str) -> None:
        self._log(f"GateClip: → {sub}")
        self._sub      = sub
        self._phase_ms = _mono_ms()

    def _hold(self, action: int) -> None:
        if action not in self._active_keys:
            UIManager.Keydown(action, 0)
            self._active_keys.add(action)

    def _release(self, action: int) -> None:
        if action in self._active_keys:
            UIManager.Keyup(action, 0)
            self._active_keys.discard(action)

    def _release_all(self) -> None:
        for action in list(self._active_keys):
            UIManager.Keyup(action, 0)
        self._active_keys.clear()

    def _enemies_at_gate(self) -> int:
        count = 0
        try:
            for aid in AgentArray.GetEnemyArray():
                if not Agent.IsAlive(aid):
                    continue
                _, ay = Agent.GetXY(aid)
                if abs(ay - self._GATE_WALL_Y) <= self._ENEMY_GATE_DY:
                    count += 1
        except Exception:
            pass
        return count
