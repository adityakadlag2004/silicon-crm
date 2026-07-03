package bo.kadlaginvestment.crm.ui

import androidx.activity.compose.BackHandler
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
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import bo.kadlaginvestment.crm.net.ApiClient
import org.json.JSONObject

/** Native Call Analytics (admin-only): employee + timeframe dropdowns,
 * totals, and the filtered call log. */
@Composable
fun CallAnalyticsScreen(
    modifier: Modifier = Modifier,
    onBack: () -> Unit,
    onSessionExpired: () -> Unit,
) {
    BackHandler(onBack = onBack)

    var range by remember { mutableStateOf("today") }
    var employee by remember { mutableStateOf<Pair<Int, String>?>(null) }  // null = all
    var empMenuOpen by remember { mutableStateOf(false) }
    var rangeMenuOpen by remember { mutableStateOf(false) }
    var data by remember { mutableStateOf<JSONObject?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var reloadKey by remember { mutableIntStateOf(0) }

    LaunchedEffect(range, employee, reloadKey) {
        data = null
        val path = "/clients/api/app/calls/analytics/?range=$range" +
            (employee?.let { "&employee_id=${it.first}" } ?: "")
        when (val r = ApiClient.get(path)) {
            is ApiClient.Result.Ok -> data = r.json
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
    }

    if (error != null) { ErrorBox(error!!, modifier) { error = null; reloadKey++ }; return }

    Column(modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        Row(
            Modifier.fillMaxWidth().padding(top = 16.dp, bottom = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "← Back",
                color = MaterialTheme.colorScheme.secondary,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.clickable(onClick = onBack).padding(end = 12.dp),
            )
            Text("Call Analytics", fontSize = 22.sp, fontWeight = FontWeight.Bold)
        }

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Box {
                OutlinedButton(onClick = { empMenuOpen = true }) {
                    Text(employee?.second ?: "All employees")
                }
                DropdownMenu(expanded = empMenuOpen, onDismissRequest = { empMenuOpen = false }) {
                    DropdownMenuItem(
                        text = { Text("All employees") },
                        onClick = { employee = null; empMenuOpen = false },
                    )
                    val es = data?.optJSONArray("employees")
                    for (i in 0 until (es?.length() ?: 0)) {
                        val e = es!!.getJSONObject(i)
                        DropdownMenuItem(
                            text = { Text(e.optString("name")) },
                            onClick = {
                                employee = e.getInt("id") to e.optString("name")
                                empMenuOpen = false
                            },
                        )
                    }
                }
            }
            Box {
                OutlinedButton(onClick = { rangeMenuOpen = true }) {
                    Text(
                        when (range) {
                            "week" -> "This week"
                            "month" -> "This month"
                            else -> "Today"
                        }
                    )
                }
                DropdownMenu(expanded = rangeMenuOpen, onDismissRequest = { rangeMenuOpen = false }) {
                    listOf("today" to "Today", "week" to "This week", "month" to "This month").forEach { (v, label) ->
                        DropdownMenuItem(text = { Text(label) }, onClick = { range = v; rangeMenuOpen = false })
                    }
                }
            }
        }
        Spacer(Modifier.height(10.dp))

        val d = data
        if (d == null) { LoadingBox(); return }

        val t = d.optJSONObject("totals") ?: JSONObject()
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            AnalyticsStat("Dialed", "${t.optInt("dialed")}", Modifier.weight(1f))
            AnalyticsStat("Connected", "${t.optInt("connected")}", Modifier.weight(1f), StatusGreen)
            AnalyticsStat("Missed", "${t.optInt("missed")}", Modifier.weight(1f), StatusRed)
            AnalyticsStat("Talk min", "${t.optDouble("talk_minutes", 0.0)}", Modifier.weight(1f))
        }
        Spacer(Modifier.height(10.dp))

        val calls = d.optJSONArray("calls")
        val rows = (0 until (calls?.length() ?: 0)).map { calls!!.getJSONObject(it) }
        LazyColumn(
            Modifier.fillMaxSize(),
            verticalArrangement = Arrangement.spacedBy(6.dp),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(bottom = 16.dp),
        ) {
            if (rows.isEmpty()) {
                item { Text("No calls in this period.", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp) }
            }
            items(rows) { c ->
                Card(
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                    elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
                ) {
                    Row(
                        Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 10.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(Modifier.weight(1f)) {
                            Text(
                                c.optString("client").ifEmpty { c.optString("phone") },
                                fontWeight = FontWeight.SemiBold, fontSize = 14.sp,
                            )
                            Text(
                                "${if (c.optString("direction") == "outgoing") "↗ Out" else "↙ In"} · ${c.optString("employee")} · ${c.optString("time")}",
                                fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        Column(horizontalAlignment = Alignment.End) {
                            Text("${c.optInt("duration")}s", fontSize = 13.sp, fontWeight = FontWeight.Bold)
                            Text(
                                if (c.optBoolean("connected")) "Connected" else "Not connected",
                                fontSize = 10.sp,
                                color = if (c.optBoolean("connected")) StatusGreen else StatusRed,
                                fontWeight = FontWeight.SemiBold,
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun AnalyticsStat(
    title: String,
    value: String,
    modifier: Modifier = Modifier,
    color: androidx.compose.ui.graphics.Color = androidx.compose.ui.graphics.Color.Unspecified,
) {
    Card(
        modifier = modifier,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.padding(10.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Text(
                value, fontSize = 17.sp, fontWeight = FontWeight.Bold,
                color = if (color == androidx.compose.ui.graphics.Color.Unspecified) MaterialTheme.colorScheme.onSurface else color,
            )
            Text(title, fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}
