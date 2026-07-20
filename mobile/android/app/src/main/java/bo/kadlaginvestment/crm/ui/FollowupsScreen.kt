package bo.kadlaginvestment.crm.ui

import android.app.DatePickerDialog
import android.app.TimePickerDialog
import android.content.Intent
import android.net.Uri
import android.provider.ContactsContract
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import java.util.Calendar
import java.util.Locale

/** CRM client name if present, else the device-saved contact name, else the number. */
private fun displayName(context: android.content.Context, f: JSONObject): String {
    val client = f.optString("client")
    if (client.isNotEmpty()) return client
    val phone = f.optString("phone")
    return ContactResolver.nameFor(context, phone) ?: phone
}

/** Native date → time pickers, chained. Hands back an ISO local timestamp
 * ("2026-07-21T15:30") — what the backend's custom_at expects. */
private fun pickDateTime(context: android.content.Context, onPicked: (String) -> Unit) {
    val cal = Calendar.getInstance()
    DatePickerDialog(context, { _, y, mo, d ->
        TimePickerDialog(context, { _, h, mi ->
            onPicked(String.format(Locale.US, "%04d-%02d-%02dT%02d:%02d", y, mo + 1, d, h, mi))
        }, cal.get(Calendar.HOUR_OF_DAY), cal.get(Calendar.MINUTE), false).show()
    }, cal.get(Calendar.YEAR), cal.get(Calendar.MONTH), cal.get(Calendar.DAY_OF_MONTH)).apply {
        datePicker.minDate = System.currentTimeMillis() - 1000
    }.show()
}

/** "2026-07-21T15:30" → "21/07 15:30" for the dialog's confirmation line. */
private fun prettyIso(iso: String): String =
    if (iso.length >= 16) "${iso.substring(8, 10)}/${iso.substring(5, 7)} ${iso.substring(11, 16)}" else iso

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
            is ApiClient.Result.Ok -> {
                data = r.json
                // Keep on-device alarms matched to the server list (arms new
                // follow-ups, drops ones completed on another device/web).
                r.json.optJSONArray("pending")?.let {
                    bo.kadlaginvestment.crm.FollowupAlarmScheduler.syncFromPending(context, it)
                }
            }
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
    }

    var showAdd by remember { mutableStateOf(false) }

    fun act(id: Int, action: String, at: String? = null) {
        scope.launch {
            val body = JSONObject().put("action", action)
            at?.let { body.put("at", it) }
            when (ApiClient.post("/clients/api/app/followups/$id/action/", body)) {
                is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                else -> { data = null; reloadKey++ }
            }
        }
    }

    if (showAdd) {
        AddFollowupDialog(
            onDismiss = { showAdd = false },
            onSave = { phone, note, iso ->
                showAdd = false
                scope.launch {
                    val body = JSONObject()
                        .put("phone", phone).put("note", note).put("custom_at", iso)
                    when (ApiClient.post("/clients/api/calls/followup/", body)) {
                        is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                        else -> { data = null; reloadKey++ }
                    }
                }
            },
        )
    }

    if (error != null) { ErrorBox(error!!, modifier) { error = null; reloadKey++ }; return }
    val d = data ?: run { LoadingBox(modifier); return }

    val stats = d.optJSONObject("stats")
    val pending = d.optJSONArray("pending")
    val pendingRows = (0 until (pending?.length() ?: 0)).map { pending!!.getJSONObject(it) }

    Box(modifier.fillMaxSize()) {
    LazyColumn(
        Modifier.fillMaxSize().padding(horizontal = rdp(16)),
        verticalArrangement = Arrangement.spacedBy(10.dp),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(top = 16.dp, bottom = 88.dp),
    ) {
        item {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("My Calls Today", fontSize = rsp(22), fontWeight = FontWeight.Bold)
                Text(
                    "↻",
                    fontSize = rsp(20),
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
                fontSize = rsp(16), fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(top = 8.dp),
            )
        }

        if (pendingRows.isEmpty()) {
            item {
                Text(
                    "Nothing pending — schedule from the post-call popup, or tap ＋ to add one yourself.",
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    fontSize = rsp(13),
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
                                fontWeight = FontWeight.SemiBold, fontSize = rsp(15),
                            )
                            Text(
                                f.optString("scheduled_at") + if (overdue) "  · DUE" else "",
                                fontSize = rsp(12),
                                color = if (overdue) StatusAmber else MaterialTheme.colorScheme.onSurfaceVariant,
                                fontWeight = if (overdue) FontWeight.Bold else FontWeight.Normal,
                            )
                        }
                        Text(f.optString("phone"), fontSize = rsp(12), color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    if (f.optString("note").isNotEmpty()) {
                        Text(f.optString("note"), fontSize = rsp(13))
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = {
                            context.startActivity(
                                Intent(Intent.ACTION_DIAL, Uri.parse("tel:" + f.optString("phone")))
                            )
                        }) { Text("📞 Call") }
                        OutlinedButton(onClick = { act(f.getInt("id"), "done") }) { Text("Done") }
                        OutlinedButton(onClick = {
                            pickDateTime(context) { iso -> act(f.getInt("id"), "reschedule", iso) }
                        }) { Text("🕑 Reschedule") }
                        OutlinedButton(onClick = { act(f.getInt("id"), "dismiss") }) { Text("✕") }
                    }
                }
            }
        }

        item { Spacer(Modifier.height(12.dp)) }
    }

        ExtendedFloatingActionButton(
            onClick = { showAdd = true },
            modifier = Modifier.align(Alignment.BottomEnd).padding(16.dp),
        ) { Text("＋  Follow-up") }
    }
}

