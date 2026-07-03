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

        item {
            Text(
                "Recent sales",
                fontSize = 16.sp,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(top = 6.dp),
            )
        }

        if (recent == null || recent.length() == 0) {
            item {
                Text(
                    "No sales yet — add one from the Add Sale tab.",
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    fontSize = 13.sp,
                )
            }
        } else {
            val rows = (0 until recent.length()).map { recent.getJSONObject(it) }
            items(rows) { s -> SaleRow(s, isAdmin) }
        }

        item { Spacer(Modifier.height(16.dp)) }
    }
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
