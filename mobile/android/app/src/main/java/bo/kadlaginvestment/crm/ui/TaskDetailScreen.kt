package bo.kadlaginvestment.crm.ui

import android.content.Intent
import android.net.Uri
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
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
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import bo.kadlaginvestment.crm.net.ApiClient
import kotlinx.coroutines.launch
import org.json.JSONObject

@Composable
fun TaskDetailScreen(
    taskId: Int,
    reloadSignal: Int,
    onBack: () -> Unit,
    onSessionExpired: () -> Unit,
    onOpenWeb: (String) -> Unit,
    onChanged: () -> Unit,
    onEdit: (JSONObject) -> Unit,
) {
    BackHandler(onBack = onBack)
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    var task by remember { mutableStateOf<JSONObject?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var reloadKey by remember { mutableIntStateOf(0) }
    var newComment by remember { mutableStateOf("") }
    var newChecklist by remember { mutableStateOf("") }
    var showComment by remember { mutableStateOf(false) }
    var confirmDelete by remember { mutableStateOf(false) }
    var statusMenu by remember { mutableStateOf(false) }

    LaunchedEffect(taskId, reloadKey, reloadSignal) {
        when (val r = ApiClient.get("/clients/api/app/tasks/$taskId/")) {
            is ApiClient.Result.Ok -> task = r.json
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
    }

    fun act(body: JSONObject, then: () -> Unit = { reloadKey++; onChanged() }) {
        scope.launch {
            when (val r = ApiClient.post("/clients/api/app/tasks/$taskId/action/", body)) {
                is ApiClient.Result.Ok -> then()
                is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                is ApiClient.Result.Error -> error = r.message
            }
        }
    }

    // Attach a file/photo from the phone: system picker → multipart upload to
    // the same endpoint the web uses (session cookie + CSRF).
    var uploading by remember { mutableStateOf(false) }
    val pickFile = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        uploading = true
        Thread {
            try {
                val cr = context.contentResolver
                var name = "attachment"
                cr.query(uri, null, null, null, null)?.use { cur ->
                    val i = cur.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME)
                    if (cur.moveToFirst() && i >= 0) name = cur.getString(i) ?: name
                }
                cr.openInputStream(uri)?.use { stream ->
                    bo.kadlaginvestment.crm.BackendClient.postMultipart(
                        "/clients/tasks/$taskId/attachment/upload/",
                        "attachments", name, cr.getType(uri) ?: "", stream,
                    )
                }
            } catch (_: Exception) {
            } finally {
                uploading = false
                reloadKey++
            }
        }.start()
    }

    if (error != null) { ErrorBox(error!!) { error = null; reloadKey++ }; return }
    val t = task ?: run { LoadingBox(); return }
    val canEdit = t.optBoolean("can_edit")
    val status = t.optString("status")
    val done = status == "completed"

    if (confirmDelete) {
        AlertDialog(
            onDismissRequest = { confirmDelete = false },
            title = { Text("Delete task?") },
            text = { Text("This moves “${t.optString("title")}” to the recycle bin.") },
            confirmButton = {
                TextButton(onClick = {
                    confirmDelete = false
                    act(JSONObject().put("action", "delete")) { onChanged(); onBack() }
                }) { Text("Delete", color = StatusRed) }
            },
            dismissButton = { TextButton(onClick = { confirmDelete = false }) { Text("Cancel") } },
        )
    }

    if (showComment) {
        AlertDialog(
            onDismissRequest = { showComment = false },
            title = { Text("Add comment") },
            text = {
                OutlinedTextField(newComment, { newComment = it }, Modifier.fillMaxWidth(),
                    label = { Text("Comment") })
            },
            confirmButton = {
                TextButton(onClick = {
                    if (newComment.isNotBlank()) {
                        act(JSONObject().put("action", "comment").put("body", newComment))
                        newComment = ""
                    }
                    showComment = false
                }) { Text("Post") }
            },
            dismissButton = { TextButton(onClick = { showComment = false }) { Text("Cancel") } },
        )
    }

    Column(Modifier.fillMaxSize()) {
        // Top bar
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("← Back", color = MaterialTheme.colorScheme.secondary, fontWeight = FontWeight.SemiBold,
                modifier = Modifier.clickable(onClick = onBack))
            Spacer(Modifier.weight(1f))
            // Status pill doubles as the status picker for anyone who can edit.
            Box {
                Box(Modifier.clickable(enabled = canEdit) { statusMenu = true }) {
                    Pill(t.optString("status_label") + if (canEdit) " ▾" else "", statusColor(status))
                }
                DropdownMenu(expanded = statusMenu, onDismissRequest = { statusMenu = false }) {
                    listOf(
                        "pending" to "Pending",
                        "in_progress" to "In Progress",
                        "completed" to "Completed",
                        "cancelled" to "Cancelled",
                    ).forEach { (value, label) ->
                        DropdownMenuItem(
                            text = { Text(label, color = statusColor(value)) },
                            onClick = {
                                statusMenu = false
                                if (value != status) {
                                    act(JSONObject().put("action", "status").put("status", value))
                                }
                            },
                        )
                    }
                }
            }
        }

        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 16.dp)) {
            Text("#${t.optInt("id")}", fontSize = 12.sp, fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(t.optString("title"), fontSize = 20.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Pill(t.optString("priority_label"), priorityColor(t.optString("priority")))
                if (t.optString("category").isNotBlank()) Pill(t.optString("category"), MaterialTheme.colorScheme.primary)
            }
            Spacer(Modifier.height(10.dp))
            InfoRow("Assigned", t.optString("assignee").ifBlank { "—" })
            if (t.optString("assignee").isNotBlank() && !done) {
                InfoRow(
                    "Seen",
                    if (t.optBoolean("acknowledged")) "✓ Acknowledged" else "Awaiting acknowledgement",
                    if (t.optBoolean("acknowledged")) StatusGreen else StatusAmber,
                )
            }
            InfoRow("Created by", t.optString("created_by").ifBlank { "—" })
            // Linked client — tap to dial straight from the task.
            if (t.optString("client").isNotBlank()) {
                val clientPhone = t.optString("client_phone")
                Box(Modifier.clickable(enabled = clientPhone.isNotBlank()) {
                    context.startActivity(
                        Intent(Intent.ACTION_DIAL, Uri.parse("tel:$clientPhone"))
                    )
                }) {
                    InfoRow(
                        "Client",
                        t.optString("client") + if (clientPhone.isNotBlank()) "  📞" else "",
                    )
                }
            }
            // Due row is always visible and, for editors, tap-to-change —
            // date picker then time picker, saved via action=due.
            fun pickDue() {
                val cal = java.util.Calendar.getInstance()
                android.app.DatePickerDialog(context, { _, y, mo, d ->
                    val date = String.format(java.util.Locale.US, "%04d-%02d-%02d", y, mo + 1, d)
                    android.app.TimePickerDialog(context, { _, h, mi ->
                        act(
                            JSONObject().put("action", "due").put("due_date", date)
                                .put("due_time", String.format(java.util.Locale.US, "%02d:%02d", h, mi))
                        )
                    }, 10, 0, false).show()
                }, cal.get(java.util.Calendar.YEAR), cal.get(java.util.Calendar.MONTH),
                    cal.get(java.util.Calendar.DAY_OF_MONTH)).show()
            }
            Box(Modifier.clickable(enabled = canEdit) { pickDue() }) {
                val due = t.optString("due_date")
                InfoRow(
                    "Due",
                    when {
                        due.isNotBlank() -> fmtDate(due) + " " + fmt12h(t.optString("due_time")) +
                            (if (canEdit) "  ✎" else "")
                        canEdit -> "Set due date…"
                        else -> "—"
                    },
                    if (status == "overdue") StatusRed else null,
                )
            }

            if (t.optString("description").isNotBlank()) {
                Spacer(Modifier.height(10.dp))
                Text(t.optString("description"), fontSize = 14.sp)
            }

            // Checklist
            val checklist = t.optJSONArray("checklist")
            if ((checklist?.length() ?: 0) > 0 || canEdit) {
                SectionTitle("Checklist · ${t.optInt("checklist_percent")}%")
                for (i in 0 until (checklist?.length() ?: 0)) {
                    val item = checklist!!.getJSONObject(i)
                    val d = item.optBoolean("done")
                    Row(
                        Modifier.fillMaxWidth().padding(vertical = 3.dp)
                            .clickable(enabled = canEdit) {
                                act(JSONObject().put("action", "checklist_toggle").put("item_id", item.optInt("id")))
                            },
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Icon(
                            if (d) Icons.Filled.CheckCircle else Icons.Filled.Check,
                            null, tint = if (d) StatusGreen else MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.size(20.dp),
                        )
                        Spacer(Modifier.size(8.dp))
                        Text(item.optString("title"), fontSize = 14.sp,
                            color = if (d) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface)
                    }
                }
                if (canEdit) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        OutlinedTextField(newChecklist, { newChecklist = it }, Modifier.weight(1f),
                            placeholder = { Text("Add item") }, singleLine = true)
                        TextButton(onClick = {
                            if (newChecklist.isNotBlank()) {
                                act(JSONObject().put("action", "add_checklist").put("title", newChecklist))
                                newChecklist = ""
                            }
                        }) { Text("Add") }
                    }
                }
            }

            // Attachments — list + add from the phone (file or photo).
            val atts = t.optJSONArray("attachments")
            if ((atts?.length() ?: 0) > 0 || canEdit) {
                SectionTitle("Attachments")
                if (canEdit) {
                    OutlinedButton(
                        onClick = { if (!uploading) pickFile.launch("*/*") },
                        modifier = Modifier.fillMaxWidth(),
                    ) { Text(if (uploading) "Uploading…" else "📎 Attach file / photo", fontSize = 13.sp) }
                }
            }
            if ((atts?.length() ?: 0) > 0) {
                for (i in 0 until atts!!.length()) {
                    val a = atts.getJSONObject(i)
                    Card(
                        Modifier.fillMaxWidth().padding(vertical = 3.dp).clickable { onOpenWeb(a.optString("url")) },
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                    ) {
                        Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                            Text(if (a.optBoolean("is_voice")) "🎙️" else "📎", fontSize = 16.sp)
                            Spacer(Modifier.size(8.dp))
                            Text(a.optString("filename"), fontSize = 13.sp, modifier = Modifier.weight(1f))
                            Text("Open", color = MaterialTheme.colorScheme.secondary, fontSize = 12.sp)
                        }
                    }
                }
            }

            // Subscribers
            val subs = t.optJSONArray("subscribers")
            if ((subs?.length() ?: 0) > 0) {
                SectionTitle("In Loop")
                Text((0 until subs!!.length()).joinToString(", ") { subs.getJSONObject(it).optString("name") },
                    fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }

            // Comments
            SectionTitle("Comments")
            val comments = t.optJSONArray("comments")
            if ((comments?.length() ?: 0) == 0) {
                Text("No comments yet.", fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            for (i in 0 until (comments?.length() ?: 0)) {
                val c = comments!!.getJSONObject(i)
                Column(Modifier.padding(vertical = 4.dp)) {
                    Text(c.optString("author"), fontWeight = FontWeight.SemiBold, fontSize = 13.sp)
                    Text(fmtDate(c.optString("at").take(10)), fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(c.optString("body"), fontSize = 14.sp)
                }
            }

            // Activity
            SectionTitle("Activity")
            val acts = t.optJSONArray("activities")
            for (i in 0 until (acts?.length() ?: 0)) {
                val a = acts!!.getJSONObject(i)
                Row(Modifier.padding(vertical = 3.dp)) {
                    Text("• ", color = MaterialTheme.colorScheme.primary)
                    Text(
                        "${a.optString("actor")} · ${a.optString("action")}" +
                            (a.optString("detail").takeIf { it.isNotBlank() }?.let { " — $it" } ?: ""),
                        fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            Spacer(Modifier.height(16.dp))
        }

        // Acknowledge: the assignee's "I have seen this" — stops the 4-hour
        // re-rings on high/critical tasks and shows up for the assigner.
        if (t.optBoolean("is_assignee") && !t.optBoolean("acknowledged") && !done) {
            Button(
                onClick = { act(JSONObject().put("action", "acknowledge")) },
                modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp),
                colors = androidx.compose.material3.ButtonDefaults.buttonColors(
                    containerColor = StatusAmber,
                    contentColor = androidx.compose.ui.graphics.Color.White,
                ),
            ) { Text("✋ Acknowledge — I've seen this task", fontSize = 14.sp, fontWeight = FontWeight.Bold) }
        }

        // Bottom actions
        Row(
            Modifier.fillMaxWidth().padding(12.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            if (canEdit) {
                Button(
                    onClick = {
                        act(JSONObject().put("action", "status").put("status", if (done) "pending" else "completed"))
                    },
                    modifier = Modifier.weight(1f),
                ) { Text(if (done) "Reopen" else "Complete", fontSize = 13.sp) }
            }
            OutlinedButton(onClick = { showComment = true }, modifier = Modifier.weight(1f)) {
                Text("Comment", fontSize = 13.sp)
            }
            if (canEdit) {
                OutlinedButton(onClick = { onEdit(t) }, modifier = Modifier.weight(1f)) {
                    Text("Edit", fontSize = 13.sp)
                }
                OutlinedButton(onClick = { confirmDelete = true }) {
                    Text("🗑", color = StatusRed)
                }
            }
        }
    }
}

@Composable
private fun InfoRow(label: String, value: String, valueColor: androidx.compose.ui.graphics.Color? = null) {
    Row(Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
        Text(label, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.width(96.dp))
        Text(value, fontSize = 13.sp, fontWeight = FontWeight.Medium, color = valueColor ?: MaterialTheme.colorScheme.onSurface)
    }
}
