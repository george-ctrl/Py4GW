# Importing both role modules registers them with SCRole via @register_role.
# Import order determines detection priority (Dasher checked first).
from .dasher import DasherRole  # noqa: F401
from .aura   import AuraRole    # noqa: F401
