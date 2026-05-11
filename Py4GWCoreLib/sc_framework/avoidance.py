"""
avoidance.py — real-time agent collision avoidance for SCMovement.

Runs as a per-tick sentinel alongside BTMovement.Move() so the game's built-in
pathfinder still handles terrain/doors while this layer steers around live agents.

Geometry (GW world space):
    goal_dir = normalize(goal - player)               # unit vec toward waypoint
    to_agent = agent_pos - player_pos
    dot      = dot(goal_dir, to_agent)                # > 0 = agent is ahead
    cross    = cross2d(goal_dir, to_agent)            # > 0 = agent left of path
    in_path  = dot > agent_radius
               AND dist < check_radius
               AND abs(cross) < path_half_width + agent_radius

Camera yaw convention (GW, standard math radians):
    0 = east,  π/2 = north,  π / -π = west,  -π/2 = south
    MoveForward travels in the direction Camera.GetYaw() faces.
    Camera.SetYaw() must be called every tick — the engine resets it otherwise.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable

import PyImGui

from Py4GWCoreLib.py4gwcorelib_src.BehaviorTree import BehaviorTree
from Py4GWCoreLib.routines_src.behaviourtrees_src.composite import BTComposite


_LOG_SRC           = "SC.Avoid"
_EVENT_THROTTLE_MS = 200.0    # min ms between repeated event-level logs (same key)
_DEBUG_THROTTLE_MS = 2_000.0  # min ms between repeated debug-tick logs


def _now_ms() -> float:
    return time.monotonic() * 1_000.0


# ── Strategy ──────────────────────────────────────────────────────────────────

class AvoidStrategy:
    STRAFE     = "strafe"      # hold StrafeLeft/Right; let Player.Move keep steering
    YAW_ADJUST = "yaw_adjust"  # rotate camera ± deflect_angle, hold MoveForward


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class AvoidanceConfig:
    """
    All tunable parameters for the avoidance sentinel.
    Pass to SCMovement.Move/RunPath as ``avoidance=AvoidanceConfig(...)``.
    Live-edit via AvoidanceDebugPanel during testing.
    """

    # ── Detection ─────────────────────────────────────────────────────────
    check_radius:      float = 250.0  # max distance ahead to scan for obstacles
    path_half_width:   float = 80.0   # half-width of the path corridor to protect
    agent_radius:      float = 60.0   # treat each agent as a circle of this radius

    # ── Escalation ────────────────────────────────────────────────────────
    escalation_ticks:  int   = 20     # strafe ticks before escalating to yaw_adjust
                                      # lower = faster escalation on stubborn obstacles

    # ── Strafe strategy ───────────────────────────────────────────────────
    strafe_release_margin: float = 40.0  # release strafe when lateral clearance > this

    # ── Yaw-adjust strategy ───────────────────────────────────────────────
    deflect_angle_deg: float = 25.0   # degrees to rotate camera off the goal heading
    yaw_restore_dist:  float = 150.0  # restore goal yaw when obstacle clears beyond this


# ── Obstacle data ─────────────────────────────────────────────────────────────

@dataclass
class ObstacleSample:
    """Closest in-path agent detected this tick."""
    agent_id: int
    position: tuple[float, float]
    dot:      float    # projection onto goal_dir — larger = further ahead
    cross:    float    # signed lateral offset — positive = agent is LEFT of path
    distance: float    # Euclidean distance from player

    @property
    def is_left(self) -> bool:
        return self.cross > 0.0


# ── Sentinel ──────────────────────────────────────────────────────────────────

class AvoidanceSentinel:
    """
    Per-tick avoidance engine.

    Tick flow each frame:
        1. Scan AgentArray for the closest alive hostile in the forward path corridor.
        2. CLEAR  → release all held keys, reset strategy to STRAFE.
        3. BLOCKED (STRAFE phase):
              hold StrafeLeft or StrafeRight away from the obstacle.
              After escalation_ticks consecutive strafe ticks with no clearance,
              promote to YAW_ADJUST.
        4. BLOCKED (YAW_ADJUST phase):
              rotate Camera.SetYaw ± deflect_angle_deg away from the obstacle,
              hold MoveForward.  Camera yaw is re-set every tick.

    Instantiate once per movement scope (one per Move call, or one shared across
    RunPath).  Discard when the waypoint/path completes.
    """

    def __init__(
        self,
        config:  AvoidanceConfig,
        goal_fn: Callable[[], tuple[float, float]],
    ):
        self._cfg              = config
        self._goal_fn          = goal_fn
        self._active_keys:     set[int] = set()
        self._avoiding         = False
        self._current_strategy = AvoidStrategy.STRAFE
        self._strafe_ticks     = 0
        self._last_sample:     ObstacleSample | None = None
        self._last_log:        dict[str, float] = {}
        self._last_yaw:        float | None = None

    # ── factory helpers ───────────────────────────────────────────────────

    @staticmethod
    def _make_parallel(
        primary:  BehaviorTree,
        sentinel: "AvoidanceSentinel",
        name:     str,
    ) -> BehaviorTree:
        """
        Return a BehaviorTree whose tick:
            1. ticks sentinel._tick_avoidance() (side-effect)
            2. ticks primary.tick() and returns its NodeState

        This pattern replaces BTComposite.Parallel (which doesn't exist in the
        framework) with a single ActionNode that owns both ticks.
        """
        def _tick(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
            sentinel._tick_avoidance()
            return primary.tick()

        return BehaviorTree(BehaviorTree.ActionNode(_tick, name=f"{name}:WithSentinel"))

    @classmethod
    def wrap_move(
        cls,
        primary: BehaviorTree,
        config:  AvoidanceConfig,
        goal:    tuple[float, float],
        name:    str = "SCMove",
    ) -> BehaviorTree:
        """
        Wrap a single-waypoint move tree with a sentinel fixed on `goal`.
        Called by SCMovement.Move when avoidance is requested.
        """
        sentinel = cls(config, lambda: goal)
        return cls._make_parallel(primary, sentinel, name)

    @classmethod
    def wrap_path(
        cls,
        step_trees:      list[BehaviorTree],
        waypoints:       list[tuple[float, float]],
        config:          AvoidanceConfig,
        outer_on_arrive: Callable[[int, tuple[float, float]], None] | None,
        name:            str,
    ) -> BehaviorTree:
        """
        Wrap each step in a RunPath with a shared sentinel that updates its
        goal_fn as waypoints are consumed.

        The shared sentinel keeps escalation state across the full path so
        a persistent blocker escalates properly even across waypoint transitions.
        """
        wp_idx   = [0]
        wp_list  = waypoints

        def _goal_fn() -> tuple[float, float]:
            i = min(wp_idx[0], len(wp_list) - 1)
            return (float(wp_list[i][0]), float(wp_list[i][1]))

        sentinel = cls(config, _goal_fn)

        wrapped: list[BehaviorTree] = []
        for i, (step, wp) in enumerate(zip(step_trees, waypoints)):
            def _make_arrive_wrapper(idx: int, coords: tuple) -> BehaviorTree:
                def _tick(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
                    wp_idx[0] = idx + 1
                    if outer_on_arrive is not None:
                        outer_on_arrive(idx, (float(coords[0]), float(coords[1])))
                    return BehaviorTree.NodeState.SUCCESS

                arrive_node = BehaviorTree(BehaviorTree.ActionNode(_tick, name=f"{name}[{idx}]:OnArrive"))
                inner = BTComposite.Sequence(step, arrive_node, name=f"{name}[{idx}]:WithArrive")
                return cls._make_parallel(inner, sentinel, name=f"{name}[{idx}]")

            wrapped.append(_make_arrive_wrapper(i, wp))

        return BTComposite.Sequence(*wrapped, name=name)

    # ── core tick ─────────────────────────────────────────────────────────

    def _tick_avoidance(self) -> None:
        from Py4GWCoreLib.Player import Player
        px, py = Player.GetXY()
        goal   = self._goal_fn()
        sample = self._sample(px, py, goal)

        if sample is None:
            if self._avoiding:
                self._log_event("clear", "Avoidance CLEAR — corridor open, releasing keys")
                self._release_all()
                self._avoiding         = False
                self._current_strategy = AvoidStrategy.STRAFE
                self._strafe_ticks     = 0
                self._last_yaw         = None
        else:
            self._last_sample = sample
            if not self._avoiding:
                side = "LEFT" if sample.is_left else "RIGHT"
                self._log_event(
                    "start",
                    f"Avoidance START — agent {sample.agent_id}  "
                    f"dist={sample.distance:.0f}  {side} of path  "
                    f"dot={sample.dot:.0f}  cross={sample.cross:.0f}",
                )
            self._avoiding = True

            # ── escalation ────────────────────────────────────────────────
            if self._current_strategy == AvoidStrategy.STRAFE:
                self._strafe_ticks += 1
                if self._strafe_ticks >= self._cfg.escalation_ticks:
                    self._log_event(
                        "escalate",
                        f"Avoidance ESCALATE → YAW_ADJUST  "
                        f"after {self._strafe_ticks} strafe ticks  "
                        f"(threshold={self._cfg.escalation_ticks})",
                    )
                    self._current_strategy = AvoidStrategy.YAW_ADJUST
                    self._release_all()   # drop strafe keys before switching

            # ── apply strategy ────────────────────────────────────────────
            if self._current_strategy == AvoidStrategy.STRAFE:
                self._apply_strafe(sample)
            else:
                self._apply_yaw_adjust(sample, (px, py), goal)

            self._log_debug(
                "tick",
                f"Avoidance TICK [{self._current_strategy}]  "
                f"agent={sample.agent_id}  dist={sample.distance:.0f}  "
                f"dot={sample.dot:.0f}  cross={sample.cross:.0f}  "
                f"strafe_ticks={self._strafe_ticks}/{self._cfg.escalation_ticks}",
            )

    # ── sample ────────────────────────────────────────────────────────────

    def _sample(
        self,
        px: float,
        py: float,
        goal: tuple[float, float],
    ) -> ObstacleSample | None:
        """
        Return the closest alive hostile in the forward path corridor, or None.

        The corridor is a rectangular band:
            ahead  : dot > agent_radius   (not behind us)
            lateral: abs(cross) < path_half_width + agent_radius
            range  : distance < check_radius
        """
        from Py4GWCoreLib.Agent import Agent
        from Py4GWCoreLib.AgentArray import AgentArray

        gdx = goal[0] - px
        gdy = goal[1] - py
        gdist = math.hypot(gdx, gdy)
        if gdist < 1.0:
            return None   # already at goal — nothing to avoid
        gx = gdx / gdist  # goal unit vector
        gy = gdy / gdist

        best: ObstacleSample | None = None

        try:
            for agent_id in AgentArray.GetEnemyArray():
                if not Agent.IsAlive(agent_id):
                    continue
                ax, ay = Agent.GetXY(agent_id)
                tox  = ax - px
                toy  = ay - py
                dot   = gx * tox + gy * toy
                cross = gx * toy - gy * tox
                dist  = math.hypot(tox, toy)

                if dot <= self._cfg.agent_radius:
                    continue   # behind us or right at our feet
                if dist > self._cfg.check_radius:
                    continue   # out of scan range
                if abs(cross) > self._cfg.path_half_width + self._cfg.agent_radius:
                    continue   # outside path corridor

                if best is None or dot < best.dot:
                    best = ObstacleSample(
                        agent_id=agent_id,
                        position=(ax, ay),
                        dot=dot,
                        cross=cross,
                        distance=dist,
                    )
        except Exception as exc:
            self._log_event("sample_err", f"Avoidance sample failed: {exc}")

        return best

    # ── strategies ────────────────────────────────────────────────────────

    def _apply_strafe(self, obs: ObstacleSample) -> None:
        """
        Slide laterally away from the obstacle.

        Obstacle LEFT  (cross > 0) → strafe RIGHT
        Obstacle RIGHT (cross < 0) → strafe LEFT

        Player.Move (pathfinding) continues running; the strafe key adds lateral
        displacement.  In GW, strafe keys do NOT cancel click-to-move in most cases.
        """
        from Py4GWCoreLib.enums_src.UI_enums import ControlAction
        if obs.is_left:
            self._hold_key(ControlAction.ControlAction_StrafeRight.value)
            self._release_key(ControlAction.ControlAction_StrafeLeft.value)
        else:
            self._hold_key(ControlAction.ControlAction_StrafeLeft.value)
            self._release_key(ControlAction.ControlAction_StrafeRight.value)

    def _apply_yaw_adjust(
        self,
        obs:        ObstacleSample,
        player_pos: tuple[float, float],
        goal:       tuple[float, float],
    ) -> None:
        """
        Arc around the obstacle by rotating the camera and holding MoveForward.

        goal_yaw = atan2(goal_dy, goal_dx)              — direction to waypoint
        sign     = -1 if obstacle left, +1 if right     — deflect away
        new_yaw  = goal_yaw + sign * deflect_rad

        Camera.SetYaw is called every tick because GW resets it each frame.
        MoveForward cancels click-to-move in GW; BTMovement.Move will re-issue
        Player.Move on the next tick when keys are released.
        """
        from Py4GWCoreLib.Camera import Camera
        from Py4GWCoreLib.enums_src.UI_enums import ControlAction

        gdx = goal[0] - player_pos[0]
        gdy = goal[1] - player_pos[1]
        goal_yaw    = math.atan2(gdy, gdx)
        deflect_rad = math.radians(self._cfg.deflect_angle_deg)
        sign        = -1.0 if obs.is_left else 1.0
        new_yaw     = goal_yaw + sign * deflect_rad

        Camera.SetYaw(new_yaw)
        self._hold_key(ControlAction.ControlAction_MoveForward.value)

        if self._last_yaw != new_yaw:
            side = "LEFT" if obs.is_left else "RIGHT"
            self._log_event(
                "yaw",
                f"Avoidance YAW  goal={math.degrees(goal_yaw):.1f}°  "
                f"deflect={math.degrees(sign * deflect_rad):+.1f}° ({side})  "
                f"→ {math.degrees(new_yaw):.1f}°",
            )
            self._last_yaw = new_yaw

    # ── key management ────────────────────────────────────────────────────

    def _hold_key(self, action: int) -> None:
        from Py4GWCoreLib.UIManager import UIManager
        if action not in self._active_keys:
            UIManager.Keydown(action, 0)
            self._active_keys.add(action)

    def _release_key(self, action: int) -> None:
        from Py4GWCoreLib.UIManager import UIManager
        if action in self._active_keys:
            UIManager.Keyup(action, 0)
            self._active_keys.discard(action)

    def _release_all(self) -> None:
        from Py4GWCoreLib.UIManager import UIManager
        for key in list(self._active_keys):
            UIManager.Keyup(key, 0)
        self._active_keys.clear()

    # ── logging ───────────────────────────────────────────────────────────

    def _log_event(self, key: str, msg: str) -> None:
        from Py4GWCoreLib.Py4GWcorelib import ConsoleLog, Console
        now  = _now_ms()
        last = self._last_log.get(key)
        if last is None or (now - last) >= _EVENT_THROTTLE_MS:
            ConsoleLog(_LOG_SRC, msg, Console.MessageType.Info, log=True)
            self._last_log[key] = now

    def _log_debug(self, key: str, msg: str) -> None:
        from Py4GWCoreLib.Py4GWcorelib import ConsoleLog, Console
        now  = _now_ms()
        last = self._last_log.get(f"dbg_{key}")
        if last is None or (now - last) >= _DEBUG_THROTTLE_MS:
            ConsoleLog(_LOG_SRC, msg, Console.MessageType.Debug, log=True)
            self._last_log[f"dbg_{key}"] = now


# ── Debug panel ───────────────────────────────────────────────────────────────

class AvoidanceDebugPanel:
    """
    Optional live-tuning ImGui window.

    Usage in a widget:
        config  = AvoidanceConfig()
        panel   = AvoidanceDebugPanel(config)

        # After building a sentinel, attach it for live stats:
        # panel.sentinel = my_sentinel

        # In draw():
        # panel.draw()

    All slider changes apply immediately to the shared AvoidanceConfig,
    so a running sentinel picks them up on the next tick.
    """

    def __init__(
        self,
        config:   AvoidanceConfig,
        sentinel: AvoidanceSentinel | None = None,
    ):
        self.config   = config
        self.sentinel = sentinel

    def draw(self) -> None:
        if not PyImGui.begin("Avoidance Tuner"):
            PyImGui.end()
            return

        # ── Detection ─────────────────────────────────────────────────────
        PyImGui.text("Detection")
        self.config.check_radius    = PyImGui.slider_float("Check radius##av",    self.config.check_radius,    50.0, 800.0)
        self.config.path_half_width = PyImGui.slider_float("Path half-width##av", self.config.path_half_width, 20.0, 300.0)
        self.config.agent_radius    = PyImGui.slider_float("Agent radius##av",    self.config.agent_radius,    10.0, 150.0)

        PyImGui.separator()

        # ── Escalation ────────────────────────────────────────────────────
        PyImGui.text("Escalation")
        self.config.escalation_ticks = PyImGui.slider_int(
            "Escalation ticks##av", self.config.escalation_ticks, 1, 120
        )
        PyImGui.text("  (ticks in STRAFE before switching to YAW_ADJUST)")

        PyImGui.separator()

        # ── Strafe ────────────────────────────────────────────────────────
        PyImGui.text("Strafe")
        self.config.strafe_release_margin = PyImGui.slider_float(
            "Release margin##av", self.config.strafe_release_margin, 10.0, 200.0
        )

        PyImGui.separator()

        # ── Yaw Adjust ────────────────────────────────────────────────────
        PyImGui.text("Yaw Adjust")
        self.config.deflect_angle_deg = PyImGui.slider_float(
            "Deflect angle (deg)##av", self.config.deflect_angle_deg, 5.0, 60.0
        )
        self.config.yaw_restore_dist = PyImGui.slider_float(
            "Restore dist##av", self.config.yaw_restore_dist, 50.0, 500.0
        )

        # ── Live stats ────────────────────────────────────────────────────
        if self.sentinel is not None:
            PyImGui.separator()
            PyImGui.text("Live State")
            state_str    = "AVOIDING" if self.sentinel._avoiding else "CLEAR"
            strategy_str = self.sentinel._current_strategy
            PyImGui.text(f"  State:    {state_str}")
            PyImGui.text(f"  Strategy: {strategy_str}")
            PyImGui.text(
                f"  Strafe ticks: {self.sentinel._strafe_ticks} / "
                f"{self.config.escalation_ticks}"
            )
            s = self.sentinel._last_sample
            if s is not None:
                PyImGui.text(f"  Obstacle:  agent={s.agent_id}  dist={s.distance:.0f}")
                PyImGui.text(
                    f"  dot={s.dot:.0f}  cross={s.cross:.0f}  "
                    f"side={'LEFT' if s.is_left else 'RIGHT'}"
                )
            else:
                PyImGui.text("  Obstacle:  none")

        PyImGui.end()
