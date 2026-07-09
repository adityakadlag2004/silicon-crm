package bo.kadlaginvestment.crm.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Canvas
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
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.LinearProgressIndicator
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import bo.kadlaginvestment.crm.net.ApiClient
import org.json.JSONArray
import org.json.JSONObject

/** Palette shared with the web Business Overview (clients/views/reports.py). */
private val OverviewColors = listOf(
    Color(0xFFE5B740), Color(0xFF3B82F6), Color(0xFF10B981), Color(0xFFEF4444),
    Color(0xFF8B5CF6), Color(0xFFF97316), Color(0xFF14B8A6), Color(0xFFEC4899),
)

private val PERIOD_OPTIONS = listOf(
    "month" to "Month", "quarter" to "Quarter", "half" to "Half-yr", "year" to "Year",
)
private val COLUMN_OPTIONS = listOf(3, 6, 9, 12, 18, 24)

private fun JSONArray?.toDoubles(): List<Double> =
    (0 until (this?.length() ?: 0)).map { this!!.optDouble(it, 0.0) }

/** Native Business Overview: period-grouped business trend split by product
 * (stacked bars), product mix for the latest period, and (admins/managers)
 * the employee leaderboard with the same product bifurcation. */
@Composable
fun ReportsScreen(
    modifier: Modifier = Modifier,
    onBack: () -> Unit,
    onSessionExpired: () -> Unit,
) {
    BackHandler(onBack = onBack)

    var period by remember { mutableStateOf("month") }
    var columns by remember { mutableIntStateOf(6) }
    var data by remember { mutableStateOf<JSONObject?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    var reloadKey by remember { mutableIntStateOf(0) }

    LaunchedEffect(period, columns, reloadKey) {
        loading = true
        error = null
        when (val r = ApiClient.get("/clients/api/app/reports/summary/?period=$period&columns=$columns")) {
            is ApiClient.Result.Ok -> data = r.json
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
        loading = false
    }

    if (error != null && data == null) { ErrorBox(error!!, modifier) { reloadKey++ }; return }
    val d = data ?: run { LoadingBox(modifier); return }

    val trend = d.optJSONArray("trend")
    val rows = (0 until (trend?.length() ?: 0)).map { trend!!.getJSONObject(it) }
    val bucketsArr = d.optJSONArray("buckets")
    val buckets = (0 until (bucketsArr?.length() ?: 0)).map { bucketsArr!!.getString(it) }
    val products = d.optJSONArray("products")
    val leaderboard = d.optJSONArray("leaderboard")
    val curLabel = listOf(d.optString("current_label"), d.optString("current_sublabel"))
        .filter { it.isNotEmpty() }.joinToString(" ")

    Column(
        modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                "← Back",
                color = MaterialTheme.colorScheme.secondary,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.clickable(onClick = onBack).padding(end = 12.dp),
            )
            Text(
                if (d.optBoolean("firm_wide")) "Business Overview" else "My Performance",
                fontSize = 22.sp, fontWeight = FontWeight.Bold,
            )
        }

        // ── Period + column controls ──
        Row(
            Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            PERIOD_OPTIONS.forEach { (key, label) ->
                FilterChip(
                    selected = period == key,
                    onClick = { period = key },
                    label = { Text(label, fontSize = 13.sp) },
                )
            }
            var colMenu by remember { mutableStateOf(false) }
            Box {
                AssistChip(onClick = { colMenu = true }, label = { Text("$columns cols ▾", fontSize = 13.sp) })
                DropdownMenu(expanded = colMenu, onDismissRequest = { colMenu = false }) {
                    COLUMN_OPTIONS.forEach { n ->
                        DropdownMenuItem(text = { Text("$n columns") }, onClick = { columns = n; colMenu = false })
                    }
                }
            }
        }

        if (loading) LinearProgressIndicator(Modifier.fillMaxWidth())

        // ── Product-split trend ──
        SectionTitle("Approved business · by product")
        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
            Column(Modifier.padding(16.dp)) {
                BucketLegend(buckets)
                Spacer(Modifier.height(10.dp))
                StackedBars(rows, buckets)
            }
        }

        // ── Latest-period product mix ──
        SectionTitle(if (curLabel.isEmpty()) "By product" else "$curLabel · by product")
        if (products == null || products.length() == 0) {
            Text("No approved sales in this period yet.", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp)
        } else {
            val maxAmt = (0 until products.length()).maxOf { products.getJSONObject(it).optDouble("amount", 0.0) }
            for (i in 0 until products.length()) {
                val p = products.getJSONObject(i)
                val color = buckets.indexOf(p.optString("name")).let { if (it >= 0) OverviewColors[it % OverviewColors.size] else MaterialTheme.colorScheme.primary }
                Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                    Column(Modifier.padding(horizontal = 14.dp, vertical = 10.dp)) {
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                            Text(p.optString("name"), fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                            Text(
                                "${rupees(p.optDouble("amount", 0.0))} · ${p.optInt("count")}",
                                fontSize = 13.sp, fontWeight = FontWeight.Bold,
                            )
                        }
                        Spacer(Modifier.height(6.dp))
                        HBar(fraction = if (maxAmt > 0) (p.optDouble("amount", 0.0) / maxAmt).toFloat() else 0f, color = color)
                    }
                }
            }
        }

        // ── Leaderboard with product bifurcation ──
        if (leaderboard != null && leaderboard.length() > 0) {
            SectionTitle(if (curLabel.isEmpty()) "Employee leaderboard" else "Leaderboard · $curLabel")
            for (i in 0 until leaderboard.length()) {
                val e = leaderboard.getJSONObject(i)
                val byProduct = e.optJSONArray("by_product").toDoubles()
                Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                    Column(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 10.dp)) {
                        Row(
                            Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(
                                    "${i + 1}",
                                    fontSize = 13.sp, fontWeight = FontWeight.Bold,
                                    color = if (i == 0) BrandGoldDark else MaterialTheme.colorScheme.onSurfaceVariant,
                                    modifier = Modifier.padding(end = 10.dp),
                                )
                                Text(e.optString("name"), fontSize = 14.sp, fontWeight = FontWeight.Medium)
                            }
                            Column(horizontalAlignment = Alignment.End) {
                                Text(rupees(e.optDouble("amount", 0.0)), fontSize = 14.sp, fontWeight = FontWeight.Bold)
                                Text(
                                    "%.1f pts".format(e.optDouble("points", 0.0)),
                                    fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                        if (byProduct.sum() > 0) {
                            Spacer(Modifier.height(8.dp))
                            StackedLine(byProduct, Modifier.fillMaxWidth())
                        }
                    }
                }
            }
        }

        Spacer(Modifier.height(20.dp))
    }
}

