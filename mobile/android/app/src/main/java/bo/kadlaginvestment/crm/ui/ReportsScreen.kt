package bo.kadlaginvestment.crm.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import org.json.JSONArray
import org.json.JSONObject

/** Four colours, not eight: three named products plus "Other". A legend you
 * have to scroll sideways is a legend nobody reads. */
private val SeriesColors = listOf(
    Color(0xFFE5B740), Color(0xFF3B82F6), Color(0xFF10B981), Color(0xFF94A3B8),
)
private const val NAMED_SERIES = 3

/** One control instead of two. "6 cols" was developer vocabulary; a range is
 * what people actually ask for. */
private data class Range(val label: String, val period: String, val columns: Int)

private val RANGES = listOf(
    Range("6M", "month", 6),
    Range("1Y", "month", 12),
    Range("3Y", "quarter", 12),
    Range("5Y", "year", 5),
)

private fun JSONArray?.toDoubles(): List<Double> =
    (0 until (this?.length() ?: 0)).map { this!!.optDouble(it, 0.0) }

/**
 * Business Overview: what the period earned, how it splits by product, and who
 * brought it in.
 *
 * Three things it does that the old screen didn't: it leads with the number
 * (nobody should decode a bar to learn this month's business), a tap on a bar
 * retells the page for that period, and a tap on a person retells it for them
 * — which is the whole of what the separate "Past Performance" screen was.
 */
