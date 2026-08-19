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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import bo.kadlaginvestment.crm.net.ApiClient
import org.json.JSONObject

/** Reports hub: a menu of report screens. Native screens open in-place;
 * complex financial tools open their web page. */
@Composable
fun ReportsHub(
    modifier: Modifier = Modifier,
    onBack: () -> Unit,
    onSessionExpired: () -> Unit,
    onOpenWeb: (String) -> Unit,
) {
    BackHandler(onBack = onBack)
    var sub by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(Unit) { if (!Session.load()) onSessionExpired() }

    when (sub) {
        "overview" -> { BackHandler { sub = null }; ReportsScreen(modifier, onBack = { sub = null }, onSessionExpired = onSessionExpired, onOpenWeb = onOpenWeb); return }
        "monthly" -> { BackHandler { sub = null }; MonthlyReportScreen(modifier, onBack = { sub = null }, onSessionExpired = onSessionExpired); return }
    }

    val isManagerPlus = Session.isManagerPlus

    Column(modifier.fillMaxSize().padding(horizontal = rdp(16))) {
        ScreenHeader("Reports", onBack = onBack)

        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp), contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 8.dp)) {
            item { ReportCard("📊", "Business Overview", "Trend by product, mix & leaderboard — 6M to 5Y") { sub = "overview" } }
            if (isManagerPlus) {
                item { ReportCard("🧾", "Monthly Report", "Product-wise + employee-wise for a month") { sub = "monthly" } }
                item { Spacer(Modifier.height(4.dp)); Text("Detailed tools (open on web)", fontSize = rsp(12), color = MaterialTheme.colorScheme.onSurfaceVariant, fontWeight = FontWeight.SemiBold) }
                item { ReportCard("💹", "Business Analytics", "Revenue vs expenses, margins", web = true) { onOpenWeb("/clients/reports/business-analytics/") } }
                item { ReportCard("💰", "Net Business", "Sales minus redemptions", web = true) { onOpenWeb("/clients/dashboard/net-business/") } }
                item { ReportCard("🔁", "Net SIP", "SIP fresh vs stopped", web = true) { onOpenWeb("/clients/dashboard/net-sip/") } }
            }
        }
    }
}

