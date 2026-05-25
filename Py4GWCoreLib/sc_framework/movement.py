"""
movement.py — SC-aware movement with pluggable recovery strategies.

Wraps the existing BTMovement.Move node with three additions:

    1. Optional pre-move safety guard
       A ``pre_move_check_fn`` callable is polled before the first movement
       command is issued.  The node yields RUNNING until the check passes.

    2. Pluggable RecoveryStrategy
       When BTMovement.Move fails (timeout / stall), SCMovement wraps it in
       a SelectorNode that attempts recovery before retrying.

    3. on_arrive callback  (new)
       A zero-arg callable fired exactly once when the move succeeds.
       For RunPath, called per-waypoint with (waypoint_index, (x, y)).

    4. Avoidance sentinel  (new)
       Pass an AvoidanceConfig to wrap the move with a per-tick agent
       collision sentinel.  See sc_framework/avoidance.py for tuning.

Both Move() and RunPath() return BehaviorTree instances that compose freely
with BTComposite.Sequence and any other BT node.
"""

from __future__ import annotations

import enum
import math as _math
import time as _time
from typing import TYPE_CHECKING, Callable

from Py4GWCoreLib.py4gwcorelib_src.BehaviorTree import BehaviorTree
from Py4GWCoreLib.routines_src.behaviourtrees_src.movement import BTMovement
from Py4GWCoreLib.routines_src.behaviourtrees_src.composite import BTComposite

if TYPE_CHECKING:
    from Py4GWCoreLib.sc_framework.avoidance import AvoidanceConfig


class RecoveryStrategy(enum.Enum):
    """Behaviour when a move step times out or stalls beyond the threshold."""
    NONE        = "none"        # Fail immediately — no retry.
    STRAFE      = "strafe"      # BTMovement's built-in strafe recovery then retry.
    CAST_SKILL  = "cast_skill"  # Call recovery_cast_fn, then retry.
    UNSTUCK_MSG = "unstuck_msg" # Broadcast multibox BruteForceUnstuck, then retry.


# ── debug overlay state ───────────────────────────────────────────────────────
# Updated at runtime when RunPath executes; read each frame by draw_path_overlay().
_overlay: dict = {
    "path":         [],    # list of (x, y) for the active path
    "wp_idx":       0,     # index of the waypoint currently being targeted (0 = first)
    "tolerance":    80.0,  # arrival radius in GW units, shown as circles on waypoints
    # Avoidance state — populated by AvoidanceSentinel._tick_avoidance() each frame:
    "avoid_cfg":    None,  # AvoidanceConfig | None
    "avoid_sample": None,  # ObstacleSample | None — closest in-path agent this tick
    "avoid_goal":   None,  # (x, y) | None — current sentinel target
}


_overlay_log_ms: float = 0.0


def _draw_thick_line_3d(
    dx,
    x1: float, y1: float, z1: float,
    x2: float, y2: float, z2: float,
    color:   int,
    n:       int   = 3,
    spacing: float = 5.0,
) -> None:
    """Draw n parallel world-space lines to simulate a thick line."""
    lx = x2 - x1
    ly = y2 - y1
    d  = _math.hypot(lx, ly)
    if d < 0.1:
        return
    px   = -ly / d * spacing
    py   =  lx / d * spacing
    half = (n - 1) * 0.5
    for i in range(n):
        s = i - half
        ox, oy = px * s, py * s
        dx.DrawLine3D(x1 + ox, y1 + oy, z1, x2 + ox, y2 + oy, z2, color, False)


