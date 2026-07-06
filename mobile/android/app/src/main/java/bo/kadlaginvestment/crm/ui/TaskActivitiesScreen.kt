package bo.kadlaginvestment.crm.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import bo.kadlaginvestment.crm.net.ApiClient
import org.json.JSONObject

@Composable
fun TaskActivitiesScreen(
    onOpenTask: (Int) -> Unit,
    onSessionExpired: () -> Unit,
) {
    var rows by remember { mutableStateOf(listOf<JSONObject>()) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(Unit) {
        when (val r = ApiClient.get("/clients/api/app/tasks/activities/")) {
            is ApiClient.Result.Ok -> {
                val arr = r.json.optJSONArray("activities")
                rows = (0 until (arr?.length() ?: 0)).map { arr!!.getJSONObject(it) }
            }
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
        loading = false
    }

    when {
        error != null -> ErrorBox(error!!) { error = null; loading = true }
        loading -> LoadingBox()
        rows.isEmpty() -> Column(Modifier.fillMaxSize().padding(24.dp)) {
            Text("No activity yet.", color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        else -> LazyColumn(
            Modifier.fillMaxSize(),
            verticalArrangement = Arrangement.spacedBy(6.dp),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(12.dp),
        ) {
            items(rows) { a ->
                Card(
                    Modifier.fillMaxWidth().clickable { onOpenTask(a.optInt("task_id")) },
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                ) {
                    Column(Modifier.padding(12.dp)) {
                        Text(
                            "${a.optString("actor")} · ${a.optString("action")}",
                            fontWeight = FontWeight.SemiBold, fontSize = 13.sp,
                        )
                        if (a.optString("detail").isNotBlank())
                            Text(a.optString("detail"), fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                            Text("#${a.optInt("task_id")} ${a.optString("task_title")}",
                                fontSize = 11.sp, color = MaterialTheme.colorScheme.secondary)
                            Text(fmtDate(a.optString("at").take(10)),
                                fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
        }
    }
}

/** "More" section: remaining task destinations. */
@Composable
fun TaskMoreScreen(
    isAdminOrManager: Boolean,
    onNavigate: (String) -> Unit,
    onOpenWeb: (String) -> Unit,
) {
    val items = buildList {
        add(Triple("🔔", "Subscribed Tasks", "route:subscribed"))
        add(Triple("📋", "All Tasks", "route:all"))
        add(Triple("📈", "Activities", "route:activities"))
        add(Triple("🗑️", "Deleted Tasks", "web:/clients/tasks/deleted/"))
        add(Triple("🔗", "Links", "web:/clients/links/"))
        if (isAdminOrManager) add(Triple("⚙️", "Settings", "web:/clients/tasks/settings/"))
    }
    LazyColumn(
        Modifier.fillMaxSize(),
        verticalArrangement = Arrangement.spacedBy(8.dp),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
    ) {
        items(items) { (icon, label, action) ->
            Card(
                Modifier.fillMaxWidth().clickable {
                    if (action.startsWith("route:")) onNavigate(action.removePrefix("route:"))
                    else onOpenWeb(action.removePrefix("web:"))
                },
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
            ) {
                Row(Modifier.fillMaxWidth().padding(14.dp)) {
                    Text(icon, fontSize = 18.sp, modifier = Modifier.padding(end = 12.dp))
                    Text(label, fontSize = 15.sp, fontWeight = FontWeight.Medium, modifier = Modifier.padding(end = 8.dp))
                    if (action.startsWith("web:")) {
                        Text("web", fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        }
    }
}
