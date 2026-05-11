"""
actions.py — generic atomic BT action node factories.

All factories accept callables for IDs, targets, and conditions so the
framework never hard-codes any GW skill ID, model ID, item ID, or quest ID.
Every function returns a BehaviorTree.

Factories:
    CastSkill          Fire a skill from a given slot, optionally targeted.
    WaitForCondition   Yield RUNNING until a callable returns True.
    InteractNPC        Target and interact with an NPC found by a resolver.
    PickupNearestItem  Pick up the nearest ground item with a given model ID.
    TakeQuest          Interact with an NPC and accept a quest dialog.
"""

from __future__ import annotations

import time
from typing import Callable

from Py4GWCoreLib.py4gwcorelib_src.BehaviorTree import BehaviorTree


def _mono_ms() -> float:
    return time.monotonic() * 1_000.0


class SCActions:
    """Collection of generic atomic BT action factories."""

    @staticmethod
    def CastSkill(
        slot_fn: Callable[[], int],
        target_fn: Callable[[], int] | None = None,
        *,
        name: str = "CastSkill",
    ) -> BehaviorTree:
        """
        Cast a skill once and return SUCCESS.

        Fires the cast into the action queue and returns immediately.  Use
        WaitForCondition afterward if you need to wait for the cast to land.

        Args:
            slot_fn:   Returns the skill bar slot (1–8) to cast.
            target_fn: Returns the target agent ID, or None for targetless.
            name:      Label shown in BT debug output.
        """
        def _tick(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
            from Py4GWCoreLib.GlobalCache import GLOBAL_CACHE
            slot = slot_fn()
            if slot < 1 or slot > 8:
                return BehaviorTree.NodeState.FAILURE
            target = target_fn() if target_fn else 0
            GLOBAL_CACHE.SkillBar.UseSkill(slot, target or 0)
            return BehaviorTree.NodeState.SUCCESS

        return BehaviorTree(BehaviorTree.ActionNode(_tick, name=name))

    @staticmethod
    def WaitForCondition(
        condition_fn: Callable[[], bool],
        *,
        timeout_ms: int = 0,
        name: str = "WaitForCondition",
    ) -> BehaviorTree:
        """
        Yield RUNNING until condition_fn() returns True.

        Args:
            condition_fn: Returns True when the wait should end.
            timeout_ms:   If > 0, return FAILURE after this many ms.
                          Use 0 (default) to wait indefinitely.
            name:         Label shown in BT debug output.
        """
        state: dict = {"start_ms": None}

        def _tick(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
            now = _mono_ms()
            if state["start_ms"] is None:
                state["start_ms"] = now

            if condition_fn():
                state["start_ms"] = None
                return BehaviorTree.NodeState.SUCCESS

            if timeout_ms > 0 and (now - state["start_ms"]) >= timeout_ms:
                state["start_ms"] = None
                return BehaviorTree.NodeState.FAILURE

            return BehaviorTree.NodeState.RUNNING

        return BehaviorTree(BehaviorTree.ActionNode(_tick, name=name))

    @staticmethod
    def InteractNPC(
        agent_id_fn: Callable[[], int | None],
        *,
        timeout_ms: int = 5_000,
        name: str = "InteractNPC",
    ) -> BehaviorTree:
        """
        Target and interact with an NPC found by agent_id_fn.

        Yields RUNNING until the agent is found; sends target + interact once.
        Returns FAILURE if agent_id_fn returns None for the full timeout_ms.

        Args:
            agent_id_fn: Returns the agent ID to interact with, or None.
            timeout_ms:  Fail if the agent is not found within this time.
            name:        Label shown in BT debug output.
        """
        state: dict = {"start_ms": None, "done": False}

        def _tick(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
            from Py4GWCoreLib.Player import Player

            if state["done"]:
                state["done"] = False
                return BehaviorTree.NodeState.SUCCESS

            now = _mono_ms()
            if state["start_ms"] is None:
                state["start_ms"] = now

            agent_id = agent_id_fn()
            if agent_id is None:
                if (now - state["start_ms"]) >= timeout_ms:
                    state["start_ms"] = None
                    return BehaviorTree.NodeState.FAILURE
                return BehaviorTree.NodeState.RUNNING

            Player.ChangeTarget(agent_id)
            Player.Interact(agent_id)
            state["start_ms"] = None
            state["done"] = True
            return BehaviorTree.NodeState.RUNNING  # give the game one frame

        return BehaviorTree(BehaviorTree.ActionNode(_tick, name=name))

    @staticmethod
    def PickupNearestItem(
        model_id: int,
        *,
        search_range: float = 1_500.0,
        timeout_ms: int = 5_000,
        name: str = "PickupItem",
    ) -> BehaviorTree:
        """
        Pick up the nearest item with the given model ID.

        Scans ItemArray each tick.  Returns SUCCESS after issuing the pickup
        command; returns FAILURE if nothing is found within timeout_ms.

        Args:
            model_id:     Model ID of the item to pick up.
            search_range: GW units to search within.
            timeout_ms:   Fail if not found within this time.
            name:         Label shown in BT debug output.
        """
        state: dict = {"start_ms": None}

        def _tick(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
            from Py4GWCoreLib.Player import Player
            from Py4GWCoreLib.ItemArray import ItemArray

            now = _mono_ms()
            if state["start_ms"] is None:
                state["start_ms"] = now

            px, py = Player.GetXY()
            item_id = ItemArray.GetNearestItemByModelID(model_id, px, py, search_range)

            if not item_id:
                if (now - state["start_ms"]) >= timeout_ms:
                    state["start_ms"] = None
                    return BehaviorTree.NodeState.FAILURE
                return BehaviorTree.NodeState.RUNNING

            Player.PickUpItem(item_id)
            state["start_ms"] = None
            return BehaviorTree.NodeState.SUCCESS

        return BehaviorTree(BehaviorTree.ActionNode(_tick, name=name))

    @staticmethod
    def TakeQuest(
        quest_id: int,
        npc_agent_id_fn: Callable[[], int | None],
        *,
        dialog_id: int = 0x85,
        timeout_ms: int = 8_000,
        name: str = "TakeQuest",
    ) -> BehaviorTree:
        """
        Accept a quest from an NPC.

        Flow: interact with NPC → wait for dialog → accept dialog.
        Returns SUCCESS if the quest appears in the quest log within timeout_ms.

        Args:
            quest_id:        GW quest ID used to verify success.
            npc_agent_id_fn: Returns the NPC agent ID, or None if not in range.
            dialog_id:       Dialog button ID to press (0x85 = first option).
            timeout_ms:      Overall timeout for the whole interaction.
            name:            Label shown in BT debug output.
        """
        import PyQuest
        state: dict = {"start_ms": None, "interacted": False}

        def _tick(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
            from Py4GWCoreLib.Player import Player
            from Py4GWCoreLib.GlobalCache import GLOBAL_CACHE

            now = _mono_ms()
            if state["start_ms"] is None:
                state["start_ms"] = now

            # Already in quest log — done.
            if PyQuest.QuestLog.IsQuestActive(quest_id):
                state["start_ms"] = None
                state["interacted"] = False
                return BehaviorTree.NodeState.SUCCESS

            if (now - state["start_ms"]) >= timeout_ms:
                state["start_ms"] = None
                state["interacted"] = False
                return BehaviorTree.NodeState.FAILURE

            if not state["interacted"]:
                agent_id = npc_agent_id_fn()
                if agent_id:
                    Player.ChangeTarget(agent_id)
                    Player.Interact(agent_id)
                    state["interacted"] = True
            else:
                # Keep pressing the accept button until the quest log updates.
                GLOBAL_CACHE.UI.AcceptDialog(dialog_id)

            return BehaviorTree.NodeState.RUNNING

        return BehaviorTree(BehaviorTree.ActionNode(_tick, name=name))
