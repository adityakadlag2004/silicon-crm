package bo.kadlaginvestment.crm.ui

import android.app.DatePickerDialog
import android.app.TimePickerDialog
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
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
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import bo.kadlaginvestment.crm.net.ApiClient
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import java.util.Calendar

private data class Cat(val id: Int, val name: String)

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun AssignTaskScreen(
    onBack: () -> Unit,
    onSessionExpired: () -> Unit,
    onCreated: () -> Unit,
) {
    BackHandler(onBack = onBack)
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    var meta by remember { mutableStateOf<JSONObject?>(null) }
    var metaError by remember { mutableStateOf<String?>(null) }

    var title by remember { mutableStateOf("") }
    var description by remember { mutableStateOf("") }
    val checklist = remember { mutableStateListOf<String>() }
    val assignees = remember { mutableStateListOf<Pair<Int, String>>() }
    val subscribers = remember { mutableStateListOf<Pair<Int, String>>() }
    val categories = remember { mutableStateListOf<Cat>() }
    var category by remember { mutableStateOf<Cat?>(null) }
    var priority by remember { mutableStateOf("medium") }
    var dueDate by remember { mutableStateOf("") }
    var dueTime by remember { mutableStateOf("") }
    var repeat by remember { mutableStateOf("") }

    var showNewCat by remember { mutableStateOf(false) }
    var newCatName by remember { mutableStateOf("") }
    var submitting by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(Unit) {
        when (val r = ApiClient.get("/clients/api/app/tasks/meta/")) {
            is ApiClient.Result.Ok -> {
                meta = r.json
                val cats = r.json.optJSONArray("categories")
                categories.clear()
                for (i in 0 until (cats?.length() ?: 0)) {
                    val c = cats!!.getJSONObject(i)
                    categories.add(Cat(c.getInt("id"), c.optString("name")))
                }
            }
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> metaError = r.message
        }
    }

    if (metaError != null) { ErrorBox(metaError!!) { metaError = null }; return }
    val m = meta ?: run { LoadingBox(); return }
    val employees = m.optJSONArray("employees")
    val users = m.optJSONArray("users")

    // ── date & time pickers ──
    fun pickDate() {
        val cal = Calendar.getInstance()
        DatePickerDialog(context, { _, y, mo, d ->
            dueDate = String.format("%04d-%02d-%02d", y, mo + 1, d)
        }, cal.get(Calendar.YEAR), cal.get(Calendar.MONTH), cal.get(Calendar.DAY_OF_MONTH)).show()
    }
    fun pickTime() {
        val cal = Calendar.getInstance()
        TimePickerDialog(context, { _, h, mi ->
            dueTime = String.format("%02d:%02d", h, mi)
        }, cal.get(Calendar.HOUR_OF_DAY), cal.get(Calendar.MINUTE), true).show()
    }

    if (showNewCat) {
        AlertDialog(
            onDismissRequest = { showNewCat = false },
            title = { Text("New category") },
            text = {
                OutlinedTextField(newCatName, { newCatName = it }, Modifier.fillMaxWidth(),
                    label = { Text("Category name") }, singleLine = true)
            },
            confirmButton = {
                TextButton(onClick = {
                    val name = newCatName.trim()
                    if (name.isNotBlank()) scope.launch {
                        val body = JSONObject().put("name", name)
                        when (val r = ApiClient.post("/clients/api/app/tasks/categories/create/", body)) {
                            is ApiClient.Result.Ok -> {
                                val c = Cat(r.json.optInt("id"), r.json.optString("name"))
                                if (categories.none { it.id == c.id }) categories.add(c)
                                category = c
                                newCatName = ""; showNewCat = false
                            }
                            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                            is ApiClient.Result.Error -> { error = r.message; showNewCat = false }
                        }
                    }
                }) { Text("Create") }
            },
            dismissButton = { TextButton(onClick = { showNewCat = false }) { Text("Cancel") } },
        )
    }

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

            // Assignees (multi-select chips)
            Text("Assign to (one or more)", fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                for (i in 0 until (employees?.length() ?: 0)) {
                    val e = employees!!.getJSONObject(i)
                    val pair = e.getInt("id") to e.optString("name")
                    val sel = assignees.any { it.first == pair.first }
                    Chip(e.optString("name"), sel) {
                        if (sel) assignees.removeAll { it.first == pair.first } else assignees.add(pair)
                    }
                }
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

            // Category + add
            Text("Category", fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Chip("None", category == null) { category = null }
                categories.forEach { c -> Chip(c.name, category?.id == c.id) { category = c } }
                Box(
                    Modifier.clip(RoundedCornerShape(20.dp))
                        .background(MaterialTheme.colorScheme.primary)
                        .clickable { showNewCat = true }
                        .padding(horizontal = 14.dp, vertical = 7.dp),
                ) { Text("+ Add", color = androidx.compose.ui.graphics.Color.White, fontSize = 13.sp, fontWeight = FontWeight.SemiBold) }
            }

            Text("Priority", fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                listOf("low" to "Low", "medium" to "Medium", "high" to "High", "critical" to "Critical")
                    .forEach { (v, l) -> Chip(l, priority == v) { priority = v } }
            }

            // Due date & time via native pickers
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                PickerBox("Due date", dueDate.ifBlank { "Pick date" }, Modifier.weight(1f)) { pickDate() }
                PickerBox("Due time", dueTime.ifBlank { "Pick time" }, Modifier.weight(1f)) { pickTime() }
            }

            // Repeat toggle
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text("Repeat task", fontSize = 14.sp, modifier = Modifier.weight(1f))
                Switch(checked = repeat.isNotBlank(), onCheckedChange = { repeat = if (it) "daily" else "" })
            }
            if (repeat.isNotBlank()) {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    listOf("daily" to "Daily", "weekly" to "Weekly", "monthly" to "Monthly")
                        .forEach { (v, l) -> Chip(l, repeat == v) { repeat = v } }
                }
            }

            Text("Attachments & voice notes can be added from the task on web.",
                fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            error?.let { Text(it, color = StatusRed, fontSize = 13.sp) }
            Spacer(Modifier.height(8.dp))
        }

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
                    category?.let { body.put("category_id", it.id) }
                    body.put("assignees", JSONArray(assignees.map { it.first }))
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

@Composable
private fun PickerBox(label: String, value: String, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Column(modifier) {
        Text(label, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Box(
            Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp))
                .background(MaterialTheme.colorScheme.surfaceVariant)
                .clickable(onClick = onClick).padding(14.dp),
        ) { Text(value) }
    }
}
