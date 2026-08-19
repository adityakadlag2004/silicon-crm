package bo.kadlaginvestment.crm.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AccountCircle
import androidx.compose.material.icons.filled.Calculate
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.DateRange
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.EmojiEvents
import androidx.compose.material.icons.filled.FilterAlt
import androidx.compose.material.icons.filled.Groups
import androidx.compose.material.icons.filled.Insights
import androidx.compose.material.icons.filled.MedicalServices
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.People
import androidx.compose.material.icons.filled.Policy
import androidx.compose.material.icons.filled.PieChart
import androidx.compose.material.icons.filled.Receipt
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.ShowChart
import androidx.compose.material.icons.filled.SimCard
import androidx.compose.material.icons.filled.Timeline
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import bo.kadlaginvestment.crm.net.ApiClient
import kotlinx.coroutines.launch
import org.json.JSONObject

/**
 * Menu rows carry a real vector icon, not an emoji.
 *
 * Emoji render differently on every OEM skin, ignore the theme tint, and sit
 * at whatever baseline the system font puts them — which is why this list read
 * as a jumble next to the Material chrome everywhere else in the app.
 */
private data class MenuEntry(
    val icon: androidx.compose.ui.graphics.vector.ImageVector,
    val label: String,
    val group: String,               // section header it sits under
    val webPath: String? = null,     // opens WebActivity
    val native: String? = null,      // native route key
    val adminOnly: Boolean = false,
)

// Grouped, not one flat run of 21 rows in the order the screens happened to
// ship. Group order is section order on screen; `groupBy` keeps it.
private val ENTRIES = listOf(
    MenuEntry(Icons.Filled.People, "Clients", "Work", native = "clients"),
    MenuEntry(Icons.Filled.FilterAlt, "Lead Pipeline", "Work", native = "leads"),
    MenuEntry(Icons.Filled.Receipt, "All Sales", "Work", native = "sales"),
    MenuEntry(Icons.Filled.Refresh, "Renewals", "Work", native = "renewals"),
    MenuEntry(Icons.Filled.Notifications, "Notifications", "Work", native = "notifications"),
    MenuEntry(Icons.Filled.DateRange, "Calendar", "Work", webPath = "/clients/calendar/view/"),

    MenuEntry(Icons.Filled.Policy, "Policies", "Insurance", native = "policies"),
    MenuEntry(Icons.Filled.MedicalServices, "Claims", "Insurance", native = "claims"),

    MenuEntry(Icons.Filled.ShowChart, "Reports", "Reports", native = "reports"),
    MenuEntry(Icons.Filled.PieChart, "Client Analysis", "Reports", webPath = "/clients/analysis/"),
    MenuEntry(Icons.Filled.Timeline, "Past Performance", "Reports", webPath = "/clients/past-performance/"),
    MenuEntry(Icons.Filled.Calculate, "Financial Planner", "Reports", webPath = "/clients/sales/financial-planner/"),
    MenuEntry(Icons.Filled.Description, "Monthly Report", "Reports",
              webPath = "/clients/reports/monthly-business/", adminOnly = true),
    MenuEntry(Icons.Filled.Insights, "Business Analytics", "Reports",
              webPath = "/clients/reports/business-analytics/", adminOnly = true),

    MenuEntry(Icons.Filled.CheckCircle, "Approve Sales", "Admin",
              webPath = "/clients/sales/approve/", adminOnly = true),
    MenuEntry(Icons.Filled.Groups, "Team", "Admin", native = "team", adminOnly = true),
    MenuEntry(Icons.Filled.EmojiEvents, "Incentives & Campaigns", "Admin",
              native = "incentives", adminOnly = true),
    MenuEntry(Icons.Filled.Call, "Call Analytics", "Admin", native = "call_analytics", adminOnly = true),
    MenuEntry(Icons.Filled.Tune, "Firm Settings", "Admin",
              webPath = "/clients/admin/firm-settings/", adminOnly = true),
    MenuEntry(Icons.Filled.Shield, "Audit Log", "Admin",
              webPath = "/clients/admin/audit-log/", adminOnly = true),

    MenuEntry(Icons.Filled.AccountCircle, "My Profile (login ID · password)", "This phone",
              webPath = "/clients/me/profile/"),
    MenuEntry(Icons.Filled.SimCard, "Office SIM (call tracking)", "This phone", native = "sim"),
    MenuEntry(Icons.Filled.Settings, "App Settings", "This phone", native = "settings", adminOnly = true),
)

/** Native menu: entry point for remaining (web) screens + logout. */
@Composable
fun MenuScreen(
    modifier: Modifier = Modifier,
    onOpenWeb: (String) -> Unit,
    onOpenNative: (String) -> Unit,
    onLoggedOut: () -> Unit,
    onSessionExpired: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    // Cached for the session — this used to pull the whole dashboard payload
    // on every visit to the Menu tab just to learn the role.
    LaunchedEffect(Unit) { if (!Session.load()) onSessionExpired() }

    val role = Session.role
    val isAdmin = Session.isAdmin

    Column(
        modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text("Menu", fontSize = rsp(22), fontWeight = FontWeight.Bold, modifier = Modifier.padding(bottom = 4.dp))

        if (role == null) {
            LoadingBox(Modifier.heightIn(min = 120.dp))
        } else {
            ENTRIES.filter { !it.adminOnly || isAdmin }
                .groupBy { it.group }
                .forEach { (group, entries) ->
                    Text(
                        group.uppercase(),
                        fontSize = rsp(11),
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(top = 10.dp, start = 4.dp),
                    )
                    entries.forEach { entry ->
                        MenuRow(entry) {
                            when {
                                entry.native != null -> onOpenNative(entry.native)
                                entry.webPath != null -> onOpenWeb(entry.webPath)
                            }
                        }
                    }
                }

            Spacer(Modifier.height(12.dp))

            Card(
                modifier = Modifier.fillMaxWidth().clickable {
                    scope.launch {
                        ApiClient.post("/clients/api/app/logout/", JSONObject())
                        onLoggedOut()
                    }
                },
                colors = CardDefaults.cardColors(containerColor = StatusRed.copy(alpha = 0.08f)),
            ) {
                Text(
                    "Log out",
                    color = StatusRed,
                    fontWeight = FontWeight.SemiBold,
                    fontSize = rsp(15),
                    modifier = Modifier.padding(14.dp),
                )
            }
            Spacer(Modifier.height(20.dp))
        }
    }
}

@Composable
private fun MenuRow(entry: MenuEntry, onClick: () -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth().clickable(onClick = onClick),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 13.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            androidx.compose.material3.Icon(
                entry.icon,
                contentDescription = null,   // the label beside it says it
                tint = MaterialTheme.colorScheme.secondary,
                modifier = Modifier.padding(end = 14.dp).size(22.dp),
            )
            Text(entry.label, fontSize = rsp(15), fontWeight = FontWeight.Medium, modifier = Modifier.weight(1f))
            // Unread count, so there's a reason to open Notifications.
            if (entry.native == "notifications" && NotificationBadge.unread > 0) {
                Box(
                    Modifier
                        .background(StatusRed, androidx.compose.foundation.shape.CircleShape)
                        .padding(horizontal = 7.dp, vertical = 2.dp)
                ) {
                    Text(
                        "${NotificationBadge.unread}",
                        color = androidx.compose.ui.graphics.Color.White,
                        fontSize = rsp(11), fontWeight = FontWeight.Bold,
                    )
                }
            }
            if (entry.webPath != null) {
                Text("web", fontSize = rsp(10), color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Text("  ›", fontSize = rsp(15), color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}
