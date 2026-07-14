package bo.kadlaginvestment.crm.ui

import android.content.Intent
import android.net.Uri
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import bo.kadlaginvestment.crm.TasksActivity
import bo.kadlaginvestment.crm.net.ApiClient
import kotlinx.coroutines.launch
import org.json.JSONObject

/**
 * Today: one agenda combining due/overdue tasks, pending call follow-ups and
 * renewals that have come due — the screen to start the working day from.
 */
@Composable
fun TodayScreen(
    modifier: Modifier = Modifier,
    onBack: () -> Unit,
    onSessionExpired: () -> Unit,
) {
    BackHandler(onBack = onBack)
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var data by remember { mutableStateOf<JSONObject?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var reloadKey by remember { mutableIntStateOf(0) }

    LaunchedEffect(reloadKey) {
        when (val r = ApiClient.get("/clients/api/app/today/")) {
            is ApiClient.Result.Ok -> data = r.json
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
    }

    if (error != null) { ErrorBox(error!!, modifier) { error = null; reloadKey++ }; return }
    val d = data ?: run { LoadingBox(modifier); return }

    val tasks = d.optJSONArray("tasks")
    val followups = d.optJSONArray("followups")
    val renewals = d.optJSONArray("renewals")
    fun dial(phone: String) {
        if (phone.isNotBlank()) context.startActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:$phone")))
    }

    LazyColumn(
        modifier.fillMaxSize().padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 16.dp),
    ) {
        item {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text("← Back", color = MaterialTheme.colorScheme.secondary, fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.clickable(onClick = onBack))
                Spacer(Modifier.padding(6.dp))
                Text("📅 Today", fontSize = 22.sp, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                Text("↻", fontSize = 20.sp, color = MaterialTheme.colorScheme.secondary,
                    modifier = Modifier.clickable { data = null; reloadKey++ }.padding(8.dp))
            }
        }

        // ── Tasks due today / overdue ──
        item { SectionTitle("Tasks (${tasks?.length() ?: 0})") }
        if ((tasks?.length() ?: 0) == 0) item { EmptyLine("No tasks due today. 🎉") }
        for (i in 0 until (tasks?.length() ?: 0)) {
            val t = tasks!!.getJSONObject(i)
            item {
                Card(
                    Modifier.fillMaxWidth().clickable { TasksActivity.open(context, t.optInt("id")) },
                    colors = CardDefaults.cardColors(
                        containerColor = if (t.optString("status") == "overdue")
                            MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surface
                    ),
                ) {
                    Column(Modifier.padding(12.dp)) {
                        Text(t.optString("title"), fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                        Row {
                            Text(
                                (t.optString("due_time").takeIf { it.isNotBlank() }?.let { fmt12h(it) }
                                    ?: fmtDate(t.optString("due_date"))) +
                                    (t.optString("client").takeIf { it.isNotBlank() }?.let { " · $it" } ?: ""),
                                fontSize = 12.sp,
                                color = if (t.optString("status") == "overdue") StatusRed
                                else MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }
        }

        // ── Call follow-ups due by tonight ──
        item { SectionTitle("Call follow-ups (${followups?.length() ?: 0})") }
        if ((followups?.length() ?: 0) == 0) item { EmptyLine("No follow-up calls pending today.") }
        for (i in 0 until (followups?.length() ?: 0)) {
            val f = followups!!.getJSONObject(i)
            item {
                Card(Modifier.fillMaxWidth(), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text(f.optString("client").ifBlank { f.optString("phone") },
                            fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                        Text(
                            f.optString("scheduled_at") +
                                (f.optString("note").takeIf { it.isNotBlank() }?.let { " — $it" } ?: ""),
                            fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedButton(onClick = { dial(f.optString("phone")) }) { Text("📞 Call", fontSize = 12.sp) }
                            OutlinedButton(onClick = {
                                scope.launch {
                                    ApiClient.post(
                                        "/clients/api/app/followups/${f.optInt("id")}/action/",
                                        JSONObject().put("action", "done"),
                                    )
                                    data = null; reloadKey++
                                }
                            }) { Text("✓ Done", fontSize = 12.sp) }
                        }
                    }
                }
            }
        }

        // ── Renewals due ──
        item { SectionTitle("Renewals due (${renewals?.length() ?: 0})") }
        if ((renewals?.length() ?: 0) == 0) item { EmptyLine("No renewals due.") }
        for (i in 0 until (renewals?.length() ?: 0)) {
            val r = renewals!!.getJSONObject(i)
            item {
                Card(
                    Modifier.fillMaxWidth().clickable { dial(r.optString("client_phone")) },
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                ) {
                    Column(Modifier.padding(12.dp)) {
                        Text(r.optString("client"), fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                        val premium = "₹" + String.format(java.util.Locale.US, "%,.0f", r.optDouble("premium", 0.0))
                        Text(
                            "${r.optString("product")} · ${fmtDate(r.optString("renewal_date"))} · $premium" +
                                if (r.optBoolean("overdue")) "  · OVERDUE" else "",
                            fontSize = 12.sp,
                            color = if (r.optBoolean("overdue")) StatusRed else MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        }

        item { Spacer(Modifier.height(12.dp)) }
    }
}

@Composable
private fun EmptyLine(text: String) {
    Text(text, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
}
