package bo.kadlaginvestment.crm.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import bo.kadlaginvestment.crm.net.ApiClient
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject

/**
 * The main task list. `kind` maps to the API tab (dashboard→all, my, delegated,
 * subscribed, all). Shows date-range + filter controls, status tabs with live
 * counts, and task cards. Marking a card done posts a status action.
 */
@Composable
fun TasksListScreen(
    kind: String,
    reloadSignal: Int,
    onOpenTask: (Int) -> Unit,
    onSessionExpired: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    val tab = when (kind) {
        "my" -> "my"
        "delegated" -> "delegated"
        "subscribed" -> "subscribed"
        else -> "all"   // dashboard + all
    }

    var q by remember { mutableStateOf("") }
    var status by remember { mutableStateOf("") }
    var range by remember { mutableStateOf("") }
    var category by remember { mutableStateOf<Pair<Int, String>?>(null) }
    var priority by remember { mutableStateOf<String?>(null) }

    var counts by remember { mutableStateOf(JSONObject()) }
    var rows by remember { mutableStateOf(listOf<JSONObject>()) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var reloadKey by remember { mutableIntStateOf(0) }
    var toast by remember { mutableStateOf<String?>(null) }

    var meta by remember { mutableStateOf<JSONObject?>(null) }
    var showFilters by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        when (val r = ApiClient.get("/clients/api/app/tasks/meta/")) {
            is ApiClient.Result.Ok -> meta = r.json
            else -> {}
        }
    }

    LaunchedEffect(tab, status, range, category, priority, reloadKey, reloadSignal) {
        loading = true; error = null
        val params = buildString {
            append("?tab=$tab")
            if (status.isNotBlank()) append("&status=$status")
            if (range.isNotBlank()) append("&range=$range")
            category?.let { append("&category=${it.first}") }
            priority?.let { append("&priority=$it") }
            if (q.isNotBlank()) append("&q=" + java.net.URLEncoder.encode(q, "UTF-8"))
        }
        when (val r = ApiClient.get("/clients/api/app/tasks/$params")) {
            is ApiClient.Result.Ok -> {
                counts = r.json.optJSONObject("counts") ?: JSONObject()
                val arr = r.json.optJSONArray("tasks")
                rows = (0 until (arr?.length() ?: 0)).map { arr!!.getJSONObject(it) }
            }
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
        loading = false
    }

    // Debounced search
    LaunchedEffect(q) {
        delay(350)
        reloadKey++
    }

    fun toggleDone(task: JSONObject) {
        scope.launch {
            val newStatus = if (task.optString("status") == "completed") "pending" else "completed"
            val body = JSONObject().put("action", "status").put("status", newStatus)
            when (val r = ApiClient.post("/clients/api/app/tasks/${task.optInt("id")}/action/", body)) {
                is ApiClient.Result.Ok -> reloadKey++
                is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                is ApiClient.Result.Error -> toast = r.message
            }
        }
    }

    // ── Filters sheet ──
    if (showFilters && meta != null) {
        TaskFilterSheet(
            meta = meta!!,
            category = category, priority = priority,
            onApply = { c, p -> category = c; priority = p; showFilters = false },
            onClear = { category = null; priority = null; showFilters = false },
            onDismiss = { showFilters = false },
        )
    }

    Column(Modifier.fillMaxSize()) {

        // Filters row: date range + filter button + search
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            DateRangeDropdown(range) { range = it }
            Box(
                Modifier
                    .size(40.dp)
                    .clip(RoundedCornerShape(10.dp))
                    .background(
                        if (category != null || priority != null) MaterialTheme.colorScheme.primary
                        else MaterialTheme.colorScheme.surfaceVariant
                    )
                    .clickable { showFilters = true },
                contentAlignment = Alignment.Center,
            ) { Text("⚙", fontSize = 16.sp) }
            OutlinedTextField(
                value = q,
                onValueChange = { q = it },
                placeholder = { Text("Search tasks") },
                leadingIcon = { Icon(Icons.Filled.Search, null) },
                singleLine = true,
                modifier = Modifier.weight(1f).height(52.dp),
                shape = RoundedCornerShape(12.dp),
            )
        }

        // Status tabs with counts
        Row(
            Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 12.dp, vertical = 4.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            StatusTab("All", counts.optInt("total"), Color(0xFF9CA3AF), status == "") { status = "" }
            StatusTab("Overdue", counts.optInt("overdue"), StatusRed, status == "overdue") { status = "overdue" }
            StatusTab("Pending", counts.optInt("pending"), Color(0xFF9CA3AF), status == "pending") { status = "pending" }
            StatusTab("In Progress", counts.optInt("in_progress"), Color(0xFF2563EB), status == "in_progress") { status = "in_progress" }
            StatusTab("Completed", counts.optInt("completed"), StatusGreen, status == "completed") { status = "completed" }
        }

        toast?.let {
            Text(it, color = StatusRed, fontSize = 12.sp, modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp))
        }

        when {
            error != null -> ErrorBox(error!!) { error = null; reloadKey++ }
            loading && rows.isEmpty() -> LoadingBox()
            rows.isEmpty() -> EmptyTasks()
            else -> LazyColumn(
                Modifier.fillMaxSize(),
                verticalArrangement = Arrangement.spacedBy(8.dp),
                contentPadding = androidx.compose.foundation.layout.PaddingValues(12.dp),
            ) {
                items(rows) { t ->
                    TaskCard(t, onOpen = { onOpenTask(t.optInt("id")) }, onToggleDone = { toggleDone(t) })
                }
                item { Spacer(Modifier.height(72.dp)) } // clearance for FAB
            }
        }
    }
}

