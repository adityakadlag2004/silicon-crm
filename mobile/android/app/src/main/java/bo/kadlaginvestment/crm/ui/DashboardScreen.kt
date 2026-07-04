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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
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
import java.text.NumberFormat
import java.util.Locale

private val inr: NumberFormat = NumberFormat.getCurrencyInstance(Locale("en", "IN")).apply {
    maximumFractionDigits = 0
}

private fun money(v: Double): String = inr.format(v)

/** Native home screen — data from /clients/api/app/dashboard/. */
@Composable
fun DashboardScreen(
    modifier: Modifier = Modifier,
    onSessionExpired: () -> Unit,
    onOpenWeb: (String) -> Unit,
) {
    var data by remember { mutableStateOf<JSONObject?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var reloadKey by remember { mutableIntStateOf(0) }

    LaunchedEffect(reloadKey) {
        error = null
        when (val r = ApiClient.get("/clients/api/app/dashboard/")) {
            is ApiClient.Result.Ok -> data = r.json
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
    }

    when {
        error != null -> Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text("Could not load dashboard", fontWeight = FontWeight.SemiBold)
                Text(error ?: "", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp)
                Spacer(Modifier.height(12.dp))
                Button(onClick = { reloadKey++ }) { Text("Retry") }
            }
        }
        data == null -> Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            CircularProgressIndicator()
        }
        else -> Dashboard(data!!, modifier, onOpenWeb, onRefresh = { data = null; reloadKey++ })
    }
}

