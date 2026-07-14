package bo.kadlaginvestment.crm.ui

import android.app.DatePickerDialog
import android.app.TimePickerDialog
import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.DateRange
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.Person
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import bo.kadlaginvestment.crm.net.ApiClient
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import java.util.Calendar

private data class Opt(val id: Int, val name: String)

/**
 * Compact half-height bottom sheet for creating or editing a task.
 * People / due / priority / category / in-loop are icon chips that open small
 * dropdowns or pickers, so the form stays short and the Assign button sits
 * mid-screen (not under the system nav bar). `editTask != null` → edit mode.
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun AssignTaskSheet(
    editTask: JSONObject?,
    onDismiss: () -> Unit,
    onDone: () -> Unit,
    onSessionExpired: () -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = false)
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = MaterialTheme.colorScheme.surface,
    ) {
        val scope = rememberCoroutineScope()
        val context = LocalContext.current
        val isEdit = editTask != null

        var meta by remember { mutableStateOf<JSONObject?>(null) }
        var title by remember { mutableStateOf(editTask?.optString("title") ?: "") }
        var description by remember { mutableStateOf(editTask?.optString("description") ?: "") }
        val assignees = remember { mutableStateListOf<Opt>() }
        val subscribers = remember { mutableStateListOf<Opt>() }
        val categories = remember { mutableStateListOf<Opt>() }
        var category by remember { mutableStateOf<Opt?>(null) }
        var priority by remember { mutableStateOf(editTask?.optString("priority")?.ifBlank { "medium" } ?: "medium") }
        var dueDate by remember { mutableStateOf(editTask?.optString("due_date") ?: "") }
        var dueTime by remember { mutableStateOf(editTask?.optString("due_time") ?: "") }
        val checklist = remember { mutableStateListOf<String>() }
        var repeat by remember { mutableStateOf(editTask?.optString("repeat_rule") ?: "") }
        var client by remember { mutableStateOf<Opt?>(null) }
        val templates = remember { mutableStateListOf<JSONObject>() }
        var openMenu by remember { mutableStateOf("") }
        var submitting by remember { mutableStateOf(false) }
        var error by remember { mutableStateOf<String?>(null) }

        // Prefill edit selections that need the JSON.
        LaunchedEffect(editTask) {
            if (editTask != null) {
                editTask.optInt("assignee_id", 0).takeIf { it > 0 }?.let {
                    assignees.add(Opt(it, editTask.optString("assignee")))
                }
                editTask.optInt("client_id", 0).takeIf { it > 0 }?.let {
                    client = Opt(it, editTask.optString("client"))
                }
                editTask.optInt("category_id", 0).takeIf { it > 0 }?.let {
                    category = Opt(it, editTask.optString("category"))
                }
                val subs = editTask.optJSONArray("subscribers")
                for (i in 0 until (subs?.length() ?: 0)) {
                    val s = subs!!.getJSONObject(i)
                    subscribers.add(Opt(s.optInt("user_id"), s.optString("name")))
                }
                val cl = editTask.optJSONArray("checklist")
                for (i in 0 until (cl?.length() ?: 0)) checklist.add(cl!!.getJSONObject(i).optString("title"))
            }
        }

        LaunchedEffect(Unit) {
            when (val r = ApiClient.get("/clients/api/app/tasks/meta/")) {
                is ApiClient.Result.Ok -> {
                    meta = r.json
                    val cats = r.json.optJSONArray("categories")
                    categories.clear()
                    for (i in 0 until (cats?.length() ?: 0)) {
                        val c = cats!!.getJSONObject(i)
                        categories.add(Opt(c.getInt("id"), c.optString("name")))
                    }
                }
                is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                is ApiClient.Result.Error -> error = r.message
            }
            // Templates power the 📋 chip (create mode only).
            if (!isEdit) {
                when (val r = ApiClient.get("/clients/api/app/tasks/templates/")) {
                    is ApiClient.Result.Ok -> {
                        val arr = r.json.optJSONArray("templates")
                        templates.clear()
                        for (i in 0 until (arr?.length() ?: 0)) templates.add(arr!!.getJSONObject(i))
                    }
                    else -> {}
                }
            }
        }

        val employees = meta?.optJSONArray("employees")
        val users = meta?.optJSONArray("users")

        fun pickDate() {
            val cal = Calendar.getInstance()
            DatePickerDialog(context, { _, y, mo, d ->
                dueDate = String.format("%04d-%02d-%02d", y, mo + 1, d)
                // chain a 12-hour time picker
                TimePickerDialog(context, { _, h, mi ->
                    dueTime = String.format("%02d:%02d", h, mi)
                }, cal.get(Calendar.HOUR_OF_DAY), 0, false).show()
            }, cal.get(Calendar.YEAR), cal.get(Calendar.MONTH), cal.get(Calendar.DAY_OF_MONTH)).show()
        }

        fun submit() {
            submitting = true; error = null
            scope.launch {
                val body = JSONObject()
                    .put("title", title).put("description", description)
                    .put("priority", priority).put("due_date", dueDate).put("due_time", dueTime)
                category?.let { body.put("category_id", it.id) } ?: body.put("category_id", JSONObject.NULL)
                client?.let { body.put("client_id", it.id) } ?: body.put("client_id", JSONObject.NULL)
                body.put("subscribers", JSONArray(subscribers.map { it.id }))
                body.put("checklist", JSONArray(checklist.filter { it.isNotBlank() }))
                body.put("repeat_rule", repeat)

                val r = if (isEdit) {
                    body.put("action", "edit")
                    body.put("assigned_to", assignees.firstOrNull()?.id ?: JSONObject.NULL)
                    ApiClient.post("/clients/api/app/tasks/${editTask!!.optInt("id")}/action/", body)
                } else {
                    body.put("assignees", JSONArray(assignees.map { it.id }))
                    ApiClient.post("/clients/api/app/tasks/create/", body)
                }
                when (r) {
                    is ApiClient.Result.Ok -> onDone()
                    is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                    is ApiClient.Result.Error -> error = r.message
                }
                submitting = false
            }
        }

        Column(
            Modifier
                .fillMaxWidth()
                .heightIn(max = 520.dp)
                .verticalScroll(rememberScrollState())
                .padding(start = 16.dp, end = 16.dp, bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(if (isEdit) "Edit Task" else "New Task", fontWeight = FontWeight.Bold, fontSize = 17.sp)

            OutlinedTextField(title, { title = it }, Modifier.fillMaxWidth(),
                label = { Text("Title") }, singleLine = true)
            OutlinedTextField(description, { description = it }, Modifier.fillMaxWidth(),
                label = { Text("Add description") }, maxLines = 3)

            // Compact icon selectors
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                // Assignees
                Box {
                    SelectorChip(Icons.Filled.Person, if (assignees.isEmpty()) "Assign" else "${assignees.size} assignee(s)",
                        active = assignees.isNotEmpty()) { openMenu = if (openMenu == "assignees") "" else "assignees" }
                    MultiMenu(openMenu == "assignees", employees, assignees) { openMenu = "" }
                }
                // Due
                SelectorChip(Icons.Filled.DateRange,
                    if (dueDate.isBlank()) "Due" else fmtDate(dueDate) + (dueTime.takeIf { it.isNotBlank() }?.let { " " + fmt12h(it) } ?: ""),
                    active = dueDate.isNotBlank()) { pickDate() }
                // Priority
                Box {
                    PriorityChip(priority) { openMenu = if (openMenu == "priority") "" else "priority" }
                    DropdownMenu(openMenu == "priority", onDismissRequest = { openMenu = "" }) {
                        listOf("low" to "Low", "medium" to "Medium", "high" to "High", "critical" to "Critical").forEach { (v, l) ->
                            DropdownMenuItem(text = { Text(l) }, onClick = { priority = v; openMenu = "" })
                        }
                    }
                }
                // Category
                Box {
                    SelectorChip(null, category?.name ?: "Category", active = category != null,
                        leadingDot = MaterialTheme.colorScheme.primary) { openMenu = if (openMenu == "category") "" else "category" }
                    DropdownMenu(openMenu == "category", onDismissRequest = { openMenu = "" }) {
                        DropdownMenuItem(text = { Text("None") }, onClick = { category = null; openMenu = "" })
                        categories.forEach { c ->
                            DropdownMenuItem(text = { Text(c.name) }, onClick = { category = c; openMenu = "" })
                        }
                        DropdownMenuItem(
                            text = { Text("+ Add category", color = MaterialTheme.colorScheme.primary) },
                            onClick = { openMenu = "newcat" },
                        )
                    }
                }
                // In Loop
                Box {
                    SelectorChip(Icons.Filled.Notifications, if (subscribers.isEmpty()) "In loop" else "${subscribers.size} in loop",
                        active = subscribers.isNotEmpty()) { openMenu = if (openMenu == "inloop") "" else "inloop" }
                    MultiMenu(openMenu == "inloop", users, subscribers) { openMenu = "" }
                }
                // Client the task is about (optional; searchable)
                SelectorChip(null, client?.name ?: "Client", active = client != null) {
                    openMenu = if (openMenu == "client") "" else "client"
                }
                // Template: prefill from a saved blueprint (create mode)
                if (!isEdit && templates.isNotEmpty()) {
                    Box {
                        SelectorChip(null, "📋 Template", active = false) {
                            openMenu = if (openMenu == "template") "" else "template"
                        }
                        DropdownMenu(openMenu == "template", onDismissRequest = { openMenu = "" }) {
                            templates.forEach { tpl ->
                                DropdownMenuItem(text = { Text(tpl.optString("name")) }, onClick = {
                                    title = tpl.optString("title")
                                    description = tpl.optString("description")
                                    priority = tpl.optString("priority").ifBlank { "medium" }
                                    tpl.optInt("category_id", 0).takeIf { it > 0 }?.let { cid ->
                                        categories.firstOrNull { it.id == cid }?.let { category = it }
                                    }
                                    checklist.clear()
                                    val cl = tpl.optJSONArray("checklist")
                                    for (i in 0 until (cl?.length() ?: 0)) checklist.add(cl!!.optString(i))
                                    openMenu = ""
                                })
                            }
                        }
                    }
                }
            }

            // Inline client search (name / phone / PAN) — appears below the chips.
            if (openMenu == "client") {
                var cq by remember { mutableStateOf("") }
                var results by remember { mutableStateOf(listOf<Opt>()) }
                LaunchedEffect(cq) {
                    if (cq.trim().length < 2) { results = emptyList(); return@LaunchedEffect }
                    kotlinx.coroutines.delay(250)  // debounce typing
                    when (val r = ApiClient.get(
                        "/clients/api/app/clients/?scope=all&q=" + java.net.URLEncoder.encode(cq.trim(), "UTF-8")
                    )) {
                        is ApiClient.Result.Ok -> {
                            val arr = r.json.optJSONArray("results")
                            results = (0 until (arr?.length() ?: 0))
                                .map { arr!!.getJSONObject(it) }
                                .map { Opt(it.optInt("id"), it.optString("name")) }
                        }
                        is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                        is ApiClient.Result.Error -> {}
                    }
                }
                Column {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        OutlinedTextField(cq, { cq = it }, Modifier.weight(1f),
                            placeholder = { Text("Search client…") }, singleLine = true)
                        if (client != null) TextButton(onClick = { client = null; openMenu = "" }) { Text("Clear") }
                        TextButton(onClick = { openMenu = "" }) { Text("✕") }
                    }
                    results.take(6).forEach { c ->
                        Text(
                            "•  ${c.name}", fontSize = 14.sp,
                            modifier = Modifier.fillMaxWidth()
                                .clickable { client = c; openMenu = "" }
                                .padding(vertical = 8.dp, horizontal = 4.dp),
                        )
                    }
                }
            }

            if (openMenu == "newcat") {
                NewCategoryRow(
                    onCancel = { openMenu = "" },
                    onCreate = { name ->
                        scope.launch {
                            val b = JSONObject().put("name", name)
                            when (val r = ApiClient.post("/clients/api/app/tasks/categories/create/", b)) {
                                is ApiClient.Result.Ok -> {
                                    val c = Opt(r.json.optInt("id"), r.json.optString("name"))
                                    if (categories.none { it.id == c.id }) categories.add(c)
                                    category = c; openMenu = ""
                                }
                                is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                                is ApiClient.Result.Error -> { error = r.message; openMenu = "" }
                            }
                        }
                    },
                )
            }

            // Checklist (compact, add-on-demand)
            checklist.forEachIndexed { i, item ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    OutlinedTextField(item, { checklist[i] = it }, Modifier.weight(1f),
                        placeholder = { Text("Checklist item") }, singleLine = true)
                    TextButton(onClick = { checklist.removeAt(i) }) { Text("✕") }
                }
            }
            TextButton(onClick = { checklist.add("") }, contentPadding = androidx.compose.foundation.layout.PaddingValues(0.dp)) {
                Text("+ Checklist item", fontSize = 13.sp)
            }

            error?.let { Text(it, color = StatusRed, fontSize = 13.sp) }

            // Save the current fields as a reusable template (admins/managers).
            if (openMenu == "savetpl") {
                var tplName by remember { mutableStateOf("") }
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    OutlinedTextField(tplName, { tplName = it }, Modifier.weight(1f),
                        placeholder = { Text("Template name — e.g. Onboarding") }, singleLine = true)
                    Button(onClick = {
                        if (tplName.isNotBlank()) scope.launch {
                            val b = JSONObject().put("name", tplName.trim()).put("title", title)
                                .put("description", description).put("priority", priority)
                                .put("checklist", JSONArray(checklist.filter { it.isNotBlank() }))
                            category?.let { b.put("category_id", it.id) }
                            when (val r = ApiClient.post("/clients/api/app/tasks/templates/save/", b)) {
                                is ApiClient.Result.Ok -> {
                                    Toast.makeText(context, "✓ Template saved", Toast.LENGTH_SHORT).show()
                                    openMenu = ""
                                }
                                is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                                is ApiClient.Result.Error -> error = r.message
                            }
                        }
                    }) { Icon(Icons.Filled.Check, "Save template") }
                    TextButton(onClick = { openMenu = "" }) { Text("✕") }
                }
            }

            // Bottom row: repeat + save-template + attach hints, Assign in front
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                SelectorChip(null, if (repeat.isBlank()) "Repeat" else repeat.replaceFirstChar { it.uppercase() },
                    active = repeat.isNotBlank()) {
                    repeat = when (repeat) { "" -> "daily"; "daily" -> "weekly"; "weekly" -> "monthly"; else -> "" }
                }
                if (!isEdit) {
                    IconAction("💾") { openMenu = if (openMenu == "savetpl") "" else "savetpl" }
                }
                IconAction("📎") { Toast.makeText(context, "Attach files from the task detail screen", Toast.LENGTH_SHORT).show() }
                Spacer(Modifier.weight(1f))
                Button(onClick = { submit() }, enabled = !submitting && title.isNotBlank()) {
                    Text(if (submitting) "…" else if (isEdit) "Save" else "Assign")
                }
            }
        }
    }
}

/** A pill selector with an optional leading icon or colored dot. Returns Unit so
 *  callers can chain `.also { DropdownMenu(...) }` for an anchored menu. */
