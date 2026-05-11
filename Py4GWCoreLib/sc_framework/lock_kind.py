"""
lock_kind.py — shared constants for SC whiteboard locks.

The shared-memory PostLock / IsLockSatisfied system uses a ``kind_id`` integer
to namespace locks.  Value 50 is not used by any existing WhiteboardLockKind
entry, so we claim it for all SC coordination primitives.

All SC scripts that share a party should use the SAME constants from this file
so their barrier IDs stay in the same namespace and cannot collide across runs.
"""

# Whiteboard lock kind reserved for SC coordination.
# Any barrier or signal posted by SCCoordinator uses this kind_id.
SC_LOCK_KIND: int = 50

# How long (ms) a posted lock stays valid before auto-expiry.
# Long enough for any legitimate slow action (90s); short enough to clean up
# stale entries if an account disconnects mid-run.
BARRIER_TTL_MS: int = 90_000