@Composable
private fun Dashboard(
    d: JSONObject,
    modifier: Modifier,
    onOpenWeb: (String) -> Unit,
    onRefresh: () -> Unit,
) {
    val isAdmin = d.optString("role") == "admin"
    val today = d.optJSONObject("today") ?: JSONObject()
    val month = d.optJSONObject("month") ?: JSONObject()
    val recent = d.optJSONArray("recent_sales")

    LazyColumn(
        modifier = modifier.fillMaxSize().padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Row(
                Modifier.fillMaxWidth().padding(top = 16.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column {
                    Text("Hello, ${d.optString("name")}", fontSize = 22.sp, fontWeight = FontWeight.Bold)
                    Text(
                        if (isAdmin) "Firm overview" else "Your performance",
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        fontSize = 13.sp,
                    )
                }
                Text(
                    "↻ Refresh",
                    color = MaterialTheme.colorScheme.secondary,
                    fontSize = 14.sp,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.clickable { onRefresh() }.padding(8.dp),
                )
            }
        }

        item {
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                StatCard(
                    title = "Today",
                    value = money(today.optDouble("amount", 0.0)),
                    sub = "${today.optInt("sales_count")} sale(s)",
                    modifier = Modifier.weight(1f),
                )
                StatCard(
                    title = "This month",
                    value = money(month.optDouble("amount", 0.0)),
                    sub = "${month.optInt("sales_count")} sale(s)",
                    modifier = Modifier.weight(1f),
                )
            }
        }

        item {
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                if (isAdmin) {
                    StatCard(
                        title = "Pending approvals",
                        value = "${d.optInt("pending_approvals")}",
                        sub = "tap to review",
                        accent = if (d.optInt("pending_approvals") > 0) StatusAmber else StatusGreen,
                        modifier = Modifier.weight(1f).clickable { onOpenWeb("/clients/sales/approve/") },
                    )
                } else {
                    StatCard(
                        title = "Points (month)",
                        value = "%.1f".format(month.optDouble("points", 0.0)),
                        sub = "incentive points",
                        modifier = Modifier.weight(1f),
                    )
                }
                StatCard(
                    title = "Follow-ups",
                    value = "${d.optInt("pending_followups")}",
                    sub = "pending calls",
                    accent = if (d.optInt("pending_followups") > 0) StatusAmber else StatusGreen,
                    modifier = Modifier.weight(1f).clickable { onOpenWeb("/clients/calls/followups/") },
                )
            }
        }

        if (isAdmin) {
            // ── Team calls today ──
            val tc = d.optJSONObject("team_calls_today")
            if (tc != null) {
                item { SectionHeader("Team calls today") }
                item {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        MiniStat("Calls", "${tc.optInt("calls")}", Modifier.weight(1f))
                        MiniStat("Connected", "${tc.optInt("connected")}", Modifier.weight(1f), StatusGreen)
                        MiniStat("Talk", compactMins(tc.optDouble("talk_minutes", 0.0)), Modifier.weight(1f))
                        MiniStat("Serious", "${tc.optInt("serious")}", Modifier.weight(1f), BrandGoldDark)
                    }
                }
            }

            // ── Today's team leaderboard ──
            val lb = d.optJSONArray("leaderboard_today")
            item { SectionHeader("Today's leaders") }
            if (lb == null || lb.length() == 0) {
                item { Text("No sales logged yet today.", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp) }
            } else {
                items((0 until lb.length()).map { lb.getJSONObject(it) to it }) { (e, i) ->
                    Card(
                        shape = RoundedCornerShape(12.dp),
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
                    ) {
                        Row(
                            Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 11.dp),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(
                                    "${i + 1}",
                                    fontSize = 14.sp, fontWeight = FontWeight.Bold,
                                    color = if (i == 0) BrandGoldDark else MaterialTheme.colorScheme.onSurfaceVariant,
                                    modifier = Modifier.padding(end = 12.dp),
                                )
                                Text(e.optString("name"), fontSize = 14.sp, fontWeight = FontWeight.Medium)
                            }
                            Column(horizontalAlignment = Alignment.End) {
                                Text(money(e.optDouble("amount", 0.0)), fontWeight = FontWeight.Bold, fontSize = 14.sp)
                                Text("${e.optInt("count")} sale(s)", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                    }
                }
            }

            // ── Month-to-date product-wise ──
            val pm = d.optJSONArray("product_mtd")
            item { SectionHeader("This month by product (till today)") }
            if (pm == null || pm.length() == 0) {
                item { Text("No approved business this month yet.", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp) }
            } else {
                val maxAmt = (0 until pm.length()).maxOf { pm.getJSONObject(it).optDouble("amount", 0.0) }
                items((0 until pm.length()).map { pm.getJSONObject(it) }) { p ->
                    Column(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                            Text(p.optString("name"), fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                            Text("${money(p.optDouble("amount", 0.0))} · ${p.optInt("count")}", fontSize = 13.sp, fontWeight = FontWeight.Bold)
                        }
                        Spacer(Modifier.height(3.dp))
                        ProgressBar(if (maxAmt > 0) (p.optDouble("amount", 0.0) / maxAmt).toFloat() else 0f)
                    }
                }
            }
        } else {
            // ── Gamified employee view ──
            val earnings = d.optJSONObject("earnings")
            if (earnings != null) {
                item { EarningsCard(earnings) }
            }

            // Active campaigns to chase
            val camps = d.optJSONArray("active_campaigns")
            if (camps != null && camps.length() > 0) {
                item { SectionHeader("🔥 Live campaigns — earn extra!") }
                items((0 until camps.length()).map { camps.getJSONObject(it) }) { c ->
                    CampaignCard(c)
                }
            }

            // My business this month by product
            val pm = d.optJSONArray("product_mtd")
            item { SectionHeader("My business this month") }
            if (pm == null || pm.length() == 0) {
                item {
                    Text(
                        "No approved sales yet this month — add one from the Add Sale tab.",
                        color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp,
                    )
                }
            } else {
                val maxAmt = (0 until pm.length()).maxOf { pm.getJSONObject(it).optDouble("amount", 0.0) }
                items((0 until pm.length()).map { pm.getJSONObject(it) }) { p ->
                    Column(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                            Text(p.optString("name"), fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                            Text("${money(p.optDouble("amount", 0.0))} · ${p.optInt("count")}", fontSize = 13.sp, fontWeight = FontWeight.Bold)
                        }
                        Spacer(Modifier.height(3.dp))
                        ProgressBar(if (maxAmt > 0) (p.optDouble("amount", 0.0) / maxAmt).toFloat() else 0f)
                    }
                }
            }
        }

        item { Spacer(Modifier.height(16.dp)) }
    }
}

@Composable
private fun SectionHeader(text: String) {
    Text(text, fontSize = 16.sp, fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 8.dp))
}

