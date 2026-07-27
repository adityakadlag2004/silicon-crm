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
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
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
import androidx.compose.material3.Surface
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

/** Server caps uploads too; this just avoids wasting the user's data first. */
private const val MAX_ATTACHMENT_BYTES = 25L * 1024 * 1024

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
            // Task actions are idempotent, so they can ride the offline outbox.
            when (val r = ApiClient.post(
                "/clients/api/app/tasks/$taskId/action/", body, offlineQueue = context,
            )) {
                is ApiClient.Result.Ok -> {
                    if (r.json.optBoolean("queued")) {
                        AppMessage.show("No internet — saved, will sync automatically")
                    }
                    then()
                }
                is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                // A failed comment must not blow away the task you were reading.
                is ApiClient.Result.Error -> AppMessage.show("Couldn't save: ${r.message}")
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
            var ok = false
            var why = ""
            try {
                val cr = context.contentResolver
                var name = "attachment"
                var size = -1L
                cr.query(uri, null, null, null, null)?.use { cur ->
                    val i = cur.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME)
                    val s = cur.getColumnIndex(android.provider.OpenableColumns.SIZE)
                    if (cur.moveToFirst()) {
                        if (i >= 0) name = cur.getString(i) ?: name
                        if (s >= 0 && !cur.isNull(s)) size = cur.getLong(s)
                    }
                }
                // Guard before spending the user's data on a doomed upload.
                if (size > MAX_ATTACHMENT_BYTES) {
                    why = "File is too large (max ${MAX_ATTACHMENT_BYTES / (1024 * 1024)} MB)"
                } else {
                    cr.openInputStream(uri)?.use { stream ->
                        val body = bo.kadlaginvestment.crm.BackendClient.postMultipart(
                            "/clients/tasks/$taskId/attachment/upload/",
                            "attachments", name, cr.getType(uri) ?: "", stream,
                        )
                        ok = body != null
                        if (!ok) why = "Upload failed — check your connection"
                    } ?: run { why = "Could not read that file" }
                }
            } catch (e: Exception) {
                why = e.message ?: "Upload failed"
            } finally {
                uploading = false
                // A silent catch used to make a failed upload look identical to
                // a successful one: the list just reloaded with nothing new.
                AppMessage.show(if (ok) "Attachment uploaded" else why.ifEmpty { "Upload failed" })
                reloadKey++
            }
        }.start()
    }

    // This screen renders outside the Scaffold, so it must supply its own themed
    // Surface. Without one, LocalContentColor defaults to black and every Text
    // with no explicit colour (title, description, comments) is invisible in
    // dark mode. Each state below sits on that surface.
    if (error != null) {
        Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
            ErrorBox(error!!) { error = null; reloadKey++ }
        }
        return
    }
    val t = task ?: run {
        Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) { LoadingBox() }
        return
    }
    val canEdit = t.optBoolean("can_edit")
    // Deleting is the assigner's call alone — assignees only move status/comment.
    val canDelete = t.optBoolean("can_delete")
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

    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
    // systemBarsPadding keeps the top bar below the status bar and — the
    // reported bug — the bottom action buttons above the device navigation bar
    // (targetSdk 36 draws edge-to-edge; the Scaffold that insets the other task
    // screens is bypassed on this detail route).
    Column(Modifier.fillMaxSize().systemBarsPadding()) {
        // Top bar
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            androidx.compose.material3.IconButton(onClick = onBack) {
                Icon(
                    Icons.AutoMirrored.Filled.ArrowBack,
                    contentDescription = "Back",
                )
            }
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

        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = rdp(16))) {
            Text("#${t.optInt("id")}", fontSize = rsp(12), fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(t.optString("title"), fontSize = rsp(20), fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Pill(t.optString("priority_label"), priorityColor(t.optString("priority")))
                if (t.optString("category").isNotBlank()) Pill(t.optString("category"), MaterialTheme.colorScheme.primary)
            }
            Spacer(Modifier.height(10.dp))
            InfoRow("Assigned", t.optString("assignee_label").ifBlank { t.optString("assignee").ifBlank { "—" } })
            // Acknowledgement, name by name — with several assignees you need to
            // see who has actually looked at it, not one combined yes/no.
            val roster = t.optJSONArray("ack_roster")
            if ((roster?.length() ?: 0) > 1) {
                Spacer(Modifier.height(4.dp))
                Text("Seen", fontSize = rsp(13), color = MaterialTheme.colorScheme.onSurfaceVariant)
                for (i in 0 until roster!!.length()) {
                    val r = roster.optJSONObject(i) ?: continue
                    val seen = r.optBoolean("acknowledged")
                    Text(
                        (if (seen) "✓ " else "○ ") + r.optString("name") + "  ·  " + r.optString("status"),
                        fontSize = rsp(13),
                        color = if (seen) StatusGreen else StatusAmber,
                        modifier = Modifier.padding(start = 8.dp, top = 2.dp),
                    )
                }
            } else if (t.optString("assignee").isNotBlank() && !done) {
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
                // Seeded with the task's CURRENT due date and time. The time
                // picker used to open at a hardcoded 10:00, so nudging a date
                // silently reset every deadline to mid-morning.
                val current = t.optString("due_date").takeIf { it.isNotBlank() }
                    ?.let { it + "T" + t.optString("due_time").ifBlank { "10:00" } } ?: ""
                pickDateTime(context, startIso = current) { iso ->
                    act(
                        JSONObject().put("action", "due")
                            .put("due_date", iso.take(10))
                            .put("due_time", iso.substring(11))
                    )
                }
            }
            Box(Modifier.clickable(enabled = canEdit) { pickDue() }) {
                val due = t.optString("due_date")
                InfoRow(
                    "Due",
                    when {
                        due.isNotBlank() -> fmtDate(due) + " " + fmt12h(t.optString("due_time")) +
                            (if (t.optBoolean("late")) "  · LATE" else "") +
                            (if (canEdit) "  ✎" else "")
                        canEdit -> "Set due date…"
                        else -> "—"
                    },
                    if (status == "overdue" || t.optBoolean("late")) StatusRed else null,
                )
            }

            if (t.optString("description").isNotBlank()) {
                Spacer(Modifier.height(10.dp))
                Text(t.optString("description"), fontSize = rsp(14))
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
                        Text(item.optString("title"), fontSize = rsp(14),
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
                    ) { Text(if (uploading) "Uploading…" else "📎 Attach file / photo", fontSize = rsp(13)) }
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
                            Text(if (a.optBoolean("is_voice")) "🎙️" else "📎", fontSize = rsp(16))
                            Spacer(Modifier.size(8.dp))
                            Text(a.optString("filename"), fontSize = rsp(13), modifier = Modifier.weight(1f))
                            Text("Open", color = MaterialTheme.colorScheme.secondary, fontSize = rsp(12))
                        }
                    }
                }
            }

            // Subscribers
            val subs = t.optJSONArray("subscribers")
            if ((subs?.length() ?: 0) > 0) {
                SectionTitle("In Loop")
                Text((0 until subs!!.length()).joinToString(", ") { subs.getJSONObject(it).optString("name") },
                    fontSize = rsp(13), color = MaterialTheme.colorScheme.onSurfaceVariant)
            }

            // Comments
            SectionTitle("Comments")
            val comments = t.optJSONArray("comments")
            if ((comments?.length() ?: 0) == 0) {
                Text("No comments yet.", fontSize = rsp(13), color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            for (i in 0 until (comments?.length() ?: 0)) {
                val c = comments!!.getJSONObject(i)
                Column(Modifier.padding(vertical = 4.dp)) {
                    Text(c.optString("author"), fontWeight = FontWeight.SemiBold, fontSize = rsp(13))
                    Text(fmtDate(c.optString("at").take(10)), fontSize = rsp(11), color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(c.optString("body"), fontSize = rsp(14))
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
                        fontSize = rsp(12), color = MaterialTheme.colorScheme.onSurfaceVariant,
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
            ) { Text("✋ Acknowledge — I've seen this task", fontSize = rsp(14), fontWeight = FontWeight.Bold) }
        }

        // Bottom actions. FlowRow, not Row: an admin looking at a pending task
        // sees up to five buttons here, which on a 360dp phone left each about
        // 60dp and truncated the labels. Wrapping to a second line is better
        // than five unreadable buttons.
        ActionRow(Modifier.fillMaxWidth().padding(12.dp)) {
            if (canEdit && (status == "pending" || status == "overdue")) {
                OutlinedButton(
                    onClick = {
                        act(JSONObject().put("action", "status").put("status", "in_progress"))
                    },
                    modifier = Modifier.widthIn(min = 96.dp).heightIn(min = TouchTarget),
                ) { Text("▶ Start", fontSize = rsp(13), maxLines = 1) }
            }
            if (canEdit) {
                Button(
                    onClick = {
                        act(JSONObject().put("action", "status").put("status", if (done) "pending" else "completed"))
                    },
                    modifier = Modifier.widthIn(min = 110.dp).heightIn(min = TouchTarget),
                ) { Text(if (done) "Reopen" else "Complete", fontSize = rsp(13), maxLines = 1) }
            }
            OutlinedButton(onClick = { showComment = true },
                modifier = Modifier.widthIn(min = 104.dp).heightIn(min = TouchTarget)) {
                Text("Comment", fontSize = rsp(13), maxLines = 1)
            }
            if (canEdit) {
                OutlinedButton(onClick = { onEdit(t) },
                    modifier = Modifier.widthIn(min = 84.dp).heightIn(min = TouchTarget)) {
                    Text("Edit", fontSize = rsp(13), maxLines = 1)
                }
            }
            if (canDelete) {
                OutlinedButton(onClick = { confirmDelete = true },
                    modifier = Modifier.heightIn(min = TouchTarget)) {
                    Text("🗑", color = StatusRed)
                }
            }
        }
    }
    }
}

@Composable
private fun InfoRow(label: String, value: String, valueColor: androidx.compose.ui.graphics.Color? = null) {
    Row(Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
        // widthIn, not width: at a large system font scale a hard 96dp
        // truncated the label while the value column sat half empty.
        Text(label, fontSize = rsp(13), color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.widthIn(min = 88.dp, max = 132.dp))
        Text(value, fontSize = rsp(13), fontWeight = FontWeight.Medium, color = valueColor ?: MaterialTheme.colorScheme.onSurface)
    }
}
