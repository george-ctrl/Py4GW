"""
coordinator.py — multi-role coordination primitives.

Provides three BT node factories built on the existing shared-memory
PostLock / IsLockSatisfied mechanism:

    signal_node(id)          Post once, return SUCCESS immediately.
                             Use to advertise "I reached point X" without
                             waiting for anyone else.

    wait_for_n_node(id, n)   Return RUNNING until n accounts have posted id.
                             Non-posting: does not add its own lock.

    barrier_node(id, n)      Post own arrival AND wait until n total have
                             arrived.  Classic rendezvous / sync point.

All three return BehaviorTree.ActionNode instances that can be dropped
anywhere inside a BT sequence tree.

Usage example:
    coord = SCCoordinator(own_email)

    # In Dasher planner:
    coord.wait_for_n_node(Signals.QUEST_GRABBED, n=3)

    # In each Aura planner:
    coord.signal_node(Signals.QUEST_GRABBED)

    # In all roles at boss entrance:
    coord.barrier_node(Barriers.ALL_AT_BOSS, required=4)
"""

from __future__ import annotations

import Py4GW
from Py4GWCoreLib.GlobalCache import GLOBAL_CACHE
from Py4GWCoreLib.py4gwcorelib_src.BehaviorTree import BehaviorTree

from .lock_kind import SC_LOCK_KIND, BARRIER_TTL_MS


class SCCoordinator:
    """
    Per-account factory for BT coordination nodes.

    Create one instance per run, passing this account's email.  Reuse the
    same instance for every node factory call; the email is embedded in
    every posted lock so shared memory can track which account posted what.
    """

    def __init__(self, email: str) -> None:
        # Email identifies this account in all PostLock calls.
        self._email = email

    # ── internal helpers ──────────────────────────────────────────────────

    @staticmethod
    def _now_ms() -> int:
        """Current tick count in milliseconds (GW's monotonic clock)."""
        return int(Py4GW.Game.get_tick_count64())

    def _post_lock(self, lock_id: int, max_holders: int) -> None:
        """Write one lock entry into shared memory under SC_LOCK_KIND."""
        GLOBAL_CACHE.ShMem.PostLock(
            self._email,
            SC_LOCK_KIND,
            lock_id,                          # key_id   — identifies this barrier/signal
            0,                                # target_id — unused for coordination
            self._now_ms() + BARRIER_TTL_MS,  # expires_at_tick
            0,                                # min_duration_ms
            max_holders,                      # how many holders are expected
            1,                                # claim_strength
        )

    @staticmethod
    def _is_satisfied(lock_id: int, required: int) -> bool:
        """True when at least ``required`` unexpired locks with lock_id exist."""
        return bool(GLOBAL_CACHE.ShMem.IsLockSatisfied(
            SC_LOCK_KIND,
            lock_id,
            0,                             # target_id
            None,                          # exclude_email — count all accounts
            SCCoordinator._now_ms(),
            required,
            1,                             # claim_strength
        ))

    # ── public node factories ─────────────────────────────────────────────

    def signal_node(self, signal_id: int, *, name: str = "") -> BehaviorTree.ActionNode:
        """
        Post a one-shot signal and return SUCCESS immediately.

        The role that calls this does NOT wait.  Other roles observe it with
        wait_for_n_node(signal_id, 1).  Safe to call multiple times — the
        lock simply refreshes its TTL on each call.

        Args:
            signal_id:  Unique integer identifying this signal (from constants.py).
            name:       Optional label shown in BT debug output.
        """
        _posted = [False]

        def _tick(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
            if not _posted[0]:
                self._post_lock(signal_id, 1)
                _posted[0] = True
            return BehaviorTree.NodeState.SUCCESS

        def _reset() -> None:
            _posted[0] = False
            BehaviorTree.ActionNode.reset  # call super via class

        node = BehaviorTree.ActionNode(_tick, name=name or f"Signal({signal_id})")
        # Patch reset so the node can be reused if the planner is restarted.
        _base_reset = node.reset
        node.reset = lambda: (setattr(_posted, '__setitem__', None) or _posted.__setitem__(0, False) or _base_reset())  # type: ignore[method-assign]
        return node

    def wait_for_n_node(
        self,
        signal_id: int,
        n: int,
        *,
        name: str = "",
    ) -> BehaviorTree.ActionNode:
        """
        Yield RUNNING until at least ``n`` accounts have posted signal_id.

        This node does NOT post anything itself; it only reads.  Pair it with
        signal_node() on the posting side.

        Args:
            signal_id:  The signal ID to watch.
            n:          Number of posts required before returning SUCCESS.
            name:       Optional label shown in BT debug output.
        """
        def _tick(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
            if self._is_satisfied(signal_id, n):
                return BehaviorTree.NodeState.SUCCESS
            return BehaviorTree.NodeState.RUNNING

        return BehaviorTree.ActionNode(_tick, name=name or f"WaitFor{n}({signal_id})")

    def barrier_node(
        self,
        barrier_id: int,
        required: int,
        *,
        name: str = "",
    ) -> BehaviorTree.ActionNode:
        """
        Rendezvous: post own arrival, then yield until ``required`` total.

        All participating roles call barrier_node with the SAME barrier_id and
        required value.  The first tick posts the lock; subsequent ticks poll
        IsLockSatisfied.  The internal posted flag resets on SUCCESS so the
        node can be reused (e.g. if the planner is restarted mid-run).

        Args:
            barrier_id:  Unique integer for this synchronization point.
            required:    Total number of accounts that must arrive.
            name:        Optional label shown in BT debug output.
        """
        _posted = [False]

        def _tick(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
            if not _posted[0]:
                self._post_lock(barrier_id, required)
                _posted[0] = True

            if self._is_satisfied(barrier_id, required):
                _posted[0] = False  # reset so node can be reused
                return BehaviorTree.NodeState.SUCCESS

            return BehaviorTree.NodeState.RUNNING

        return BehaviorTree.ActionNode(
            _tick,
            name=name or f"Barrier({barrier_id},{required})",
        )
