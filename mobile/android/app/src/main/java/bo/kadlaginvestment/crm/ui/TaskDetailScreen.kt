package bo.kadlaginvestment.crm.ui

import androidx.activity.compose.BackHandler
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import bo.kadlaginvestment.crm.net.ApiClient
import kotlinx.coroutines.launch
import org.json.JSONObject

@Composable
fun TaskDetailScreen(
    taskId: Int,
    onBack: () -> Unit,
    onSessionExpired: () -> Unit,
    onOpenWeb: (String) -> Unit,
    onChanged: () -> Unit,
) {
    BackHandler(onBack = onBack)
    val scope = rememberCoroutineScope()

    var task by remember { mutableStateOf<JSONObject?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var reloadKey by remember { mutableIntStateOf(0) }
    var newComment by remember { mutableStateOf("") }
    var newChecklist by remember { mutableStateOf("") }
    var showComment by remember { mutableStateOf(false) }
    var showEdit by remember { mutableStateOf(false) }
    var confirmDelete by remember { mutableStateOf(false) }

    LaunchedEffect(taskId, reloadKey) {
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

    if (showEdit) TaskEditDialog(t, onDismiss = { showEdit = false }, onSave = { body ->
        act(body); showEdit = false
    })

    Column(Modifier.fillMaxSize()) {
        // Top bar
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("← Back", color = MaterialTheme.colorScheme.secondary, fontWeight = FontWeight.SemiBold,
                modifier = Modifier.clickable(onClick = onBack))
            Spacer(Modifier.weight(1f))
            Pill(t.optString("status_label"), statusColor(status))
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
            InfoRow("Created by", t.optString("created_by").ifBlank { "—" })
            if (t.optString("due_date").isNotBlank())
                InfoRow("Due", fmtDate(t.optString("due_date")) + " " + t.optString("due_time"),
                    if (status == "overdue") StatusRed else null)

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

            // Attachments
            val atts = t.optJSONArray("attachments")
            if ((atts?.length() ?: 0) > 0) {
                SectionTitle("Attachments")
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
                OutlinedButton(onClick = { showEdit = true }, modifier = Modifier.weight(1f)) {
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

/** Edit priority + due date + description. */
@Composable
private fun TaskEditDialog(task: JSONObject, onDismiss: () -> Unit, onSave: (JSONObject) -> Unit) {
    var priority by remember { mutableStateOf(task.optString("priority")) }
    var due by remember { mutableStateOf(task.optString("due_date")) }
    var desc by remember { mutableStateOf(task.optString("description")) }
    val priorities = listOf("low" to "Low", "medium" to "Medium", "high" to "High", "critical" to "Critical")

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Edit task") },
        text = {
            Column {
                Text("Priority", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    priorities.forEach { (v, l) -> Chip(l, priority == v) { priority = v } }
                }
                Spacer(Modifier.height(10.dp))
                OutlinedTextField(due, { due = it }, Modifier.fillMaxWidth(),
                    label = { Text("Due date (YYYY-MM-DD)") }, singleLine = true)
                Spacer(Modifier.height(10.dp))
                OutlinedTextField(desc, { desc = it }, Modifier.fillMaxWidth(),
                    label = { Text("Description") })
            }
        },
        confirmButton = {
            TextButton(onClick = {
                // Fire the changed pieces as separate actions.
                onSave(JSONObject().put("action", "priority").put("priority", priority))
                if (due != task.optString("due_date"))
                    onSave(JSONObject().put("action", "due").put("due_date", due))
                if (desc != task.optString("description"))
                    onSave(JSONObject().put("action", "description").put("description", desc))
            }) { Text("Save") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}
