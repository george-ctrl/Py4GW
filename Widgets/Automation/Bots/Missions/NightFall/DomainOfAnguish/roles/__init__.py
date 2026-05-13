# Import all role modules so @register_role decorators fire before SCRole.detect() runs.
from . import tank    # noqa: F401  (MainTankRole, TrenchTankRole)
from . import spiker  # noqa: F401  (VoRRole, TKRole, EmpathyRole, BackfireRole)
from . import support # noqa: F401  (UAMonkRole, EmoRole)