@Composable
fun ReportsScreen(
    modifier: Modifier = Modifier,
    onBack: () -> Unit,
    onSessionExpired: () -> Unit,
    onOpenWeb: (String) -> Unit = {},
    employeeMode: Boolean = false,
) {
    BackHandler(onBack = onBack)

    var rangeIdx by rememberSaveable { mutableIntStateOf(0) }
    var select by rememberSaveable { mutableIntStateOf(0) }   // 0 = latest column
    var employeeId by rememberSaveable { mutableStateOf<Int?>(null) }

    val range = RANGES[rangeIdx]
    val path = buildString {
        append("/clients/api/app/reports/summary/")
        append("?period=${range.period}&columns=${range.columns}&select=$select")
        employeeId?.let { append("&employee_id=$it") }
    }
    val loader = rememberLoader(path, onSessionExpired = onSessionExpired)
    val d = loader.data

    Column(modifier.fillMaxSize()) {
        ScreenHeader(
            when {
                employeeId != null -> d?.optString("scope_name").orEmpty().ifEmpty { "Performance" }
                employeeMode -> "Employee Performance"
                d?.optBoolean("firm_wide") == true -> "Business Overview"
                else -> "My Performance"
            },
            onBack = onBack,
            modifier = Modifier.padding(horizontal = 8.dp),
        )
        RefreshingBar(loader.refreshing)
        ErrorStrip(loader.error.takeIf { d != null })

        if (d == null) {
            if (loader.error != null) ErrorBox(loader.error!!, Modifier.fillMaxSize()) { loader.reload() }
            else LoadingBox(Modifier.fillMaxSize())
            return@Column
        }

        // Employee Performance opens on "whose?" — the same report, one person
        // at a time, product-wise across the months.
        if (employeeMode && employeeId == null) {
            EmployeePicker(d.optJSONArray("employees")) { employeeId = it }
            return@Column
        }

        val rows = d.optJSONArray("trend").let { t ->
            (0 until (t?.length() ?: 0)).map { t!!.getJSONObject(it) }
        }
        val bucketsArr = d.optJSONArray("buckets")
        val buckets = (0 until (bucketsArr?.length() ?: 0)).map { bucketsArr!!.getString(it) }

        // Colour slots: the three biggest products over the whole window keep a
        // colour, everything else shares "Other". Stable while you browse
        // periods, so a colour never changes meaning under your finger.
        val bucketTotals = DoubleArray(buckets.size)
        rows.forEach { r ->
            r.optJSONArray("by_product").toDoubles().forEachIndexed { i, v ->
                if (i < bucketTotals.size) bucketTotals[i] += v
            }
        }
        val named = bucketTotals.indices.sortedByDescending { bucketTotals[it] }
            .filter { bucketTotals[it] > 0 }.take(NAMED_SERIES)
        val slotOf = { idx: Int -> named.indexOf(idx).let { if (it >= 0) it else NAMED_SERIES } }
        val hasOther = bucketTotals.indices.any { it !in named && bucketTotals[it] > 0 }
        val legend = named.map { buckets[it] } + if (hasOther) listOf("Other") else emptyList()
        val seriesOf = { r: JSONObject ->
            val out = DoubleArray(NAMED_SERIES + 1)
            r.optJSONArray("by_product").toDoubles().forEachIndexed { i, v -> out[slotOf(i)] += v }
            out.toList()
        }

        val selIdx = (rows.size - 1 - select).coerceIn(0, (rows.size - 1).coerceAtLeast(0))
        val cur = rows.getOrNull(selIdx)
        val prev = rows.getOrNull(selIdx - 1)
        val curLabel = listOf(d.optString("current_label"), d.optString("current_sublabel"))
            .filter { it.isNotEmpty() }.joinToString(" ")

        RefreshableBox(refreshing = loader.refreshing, onRefresh = { loader.reload() }) {
            Column(
                Modifier.fillMaxSize().verticalScroll(rememberScrollState())
                    .padding(horizontal = 16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                // ── Range + active drill-down ──
                Row(
                    Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    RANGES.forEachIndexed { i, r ->
                        FilterChip(
                            selected = rangeIdx == i,
                            onClick = { rangeIdx = i; select = 0 },
                            label = { Text(r.label, fontSize = rsp(13)) },
                        )
                    }
                    if (employeeId != null) {
                        FilterChip(
                            selected = true,
                            onClick = { employeeId = null },
                            label = { Text(d.optString("scope_name"), fontSize = rsp(13)) },
                            trailingIcon = {
                                Icon(
                                    Icons.Filled.Close,
                                    contentDescription =
                                        if (employeeMode) "Pick a different employee"
                                        else "Show the whole team again",
                                )
                            },
                        )
                    }
                }

                // ── The number, first ──
                HeroRow(
                    amount = cur?.optDouble("amount", 0.0) ?: 0.0,
                    count = cur?.optInt("count") ?: 0,
                    previous = prev?.optDouble("amount", 0.0),
                    label = curLabel,
                )

                // ── Trend ──
                Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                    Column(Modifier.padding(14.dp)) {
                        SeriesLegend(legend)
                        Spacer(Modifier.height(10.dp))
                        StackedBars(
                            rows = rows,
                            seriesOf = seriesOf,
                            selectedIndex = selIdx,
                            onSelect = { i -> select = rows.size - 1 - i },
                        )
                    }
                }

                // ── The selected period, by product ──
                val products = d.optJSONArray("products")
                SectionTitle(if (curLabel.isEmpty()) "By product" else "$curLabel · by product")
                if (products == null || products.length() == 0) {
                    Text(
                        "No approved sales in this period.",
                        color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = rsp(13),
                    )
                } else {
                    val total = (0 until products.length())
                        .sumOf { products.getJSONObject(it).optDouble("amount", 0.0) }
                    for (i in 0 until products.length()) {
                        val p = products.getJSONObject(i)
                        val name = p.optString("name")
                        val amt = p.optDouble("amount", 0.0)
                        ProductRow(
                            name = name,
                            amount = amt,
                            count = p.optInt("count"),
                            share = if (total > 0) (amt / total).toFloat() else 0f,
                            color = SeriesColors[slotOf(buckets.indexOf(name))],
                            onClick = {
                                onOpenWeb(
                                    "/clients/sales/?product=" +
                                        java.net.URLEncoder.encode(name, "UTF-8") +
                                        "&start_date=${d.optString("current_start")}" +
                                        "&end_date=${d.optString("current_end")}"
                                )
                            },
                        )
                    }
                }

                // ── Who brought it in ──
                val leaderboard = d.optJSONArray("leaderboard")
                if (leaderboard != null && leaderboard.length() > 0 && employeeId == null) {
                    SectionTitle("Leaderboard · ${curLabel.ifEmpty { "this period" }}")
                    for (i in 0 until leaderboard.length()) {
                        val e = leaderboard.getJSONObject(i)
                        LeaderRow(
                            rank = i + 1,
                            row = e,
                            series = seriesOf(e),
                            onClick = { employeeId = e.optInt("employee_id").takeIf { it > 0 } },
                        )
                    }
                }

                Spacer(Modifier.height(20.dp))
            }
        }
    }
}

/** Who to report on. Every active employee, not only those with sales in the
 * window — "nothing this quarter" is itself the answer you came for. */