@Composable
private fun EarningsCard(e: JSONObject) {
    val salary = e.optDouble("salary", 0.0)
    val earned = e.optDouble("earned", 0.0)
    val justified = e.optBoolean("justified")
    val hasSalary = !e.isNull("percent") && salary > 0
    val percent = if (hasSalary) e.optDouble("percent", 0.0) else 0.0
    val fraction = (earned / salary).coerceIn(0.0, 1.0).toFloat()

    Card(
        shape = RoundedCornerShape(18.dp),
        colors = CardDefaults.cardColors(
            containerColor = if (justified) StatusGreen.copy(alpha = 0.12f)
            else MaterialTheme.colorScheme.primaryContainer
        ),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(
                if (justified) "🎉 Salary justified!" else "💪 Your earning progress",
                fontSize = 16.sp, fontWeight = FontWeight.Bold,
                color = if (justified) StatusGreen else BrandGoldDark,
            )

            if (!hasSalary) {
                Text("You've earned ${money(earned)} in incentive points this month.", fontSize = 14.sp)
            } else {
                Text(
                    "${money(earned)} earned  ·  ${money(salary)} salary",
                    fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                // Progress bar
                androidx.compose.foundation.layout.Box(
                    Modifier.fillMaxWidth().height(14.dp)
                        .background(MaterialTheme.colorScheme.surface, RoundedCornerShape(8.dp))
                ) {
                    androidx.compose.foundation.layout.Box(
                        Modifier.fillMaxWidth(fraction).height(14.dp)
                            .background(if (justified) StatusGreen else BrandGold, RoundedCornerShape(8.dp))
                    )
                }
                Text(
                    "${"%.0f".format(percent)}% of salary",
                    fontSize = 11.sp, fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (justified) {
                    Text(
                        "You're ${money(e.optDouble("surplus", 0.0))} above your salary — that's your bonus zone. Keep selling! 🚀",
                        fontSize = 13.sp, fontWeight = FontWeight.Medium, color = StatusGreen,
                    )
                } else {
                    Text(
                        "Just ${money(e.optDouble("remaining", 0.0))} more to justify your salary this month.",
                        fontSize = 13.sp, fontWeight = FontWeight.Medium,
                    )
                }
            }
        }
    }
}

@Composable
private fun CampaignCard(c: JSONObject) {
    Card(
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.fillMaxWidth().padding(14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text(c.optString("name"), fontSize = 15.sp, fontWeight = FontWeight.Bold, color = BrandGoldDark)
                Text("ends ${c.optString("ends")}", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            val products = c.optJSONArray("products")
            for (i in 0 until (products?.length() ?: 0)) {
                val p = products!!.getJSONObject(i)
                val benefit = if (p.optString("benefit_type") == "unit") {
                    "${"%.1f".format(p.optDouble("points_per_unit", 0.0))} pts per ${money(p.optDouble("unit_amount", 0.0))}"
                } else {
                    "target payout — hit the slab for a bonus"
                }
                Text("• ${p.optString("product")}: $benefit", fontSize = 13.sp)
            }
        }
    }
}

@Composable
private fun MiniStat(
    title: String,
    value: String,
    modifier: Modifier = Modifier,
    accent: androidx.compose.ui.graphics.Color = androidx.compose.ui.graphics.Color.Unspecified,
) {
    Card(
        modifier = modifier,
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.padding(vertical = 12.dp, horizontal = 8.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Text(
                value, fontSize = 16.sp, fontWeight = FontWeight.Bold,
                color = if (accent == androidx.compose.ui.graphics.Color.Unspecified) MaterialTheme.colorScheme.onSurface else accent,
            )
            Text(title, fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun ProgressBar(fraction: Float) {
    val track = MaterialTheme.colorScheme.surfaceVariant
    val fill = MaterialTheme.colorScheme.primary
    androidx.compose.foundation.layout.Box(
        Modifier.fillMaxWidth().height(7.dp).background(track, RoundedCornerShape(4.dp))
    ) {
        androidx.compose.foundation.layout.Box(
            Modifier.fillMaxWidth(fraction.coerceIn(0f, 1f)).height(7.dp).background(fill, RoundedCornerShape(4.dp))
        )
    }
}

private fun compactMins(totalMinutes: Double): String {
    val totalSec = (totalMinutes * 60).toInt()
    val h = totalSec / 3600
    val m = (totalSec % 3600) / 60
    return if (h > 0) "${h}h${m}m" else "${m}m"
}

@Composable
private fun StatCard(
    title: String,
    value: String,
    sub: String,
    modifier: Modifier = Modifier,
    accent: androidx.compose.ui.graphics.Color = MaterialTheme.colorScheme.onSurface,
) {
    Card(
        modifier = modifier,
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.padding(16.dp)) {
            Text(title, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(value, fontSize = 20.sp, fontWeight = FontWeight.Bold, color = accent)
            Text(sub, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun SaleRow(s: JSONObject, showEmployee: Boolean) {
    Card(
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text(s.optString("client"), fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                Text(
                    s.optString("product") + if (showEmployee) " · ${s.optString("employee")}" else "",
                    fontSize = 12.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Column(horizontalAlignment = Alignment.End) {
                Text(money(s.optDouble("amount", 0.0)), fontWeight = FontWeight.Bold, fontSize = 14.sp)
                StatusChip(s.optString("status"))
            }
        }
    }
}

@Composable
private fun StatusChip(status: String) {
    val (bg, label) = when (status) {
        "approved" -> StatusGreen to "Approved"
        "rejected" -> StatusRed to "Rejected"
        else -> StatusAmber to "Pending"
    }
    Box(
        Modifier
            .background(bg.copy(alpha = 0.14f), RoundedCornerShape(8.dp))
            .padding(horizontal = 8.dp, vertical = 2.dp)
    ) {
        Text(label, fontSize = 10.sp, color = bg, fontWeight = FontWeight.SemiBold)
    }
}
