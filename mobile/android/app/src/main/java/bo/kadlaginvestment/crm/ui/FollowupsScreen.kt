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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import bo.kadlaginvestment.crm.net.ApiClient
import kotlinx.coroutines.launch
import org.json.JSONObject

/** Native Call Follow-ups: pending list with Call / Done / Snooze / Dismiss. */
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

    val pending = d.optJSONArray("pending")
    val done = d.optJSONArray("done")
    val pendingRows = (0 until (pending?.length() ?: 0)).map { pending!!.getJSONObject(it) }
    val doneRows = (0 until (done?.length() ?: 0)).map { done!!.getJSONObject(it) }

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
                Text("Call Follow-ups", fontSize = 22.sp, fontWeight = FontWeight.Bold)
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
                                f.optString("client").ifEmpty { f.optString("phone") },
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

        if (doneRows.isNotEmpty()) {
            item { SectionTitle("Recently completed") }
            items(doneRows) { f ->
                Row(
                    Modifier.fillMaxWidth().padding(vertical = 4.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column {
                        Text(
                            f.optString("client").ifEmpty { f.optString("phone") },
                            fontSize = 13.sp, fontWeight = FontWeight.SemiBold,
                        )
                        Text(f.optString("scheduled_at"), fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    StatusPill(f.optString("status"))
                }
            }
        }

        item { Spacer(Modifier.height(12.dp)) }
    }
}
