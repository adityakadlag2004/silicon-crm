"""
clients.views package – split from monolithic views.py into domain modules.

All view functions are re-exported here so that existing imports
(e.g. ``from clients.views import admin_dashboard``) continue to work.
"""

from .helpers import *  # noqa: F401,F403
from .auth import *  # noqa: F401,F403
from .leads import *  # noqa: F401,F403
from .dashboards import *  # noqa: F401,F403
from .clients_views import *  # noqa: F401,F403
from .sales import *  # noqa: F401,F403
from .campaigns import *  # noqa: F401,F403
from .reports import *  # noqa: F401,F403
from .calendar_views import *  # noqa: F401,F403
from .messaging import *  # noqa: F401,F403
from .notifications import *  # noqa: F401,F403
from .team import *  # noqa: F401,F403
from .renewal_views import *  # noqa: F401,F403
from .audit import *  # noqa: F401,F403
from .lead_records import *  # noqa: F401,F403
from .calls import *  # noqa: F401,F403
from .app_api import *  # noqa: F401,F403
from . import tasks  # noqa: F401  (task views referenced as views.tasks.* in urls)
from . import links  # noqa: F401  (link views referenced as views.links.* in urls)
from . import app_tasks_api  # noqa: F401  (native JSON endpoints as views.app_tasks_api.*)
from . import app_settings_api  # noqa: F401  (native Settings screen as views.app_settings_api.*)
from . import mf  # noqa: F401  (Mutual Funds RTA-feed views as views.mf.*)
from . import kyc  # noqa: F401  (Client KYC issues / merge views as views.kyc.*)