@Composable
private fun EmployeePicker(employees: JSONArray?, onPick: (Int) -> Unit) {
    if (employees == null || employees.length() == 0) {
        EmptyState("👥", "No employees", "Nobody to report on yet.")
        return
    }
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(
            "Whose performance?",
            fontSize = rsp(13), color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        for (i in 0 until employees.length()) {
            val e = employees.getJSONObject(i)
            val name = e.optString("name")
            Card(
                modifier = Modifier.fillMaxWidth()
                    .clickable(onClickLabel = "Show $name") { onPick(e.optInt("id")) },
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            ) {
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 14.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(name, fontSize = rsp(15), fontWeight = FontWeight.Medium, modifier = Modifier.weight(1f))
                    Text("›", fontSize = rsp(15), color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
        Spacer(Modifier.height(20.dp))
    }
}

/** Amount, sales count and the change against the period before it. */
@Composable
private fun HeroRow(amount: Double, count: Int, previous: Double?, label: String) {
    val delta = if (previous != null && previous > 0) (amount - previous) / previous * 100 else null
    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 14.dp)) {
            Text(
                label.ifEmpty { "This period" },
                fontSize = rsp(12), color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(2.dp))
            Text(rupees(amount), fontSize = rsp(26), fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(4.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "$count ${if (count == 1) "sale" else "sales"}",
                    fontSize = rsp(12), color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (delta != null) {
                    Text(
                        "  ·  ${if (delta >= 0) "▲" else "▼"} ${"%.0f".format(kotlin.math.abs(delta))}% vs previous",
                        fontSize = rsp(12), fontWeight = FontWeight.SemiBold,
                        color = if (delta >= 0) StatusGreen else StatusRed,
                    )
                }
            }
        }
    }
}

/** Colour key — at most four entries, so it fits on one line. */
@Composable
private fun SeriesLegend(names: List<String>) {
    Row(
        Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        names.forEachIndexed { i, name ->
            Row(Modifier.weight(1f, fill = false), verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier.width(10.dp).height(10.dp)
                        .clip(RoundedCornerShape(3.dp))
                        .background(SeriesColors[i % SeriesColors.size]),
                )
                Spacer(Modifier.width(5.dp))
                Text(
                    name, fontSize = rsp(11), maxLines = 1,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

/**
 * One column per period, each split by product, tap to select. Columns keep a
 * readable width and the chart scrolls sideways instead of shrinking to
 * 12dp-wide slivers when you ask for three years.
 */
@Composable
private fun StackedBars(
    rows: List<JSONObject>,
    seriesOf: (JSONObject) -> List<Double>,
    selectedIndex: Int,
    onSelect: (Int) -> Unit,
    plotHeight: Dp = 150.dp,
) {
    if (rows.isEmpty()) {
        Text("No data.", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = rsp(13))
        return
    }
    val scroll = rememberScrollState()
    LaunchedEffect(rows.size) { scroll.scrollTo(scroll.maxValue) }   // open on the latest
    val maxAmount = rows.maxOf { it.optDouble("amount", 0.0) }
    val divider = MaterialTheme.colorScheme.surface
    val selectedTint = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.6f)

    Row(Modifier.fillMaxWidth().horizontalScroll(scroll)) {
        rows.forEachIndexed { i, r ->
            val amount = r.optDouble("amount", 0.0)
            val label = r.optString("label")
            val selected = i == selectedIndex
            Column(
                Modifier
                    .width(rdp(46))
                    .clip(RoundedCornerShape(6.dp))
                    .background(if (selected) selectedTint else Color.Transparent)
                    .clickable(onClickLabel = "Show $label") { onSelect(i) }
                    .semantics { contentDescription = "$label: ${compactRupees(amount)}" }
                    .padding(vertical = 4.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text(
                    if (amount > 0) compactRupees(amount) else "–",
                    fontSize = rsp(9), fontWeight = FontWeight.SemiBold, maxLines = 1,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(4.dp))
                Box(
                    Modifier.width(rdp(26)).height(plotHeight),
                    contentAlignment = Alignment.BottomCenter,
                ) {
                    val barHeight = if (maxAmount > 0) plotHeight * (amount / maxAmount).toFloat() else 0.dp
                    Column(
                        Modifier.fillMaxWidth().height(barHeight)
                            .clip(RoundedCornerShape(topStart = 5.dp, topEnd = 5.dp)),
                    ) {
                        val series = seriesOf(r)
                        val visible = series.indices.reversed().filter { series[it] > 0 }
                        if (visible.isNotEmpty() && amount > 0) {
                            visible.forEachIndexed { pos, idx ->
                                if (pos > 0) Box(Modifier.fillMaxWidth().height(2.dp).background(divider))
                                Box(
                                    Modifier.fillMaxWidth()
                                        .height(barHeight * (series[idx] / amount).toFloat())
                                        .background(SeriesColors[idx % SeriesColors.size]),
                                )
                            }
                        } else if (amount > 0) {
                            Box(Modifier.fillMaxSize().background(SeriesColors[0]))
                        }
                    }
                }
                Spacer(Modifier.height(4.dp))
                Text(
                    label, fontSize = rsp(10), maxLines = 1,
                    fontWeight = if (selected) FontWeight.Bold else FontWeight.SemiBold,
                )
                val sub = r.optString("sublabel")
                if (sub.isNotEmpty()) {
                    Text(sub, fontSize = rsp(9), color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
                }
            }
        }
    }
}

/** A product's business in the selected period — tap opens the sales behind it. */
@Composable
private fun ProductRow(
    name: String,
    amount: Double,
    count: Int,
    share: Float,
    color: Color,
    onClick: () -> Unit,
) {
    Card(
        modifier = Modifier.fillMaxWidth()
            .clickable(onClickLabel = "Open $name sales") { onClick() },
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
    ) {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 10.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text(name, fontWeight = FontWeight.SemiBold, fontSize = rsp(14))
                Text(rupees(amount), fontSize = rsp(13), fontWeight = FontWeight.Bold)
            }
            Spacer(Modifier.height(6.dp))
            Box(
                Modifier.fillMaxWidth().height(8.dp)
                    .clip(RoundedCornerShape(4.dp))
                    .background(MaterialTheme.colorScheme.surfaceVariant),
            ) {
                Box(Modifier.fillMaxWidth(share.coerceIn(0f, 1f)).fillMaxHeight().background(color))
            }
            Spacer(Modifier.height(4.dp))
            Text(
                "${"%.0f".format(share * 100)}% of the period · $count ${if (count == 1) "sale" else "sales"}",
                fontSize = rsp(11), color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

/** A leaderboard row — tap retells the whole report for that person. */
@Composable
private fun LeaderRow(rank: Int, row: JSONObject, series: List<Double>, onClick: () -> Unit) {
    val name = row.optString("name")
    Card(
        modifier = Modifier.fillMaxWidth()
            .clickable(onClickLabel = "Show only $name") { onClick() },
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
    ) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 10.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        "$rank", fontSize = rsp(13), fontWeight = FontWeight.Bold,
                        color = if (rank == 1) BrandGoldDark else MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(end = 10.dp),
                    )
                    Text(name, fontSize = rsp(14), fontWeight = FontWeight.Medium)
                }
                Column(horizontalAlignment = Alignment.End) {
                    Text(rupees(row.optDouble("amount", 0.0)), fontSize = rsp(14), fontWeight = FontWeight.Bold)
                    Text(
                        "%.0f pts".format(row.optDouble("points", 0.0)),
                        fontSize = rsp(11), color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            if (series.sum() > 0) {
                Spacer(Modifier.height(8.dp))
                Row(Modifier.fillMaxWidth().height(7.dp).clip(RoundedCornerShape(4.dp))) {
                    series.forEachIndexed { i, v ->
                        if (v > 0) {
                            Box(
                                Modifier.fillMaxHeight().weight(v.toFloat())
                                    .background(SeriesColors[i % SeriesColors.size]),
                            )
                        }
                    }
                }
            }
        }
    }
}

/** Indian-style compact money: 1,24,000 → "1.24L", 24,000 → "24K",
 * 3,50,00,000 → "3.5Cr". Up to 2 decimals, trailing zeros trimmed. */
private fun compactRupees(v: Double): String {
    fun trim(x: Double): String =
        String.format(java.util.Locale.US, "%.2f", x).trimEnd('0').trimEnd('.')
    return when {
        v >= 1_00_00_000 -> trim(v / 1_00_00_000) + "Cr"
        v >= 1_00_000 -> trim(v / 1_00_000) + "L"
        v >= 1_000 -> trim(v / 1_000) + "K"
        else -> trim(v)
    }
}
