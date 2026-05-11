"""
services.py — generic parallel service tree factories.

These factories know nothing about Guild Wars skills, items, consumables, or
any game-specific content.  They accept plain callables so every speedclear
can instantiate them with its own logic.

Four patterns:

    UpkeepService        Recast anything when a condition is True.
                         Use for skill buffs, consumables, item effects, etc.

    StuckWatchdog        Monitor position delta; call a recovery callback
                         when the player has not moved for N ms.
                         Also writes STUCK=True/False to the BT blackboard.

    PartyFollowService   Find a party member via a resolver function;
                         trigger a follow action (EE, DC, etc.) when too far.
                         Caches the resolved target ID after first success.

    PeriodicService      Call an action every N ms.
                         Catch-all for anything that doesn't fit the above.

All four return a BehaviorTree whose root action always returns RUNNING, so
BottingTree can run them as forever-looping parallel services via AddServiceTree.

Usage (in an SC role file — NOT inside sc_framework):

    from Py4GWCoreLib.sc_framework.services import UpkeepService, StuckWatchdog

    # Shadow Form upkeep — specific to TotF, defined in TotF role:
    UpkeepService.build(
        "ShadowFormUpkeep",
        should_cast_fn = lambda: (
            not GLOBAL_CACHE.Effects.HasEffect(player_id, SkillID.ShadowForm)
            or GLOBAL_CACHE.Effects.GetEffectTimeRemaining(player_id, SkillID.ShadowForm) < 3_000
        ),
        cast_fn = lambda: GLOBAL_CACHE.SkillBar.UseSkill(sf_slot),
    )
"""

from __future__ import annotations

import time
from typing import Callable

from Py4GWCoreLib.py4gwcorelib_src.BehaviorTree import BehaviorTree


def _mono_ms() -> float:
    """Monotonic time in milliseconds — used for cooldown tracking."""
    return time.monotonic() * 1_000.0


# ── UpkeepService ─────────────────────────────────────────────────────────────

