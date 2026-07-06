package bo.kadlaginvestment.crm.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.ui.draw.clip
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
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
import org.json.JSONArray
import org.json.JSONObject

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun AssignTaskScreen(
    onBack: () -> Unit,
    onSessionExpired: () -> Unit,
    onCreated: () -> Unit,
) {
    BackHandler(onBack = onBack)
    val scope = rememberCoroutineScope()

    var meta by remember { mutableStateOf<JSONObject?>(null) }
    var metaError by remember { mutableStateOf<String?>(null) }

    var title by remember { mutableStateOf("") }
    var description by remember { mutableStateOf("") }
    val checklist = remember { mutableStateListOf<String>() }
    var assignee by remember { mutableStateOf<Pair<Int, String>?>(null) }
    val subscribers = remember { mutableStateListOf<Pair<Int, String>>() }
    var category by remember { mutableStateOf<Pair<Int, String>?>(null) }
    var priority by remember { mutableStateOf("medium") }
    var dueDate by remember { mutableStateOf("") }
    var dueTime by remember { mutableStateOf("") }
    var repeat by remember { mutableStateOf("") }

    var submitting by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(Unit) {
        when (val r = ApiClient.get("/clients/api/app/tasks/meta/")) {
            is ApiClient.Result.Ok -> meta = r.json
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> metaError = r.message
        }
    }

    if (metaError != null) { ErrorBox(metaError!!) { metaError = null }; return }
    val m = meta ?: run { LoadingBox(); return }
    val employees = m.optJSONArray("employees")
    val users = m.optJSONArray("users")
    val categories = m.optJSONArray("categories")

    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("← Back", color = MaterialTheme.colorScheme.secondary, fontWeight = FontWeight.SemiBold,
                modifier = Modifier.clickable(onClick = onBack))
            Spacer(Modifier.width(12.dp))
            Text("Assign Task", fontSize = 20.sp, fontWeight = FontWeight.Bold)
        }

        Column(
            Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            OutlinedTextField(title, { title = it }, Modifier.fillMaxWidth(),
                label = { Text("Task title") }, singleLine = true)
            OutlinedTextField(description, { description = it }, Modifier.fillMaxWidth(),
                label = { Text("Description") })

            // Checklist
            Text("Checklist", fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            checklist.forEachIndexed { i, item ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    OutlinedTextField(item, { checklist[i] = it }, Modifier.weight(1f),
                        placeholder = { Text("Item ${i + 1}") }, singleLine = true)
                    TextButton(onClick = { checklist.removeAt(i) }) { Text("✕") }
                }
            }
            TextButton(onClick = { checklist.add("") }) { Text("+ Add checklist item") }

            PickerField("Assign to", assignee?.second ?: "Unassigned", employees) { id, name ->
                assignee = id to name
            }

            // Subscribers (multi-select chips)
            Text("Subscribers (In Loop)", fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                for (i in 0 until (users?.length() ?: 0)) {
                    val u = users!!.getJSONObject(i)
                    val pair = u.getInt("id") to u.optString("name")
                    val sel = subscribers.any { it.first == pair.first }
                    Chip(u.optString("name"), sel) {
                        if (sel) subscribers.removeAll { it.first == pair.first } else subscribers.add(pair)
                    }
                }
            }

            PickerField("Category", category?.second ?: "None", categories) { id, name ->
                category = id to name
            }

            Text("Priority", fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                listOf("low" to "Low", "medium" to "Medium", "high" to "High", "critical" to "Critical")
                    .forEach { (v, l) -> Chip(l, priority == v) { priority = v } }
            }

            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(dueDate, { dueDate = it }, Modifier.weight(1f),
                    label = { Text("Due date (YYYY-MM-DD)") }, singleLine = true)
                OutlinedTextField(dueTime, { dueTime = it }, Modifier.weight(1f),
                    label = { Text("Time (HH:MM)") }, singleLine = true)
            }

            // Repeat toggle
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text("Repeat task", fontSize = 14.sp, modifier = Modifier.weight(1f))
                Switch(checked = repeat.isNotBlank(), onCheckedChange = { repeat = if (it) "daily" else "" })
            }
            if (repeat.isNotBlank()) {
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    listOf("daily" to "Daily", "weekly" to "Weekly", "monthly" to "Monthly")
                        .forEach { (v, l) -> Chip(l, repeat == v) { repeat = v } }
                }
            }

            Text("Attachments & voice notes can be added from the task on web.",
                fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)

            error?.let { Text(it, color = StatusRed, fontSize = 13.sp) }
            Spacer(Modifier.height(8.dp))
        }

        // Fixed bottom Assign button
        Button(
            onClick = {
                submitting = true; error = null
                scope.launch {
                    val body = JSONObject()
                        .put("title", title)
                        .put("description", description)
                        .put("priority", priority)
                        .put("due_date", dueDate)
                        .put("due_time", dueTime)
                        .put("repeat_rule", repeat)
                    assignee?.let { body.put("assigned_to", it.first) }
                    category?.let { body.put("category_id", it.first) }
                    body.put("checklist", JSONArray(checklist.filter { it.isNotBlank() }))
                    body.put("subscribers", JSONArray(subscribers.map { it.first }))
                    when (val r = ApiClient.post("/clients/api/app/tasks/create/", body)) {
                        is ApiClient.Result.Ok -> onCreated()
                        is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                        is ApiClient.Result.Error -> error = r.message
                    }
                    submitting = false
                }
            },
            enabled = !submitting && title.isNotBlank(),
            modifier = Modifier.fillMaxWidth().padding(16.dp).height(52.dp),
        ) {
            Text(if (submitting) "Assigning…" else "Assign Task", fontSize = 16.sp)
        }
    }
}

/** Read-only field that opens a dropdown of {id,name} options from a JSONArray. */
@Composable
private fun PickerField(
    label: String,
    current: String,
    options: JSONArray?,
    onPick: (Int, String) -> Unit,
) {
    var open by remember { mutableStateOf(false) }
    Column {
        Text(label, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Box {
            Row(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp))
                    .background(MaterialTheme.colorScheme.surfaceVariant)
                    .clickable { open = true }.padding(14.dp),
            ) { Text(current) }
            DropdownMenu(open, onDismissRequest = { open = false }) {
                for (i in 0 until (options?.length() ?: 0)) {
                    val o = options!!.getJSONObject(i)
                    DropdownMenuItem(text = { Text(o.optString("name")) },
                        onClick = { onPick(o.getInt("id"), o.optString("name")); open = false })
                }
            }
        }
    }
}
