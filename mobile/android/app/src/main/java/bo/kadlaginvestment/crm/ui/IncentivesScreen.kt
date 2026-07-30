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
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import bo.kadlaginvestment.crm.net.ApiClient
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

/** Native Incentive Rules + Campaigns builders (admin-only).
 * Reads via /api/app/incentives|campaigns; writes reuse the existing web
 * AJAX endpoints, so behavior (validation, overlap rules) is identical. */
@Composable
fun IncentivesScreen(
    modifier: Modifier = Modifier,
    onBack: () -> Unit,
    onSessionExpired: () -> Unit,
) {
    BackHandler(onBack = onBack)
    var tab by remember { mutableStateOf(0) } // 0 = rules, 1 = campaigns

    Column(modifier.fillMaxSize().padding(horizontal = rdp(16))) {
        ScreenHeader("Incentives", onBack = onBack)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Chip("Rules", tab == 0) { tab = 0 }
            Chip("Campaigns", tab == 1) { tab = 1 }
        }
        Spacer(Modifier.height(8.dp))
        if (tab == 0) RulesTab(onSessionExpired) else CampaignsTab(onSessionExpired)
    }
}

// ── Rules tab ────────────────────────────────────────────────────────────────

@Composable
private fun RulesTab(onSessionExpired: () -> Unit) {
    val scope = rememberCoroutineScope()
    var data by remember { mutableStateOf<JSONObject?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var actionError by remember { mutableStateOf<String?>(null) }
    var reloadKey by remember { mutableIntStateOf(0) }
    var addingRule by remember { mutableStateOf(false) }
    var editRule by remember { mutableStateOf<JSONObject?>(null) }
    var slabFor by remember { mutableStateOf<JSONObject?>(null) } // rule to add slab to

    fun mutate(path: String, body: JSONObject = JSONObject()) {
        scope.launch {
            actionError = null
            when (val r = ApiClient.post(path, body)) {
                is ApiClient.Result.Ok -> { data = null; reloadKey++ }
                is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                is ApiClient.Result.Error -> actionError = r.message
            }
        }
    }

    LaunchedEffect(reloadKey) {
        when (val r = ApiClient.get("/clients/api/app/incentives/")) {
            is ApiClient.Result.Ok -> data = r.json
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
    }
    if (error != null) { ErrorBox(error!!) { error = null; reloadKey++ }; return }
    val d = data ?: run { LoadingBox(); return }
    val rules = d.optJSONArray("rules") ?: JSONArray()
    val available = d.optJSONArray("available_products") ?: JSONArray()

    if (addingRule) {
        RuleDialog(
            title = "New incentive rule",
            products = available,
            onDismiss = { addingRule = false },
            onSave = { productId, unit, points ->
                addingRule = false
                mutate("/clients/incentives/rule/add/", JSONObject()
                    .put("product_id", productId).put("unit_amount", unit).put("points_per_unit", points))
            },
        )
    }
    editRule?.let { rule ->
        RuleDialog(
            title = "Edit — ${rule.optString("product")}",
            products = null,
            initialUnit = "%.0f".format(rule.optDouble("unit_amount", 0.0)),
            initialPoints = "%.3f".format(rule.optDouble("points_per_unit", 0.0)),
            onDismiss = { editRule = null },
            onSave = { _, unit, points ->
                val id = rule.getInt("id")
                editRule = null
                mutate("/clients/incentives/rule/$id/update/", JSONObject()
                    .put("unit_amount", unit).put("points_per_unit", points))
            },
        )
    }
    slabFor?.let { rule ->
        SlabDialog(
            title = "New slab — ${rule.optString("product")}",
            payoutIsPercent = rule.optString("slab_unit") == "percent",
            onDismiss = { slabFor = null },
            onSave = { threshold, payout, label ->
                val id = rule.getInt("id")
                slabFor = null
                mutate("/clients/incentives/rule/$id/slab/add/", JSONObject()
                    .put("threshold", threshold).put("payout", payout).put("label", label))
            },
        )
    }

    actionError?.let { Text(it, color = StatusRed, fontSize = rsp(13), fontWeight = FontWeight.SemiBold) }

    LazyColumn(
        Modifier.fillMaxSize(),
        verticalArrangement = Arrangement.spacedBy(8.dp),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 8.dp),
    ) {
        item {
            Button(onClick = { addingRule = true }, enabled = available.length() > 0) {
                Text(if (available.length() > 0) "＋ Add rule" else "All products have rules")
            }
        }
        items((0 until rules.length()).map { rules.getJSONObject(it) }) { rule ->
            Card(
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
            ) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(rule.optString("product"), fontWeight = FontWeight.Bold, fontSize = rsp(15))
                        Switch(
                            checked = rule.optBoolean("active"),
                            onCheckedChange = { on ->
                                mutate("/clients/incentives/rule/${rule.getInt("id")}/update/",
                                    JSONObject().put("active", on))
                            },
                        )
                    }
                    val isPercent = rule.optString("slab_unit") == "percent"
                    val perFy = rule.optString("slab_period") == "fy"
                    Text(
                        if (isPercent)
                            "Rate bands on the seller's own ${if (perFy) "yearly" else "monthly"} volume"
                        else
                            "%.3f pts per %s".format(
                                rule.optDouble("points_per_unit", 0.0),
                                rupees(rule.optDouble("unit_amount", 0.0)),
                            ),
                        fontSize = rsp(13), color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    if (isPercent && !rule.isNull("port_percent")) {
                        Text(
                            "Port: %.2f%% flat, outside the bands".format(rule.optDouble("port_percent", 0.0)),
                            fontSize = rsp(12), color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    if (!isPercent && (rule.optJSONArray("slabs")?.length() ?: 0) > 0) {
                        Text(
                            "Plus a ${if (perFy) "Apr–Mar" else "monthly"} bonus ladder — each rung is the " +
                                "total earned by then, so only the difference is released.",
                            fontSize = rsp(12), color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    val slabs = rule.optJSONArray("slabs") ?: JSONArray()
                    for (i in 0 until slabs.length()) {
                        val s = slabs.getJSONObject(i)
                        Row(
                            Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(
                                (if (isPercent)
                                    "${rupees(s.optDouble("threshold", 0.0))} and up → %.2f%%".format(
                                        s.optDouble("payout", 0.0))
                                else
                                    "Reach ${rupees(s.optDouble("threshold", 0.0))} → ${
                                        "%.0f".format(s.optDouble("payout", 0.0))} pts") +
                                    (s.optString("label").takeIf { it.isNotEmpty() }?.let { " ($it)" } ?: ""),
                                fontSize = rsp(12),
                            )
                            Text(
                                "✕",
                                color = StatusRed, fontSize = rsp(14),
                                modifier = Modifier.clickable {
                                    mutate("/clients/incentives/slab/${s.getInt("id")}/delete/")
                                }.padding(4.dp),
                            )
                        }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        Text("Edit", color = MaterialTheme.colorScheme.secondary, fontSize = rsp(13),
                            fontWeight = FontWeight.SemiBold,
                            modifier = Modifier.clickable { editRule = rule }.padding(4.dp))
                        Text("＋ Slab", color = MaterialTheme.colorScheme.secondary, fontSize = rsp(13),
                            fontWeight = FontWeight.SemiBold,
                            modifier = Modifier.clickable { slabFor = rule }.padding(4.dp))
                        Text("Delete", color = StatusRed, fontSize = rsp(13),
                            fontWeight = FontWeight.SemiBold,
                            modifier = Modifier.clickable {
                                mutate("/clients/incentives/rule/${rule.getInt("id")}/delete/")
                            }.padding(4.dp))
                    }
                }
            }
        }
        item { Spacer(Modifier.height(12.dp)) }
    }
}

// ── Campaigns tab ────────────────────────────────────────────────────────────

@Composable
private fun CampaignsTab(onSessionExpired: () -> Unit) {
    val scope = rememberCoroutineScope()
    var data by remember { mutableStateOf<JSONObject?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var actionError by remember { mutableStateOf<String?>(null) }
    var reloadKey by remember { mutableIntStateOf(0) }
    var addingCampaign by remember { mutableStateOf(false) }
    var addProductTo by remember { mutableStateOf<JSONObject?>(null) }
    var slabForCp by remember { mutableStateOf<JSONObject?>(null) }

    fun mutate(path: String, body: JSONObject = JSONObject()) {
        scope.launch {
            actionError = null
            when (val r = ApiClient.post(path, body)) {
                is ApiClient.Result.Ok -> { data = null; reloadKey++ }
                is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                is ApiClient.Result.Error -> actionError = r.message
            }
        }
    }

    LaunchedEffect(reloadKey) {
        when (val r = ApiClient.get("/clients/api/app/campaigns/")) {
            is ApiClient.Result.Ok -> data = r.json
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
    }
    if (error != null) { ErrorBox(error!!) { error = null; reloadKey++ }; return }
    val d = data ?: run { LoadingBox(); return }
    val campaigns = d.optJSONArray("campaigns") ?: JSONArray()
    val products = d.optJSONArray("products") ?: JSONArray()

    if (addingCampaign) {
        CampaignDialog(
            onDismiss = { addingCampaign = false },
            onSave = { name, desc, start, end ->
                addingCampaign = false
                mutate("/clients/campaigns/add/", JSONObject()
                    .put("name", name).put("description", desc)
                    .put("start_date", start).put("end_date", end))
            },
        )
    }
    addProductTo?.let { campaign ->
        CampaignProductDialog(
            products = products,
            onDismiss = { addProductTo = null },
            onSave = { productId, benefitType, unit, points ->
                val id = campaign.getInt("id")
                addProductTo = null
                mutate("/clients/campaigns/$id/product/add/", JSONObject()
                    .put("product_id", productId).put("benefit_type", benefitType)
                    .put("unit_amount", unit).put("points_per_unit", points))
            },
        )
    }
    slabForCp?.let { cp ->
        SlabDialog(
            title = "Campaign slab — ${cp.optString("product_name")}",
            onDismiss = { slabForCp = null },
            onSave = { threshold, payout, label ->
                val id = cp.getInt("id")
                slabForCp = null
                mutate("/clients/campaigns/product/$id/slab/add/", JSONObject()
                    .put("threshold", threshold).put("payout", payout).put("label", label))
            },
        )
    }

    actionError?.let { Text(it, color = StatusRed, fontSize = rsp(13), fontWeight = FontWeight.SemiBold) }

    LazyColumn(
        Modifier.fillMaxSize(),
        verticalArrangement = Arrangement.spacedBy(8.dp),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 8.dp),
    ) {
        item { Button(onClick = { addingCampaign = true }) { Text("＋ New campaign") } }
        items((0 until campaigns.length()).map { campaigns.getJSONObject(it) }) { c ->
            Card(
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
            ) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(Modifier.weight(1f)) {
                            Text(c.optString("name"), fontWeight = FontWeight.Bold, fontSize = rsp(15))
                            Text(
                                "${c.optString("start_date")} → ${c.optString("end_date")}",
                                fontSize = rsp(12), color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        Switch(
                            checked = c.optBoolean("is_active"),
                            onCheckedChange = { on ->
                                mutate("/clients/campaigns/${c.getInt("id")}/update/",
                                    JSONObject().put("is_active", on))
                            },
                        )
                    }
                    val cps = c.optJSONArray("products") ?: JSONArray()
                    for (i in 0 until cps.length()) {
                        val cp = cps.getJSONObject(i)
                        Column(
                            Modifier
                                .fillMaxWidth()
                                .padding(start = 6.dp),
                        ) {
                            Row(
                                Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Text(
                                    cp.optString("product_name") + if (cp.optString("benefit_type") == "unit")
                                        "  ·  %.3f pts / %s".format(
                                            cp.optDouble("points_per_unit", 0.0),
                                            rupees(cp.optDouble("unit_amount", 0.0)),
                                        )
                                    else "  ·  target slabs",
                                    fontSize = rsp(13), fontWeight = FontWeight.SemiBold,
                                )
                                Text(
                                    "✕", color = StatusRed, fontSize = rsp(14),
                                    modifier = Modifier.clickable {
                                        mutate("/clients/campaigns/product/${cp.getInt("id")}/delete/")
                                    }.padding(4.dp),
                                )
                            }
                            val slabs = cp.optJSONArray("slabs") ?: JSONArray()
                            for (j in 0 until slabs.length()) {
                                val s = slabs.getJSONObject(j)
                                Row(
                                    Modifier.fillMaxWidth(),
                                    horizontalArrangement = Arrangement.SpaceBetween,
                                ) {
                                    Text(
                                        "  ${rupees(s.optDouble("threshold", 0.0))} → ${"%.0f".format(s.optDouble("payout", 0.0))} pts",
                                        fontSize = rsp(12), color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                    Text(
                                        "✕", color = StatusRed, fontSize = rsp(12),
                                        modifier = Modifier.clickable {
                                            mutate("/clients/campaigns/slab/${s.getInt("id")}/delete/")
                                        }.padding(2.dp),
                                    )
                                }
                            }
                            if (cp.optString("benefit_type") == "target") {
                                Text(
                                    "＋ Add slab",
                                    color = MaterialTheme.colorScheme.secondary,
                                    fontSize = rsp(12), fontWeight = FontWeight.SemiBold,
                                    modifier = Modifier.clickable { slabForCp = cp }.padding(2.dp),
                                )
                            }
                        }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        Text("＋ Product", color = MaterialTheme.colorScheme.secondary, fontSize = rsp(13),
                            fontWeight = FontWeight.SemiBold,
                            modifier = Modifier.clickable { addProductTo = c }.padding(4.dp))
                        Text("Delete campaign", color = StatusRed, fontSize = rsp(13),
                            fontWeight = FontWeight.SemiBold,
                            modifier = Modifier.clickable {
                                mutate("/clients/campaigns/${c.getInt("id")}/delete/")
                            }.padding(4.dp))
                    }
                }
            }
        }
        item { Spacer(Modifier.height(12.dp)) }
    }
}

// ── Shared dialogs ───────────────────────────────────────────────────────────

@Composable
private fun RuleDialog(
    title: String,
    products: JSONArray?,        // null = edit mode (no product picker)
    initialUnit: String = "",
    initialPoints: String = "",
    onDismiss: () -> Unit,
    onSave: (productId: Int, unit: String, points: String) -> Unit,
) {
    var productId by remember { mutableStateOf<Int?>(null) }
    var productName by remember { mutableStateOf("") }
    var menuOpen by remember { mutableStateOf(false) }
    var unit by remember { mutableStateOf(initialUnit) }
    var points by remember { mutableStateOf(initialPoints) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                if (products != null) {
                    Box {
                        OutlinedTextField(
                            value = productName, onValueChange = {}, readOnly = true, enabled = false,
                            label = { Text("Product") },
                            modifier = Modifier.fillMaxWidth().clickable { menuOpen = true },
                            colors = androidx.compose.material3.OutlinedTextFieldDefaults.colors(
                                disabledTextColor = MaterialTheme.colorScheme.onSurface,
                                disabledBorderColor = MaterialTheme.colorScheme.outline,
                                disabledLabelColor = MaterialTheme.colorScheme.onSurfaceVariant,
                            ),
                        )
                        DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                            for (i in 0 until products.length()) {
                                val p = products.getJSONObject(i)
                                DropdownMenuItem(
                                    text = { Text(p.optString("name")) },
                                    onClick = {
                                        productId = p.getInt("id")
                                        productName = p.optString("name")
                                        menuOpen = false
                                    },
                                )
                            }
                        }
                    }
                }
                OutlinedTextField(
                    value = unit, onValueChange = { unit = it.filter { ch -> ch.isDigit() || ch == '.' } },
                    label = { Text("Unit amount (₹, e.g. 1000)") }, modifier = Modifier.fillMaxWidth(), singleLine = true,
                )
                OutlinedTextField(
                    value = points, onValueChange = { points = it.filter { ch -> ch.isDigit() || ch == '.' } },
                    label = { Text("Points per unit") },
                    keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                        keyboardType = androidx.compose.ui.text.input.KeyboardType.Decimal
                    ), modifier = Modifier.fillMaxWidth(), singleLine = true,
                )
            }
        },
        confirmButton = {
            TextButton(
                onClick = { onSave(productId ?: 0, unit, points) },
                enabled = unit.isNotBlank() && points.isNotBlank() && (products == null || productId != null),
            ) { Text("Save") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
private fun SlabDialog(
    title: String,
    // A rate-mode rule's payout is a PERCENT for the band, not rupees. Labelling
    // it "points" is how someone types 500 meaning rupees and writes a 500% rate.
    payoutIsPercent: Boolean = false,
    onDismiss: () -> Unit,
    onSave: (threshold: String, payout: String, label: String) -> Unit,
) {
    var threshold by remember { mutableStateOf("") }
    var payout by remember { mutableStateOf("") }
    var label by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(value = threshold, onValueChange = { threshold = it.filter { ch -> ch.isDigit() || ch == '.' } },
                    label = { Text("Threshold (₹ cumulative)") },
                    keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                        keyboardType = androidx.compose.ui.text.input.KeyboardType.Decimal
                    ), modifier = Modifier.fillMaxWidth(), singleLine = true)
                OutlinedTextField(value = payout, onValueChange = { payout = it.filter { ch -> ch.isDigit() || ch == '.' } },
                    label = { Text(if (payoutIsPercent) "Rate for this band (%)" else "Payout (points)") },
                    supportingText = {
                        Text(
                            if (payoutIsPercent) "A percentage, e.g. 2.25 — the whole period is paid at it."
                            else "Rupees earned in total once the threshold is reached.",
                            fontSize = rsp(11),
                        )
                    },
                    modifier = Modifier.fillMaxWidth(), singleLine = true)
                OutlinedTextField(value = label, onValueChange = { label = it },
                    label = { Text("Label (optional)") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
            }
        },
        confirmButton = {
            TextButton(
                onClick = { onSave(threshold, payout, label) },
                enabled = threshold.isNotBlank() && payout.isNotBlank(),
            ) { Text("Add") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
private fun CampaignDialog(
    onDismiss: () -> Unit,
    onSave: (name: String, desc: String, start: String, end: String) -> Unit,
) {
    var name by remember { mutableStateOf("") }
    var desc by remember { mutableStateOf("") }
    var start by remember { mutableStateOf("") }
    var end by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("New campaign") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(value = name, onValueChange = { name = it }, label = { Text("Name") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
                OutlinedTextField(value = desc, onValueChange = { desc = it }, label = { Text("Description") }, modifier = Modifier.fillMaxWidth())
                DateField("Start date", start) { start = it }
                DateField("End date", end) { end = it }
            }
        },
        confirmButton = {
            TextButton(
                onClick = { onSave(name, desc, start.trim(), end.trim()) },
                enabled = name.isNotBlank() && start.isNotBlank() && end.isNotBlank(),
            ) { Text("Create") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
private fun CampaignProductDialog(
    products: JSONArray,
    onDismiss: () -> Unit,
    onSave: (productId: Int, benefitType: String, unit: String, points: String) -> Unit,
) {
    var productId by remember { mutableStateOf<Int?>(null) }
    var productName by remember { mutableStateOf("") }
    var menuOpen by remember { mutableStateOf(false) }
    var benefitType by remember { mutableStateOf("unit") }
    var unit by remember { mutableStateOf("") }
    var points by remember { mutableStateOf("") }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Add product to campaign") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Box {
                    OutlinedTextField(
                        value = productName, onValueChange = {}, readOnly = true, enabled = false,
                        label = { Text("Product") },
                        modifier = Modifier.fillMaxWidth().clickable { menuOpen = true },
                        colors = androidx.compose.material3.OutlinedTextFieldDefaults.colors(
                            disabledTextColor = MaterialTheme.colorScheme.onSurface,
                            disabledBorderColor = MaterialTheme.colorScheme.outline,
                            disabledLabelColor = MaterialTheme.colorScheme.onSurfaceVariant,
                        ),
                    )
                    DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                        for (i in 0 until products.length()) {
                            val p = products.getJSONObject(i)
                            DropdownMenuItem(
                                text = { Text(p.optString("name")) },
                                onClick = {
                                    productId = p.getInt("id")
                                    productName = p.optString("name")
                                    menuOpen = false
                                },
                            )
                        }
                    }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Chip("Points per unit", benefitType == "unit") { benefitType = "unit" }
                    Chip("Target payout", benefitType == "target") { benefitType = "target" }
                }
                if (benefitType == "unit") {
                    OutlinedTextField(value = unit, onValueChange = { unit = it.filter { ch -> ch.isDigit() || ch == '.' } },
                        label = { Text("Unit amount (₹)") },
                    keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                        keyboardType = androidx.compose.ui.text.input.KeyboardType.Decimal
                    ), modifier = Modifier.fillMaxWidth(), singleLine = true)
                    OutlinedTextField(value = points, onValueChange = { points = it.filter { ch -> ch.isDigit() || ch == '.' } },
                        label = { Text("Points per unit") },
                    keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                        keyboardType = androidx.compose.ui.text.input.KeyboardType.Decimal
                    ), modifier = Modifier.fillMaxWidth(), singleLine = true)
                } else {
                    Text("Add slabs after creating (target payouts use cumulative slabs).",
                        fontSize = rsp(12), color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick = { onSave(productId ?: 0, benefitType, unit, points) },
                enabled = productId != null && (benefitType == "target" || (unit.isNotBlank() && points.isNotBlank())),
            ) { Text("Add") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}