/** Colored dot + name for each product bucket, wrapping horizontally. */
@Composable
private fun BucketLegend(buckets: List<String>) {
    Row(
        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        buckets.forEachIndexed { i, name ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier.width(11.dp).height(11.dp)
                        .clip(RoundedCornerShape(3.dp))
                        .background(OverviewColors[i % OverviewColors.size]),
                )
                Spacer(Modifier.width(5.dp))
                Text(name, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

/** Horizontally-scrollable stacked bars: one column per period, each split
 * into product segments (bottom-up, index-aligned to [buckets]). */
@Composable
private fun StackedBars(
    rows: List<JSONObject>,
    buckets: List<String>,
    plotHeight: Dp = 150.dp,
) {
    if (rows.isEmpty()) {
        Text("No data.", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp)
        return
    }
    val maxAmount = rows.maxOf { it.optDouble("amount", 0.0) }
    val dividerColor = MaterialTheme.colorScheme.surface
    Row(
        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        rows.forEach { r ->
            val amount = r.optDouble("amount", 0.0)
            val byProduct = r.optJSONArray("by_product").toDoubles()
            val barHeight = if (maxAmount > 0) plotHeight * (amount / maxAmount).toFloat() else 0.dp
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text(
                    if (amount > 0) compactRupees(amount) else "–",
                    fontSize = 9.sp, fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(4.dp))
                Box(Modifier.width(28.dp).height(plotHeight), contentAlignment = Alignment.BottomCenter) {
                    Column(
                        Modifier.width(28.dp).height(barHeight)
                            .clip(RoundedCornerShape(topStart = 6.dp, topEnd = 6.dp)),
                    ) {
                        // Highest bucket index on top, bucket 0 at the bottom,
                        // with a thin divider line between each compartment.
                        val visible = byProduct.indices.reversed().filter { byProduct[it] > 0 }
                        if (visible.isNotEmpty() && amount > 0) {
                            visible.forEachIndexed { pos, idx ->
                                if (pos > 0) {
                                    Box(Modifier.fillMaxWidth().height(2.dp).background(dividerColor))
                                }
                                Box(
                                    Modifier.fillMaxWidth()
                                        .height(barHeight * (byProduct[idx] / amount).toFloat())
                                        .background(OverviewColors[idx % OverviewColors.size]),
                                )
                            }
                        } else if (amount > 0) {
                            // No product split from the server (e.g. backend not
                            // yet updated) — still show a solid column so the
                            // trend is never invisible.
                            Box(Modifier.fillMaxSize().background(OverviewColors[0]))
                        }
                    }
                }
                Spacer(Modifier.height(4.dp))
                Text(r.optString("label"), fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
                val sub = r.optString("sublabel")
                if (sub.isNotEmpty()) {
                    Text(sub, fontSize = 9.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    }
}

/** Thin horizontal stacked bar summarizing a product split (leaderboard row). */
@Composable
private fun StackedLine(values: List<Double>, modifier: Modifier = Modifier) {
    if (values.sum() <= 0) return
    Row(modifier.height(7.dp).clip(RoundedCornerShape(4.dp))) {
        values.forEachIndexed { i, v ->
            if (v > 0) {
                Box(
                    Modifier.fillMaxHeight().weight(v.toFloat())
                        .background(OverviewColors[i % OverviewColors.size]),
                )
            }
        }
    }
}

@Composable
private fun HBar(fraction: Float, color: Color = MaterialTheme.colorScheme.primary) {
    val trackColor = MaterialTheme.colorScheme.surfaceVariant
    Canvas(Modifier.fillMaxWidth().height(8.dp)) {
        drawRoundRect(
            color = trackColor, size = size,
            cornerRadius = androidx.compose.ui.geometry.CornerRadius(6f, 6f),
        )
        drawRoundRect(
            color = color,
            size = Size(size.width * fraction.coerceIn(0f, 1f), size.height),
            cornerRadius = androidx.compose.ui.geometry.CornerRadius(6f, 6f),
        )
    }
}

private fun compactRupees(v: Double): String = when {
    v >= 1_00_00_000 -> "%.1fCr".format(v / 1_00_00_000)
    v >= 1_00_000 -> "%.1fL".format(v / 1_00_000)
    v >= 1_000 -> "%.0fK".format(v / 1_000)
    else -> "%.0f".format(v)
}
