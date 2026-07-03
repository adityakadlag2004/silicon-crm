package bo.kadlaginvestment.crm.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
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
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import bo.kadlaginvestment.crm.net.ApiClient
import org.json.JSONObject

/** Native Reports: 6-month business trend (bar chart), product mix for the
 * current month, and (admins/managers) the employee leaderboard. */
@Composable
fun ReportsScreen(
    modifier: Modifier = Modifier,
    onBack: () -> Unit,
    onSessionExpired: () -> Unit,
) {
    BackHandler(onBack = onBack)

    var data by remember { mutableStateOf<JSONObject?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var reloadKey by remember { mutableIntStateOf(0) }

    LaunchedEffect(reloadKey) {
        when (val r = ApiClient.get("/clients/api/app/reports/summary/")) {
            is ApiClient.Result.Ok -> data = r.json
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
    }

    if (error != null) { ErrorBox(error!!, modifier) { error = null; reloadKey++ }; return }
    val d = data ?: run { LoadingBox(modifier); return }

    val trend = d.optJSONArray("trend")
    val months = (0 until (trend?.length() ?: 0)).map { trend!!.getJSONObject(it) }
    val products = d.optJSONArray("products")
    val leaderboard = d.optJSONArray("leaderboard")

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
                if (d.optBoolean("firm_wide")) "Business Reports" else "My Performance",
                fontSize = 22.sp, fontWeight = FontWeight.Bold,
            )
        }

        SectionTitle("Last 6 months · approved business")
        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
            Column(Modifier.padding(16.dp)) {
                MonthBarChart(months)
                Spacer(Modifier.height(8.dp))
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    months.forEach { m ->
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Text(m.optString("label"), fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
                            Text(
                                compactRupees(m.optDouble("amount", 0.0)),
                                fontSize = 9.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }
        }

        SectionTitle("This month by product")
        if (products == null || products.length() == 0) {
            Text("No approved sales this month yet.", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp)
        } else {
            val maxAmt = (0 until products.length()).maxOf { products.getJSONObject(it).optDouble("amount", 0.0) }
            for (i in 0 until products.length()) {
                val p = products.getJSONObject(i)
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
                        HBar(fraction = if (maxAmt > 0) (p.optDouble("amount", 0.0) / maxAmt).toFloat() else 0f)
                    }
                }
            }
        }

        if (leaderboard != null && leaderboard.length() > 0) {
            SectionTitle("Employee leaderboard · this month")
            for (i in 0 until leaderboard.length()) {
                val e = leaderboard.getJSONObject(i)
                Row(
                    Modifier.fillMaxWidth().padding(vertical = 6.dp),
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
            }
        }

        Spacer(Modifier.height(20.dp))
    }
}

@Composable
private fun MonthBarChart(months: List<JSONObject>) {
    val barColor = MaterialTheme.colorScheme.primary
    val trackColor = MaterialTheme.colorScheme.surfaceVariant
    val maxAmount = months.maxOfOrNull { it.optDouble("amount", 0.0) } ?: 0.0

    Canvas(Modifier.fillMaxWidth().height(140.dp)) {
        if (months.isEmpty()) return@Canvas
        val slot = size.width / months.size
        val barWidth = slot * 0.55f
        months.forEachIndexed { i, m ->
            val x = i * slot + (slot - barWidth) / 2
            // Track (full height, subtle)
            drawRoundRect(
                color = trackColor,
                topLeft = Offset(x, 0f),
                size = Size(barWidth, size.height),
                cornerRadius = androidx.compose.ui.geometry.CornerRadius(8f, 8f),
            )
            val amount = m.optDouble("amount", 0.0)
            if (maxAmount > 0 && amount > 0) {
                val h = (amount / maxAmount * size.height).toFloat().coerceAtLeast(6f)
                drawRoundRect(
                    color = barColor,
                    topLeft = Offset(x, size.height - h),
                    size = Size(barWidth, h),
                    cornerRadius = androidx.compose.ui.geometry.CornerRadius(8f, 8f),
                )
            }
        }
    }
}

@Composable
private fun HBar(fraction: Float) {
    val barColor = MaterialTheme.colorScheme.primary
    val trackColor = MaterialTheme.colorScheme.surfaceVariant
    Canvas(Modifier.fillMaxWidth().height(8.dp)) {
        drawRoundRect(
            color = trackColor, size = size,
            cornerRadius = androidx.compose.ui.geometry.CornerRadius(6f, 6f),
        )
        drawRoundRect(
            color = barColor,
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
