package bo.kadlaginvestment.crm.ui

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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ScrollableTabRow
import androidx.compose.material3.Tab
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
import kotlinx.coroutines.launch
import org.json.JSONObject

private val STATUS_TABS = listOf(
    "" to "All", "overdue" to "Overdue", "pending" to "Pending",
    "in_progress" to "In Progress", "completed" to "Completed",
)

/**
 * Reusable swipeable task view: optional team scorecard on top, then a pager of
 * per-status pages (All/Overdue/Pending/In Progress/Completed). Used by every
 * task list tab — Dashboard (with scorecard), My Tasks, Delegated, Subscribed,
 * All — so the experience is identical everywhere. `tab` = the API scope
 * (all/my/delegated/subscribed).
 */
@Composable
fun TaskPagerScreen(
    tab: String,
    showScorecard: Boolean,
    reloadSignal: Int,
    onOpenTask: (Int) -> Unit,
    onSessionExpired: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    val pager = rememberPagerState(pageCount = { STATUS_TABS.size })
    var query by remember { mutableStateOf("") }

    Column(Modifier.fillMaxSize()) {
        if (showScorecard) Scorecard(reloadSignal, onSessionExpired)

        // Search: title, description, category, or #id.
        androidx.compose.material3.OutlinedTextField(
            query, { query = it },
            Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
            placeholder = { Text("Search tasks…", fontSize = 13.sp) },
            singleLine = true,
            trailingIcon = {
                if (query.isNotEmpty()) Text(
                    "✕",
                    Modifier.clickable { query = "" }.padding(8.dp),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            },
        )

        ScrollableTabRow(
            selectedTabIndex = pager.currentPage,
            edgePadding = 12.dp,
            containerColor = MaterialTheme.colorScheme.background,
        ) {
            STATUS_TABS.forEachIndexed { i, (_, label) ->
                Tab(
                    selected = pager.currentPage == i,
                    onClick = { scope.launch { pager.animateScrollToPage(i) } },
                    text = { Text(label, fontSize = 13.sp) },
                )
            }
        }

        HorizontalPager(state = pager, modifier = Modifier.weight(1f)) { page ->
            StatusTaskList(tab, STATUS_TABS[page].first, query, reloadSignal, onOpenTask, onSessionExpired)
        }
    }
}

@Composable
private fun Scorecard(reloadSignal: Int, onSessionExpired: () -> Unit) {
    var period by remember { mutableStateOf("week") }
    var rows by remember { mutableStateOf(listOf<JSONObject>()) }
    var open by remember { mutableStateOf(false) }

    LaunchedEffect(period, reloadSignal) {
        when (val r = ApiClient.get("/clients/api/app/tasks/scorecard/?period=$period")) {
            is ApiClient.Result.Ok -> {
                val arr = r.json.optJSONArray("scorecard")
                rows = (0 until (arr?.length() ?: 0)).map { arr!!.getJSONObject(it) }
            }
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> {}
        }
    }

    Column(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text("Team scorecard", fontWeight = FontWeight.Bold, fontSize = 15.sp, modifier = Modifier.weight(1f))
            Box {
                Row(
                    Modifier.clip(RoundedCornerShape(8.dp))
                        .background(MaterialTheme.colorScheme.surfaceVariant)
                        .clickable { open = true }.padding(horizontal = 10.dp, vertical = 6.dp),
                ) {
                    Text(period.replaceFirstChar { it.uppercase() }, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                    Text(" ▾", fontSize = 12.sp)
                }
                DropdownMenu(open, onDismissRequest = { open = false }) {
                    listOf("day" to "Day", "week" to "Week", "month" to "Month").forEach { (v, l) ->
                        DropdownMenuItem(text = { Text(l) }, onClick = { period = v; open = false })
                    }
                }
            }
        }
        Spacer(Modifier.height(6.dp))
        if (rows.isEmpty()) {
            Text("No data for this period.", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        } else {
            LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                items(rows) { s -> ScoreCardItem(s) }
            }
        }
    }
}

@Composable
private fun ScoreCardItem(s: JSONObject) {
    val pct = s.optInt("completed_pct")
    val color = when {
        pct >= 75 -> StatusGreen
        pct >= 40 -> StatusAmber
        else -> StatusRed
    }
    Column(
        Modifier
            .width(130.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(MaterialTheme.colorScheme.surface)
            .padding(12.dp),
    ) {
        Text(s.optString("name"), fontWeight = FontWeight.SemiBold, fontSize = 13.sp, maxLines = 1)
        Spacer(Modifier.height(6.dp))
        Text("$pct%", fontWeight = FontWeight.Bold, fontSize = 22.sp, color = color)
        Text("completed", fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(4.dp))
        // mini bar
        Box(Modifier.fillMaxWidth().height(5.dp).clip(RoundedCornerShape(3.dp)).background(MaterialTheme.colorScheme.surfaceVariant)) {
            Box(Modifier.fillMaxWidth(pct / 100f).height(5.dp).clip(RoundedCornerShape(3.dp)).background(color))
        }
        Spacer(Modifier.height(4.dp))
        Text(
            "${s.optInt("completed")}/${s.optInt("total")} done" +
                (s.optInt("overdue").takeIf { it > 0 }?.let { " · $it overdue" } ?: ""),
            fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        // Timeliness: % of dated completions finished by their due date.
        if (!s.isNull("on_time_pct")) {
            Text(
                "⏱ ${s.optInt("on_time_pct")}% on time",
                fontSize = 10.sp,
                color = if (s.optInt("on_time_pct") >= 70) StatusGreen else StatusAmber,
            )
        }
    }
}

/** A single status page inside the pager, scoped to `tab`. */
@Composable
private fun StatusTaskList(
    tab: String,
    status: String,
    query: String,
    reloadSignal: Int,
    onOpenTask: (Int) -> Unit,
    onSessionExpired: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    var rows by remember { mutableStateOf(listOf<JSONObject>()) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var localReload by remember { mutableIntStateOf(0) }

    LaunchedEffect(tab, status, query, reloadSignal, localReload) {
        if (query.isNotBlank()) kotlinx.coroutines.delay(300)  // debounce typing
        loading = true; error = null
        var q = if (status.isBlank()) "?tab=$tab" else "?tab=$tab&status=$status"
        if (query.isNotBlank()) q += "&q=" + java.net.URLEncoder.encode(query.trim(), "UTF-8")
        when (val r = ApiClient.get("/clients/api/app/tasks/$q")) {
            is ApiClient.Result.Ok -> {
                val arr = r.json.optJSONArray("tasks")
                rows = (0 until (arr?.length() ?: 0)).map { arr!!.getJSONObject(it) }
            }
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
        loading = false
    }

    fun toggleDone(t: JSONObject) {
        scope.launch {
            val ns = if (t.optString("status") == "completed") "pending" else "completed"
            val body = JSONObject().put("action", "status").put("status", ns)
            when (ApiClient.post("/clients/api/app/tasks/${t.optInt("id")}/action/", body)) {
                is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                else -> localReload++
            }
        }
    }

    when {
        error != null -> ErrorBox(error!!) { error = null; localReload++ }
        loading && rows.isEmpty() -> LoadingBox()
        rows.isEmpty() -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text("🗒️", fontSize = 40.sp)
                Text("No Tasks Here", fontWeight = FontWeight.Bold, fontSize = 16.sp)
                Text("Nothing in this list.", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        else -> LazyColumn(
            Modifier.fillMaxSize(),
            verticalArrangement = Arrangement.spacedBy(8.dp),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(12.dp),
        ) {
            items(rows) { t -> TaskCard(t, onOpen = { onOpenTask(t.optInt("id")) }, onToggleDone = { toggleDone(t) }) }
            item { Spacer(Modifier.height(72.dp)) }
        }
    }
}