class UpkeepService:
    """
    Maintain any recurring action via a should-cast / cast pair.

    The service calls ``cast_fn()`` whenever ``should_cast_fn()`` returns True,
    subject to a per-call cooldown so it does not hammer the action queue on
    every frame.

    This is the single pattern for all upkeep needs:
        - Skill enchantments (Shadow Form, Shroud of Distress, …)
        - Consumable effects (Essence, Grail, food, …)
        - Any other "check active, reapply if not" pattern
    """

    @staticmethod
    def build(
        name: str,
        should_cast_fn: Callable[[], bool],
        cast_fn: Callable[[], None],
        *,
        cooldown_ms: int = 250,
    ) -> BehaviorTree:
        """
        Build a parallel service that maintains an effect or buff.

        The returned BehaviorTree always returns RUNNING; attach it to
        BottingTree via AddServiceTree.

        Args:
            name:           Label shown in BT debug output.
            should_cast_fn: Returns True when cast_fn should be triggered.
                            Called every frame; keep it cheap.
            cast_fn:        Called once per cooldown window when needed.
                            Should fire-and-forget into the action queue.
            cooldown_ms:    Minimum ms between successive cast attempts.
                            Prevents spam when the effect has a cast time.
        """
        state: dict = {"last_cast_ms": 0.0}

        def _tick(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
            now = _mono_ms()
            if should_cast_fn() and (now - state["last_cast_ms"]) >= cooldown_ms:
                cast_fn()
                state["last_cast_ms"] = now
            return BehaviorTree.NodeState.RUNNING

        return BehaviorTree(BehaviorTree.ActionNode(_tick, name=name))


# ── StuckWatchdog ─────────────────────────────────────────────────────────────

class StuckWatchdog:
    """
    Monitor the player's position; fire a recovery callback when stalled.

    Samples the position every ``sample_interval_ms`` ms.  If the player
    has not moved more than ``progress_radius`` units within
    ``stuck_threshold_ms`` ms, the watchdog sets ``blackboard["STUCK"] = True``
    and calls ``on_stuck()`` once per stuck event (resets after next movement).

    Planner nodes can gate movement on ``blackboard.get("STUCK", False)`` if
    they want to pause until the watchdog clears the flag.
    """

    @staticmethod
    def build_service(
        *,
        stuck_threshold_ms: int = 2_000,
        sample_interval_ms: int = 500,
        progress_radius: float = 50.0,
        on_stuck: Callable[[], None] | None = None,
    ) -> BehaviorTree:
        """
        Build a parallel position-monitor service.

        Args:
            stuck_threshold_ms: ms of no progress before declaring stuck.
            sample_interval_ms: how often (ms) to sample the position.
            progress_radius:    minimum GW units moved to count as progress.
            on_stuck:           optional callback invoked once per stuck event.
                                Suitable for casting an unstuck skill, sending
                                a multibox unstuck message, etc.
        """
        from Py4GWCoreLib.Player import Player
        from Py4GWCoreLib.py4gwcorelib_src.Utils import Utils

        state: dict = {
            "last_pos":        None,   # (x, y) at last detected progress
            "last_progress_ms": None,  # monotonic ms when progress was last seen
            "last_sample_ms":  0.0,    # throttle: only sample every N ms
            "stuck_fired":     False,  # prevent re-firing until player moves again
        }

        def _tick(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
            now = _mono_ms()

            # Only sample position at the configured interval.
            if now - state["last_sample_ms"] < sample_interval_ms:
                return BehaviorTree.NodeState.RUNNING

            state["last_sample_ms"] = now
            pos = Player.GetXY()

            # First sample — initialise baseline.
            if state["last_pos"] is None:
                state["last_pos"] = pos
                state["last_progress_ms"] = now
                node.blackboard["STUCK"] = False
                return BehaviorTree.NodeState.RUNNING

            dist = Utils.Distance(pos, state["last_pos"])

            if dist >= progress_radius:
                # Made progress — reset baseline.
                state["last_pos"] = pos
                state["last_progress_ms"] = now
                state["stuck_fired"] = False
                node.blackboard["STUCK"] = False
            elif (now - state["last_progress_ms"]) >= stuck_threshold_ms:
                # No progress for too long.
                node.blackboard["STUCK"] = True
                if on_stuck and not state["stuck_fired"]:
                    on_stuck()
                    state["stuck_fired"] = True
            else:
                node.blackboard["STUCK"] = False

            return BehaviorTree.NodeState.RUNNING

        return BehaviorTree(BehaviorTree.ActionNode(_tick, name="StuckWatchdog"))


# ── PartyFollowService ────────────────────────────────────────────────────────

class PartyFollowService:
    """
    Follow a party member using an injected action (Ebon Escape, DC, etc.).

    Each tick the service checks the distance to the target.  When farther
    than ``max_distance``, it calls ``follow_fn(agent_id)`` subject to a
    cooldown.

    The target ID is resolved lazily via ``target_fn`` and cached after the
    first successful lookup.  The cache is invalidated if the agent becomes
    invalid (e.g. after a map transition).
    """

    @staticmethod
    def build(
        name: str,
        target_fn: Callable[[], int | None],
        follow_fn: Callable[[int], None],
        *,
        max_distance: float = 500.0,
        cooldown_ms: int = 1_500,
    ) -> BehaviorTree:
        """
        Build a parallel follow service.

        Args:
            name:         Label shown in BT debug output.
            target_fn:    Returns the agent ID to follow, or None if not found.
                          Called on first tick and after cache invalidation.
            follow_fn:    Called with the agent ID when player is too far away.
                          Typically fires a skill into the action queue.
            max_distance: GW units beyond which follow_fn is triggered.
            cooldown_ms:  Minimum ms between follow attempts.
        """
        from Py4GWCoreLib.Player import Player
        from Py4GWCoreLib.Agent import Agent
        from Py4GWCoreLib.py4gwcorelib_src.Utils import Utils

        state: dict = {
            "target_id":     None,  # cached agent ID of the target
            "last_follow_ms": 0.0,  # last time follow_fn was triggered
        }

        def _tick(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
            now = _mono_ms()

            # Re-resolve when cache is empty or target became invalid.
            if state["target_id"] is None or not Agent.IsValid(state["target_id"]):
                state["target_id"] = target_fn()

            target_id = state["target_id"]
            if target_id is None:
                return BehaviorTree.NodeState.RUNNING  # target not in range yet

            my_pos     = Player.GetXY()
            target_pos = Agent.GetXY(target_id)
            distance   = Utils.Distance(my_pos, target_pos)

            if distance > max_distance and (now - state["last_follow_ms"]) >= cooldown_ms:
                follow_fn(target_id)
                state["last_follow_ms"] = now

            return BehaviorTree.NodeState.RUNNING

        return BehaviorTree(BehaviorTree.ActionNode(_tick, name=name))


# ── PeriodicService ───────────────────────────────────────────────────────────

class PeriodicService:
    """
    Call an action on a fixed interval.

    Catch-all for anything that doesn't fit UpkeepService, StuckWatchdog,
    or PartyFollowService.  Examples: applying War Supplies on a timer,
    re-sending a multibox command every 30 s, refreshing a blackboard value.
    """

    @staticmethod
    def build(
        name: str,
        action_fn: Callable[[], None],
        *,
        interval_ms: int = 5_000,
        run_immediately: bool = True,
    ) -> BehaviorTree:
        """
        Build a parallel periodic action service.

        Args:
            name:            Label shown in BT debug output.
            action_fn:       Callable invoked every interval_ms.
            interval_ms:     Time between invocations in milliseconds.
            run_immediately: If True, fires on the very first tick rather
                             than waiting for the first full interval.
        """
        state: dict = {"last_ms": 0.0 if run_immediately else _mono_ms()}

        def _tick(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
            now = _mono_ms()
            if (now - state["last_ms"]) >= interval_ms:
                action_fn()
                state["last_ms"] = now
            return BehaviorTree.NodeState.RUNNING

        return BehaviorTree(BehaviorTree.ActionNode(_tick, name=name))