def draw_path_overlay() -> None:
    """
    Render the active movement path, waypoint markers, and avoidance collision
    geometry in 3D world space.

    Call every frame from draw() while movement debug is enabled.  Silently
    no-ops if nothing is active or if DXOverlay is unavailable.

    Visual encoding — path:
        Cyan line     — segment currently being traversed
        Green line    — completed segments
        Grey line     — future segments not yet reached
        Yellow circle — current target waypoint (tolerance radius)
        Green circle  — completed waypoints
        Grey circle   — future waypoints
        Cyan line     — straight line from player position to current target

    Visual encoding — avoidance (when AvoidanceSentinel is active):
        Yellow ring   — scan radius (check_radius) centred on player
        Blue lines    — path corridor (±path_half_width) from player to goal
        Bright-red circle — current blocker (in-path enemy this tick)
        Red circle    — in-path enemy (passes corridor test but not closest)
        Orange circle — enemy in scan range but outside the path corridor
    """
    global _overlay_log_ms
    path = _overlay["path"]
    cfg  = _overlay.get("avoid_cfg")

    now = _time.monotonic() * 1_000.0
    if now - _overlay_log_ms >= 2_000.0:
        from Py4GWCoreLib.py4gwcorelib_src.Console import ConsoleLog, Console
        ConsoleLog("SC.Overlay", f"draw_path_overlay called — path={len(path)} wps  cfg={'SET' if cfg is not None else 'NONE'}  avoid_goal={_overlay.get('avoid_goal')}", Console.MessageType.Debug)
        _overlay_log_ms = now

    if len(path) < 1 and cfg is None:
        return
    try:
        from Py4GWCoreLib.DXOverlay import DXOverlay
        from Py4GWCoreLib.Player import Player
        from Py4GWCoreLib.py4gwcorelib_src.Color import Color

        idx  = _overlay["wp_idx"]
        tol  = _overlay["tolerance"]
        dx   = DXOverlay()
        Z_OFS = 100  # lift lines slightly off the ground

        C_DONE   = Color( 80, 200,  80, 200).to_dx_color()
        C_FUTURE = Color(160, 160, 160, 150).to_dx_color()
        C_ACTIVE = Color( 50, 230, 255, 255).to_dx_color()
        C_TARGET = Color(255, 220,  50, 255).to_dx_color()
        C_DONE_M = Color( 60, 150,  60, 160).to_dx_color()

        px, py = Player.GetXY()
        pz     = DXOverlay.FindZ(px, py) - Z_OFS

        # ── path segments ─────────────────────────────────────────────────
        for i in range(len(path) - 1):
            x1, y1 = path[i]
            x2, y2 = path[i + 1]
            z1 = DXOverlay.FindZ(x1, y1) - Z_OFS
            z2 = DXOverlay.FindZ(x2, y2) - Z_OFS
            col = C_DONE if i < idx else (C_ACTIVE if i == idx else C_FUTURE)
            _draw_thick_line_3d(dx, x1, y1, z1, x2, y2, z2, col)

        # ── waypoint circles (tolerance radius) ───────────────────────────
        for i, (wx, wy) in enumerate(path):
            wz  = DXOverlay.FindZ(wx, wy) - Z_OFS
            col = C_TARGET if i == idx else (C_DONE_M if i < idx else C_FUTURE)
            dx.DrawPoly3D(wx, wy, wz, tol, col, 12, False)

        # ── player → current target ───────────────────────────────────────
        if 0 <= idx < len(path):
            tx, ty = path[idx]
            tz = DXOverlay.FindZ(tx, ty) - Z_OFS
            _draw_thick_line_3d(dx, px, py, pz, tx, ty, tz, C_ACTIVE)

        # ── avoidance overlay ─────────────────────────────────────────────
        if cfg is not None:
            from Py4GWCoreLib.Agent import Agent
            from Py4GWCoreLib.AgentArray import AgentArray

            C_SCAN_R = Color(255, 200,  50,  55).to_dx_color()  # scan radius ring
            C_CORR   = Color( 80, 120, 255, 110).to_dx_color()  # corridor lines
            C_BLOCK  = Color(255,  20,  20, 255).to_dx_color()  # active blocker
            C_ENEMY  = Color(220,  70,  70, 180).to_dx_color()  # in-path enemy
            C_NEAR   = Color(200, 140,  60, 120).to_dx_color()  # nearby, not in path

            sample = _overlay.get("avoid_sample")
            goal   = _overlay.get("avoid_goal")
            if goal is None and 0 <= idx < len(path):
                goal = path[idx]

            # scan radius circle centred on player
            dx.DrawPoly3D(px, py, pz, cfg.check_radius, C_SCAN_R, 32, False)

            if goal is not None:
                gdx   = goal[0] - px
                gdy   = goal[1] - py
                gdist = _math.hypot(gdx, gdy)

                if gdist > 1.0:
                    gux    = gdx / gdist          # unit vector toward goal
                    guy    = gdy / gdist
                    perp_x = -guy                 # perpendicular (left)
                    perp_y =  gux
                    reach  = min(cfg.check_radius, gdist)
                    hw     = cfg.path_half_width

                    # corridor: two side lines + a closing cap at check_radius
                    lx0 = px + perp_x * hw
                    ly0 = py + perp_y * hw
                    lx1 = px + gux * reach + perp_x * hw
                    ly1 = py + guy * reach + perp_y * hw
                    rx0 = px - perp_x * hw
                    ry0 = py - perp_y * hw
                    rx1 = px + gux * reach - perp_x * hw
                    ry1 = py + guy * reach - perp_y * hw

                    z_l0 = DXOverlay.FindZ(lx0, ly0) - Z_OFS
                    z_l1 = DXOverlay.FindZ(lx1, ly1) - Z_OFS
                    z_r0 = DXOverlay.FindZ(rx0, ry0) - Z_OFS
                    z_r1 = DXOverlay.FindZ(rx1, ry1) - Z_OFS
                    _draw_thick_line_3d(dx, lx0, ly0, z_l0, lx1, ly1, z_l1, C_CORR)
                    _draw_thick_line_3d(dx, rx0, ry0, z_r0, rx1, ry1, z_r1, C_CORR)
                    _draw_thick_line_3d(dx, lx1, ly1, z_l1, rx1, ry1, z_r1, C_CORR)

                    # enemy agents: colour by their corridor status
                    blocker_id = sample.agent_id if sample is not None else None
                    try:
                        for aid in AgentArray.GetEnemyArray():
                            if not Agent.IsAlive(aid):
                                continue
                            ax, ay = Agent.GetXY(aid)
                            tox  = ax - px
                            toy  = ay - py
                            dist = _math.hypot(tox, toy)
                            if dist > cfg.check_radius * 1.5:
                                continue
                            az     = DXOverlay.FindZ(ax, ay) - Z_OFS
                            dot_av = gux * tox + guy * toy
                            crs_av = gux * toy - guy * tox
                            in_path = (
                                dot_av > cfg.agent_radius
                                and dist <= cfg.check_radius
                                and abs(crs_av) <= cfg.path_half_width + cfg.agent_radius
                            )
                            if aid == blocker_id:
                                col = C_BLOCK
                            elif in_path:
                                col = C_ENEMY
                            else:
                                col = C_NEAR
                            dx.DrawPoly3D(ax, ay, az, cfg.agent_radius, col, 8, False)
                    except Exception as _eexc:
                        from Py4GWCoreLib.py4gwcorelib_src.Console import ConsoleLog, Console
                        ConsoleLog("SC.Overlay", f"enemy loop ERROR: {_eexc}", Console.MessageType.Error)

    except Exception as _exc:
        from Py4GWCoreLib.py4gwcorelib_src.Console import ConsoleLog, Console
        ConsoleLog("SC.Overlay", f"draw_path_overlay ERROR: {_exc}", Console.MessageType.Error)


