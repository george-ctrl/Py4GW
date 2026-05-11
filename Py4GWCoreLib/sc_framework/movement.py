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
        on_arrive:          Callable[[], None] | None   = None,
        avoidance:          "AvoidanceConfig | None"    = None,
        name:               str                         = "SCMove",
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
        steps: list[BehaviorTree | BehaviorTree.Node] = []

        # ── safety guard ─────────────────────────────────────────────────
        if pre_move_check_fn is not None:
            def _guard(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
                return (
                    BehaviorTree.NodeState.SUCCESS
                    if pre_move_check_fn()
                    else BehaviorTree.NodeState.RUNNING
                )
            steps.append(BehaviorTree.ActionNode(_guard, name=f"{name}:Guard"))

        # ── core move ────────────────────────────────────────────────────
        move_tree = BTMovement.Move(
            x=x,
            y=y,
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
                x=x, y=y,
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
        # Appended as the final step in the sequence; fires only when all
        # prior steps (guard + move + recovery) have succeeded.
        if on_arrive is not None:
            def _arrive(_: BehaviorTree.Node, _fn=on_arrive) -> BehaviorTree.NodeState:
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

        # ── avoidance path (shared sentinel) ──────────────────────────────
        # Build step trees without on_arrive or avoidance first, then let
        # AvoidanceSentinel.wrap_path inject both the arrive callbacks and the
        # parallel sentinel wrapping in one pass.
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

            return AvoidanceSentinel.wrap_path(
                step_trees=base_steps,
                waypoints=[(float(wp[0]), float(wp[1])) for wp in waypoints],
                config=avoidance,
                outer_on_arrive=on_arrive,
                name=name,
            )

        # ── standard path (no avoidance) ──────────────────────────────────
        def _make_step(i: int, wp: tuple[float, float]) -> BehaviorTree:
            step_arrive: Callable[[], None] | None = None
            if on_arrive is not None:
                def _cb(_i: int = i, _wp: tuple = wp, _fn=on_arrive) -> None:
                    _fn(_i, (float(_wp[0]), float(_wp[1])))
                step_arrive = _cb

            return SCMovement.Move(
                x=float(wp[0]),
                y=float(wp[1]),
                tolerance=tolerance,
                timeout_ms=per_waypoint_timeout_ms,
                stall_threshold_ms=stall_threshold_ms,
                pre_move_check_fn=pre_move_check_fn,
                recovery=recovery,
                recovery_cast_fn=recovery_cast_fn,
                on_arrive=step_arrive,
                name=f"{name}[{i}]",
            )

        step_trees = [_make_step(i, wp) for i, wp in enumerate(waypoints)]
        return BTComposite.Sequence(*step_trees, name=name)

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
