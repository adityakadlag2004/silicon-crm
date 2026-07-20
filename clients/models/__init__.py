"""Silicon CRM models, split by domain.

Every model still lives in the `clients` app (same app_label, same
tables, same migrations) — this package split is organizational only.
All names are re-exported so `from clients.models import X` keeps
working everywhere.
"""
from .hr import (  # noqa: F401
    Employee,
    EmployeeMilestone,
    EmployeeTarget,
    ManagerAccessConfig,
)
from .catalog import (  # noqa: F401
    Product,
    ProductMarginSlab,
)
from .ops import (  # noqa: F401
    ExpenseCategory,
    Expense,
    AuditLog,
    FirmSettings,
)
from .clients import (  # noqa: F401
    Client,
    ClientMappingAudit,
    Family,
    Renewal,
)
from .insurance import (  # noqa: F401
    InsuranceClaim,
    InsurancePolicy,
    Meeting,
)
from .targets import (  # noqa: F401
    Target,
    BusinessTarget,
    MonthlyTargetHistory,
)
from .incentives import (  # noqa: F401
    IncentiveRule,
    IncentiveSlab,
    Campaign,
    CampaignProduct,
    CampaignSlab,
    campaign_product_overlaps,
)
from .sales import (  # noqa: F401
    Sale,
    Redemption,
    NetBusinessEntry,
    NetSipEntry,
)
from .leads import (  # noqa: F401
    Lead,
    LeadRemark,
    LeadFollowUp,
    LeadFamilyMember,
    LeadProductProgress,
)
from .engagement import (  # noqa: F401
    CalendarEvent,
    MessageTemplate,
    MessageLog,
    Notification,
)
from .calls import (  # noqa: F401
    CallTrackingSettings,
    CallLogEntry,
    CallFollowUp,
    AppDeviceStatus,
    PushDevice,
)
from .tasks import (  # noqa: F401
    TaskCategory,
    Task,
    TaskSubscriber,
    TaskChecklistItem,
    TaskComment,
    TaskAttachment,
    TaskActivity,
    TaskTemplate,
    RecurringTaskRule,
    TaskReminderSetting,
    NotificationPreference,
    SavedTaskFilter,
)
from .links import (  # noqa: F401
    LinkCategory,
    Link,
    LinkFavorite,
)
from .mf import (  # noqa: F401
    RTA_CAMS,
    RTA_KFIN,
    RTA_CHOICES,
    normalize_broker_code,
    ArnAccount,
    MutualFundFolio,
    MutualFundTransaction,
    RTAFeedImport,
    SipRegistration,
)