class SCMovement:
    """
    Factory for SC-aware movement trees.

    All methods return BehaviorTree instances; they are stateless and
    safe to compose freely inside a planner sequence.
    """

    # ── single waypoint ──────────────────────────────────────────────────

    @staticmethod
    def Move(
        x: float,
        y: float,
        *,
        tolerance:          float                       = 80.0,
        timeout_ms:         int                         = 20_000,
        stall_threshold_ms: int                         = 1_500,
        pre_move_check_fn:  Callable[[], bool] | None   = None,
        recovery:           RecoveryStrategy            = RecoveryStrategy.STRAFE,
        recovery_cast_fn:   Callable[[], None] | None   = None,
        on_arrive:          Callable[[], None] | None    = None,
        avoidance:          "AvoidanceConfig | None"    = None,
        log_fn:             Callable[[str], None] | None = None,
        name:               str                          = "SCMove",
    ) -> BehaviorTree:
        """
        Move to (x, y) with an optional safety guard and recovery on failure.

        Args:
            x, y:               Target coordinates in GW world space.
            tolerance:          Arrival radius in GW units.
            timeout_ms:         Give up and trigger recovery after this many ms.
            stall_threshold_ms: How long to wait before BTMovement's internal
                                strafe recovery kicks in (forwarded to BTMovement).
            pre_move_check_fn:  If provided, yields RUNNING until this returns True.
            recovery:           What to do when BTMovement.Move fails.
            recovery_cast_fn:   Callable invoked before retry when
                                strategy == CAST_SKILL.  Ignored otherwise.
            on_arrive:          Called with no arguments when the move succeeds.
                                Fires exactly once per Move() call.
            avoidance:          If provided, wraps the move with a real-time
                                agent-collision sentinel (AvoidanceConfig).
            name:               Label shown in BT debug output.
        """
        _tx = float(x)
        _ty = float(y)
        steps: list[BehaviorTree | BehaviorTree.Node] = []

        # ── safety guard ─────────────────────────────────────────────────
        if pre_move_check_fn is not None:
            _gls: dict = {"last_ms": 0.0}
            def _guard(_node: BehaviorTree.Node, _s=_gls) -> BehaviorTree.NodeState:
                if pre_move_check_fn():
                    return BehaviorTree.NodeState.SUCCESS
                if log_fn is not None:
                    now = _time.monotonic() * 1_000.0
                    if now - _s["last_ms"] >= 2_000.0:
                        log_fn(f"{name}: guard waiting…")
                        _s["last_ms"] = now
                return BehaviorTree.NodeState.RUNNING
            steps.append(BehaviorTree.ActionNode(_guard, name=f"{name}:Guard"))

        # ── core move ────────────────────────────────────────────────────
        if log_fn is not None:
            log_fn(f"{name}: → ({_tx:.0f}, {_ty:.0f})")

        move_tree = BTMovement.Move(
            x=_tx,
            y=_ty,
            tolerance=tolerance,
            timeout_ms=timeout_ms,
            stall_threshold_ms=stall_threshold_ms,
        )

        if recovery == RecoveryStrategy.NONE:
            steps.append(move_tree)
        else:
            recovery_branch = SCMovement._build_recovery_branch(
                strategy=recovery,
                recovery_cast_fn=recovery_cast_fn,
                x=_tx, y=_ty,
                tolerance=tolerance,
                timeout_ms=timeout_ms,
                name=name,
            )
            steps.append(
                BehaviorTree.SelectorNode(
                    children=[move_tree, recovery_branch],
                    name=f"{name}:WithRecovery",
                )
            )

        # ── on_arrive callback ────────────────────────────────────────────
        if on_arrive is not None or log_fn is not None:
            _user_arrive = on_arrive
            def _arrive(
                _: BehaviorTree.Node,
                _fn=_user_arrive, _lf=log_fn, _ax=_tx, _ay=_ty,
            ) -> BehaviorTree.NodeState:
                if _lf is not None:
                    _lf(f"{name}: arrived ({_ax:.0f}, {_ay:.0f})")
                if _fn is not None:
                    _fn()
                return BehaviorTree.NodeState.SUCCESS
            steps.append(BehaviorTree.ActionNode(_arrive, name=f"{name}:OnArrive"))

        # ── assemble base tree ────────────────────────────────────────────
        if len(steps) == 1:
            s = steps[0]
            tree: BehaviorTree = s if isinstance(s, BehaviorTree) else BehaviorTree(s)
        else:
            tree = BTComposite.Sequence(*steps, name=name)

        # ── avoidance sentinel wrap ───────────────────────────────────────
        # Wraps the assembled tree so the sentinel ticks alongside each frame.
        # The sentinel holds no movement state itself; it only injects key presses.
        if avoidance is not None:
            from Py4GWCoreLib.sc_framework.avoidance import AvoidanceSentinel
            tree = AvoidanceSentinel.wrap_move(tree, avoidance, (float(x), float(y)), name)

        return tree

    # ── waypoint path ─────────────────────────────────────────────────────

    @staticmethod
    def RunPath(
        waypoints: list[tuple[float, float]],
        *,
        tolerance:               float                                           = 80.0,
        per_waypoint_timeout_ms: int                                             = 20_000,
        stall_threshold_ms:      int                                             = 1_500,
        pre_move_check_fn:       Callable[[], bool] | None                       = None,
        recovery:                RecoveryStrategy                                = RecoveryStrategy.STRAFE,
        recovery_cast_fn:        Callable[[], None] | None                       = None,
        on_arrive:               Callable[[int, tuple[float, float]], None] | None = None,
        avoidance:               "AvoidanceConfig | None"                        = None,
        log_fn:                  Callable[[str], None] | None                    = None,
        name:                    str                                             = "SCRunPath",
    ) -> BehaviorTree:
        """
        Follow a sequence of waypoints, applying Move() to each one.

        Args:
            waypoints:               List of (x, y) tuples to visit in order.
            per_waypoint_timeout_ms: Independent timeout applied to each step.
            on_arrive:               Called as on_arrive(index, (x, y)) each time
                                     a waypoint is reached.  Useful for tracking
                                     the current goal for an external sentinel.
            avoidance:               If provided, all steps share a single
                                     AvoidanceSentinel whose goal_fn updates as
                                     each waypoint is consumed.  The sentinel
                                     retains its escalation state across the
                                     full path so a persistent blocker escalates
                                     correctly across waypoint transitions.
            (other args):            See Move() — applied uniformly to all steps.
        """
        if not waypoints:
            return BehaviorTree(BehaviorTree.SucceederNode(name=f"{name}:Empty"))

        _n_wps     = len(waypoints)
        _path_snap = [(float(wp[0]), float(wp[1])) for wp in waypoints]
        _tol_snap  = tolerance

        # ── overlay start node + per-waypoint arrive wrapper ──────────────
        # The start node runs once at tick-time (when this path first executes)
        # and sets up the 3D overlay state.  The arrive wrapper updates the
        # active waypoint index and logs each arrival.
        def _path_start(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
            _overlay["path"]         = _path_snap
            _overlay["wp_idx"]       = 0
            _overlay["tolerance"]    = _tol_snap
            _overlay["avoid_cfg"]    = None   # cleared until sentinel sets it
            _overlay["avoid_sample"] = None
            _overlay["avoid_goal"]   = None
            if log_fn is not None:
                log_fn(f"{name}: {_n_wps} waypoints")
            return BehaviorTree.NodeState.SUCCESS

        start_node = BehaviorTree(
            BehaviorTree.ActionNode(_path_start, name=f"{name}:Start")
        )

        _outer_on_arrive = on_arrive

        def _arrive_wrapper(i: int, wp: tuple) -> None:
            _overlay["wp_idx"] = i + 1
            if log_fn is not None:
                log_fn(
                    f"{name} [{i + 1}/{_n_wps}]:"
                    f" ({float(wp[0]):.0f}, {float(wp[1]):.0f})"
                )
            if _outer_on_arrive is not None:
                _outer_on_arrive(i, wp)

        # ── avoidance path (shared sentinel) ──────────────────────────────
        if avoidance is not None:
            from Py4GWCoreLib.sc_framework.avoidance import AvoidanceSentinel

            base_steps = [
                SCMovement.Move(
                    x=float(wp[0]),
                    y=float(wp[1]),
                    tolerance=tolerance,
                    timeout_ms=per_waypoint_timeout_ms,
                    stall_threshold_ms=stall_threshold_ms,
                    pre_move_check_fn=pre_move_check_fn,
                    recovery=recovery,
                    recovery_cast_fn=recovery_cast_fn,
                    # on_arrive and avoidance handled by wrap_path below
                    name=f"{name}[{i}]",
                )
                for i, wp in enumerate(waypoints)
            ]

            wrapped = AvoidanceSentinel.wrap_path(
                step_trees=base_steps,
                waypoints=_path_snap,
                config=avoidance,
                outer_on_arrive=_arrive_wrapper,
                name=name,
            )
            return BTComposite.Sequence(start_node, wrapped, name=f"{name}:WithStart")

        # ── standard path (no avoidance) ──────────────────────────────────
        def _make_step(i: int, wp: tuple[float, float]) -> BehaviorTree:
            def _cb(_i: int = i, _wp: tuple = wp) -> None:
                _arrive_wrapper(_i, (float(_wp[0]), float(_wp[1])))

            return SCMovement.Move(
                x=float(wp[0]),
                y=float(wp[1]),
                tolerance=tolerance,
                timeout_ms=per_waypoint_timeout_ms,
                stall_threshold_ms=stall_threshold_ms,
                pre_move_check_fn=pre_move_check_fn,
                recovery=recovery,
                recovery_cast_fn=recovery_cast_fn,
                on_arrive=_cb,
                name=f"{name}[{i}]",
            )

        step_trees = [_make_step(i, wp) for i, wp in enumerate(waypoints)]
        return BTComposite.Sequence(start_node, *step_trees, name=name)

    # ── private helpers ───────────────────────────────────────────────────

    @staticmethod
    def _build_recovery_branch(
        strategy:         RecoveryStrategy,
        recovery_cast_fn: Callable[[], None] | None,
        x:                float,
        y:                float,
        tolerance:        float,
        timeout_ms:       int,
        name:             str,
    ) -> BehaviorTree:
        """
        Build the fallback subtree used inside the SelectorNode.

        The retry move is always a fresh BTMovement.Move with the same target.
        The strategy determines what (if anything) runs before the retry.
        """
        retry_tree = BTMovement.Move(x=x, y=y, tolerance=tolerance, timeout_ms=timeout_ms)

        if strategy == RecoveryStrategy.CAST_SKILL and recovery_cast_fn is not None:
            def _cast(_: BehaviorTree.Node, _fn=recovery_cast_fn) -> BehaviorTree.NodeState:
                _fn()
                return BehaviorTree.NodeState.SUCCESS

            return BTComposite.Sequence(
                BehaviorTree.ActionNode(_cast, name=f"{name}:RecoveryCast"),
                retry_tree,
                name=f"{name}:RecoverySeq",
            )

        if strategy == RecoveryStrategy.UNSTUCK_MSG:
            def _unstuck(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
                from Py4GWCoreLib.GlobalCache import GLOBAL_CACHE
                from Py4GWCoreLib.enums_src.Multiboxing_enums import SharedCommandType
                for acc in GLOBAL_CACHE.ShMem.GetAllAccountData():
                    GLOBAL_CACHE.ShMem.SendMessage(
                        "", acc.AccountEmail, SharedCommandType.BruteForceUnstuck, []
                    )
                return BehaviorTree.NodeState.SUCCESS

            return BTComposite.Sequence(
                BehaviorTree.ActionNode(_unstuck, name=f"{name}:UnstuckMsg"),
                retry_tree,
                name=f"{name}:RecoverySeq",
            )

        # STRAFE: BTMovement handles strafe internally — just retry the move.
        return retry_tree