@Composable
private fun ReportCard(icon: String, title: String, subtitle: String, web: Boolean = false, onClick: () -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth().clickable(onClick = onClick),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(icon, fontSize = rsp(22), modifier = Modifier.padding(end = 14.dp))
            Column(Modifier.weight(1f)) {
                Text(title, fontSize = rsp(15), fontWeight = FontWeight.SemiBold)
                Text(subtitle, fontSize = rsp(12), color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Text(if (web) "web ›" else "›", fontSize = rsp(13), color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

// ── Monthly report ───────────────────────────────────────────────────────────

@Composable
private fun MonthlyReportScreen(
    modifier: Modifier,
    onBack: () -> Unit,
    onSessionExpired: () -> Unit,
) {
    var month by remember { mutableIntStateOf(0) }   // 0 = default (current)
    var year by remember { mutableIntStateOf(0) }
    var monthMenu by remember { mutableStateOf(false) }
    var yearMenu by remember { mutableStateOf(false) }
    var data by remember { mutableStateOf<JSONObject?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var reloadKey by remember { mutableIntStateOf(0) }

    LaunchedEffect(month, year, reloadKey) {
        data = null
        val params = buildString {
            if (month > 0) append("month=$month&")
            if (year > 0) append("year=$year")
        }
        when (val r = ApiClient.get("/clients/api/app/reports/monthly/?$params")) {
            is ApiClient.Result.Ok -> data = r.json
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
    }

    if (error != null) { ErrorBox(error!!, modifier) { error = null; reloadKey++ }; return }

    Column(modifier.fillMaxSize().padding(horizontal = rdp(16))) {
        ReportHeader("Monthly Report", onBack)

        val d = data
        Row(Modifier.padding(bottom = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Box {
                OutlinedButton(onClick = { monthMenu = true }) {
                    Text(d?.optString("month_label") ?: "Month")
                }
                DropdownMenu(expanded = monthMenu, onDismissRequest = { monthMenu = false }) {
                    val ms = d?.optJSONArray("months")
                    for (i in 0 until (ms?.length() ?: 0)) {
                        val mo = ms!!.getJSONObject(i)
                        DropdownMenuItem(text = { Text(mo.optString("label")) }, onClick = { month = mo.optInt("value"); monthMenu = false })
                    }
                }
            }
            Box {
                OutlinedButton(onClick = { yearMenu = true }) {
                    Text(if (year > 0) "$year" else d?.optInt("year")?.toString() ?: "Year")
                }
                DropdownMenu(expanded = yearMenu, onDismissRequest = { yearMenu = false }) {
                    val ys = d?.optJSONArray("years")
                    for (i in 0 until (ys?.length() ?: 0)) {
                        val yv = ys!!.getInt(i)
                        DropdownMenuItem(text = { Text("$yv") }, onClick = { year = yv; yearMenu = false })
                    }
                }
            }
        }

        if (d == null) { LoadingBox(); return }

        val products = d.optJSONArray("products")
        val prodRows = (0 until (products?.length() ?: 0)).map { products!!.getJSONObject(it) }
        val employees = d.optJSONArray("employees")
        val maxProd = prodRows.maxOfOrNull { it.optDouble("amount", 0.0) } ?: 0.0

        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp), contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 8.dp)) {
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    HeroStat("Total business", rupees(d.optDouble("total_amount", 0.0)), Modifier.weight(1f))
                    HeroStat("Points", "%.0f".format(d.optDouble("total_points", 0.0)), Modifier.weight(1f))
                    HeroStat("Sales", "${d.optInt("total_count")}", Modifier.weight(1f))
                }
            }
            item { SectionTitle("By product") }
            if (prodRows.isEmpty()) item { Text("No approved business this month.", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = rsp(13)) }
            items(prodRows) { p ->
                Column(Modifier.fillMaxWidth()) {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        Text(p.optString("name"), fontSize = rsp(13), fontWeight = FontWeight.SemiBold)
                        Text("${rupees(p.optDouble("amount", 0.0))} · ${p.optInt("count")}", fontSize = rsp(13), fontWeight = FontWeight.Bold)
                    }
                    Spacer(Modifier.height(3.dp))
                    BarLine(if (maxProd > 0) (p.optDouble("amount", 0.0) / maxProd).toFloat() else 0f)
                    Spacer(Modifier.height(6.dp))
                }
            }
            if (employees != null) {
                item { SectionTitle("By employee") }
                items((0 until employees.length()).map { employees.getJSONObject(it) }) { e ->
                    Row(
                        Modifier.fillMaxWidth().padding(vertical = 5.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Text(e.optString("name"), fontSize = rsp(14), fontWeight = FontWeight.Medium)
                        Column(horizontalAlignment = Alignment.End) {
                            Text(rupees(e.optDouble("amount", 0.0)), fontSize = rsp(14), fontWeight = FontWeight.Bold)
                            Text("%.0f pts".format(e.optDouble("points", 0.0)), fontSize = rsp(11), color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
            item { Spacer(Modifier.height(16.dp)) }
        }
    }
}

// ── Shared bits ──────────────────────────────────────────────────────────────

@Composable
private fun ReportHeader(title: String, onBack: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(top = 16.dp, bottom = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        ScreenHeader(title, onBack = onBack)
    }
}

@Composable
private fun HeroStat(title: String, value: String, modifier: Modifier = Modifier) {
    Card(
        modifier = modifier,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.padding(12.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Text(value, fontSize = rsp(17), fontWeight = FontWeight.Bold)
            Text(title, fontSize = rsp(10), color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun BarLine(fraction: Float) {
    val track = MaterialTheme.colorScheme.surfaceVariant
    val fill = MaterialTheme.colorScheme.primary
    Box(
        Modifier.fillMaxWidth().height(8.dp).background(track, RoundedCornerShape(4.dp))
    ) {
        Box(
            Modifier.fillMaxWidth(fraction.coerceIn(0f, 1f)).height(8.dp)
                .background(fill, RoundedCornerShape(4.dp))
        )
    }
}
