package bo.kadlaginvestment.crm.ui

import android.content.Intent
import android.net.Uri
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
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import bo.kadlaginvestment.crm.net.ApiClient
import bo.kadlaginvestment.crm.net.ContactResolver
import kotlinx.coroutines.launch
import org.json.JSONObject

/** CRM client name if present, else the device-saved contact name, else the number. */
private fun displayName(context: android.content.Context, f: JSONObject): String {
    val client = f.optString("client")
    if (client.isNotEmpty()) return client
    val phone = f.optString("phone")
    return ContactResolver.nameFor(context, phone) ?: phone
}

/** Call Follow-ups: today's personal call performance on top, then the
 * pending follow-up list (completed/dismissed items disappear). */
@Composable
fun FollowupsScreen(
    modifier: Modifier = Modifier,
    onSessionExpired: () -> Unit,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var data by remember { mutableStateOf<JSONObject?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var reloadKey by remember { mutableIntStateOf(0) }

    LaunchedEffect(reloadKey) {
        when (val r = ApiClient.get("/clients/api/app/followups/")) {
            is ApiClient.Result.Ok -> data = r.json
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
    }

    fun act(id: Int, action: String) {
        scope.launch {
            when (ApiClient.post("/clients/api/app/followups/$id/action/", JSONObject().put("action", action))) {
                is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                else -> { data = null; reloadKey++ }
            }
        }
    }

    if (error != null) { ErrorBox(error!!, modifier) { error = null; reloadKey++ }; return }
    val d = data ?: run { LoadingBox(modifier); return }

    val stats = d.optJSONObject("stats")
    val pending = d.optJSONArray("pending")
    val pendingRows = (0 until (pending?.length() ?: 0)).map { pending!!.getJSONObject(it) }

    LazyColumn(
        modifier.fillMaxSize().padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 16.dp),
    ) {
        item {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("My Calls Today", fontSize = 22.sp, fontWeight = FontWeight.Bold)
                Text(
                    "↻",
                    fontSize = 20.sp,
                    color = MaterialTheme.colorScheme.secondary,
                    modifier = Modifier
                        .clickable { data = null; reloadKey++ }
                        .padding(8.dp),
                )
            }
        }

        // ── Today's call performance ──
        if (stats != null) {
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    StatBlock("Dialed", "${stats.optInt("calls")}", Modifier.weight(1f))
                    StatBlock(
                        "Talk time",
                        formatMinutes(stats.optDouble("talk_minutes", 0.0)),
                        Modifier.weight(1f),
                    )
                }
            }
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    StatBlock("Connected", "${stats.optInt("connected")}", Modifier.weight(1f), StatusGreen)
                    StatBlock("Serious", "${stats.optInt("serious")}", Modifier.weight(1f), BrandGoldDark, sub = "2½ min+")
                }
            }
        }

        item {
            Text(
                "Pending follow-ups (${pendingRows.size})",
                fontSize = 16.sp, fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(top = 8.dp),
            )
        }

        if (pendingRows.isEmpty()) {
            item {
                Text(
                    "Nothing pending — schedule follow-ups from the popup after calls.",
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    fontSize = 13.sp,
                )
            }
        }

        items(pendingRows) { f ->
            val overdue = f.optBoolean("overdue")
            Card(
                colors = CardDefaults.cardColors(
                    containerColor = if (overdue) MaterialTheme.colorScheme.primaryContainer
                    else MaterialTheme.colorScheme.surface
                ),
                elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
            ) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        Column {
                            Text(
                                displayName(context, f),
                                fontWeight = FontWeight.SemiBold, fontSize = 15.sp,
                            )
                            Text(
                                f.optString("scheduled_at") + if (overdue) "  · DUE" else "",
                                fontSize = 12.sp,
                                color = if (overdue) StatusAmber else MaterialTheme.colorScheme.onSurfaceVariant,
                                fontWeight = if (overdue) FontWeight.Bold else FontWeight.Normal,
                            )
                        }
                        Text(f.optString("phone"), fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    if (f.optString("note").isNotEmpty()) {
                        Text(f.optString("note"), fontSize = 13.sp)
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = {
                            context.startActivity(
                                Intent(Intent.ACTION_DIAL, Uri.parse("tel:" + f.optString("phone")))
                            )
                        }) { Text("📞 Call") }
                        OutlinedButton(onClick = { act(f.getInt("id"), "done") }) { Text("Done") }
                        OutlinedButton(onClick = { act(f.getInt("id"), "snooze") }) { Text("+1h") }
                        OutlinedButton(onClick = { act(f.getInt("id"), "dismiss") }) { Text("✕") }
                    }
                }
            }
        }

        item { Spacer(Modifier.height(12.dp)) }
    }
}

@Composable
private fun StatBlock(
    title: String,
    value: String,
    modifier: Modifier = Modifier,
    accent: Color = Color.Unspecified,
    sub: String? = null,
) {
    Card(
        modifier = modifier,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.padding(vertical = 14.dp, horizontal = 12.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Text(
                value,
                fontSize = 24.sp,
                fontWeight = FontWeight.Bold,
                color = if (accent == Color.Unspecified) MaterialTheme.colorScheme.onSurface else accent,
            )
            Text(title, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (sub != null) {
                Text(sub, fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

private fun formatMinutes(totalMinutes: Double): String {
    val totalSec = (totalMinutes * 60).toInt()
    val h = totalSec / 3600
    val m = (totalSec % 3600) / 60
    val s = totalSec % 60
    return when {
        h > 0 -> "${h}h ${m}m"
        m > 0 -> "${m}m ${s}s"
        else -> "${s}s"
    }
}
