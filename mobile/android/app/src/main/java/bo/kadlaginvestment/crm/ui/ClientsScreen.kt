package bo.kadlaginvestment.crm.ui

import android.content.Intent
import android.net.Uri
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import bo.kadlaginvestment.crm.net.ApiClient
import kotlinx.coroutines.delay
import org.json.JSONObject

/** Native Clients: My/All list with search → tap for the client profile. */
@Composable
fun ClientsScreen(
    modifier: Modifier = Modifier,
    onSessionExpired: () -> Unit,
    onOpenWeb: (String) -> Unit,
) {
    var selectedClientId by remember { mutableStateOf<Int?>(null) }

    if (selectedClientId != null) {
        BackHandler { selectedClientId = null }
        ClientDetail(
            clientId = selectedClientId!!,
            modifier = modifier,
            onBack = { selectedClientId = null },
            onSessionExpired = onSessionExpired,
            onOpenWeb = onOpenWeb,
        )
        return
    }

    var scopeMy by remember { mutableStateOf(true) }
    var q by remember { mutableStateOf("") }
    var rows by remember { mutableStateOf(listOf<JSONObject>()) }
    var page by remember { mutableIntStateOf(1) }
    var hasMore by remember { mutableStateOf(false) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var reloadKey by remember { mutableIntStateOf(0) }

    LaunchedEffect(scopeMy, q, page, reloadKey) {
        loading = true
        error = null
        if (q.isNotEmpty()) delay(350) // debounce typing
        val path = "/clients/api/app/clients/?scope=" + (if (scopeMy) "my" else "all") +
            "&page=$page&q=" + java.net.URLEncoder.encode(q, "UTF-8")
        when (val r = ApiClient.get(path)) {
            is ApiClient.Result.Ok -> {
                val arr = r.json.optJSONArray("results")
                val newRows = (0 until (arr?.length() ?: 0)).map { arr!!.getJSONObject(it) }
                rows = if (page == 1) newRows else rows + newRows
                hasMore = r.json.optBoolean("has_more")
            }
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
        loading = false
    }

    Column(modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        Row(
            Modifier.fillMaxWidth().padding(top = 16.dp, bottom = 8.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("Clients", fontSize = 22.sp, fontWeight = FontWeight.Bold)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Chip("My", scopeMy) { scopeMy = true; page = 1 }
                Chip("All", !scopeMy) { scopeMy = false; page = 1 }
            }
        }

        OutlinedTextField(
            value = q,
            onValueChange = { q = it; page = 1 },
            label = { Text("Search name / phone / PAN") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
        )

        when {
            error != null -> ErrorBox(error!!) { reloadKey++ }
            loading && rows.isEmpty() -> LoadingBox()
            else -> LazyColumn(
                Modifier.fillMaxSize(),
                verticalArrangement = Arrangement.spacedBy(8.dp),
                contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 12.dp),
            ) {
                if (rows.isEmpty()) {
                    item {
                        Text(
                            if (scopeMy) "No clients mapped to you yet." else "No clients found.",
                            color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp,
                        )
                    }
                }
                items(rows) { c ->
                    Card(
                        modifier = Modifier.fillMaxWidth().clickable { selectedClientId = c.getInt("id") },
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
                    ) {
                        Row(
                            Modifier.fillMaxWidth().padding(14.dp),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Column(Modifier.weight(1f)) {
                                Text(c.optString("name"), fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
                                Text(
                                    listOf(c.optString("phone"), c.optString("mapped_to"))
                                        .filter { it.isNotEmpty() }.joinToString(" · "),
                                    fontSize = 12.sp,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            Text("#${c.optInt("id")}", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
                if (hasMore) {
                    item {
                        TextButton(onClick = { page += 1 }, modifier = Modifier.fillMaxWidth()) {
                            Text(if (loading) "Loading…" else "Load more")
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun ClientDetail(
    clientId: Int,
    modifier: Modifier,
    onBack: () -> Unit,
    onSessionExpired: () -> Unit,
    onOpenWeb: (String) -> Unit,
) {
    val context = LocalContext.current
    var data by remember { mutableStateOf<JSONObject?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var reloadKey by remember { mutableIntStateOf(0) }

    LaunchedEffect(clientId, reloadKey) {
        when (val r = ApiClient.get("/clients/api/app/clients/$clientId/")) {
            is ApiClient.Result.Ok -> data = r.json
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
    }

    if (error != null) { ErrorBox(error!!, modifier) { error = null; reloadKey++ }; return }
    val d = data ?: run { LoadingBox(modifier); return }

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
            Text(d.optString("name"), fontSize = 20.sp, fontWeight = FontWeight.Bold)
        }

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            val phone = d.optString("phone")
            if (phone.isNotEmpty()) {
                Button(onClick = {
                    context.startActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:$phone")))
                }) { Text("📞 Call") }
                OutlinedButton(onClick = {
                    val digits = phone.filter { it.isDigit() }.let { if (it.length == 10) "91$it" else it }
                    context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://wa.me/$digits")))
                }) { Text("WhatsApp") }
            }
            OutlinedButton(onClick = { onOpenWeb("/clients/$clientId/edit/") }) { Text("Edit") }
        }

        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
            Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                InfoRow("Phone", d.optString("phone"))
                InfoRow("Email", d.optString("email"))
                InfoRow("PAN", d.optString("pan"))
                InfoRow("Address", d.optString("address"))
                InfoRow("Mapped to", d.optString("mapped_to"))
                val products = buildList {
                    if (d.optBoolean("sip_status")) add("SIP")
                    if (d.optBoolean("health_status")) add("Health")
                    if (d.optBoolean("life_status")) add("Life")
                }
                InfoRow("Active products", if (products.isEmpty()) "—" else products.joinToString(", "))
                val ren = d.optJSONObject("renewals")
                InfoRow(
                    "Renewals",
                    "${ren?.optInt("count") ?: 0} · ${rupees(ren?.optDouble("premium", 0.0) ?: 0.0)} premium",
                )
            }
        }

        SectionTitle("Recent sales")
        val sales = d.optJSONArray("recent_sales")
        if (sales == null || sales.length() == 0) {
            Text("No sales for this client yet.", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp)
        } else {
            for (i in 0 until sales.length()) {
                val s = sales.getJSONObject(i)
                Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                    Row(
                        Modifier.fillMaxWidth().padding(12.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column {
                            Text(s.optString("product"), fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                            Text(s.optString("date"), fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        Column(horizontalAlignment = Alignment.End) {
                            Text(rupees(s.optDouble("amount", 0.0)), fontWeight = FontWeight.Bold, fontSize = 14.sp)
                            StatusPill(s.optString("status"))
                        }
                    }
                }
                Spacer(Modifier.height(4.dp))
            }
        }
        Spacer(Modifier.height(20.dp))
    }
}

@Composable
private fun InfoRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth()) {
        Text(label, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(0.35f))
        Text(if (value.isEmpty()) "—" else value, fontSize = 13.sp, modifier = Modifier.weight(0.65f))
    }
}
