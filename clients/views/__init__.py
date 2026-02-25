"""
clients.views package – split from monolithic views.py into domain modules.

All view functions are re-exported here so that existing imports
(e.g. ``from clients.views import admin_dashboard``) continue to work.
"""

from .helpers import *  # noqa: F401,F403
from .auth import *  # noqa: F401,F403
from .leads import *  # noqa: F401,F403
from .calling import *  # noqa: F401,F403
from .dashboards import *  # noqa: F401,F403
from .clients_views import *  # noqa: F401,F403
from .sales import *  # noqa: F401,F403
from .reports import *  # noqa: F401,F403
from .calendar_views import *  # noqa: F401,F403
from .messaging import *  # noqa: F401,F403
from .notifications import *  # noqa: F401,F403