/** Manual follow-up: a number and a moment, no call required. */
@Composable
private fun AddFollowupDialog(
    onDismiss: () -> Unit,
    onSave: (phone: String, note: String, iso: String) -> Unit,
) {
    val context = LocalContext.current
    var phone by remember { mutableStateOf("") }
    var pickedName by remember { mutableStateOf("") }
    var note by remember { mutableStateOf("") }
    var iso by remember { mutableStateOf("") }

    // ACTION_PICK on the Phone table: the user picks one *number*, so contacts
    // with several numbers resolve themselves, and the returned row is readable
    // without READ_CONTACTS (the picker grants access to just that row).
    val pickContact = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        val uri = result.data?.data ?: return@rememberLauncherForActivityResult
        try {
            context.contentResolver.query(
                uri,
                arrayOf(
                    ContactsContract.CommonDataKinds.Phone.NUMBER,
                    ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
                ),
                null, null, null,
            )?.use { c ->
                if (c.moveToFirst()) {
                    phone = c.getString(0).orEmpty().filterNot { it.isWhitespace() }
                    pickedName = c.getString(1).orEmpty()
                }
            }
        } catch (_: Exception) {
            // provider hiccup → the number can still be typed in by hand
        }
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("New follow-up") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedButton(
                    onClick = {
                        pickContact.launch(
                            Intent(
                                Intent.ACTION_PICK,
                                ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
                            )
                        )
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) { Text(if (pickedName.isEmpty()) "👤 Pick from contacts" else "👤 $pickedName") }
                OutlinedTextField(
                    phone, { phone = it; pickedName = "" },
                    label = { Text("Phone number") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    note, { note = it },
                    label = { Text("Note (optional)") },
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedButton(
                    onClick = { pickDateTime(context) { iso = it } },
                    modifier = Modifier.fillMaxWidth(),
                ) { Text(if (iso.isEmpty()) "Pick date & time" else "🕑 ${prettyIso(iso)}") }
            }
        },
        confirmButton = {
            TextButton(
                onClick = { onSave(phone.trim(), note.trim(), iso) },
                enabled = phone.isNotBlank() && iso.isNotEmpty(),
            ) { Text("Save") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
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
                fontSize = rsp(24),
                fontWeight = FontWeight.Bold,
                color = if (accent == Color.Unspecified) MaterialTheme.colorScheme.onSurface else accent,
            )
            Text(title, fontSize = rsp(12), color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (sub != null) {
                Text(sub, fontSize = rsp(10), color = MaterialTheme.colorScheme.onSurfaceVariant)
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