@Composable
private fun SelectorChip(
    icon: androidx.compose.ui.graphics.vector.ImageVector?,
    label: String,
    active: Boolean,
    leadingDot: Color? = null,
    onClick: () -> Unit,
) {
    val bg = if (active) MaterialTheme.colorScheme.primary.copy(alpha = 0.16f) else MaterialTheme.colorScheme.surfaceVariant
    val fg = if (active) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface
    Row(
        Modifier.clip(RoundedCornerShape(20.dp)).background(bg).clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (icon != null) { Icon(icon, null, Modifier.size(16.dp), tint = fg); Spacer(Modifier.width(5.dp)) }
        if (leadingDot != null) { Box(Modifier.size(9.dp).clip(RoundedCornerShape(3.dp)).background(leadingDot)); Spacer(Modifier.width(5.dp)) }
        Text(label, color = fg, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
private fun PriorityChip(priority: String, onClick: () -> Unit) {
    SelectorChip(null, "Priority: " + priority.replaceFirstChar { it.uppercase() },
        active = true, leadingDot = priorityColor(priority), onClick = onClick)
}

/** Multi-select dropdown that toggles items without closing, syncing `selected`. */
@Composable
private fun MultiMenu(
    expanded: Boolean,
    options: JSONArray?,
    selected: androidx.compose.runtime.snapshots.SnapshotStateList<Opt>,
    onClose: () -> Unit,
) {
    DropdownMenu(expanded, onDismissRequest = onClose) {
        for (i in 0 until (options?.length() ?: 0)) {
            val o = options!!.getJSONObject(i)
            val opt = Opt(o.getInt("id"), o.optString("name"))
            val isSel = selected.any { it.id == opt.id }
            DropdownMenuItem(
                text = { Text((if (isSel) "✓ " else "   ") + opt.name) },
                onClick = { if (isSel) selected.removeAll { it.id == opt.id } else selected.add(opt) },
            )
        }
    }
}

@Composable
private fun NewCategoryRow(onCancel: () -> Unit, onCreate: (String) -> Unit) {
    var name by remember { mutableStateOf("") }
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        OutlinedTextField(name, { name = it }, Modifier.weight(1f), placeholder = { Text("New category") }, singleLine = true)
        Button(onClick = { if (name.isNotBlank()) onCreate(name.trim()) }) { Icon(Icons.Filled.Check, "Create") }
        TextButton(onClick = onCancel) { Text("✕") }
    }
}

@Composable
private fun IconAction(emoji: String, onClick: () -> Unit) {
    Box(
        Modifier.size(38.dp).clip(RoundedCornerShape(10.dp))
            .background(MaterialTheme.colorScheme.surfaceVariant).clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) { Text(emoji, fontSize = 16.sp) }
}
