"""
movement.py — SC-aware movement with pluggable recovery strategies.

Wraps the existing BTMovement.Move node with two additions:

    1. Optional pre-move safety guard
       A ``pre_move_check_fn`` callable is polled before the first movement
       command is issued.  The node yields RUNNING until the check passes.
       The SC role supplies the check (e.g. "wait for Shadow Form to be
       active"); the framework never references any specific skill.

    2. Pluggable RecoveryStrategy
       When BTMovement.Move fails (timeout / stall), SCMovement wraps it in
       a SelectorNode that attempts recovery before retrying.  The strategy
       enum selects the recovery behaviour; the SC role supplies the cast
       callable for CAST_SKILL.

Both Move() and RunPath() return BehaviorTree instances that compose freely
with BTComposite.Sequence and any other BT node.
"""

from __future__ import annotations

import enum
from typing import Callable

from Py4GWCoreLib.py4gwcorelib_src.BehaviorTree import BehaviorTree
from Py4GWCoreLib.routines_src.behaviourtrees_src.movement import BTMovement
from Py4GWCoreLib.routines_src.behaviourtrees_src.composite import BTComposite


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
        tolerance: float = 80.0,
        timeout_ms: int = 20_000,
        stall_threshold_ms: int = 1_500,
        pre_move_check_fn: Callable[[], bool] | None = None,
        recovery: RecoveryStrategy = RecoveryStrategy.STRAFE,
        recovery_cast_fn: Callable[[], None] | None = None,
        name: str = "SCMove",
    ) -> BehaviorTree:
        """
        Move to (x, y) with an optional safety guard and recovery on failure.

        Args:
            x, y:               Target coordinates in GW world space.
            tolerance:          Arrival radius in GW units.
            timeout_ms:         Give up and trigger recovery after this many ms.
            stall_threshold_ms: How long to wait before BTMovement's internal
                                strafe recovery kicks in (forwarded to BTMovement).
            pre_move_check_fn:  If provided, the move waits (RUNNING) until
                                this returns True.  Use for "wait for SF active",
                                "wait out of combat", etc.  None = always safe.
            recovery:           What to do when BTMovement.Move fails.
            recovery_cast_fn:   Callable invoked before retry when
                                strategy == CAST_SKILL.  Ignored otherwise.
            name:               Label shown in BT debug output.
        """
        steps: list[BehaviorTree | BehaviorTree.Node] = []

        # ── safety guard ─────────────────────────────────────────────────
        # Yield RUNNING until pre_move_check_fn passes.  This node is cheap
        # to evaluate and prevents the character from running naked.
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
            # No recovery: just append the move and let it fail.
            steps.append(move_tree)
        else:
            # Wrap in a SelectorNode: try move first, fall back to recovery.
            # SelectorNode resumes from the RUNNING child on the next tick,
            # so it will stay in the move until it finishes or fails.
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

        # ── assemble ─────────────────────────────────────────────────────
        if len(steps) == 1:
            s = steps[0]
            return s if isinstance(s, BehaviorTree) else BehaviorTree(s)

        return BTComposite.Sequence(*steps, name=name)

    # ── waypoint path ─────────────────────────────────────────────────────

    @staticmethod
    def RunPath(
        waypoints: list[tuple[float, float]],
        *,
        tolerance: float = 80.0,
        per_waypoint_timeout_ms: int = 20_000,
        stall_threshold_ms: int = 1_500,
        pre_move_check_fn: Callable[[], bool] | None = None,
        recovery: RecoveryStrategy = RecoveryStrategy.STRAFE,
        recovery_cast_fn: Callable[[], None] | None = None,
        name: str = "SCRunPath",
    ) -> BehaviorTree:
        """
        Follow a sequence of waypoints, applying Move() to each one.

        All Move() parameters are forwarded to each waypoint step.  The path
        returns SUCCESS only after every waypoint has been reached in order.
        An empty waypoint list returns SUCCESS immediately.

        Args:
            waypoints:               List of (x, y) tuples to visit in order.
            per_waypoint_timeout_ms: Independent timeout applied to each step.
            (other args):            See Move() — applied uniformly to all steps.
        """
        if not waypoints:
            # Nothing to do — treat as trivially succeeded.
            return BehaviorTree(BehaviorTree.SucceederNode(name=f"{name}:Empty"))

        step_trees = [
            SCMovement.Move(
                x=float(wp[0]),
                y=float(wp[1]),
                tolerance=tolerance,
                timeout_ms=per_waypoint_timeout_ms,
                stall_threshold_ms=stall_threshold_ms,
                pre_move_check_fn=pre_move_check_fn,
                recovery=recovery,
                recovery_cast_fn=recovery_cast_fn,
                name=f"{name}[{i}]",
            )
            for i, wp in enumerate(waypoints)
        ]

        return BTComposite.Sequence(*step_trees, name=name)

    # ── private helpers ───────────────────────────────────────────────────

    @staticmethod
    def _build_recovery_branch(
        strategy: RecoveryStrategy,
        recovery_cast_fn: Callable[[], None] | None,
        x: float,
        y: float,
        tolerance: float,
        timeout_ms: int,
        name: str,
    ) -> BehaviorTree:
        """
        Build the fallback subtree used inside the SelectorNode.

        The retry move is always a fresh BTMovement.Move with the same target.
        The strategy determines what (if anything) runs before the retry.
        """
        retry_tree = BTMovement.Move(x=x, y=y, tolerance=tolerance, timeout_ms=timeout_ms)

        if strategy == RecoveryStrategy.CAST_SKILL and recovery_cast_fn is not None:
            # Fire the recovery skill once (succeeds immediately), then retry.
            def _cast(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
                recovery_cast_fn()
                return BehaviorTree.NodeState.SUCCESS

            return BTComposite.Sequence(
                BehaviorTree.ActionNode(_cast, name=f"{name}:RecoveryCast"),
                retry_tree,
                name=f"{name}:RecoverySeq",
            )

        if strategy == RecoveryStrategy.UNSTUCK_MSG:
            # Broadcast BruteForceUnstuck to all multibox accounts, then retry.
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
