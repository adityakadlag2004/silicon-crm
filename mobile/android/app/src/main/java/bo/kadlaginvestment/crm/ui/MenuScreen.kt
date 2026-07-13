package bo.kadlaginvestment.crm.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
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

private data class MenuEntry(
    val icon: String,
    val label: String,
    val webPath: String? = null,     // opens WebActivity
    val native: String? = null,      // native route key
    val adminOnly: Boolean = false,
)

private val ENTRIES = listOf(
    MenuEntry("👥", "Clients", native = "clients"),
    MenuEntry("📶", "Office SIM (call tracking)", native = "sim"),
    MenuEntry("🔔", "Notifications", native = "notifications"),
    MenuEntry("🧾", "All Sales", native = "sales"),
    MenuEntry("🔁", "Renewals", native = "renewals"),
    MenuEntry("🫧", "Lead Pipeline", native = "leads"),
    MenuEntry("📈", "Reports", native = "reports"),
    MenuEntry("📇", "Lead Records", native = "sheets"),
    MenuEntry("📅", "Calendar", webPath = "/clients/calendar/view/"),
    MenuEntry("🧮", "Financial Planner", webPath = "/clients/sales/financial-planner/"),
    MenuEntry("📊", "Client Analysis", webPath = "/clients/analysis/"),
    MenuEntry("📈", "Past Performance", webPath = "/clients/past-performance/"),
    MenuEntry("📞", "Call Analytics", native = "call_analytics", adminOnly = true),
    MenuEntry("⚙️", "App Settings", native = "settings", adminOnly = true),
    MenuEntry("✅", "Approve Sales (web)", webPath = "/clients/sales/approve/", adminOnly = true),
    MenuEntry("👥", "Team", native = "team", adminOnly = true),
    MenuEntry("🏆", "Incentives & Campaigns", native = "incentives", adminOnly = true),
    MenuEntry("📄", "Monthly Report", webPath = "/clients/reports/monthly-business/", adminOnly = true),
    MenuEntry("🧠", "Business Analytics", webPath = "/clients/reports/business-analytics/", adminOnly = true),
    MenuEntry("🛡️", "Audit Log", webPath = "/clients/admin/audit-log/", adminOnly = true),
    MenuEntry("⚙️", "Firm Settings", webPath = "/clients/admin/firm-settings/", adminOnly = true),
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
    var role by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(Unit) {
        when (val r = ApiClient.get("/clients/api/app/dashboard/")) {
            is ApiClient.Result.Ok -> role = r.json.optString("role")
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> role = "employee"  // degrade: show non-admin menu
        }
    }

    val isAdmin = role == "admin"

    Column(
        modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text("Menu", fontSize = 22.sp, fontWeight = FontWeight.Bold, modifier = Modifier.padding(bottom = 4.dp))

        if (role == null) {
            LoadingBox(Modifier.height(120.dp))
        } else {
            ENTRIES.filter { !it.adminOnly || isAdmin }.forEach { entry ->
                Card(
                    modifier = Modifier.fillMaxWidth().clickable {
                        when {
                            entry.native != null -> onOpenNative(entry.native)
                            entry.webPath != null -> onOpenWeb(entry.webPath)
                        }
                    },
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                    elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
                ) {
                    Row(
                        Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 13.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(entry.icon, fontSize = 18.sp, modifier = Modifier.padding(end = 12.dp))
                        Text(entry.label, fontSize = 15.sp, fontWeight = FontWeight.Medium, modifier = Modifier.weight(1f))
                        if (entry.webPath != null) {
                            Text("web", fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        Text("  ›", fontSize = 15.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
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
                    fontSize = 15.sp,
                    modifier = Modifier.padding(14.dp),
                )
            }
            Spacer(Modifier.height(20.dp))
        }
    }
}