@Composable
private fun DateRangeDropdown(selected: String, onSelect: (String) -> Unit) {
    var open by remember { mutableStateOf(false) }
    val label = when (selected) {
        "today" -> "Today"; "tomorrow" -> "Tomorrow"; "this_week" -> "This Week"
        "this_month" -> "This Month"; else -> "Any date"
    }
    Box {
        Row(
            Modifier
                .clip(RoundedCornerShape(10.dp))
                .background(MaterialTheme.colorScheme.surfaceVariant)
                .clickable { open = true }
                .padding(horizontal = 10.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(label, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
            Icon(Icons.Filled.KeyboardArrowDown, null, Modifier.size(16.dp))
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            listOf(
                "" to "Any date", "today" to "Today", "tomorrow" to "Tomorrow",
                "this_week" to "This Week", "this_month" to "This Month",
            ).forEach { (v, l) ->
                DropdownMenuItem(text = { Text(l) }, onClick = { onSelect(v); open = false })
            }
        }
    }
}

@Composable
private fun StatusTab(label: String, count: Int, color: Color, selected: Boolean, onClick: () -> Unit) {
    Column(
        Modifier.clickable(onClick = onClick).padding(vertical = 4.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(8.dp).clip(CircleShape).background(color))
            Spacer(Modifier.size(5.dp))
            Text(
                "$label ($count)",
                fontSize = 13.sp,
                fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
                color = if (selected) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Spacer(Modifier.size(3.dp))
        Box(
            Modifier
                .height(2.dp)
                .size(width = 40.dp, height = 2.dp)
                .background(if (selected) MaterialTheme.colorScheme.primary else Color.Transparent)
        )
    }
}

@Composable
private fun EmptyTasks() {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text("🗒️", fontSize = 44.sp)
            Spacer(Modifier.height(8.dp))
            Text("No Tasks Here", fontWeight = FontWeight.Bold, fontSize = 17.sp)
            Text(
                "It seems that you don't have any tasks in this list.",
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                fontSize = 13.sp,
                modifier = Modifier.padding(horizontal = 40.dp, vertical = 4.dp),
            )
        }
    }
}
