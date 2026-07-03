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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
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
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import bo.kadlaginvestment.crm.net.ApiClient
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject

/** Native Renewals: month summary, searchable list, add-renewal form. */
@Composable
fun RenewalsScreen(
    modifier: Modifier = Modifier,
    onBack: (() -> Unit)? = null,
    onSessionExpired: () -> Unit,
) {
    if (onBack != null) BackHandler(onBack = onBack)
    var adding by remember { mutableStateOf(false) }

    if (adding) {
        BackHandler { adding = false }
        AddRenewalForm(modifier, onDone = { adding = false }, onSessionExpired = onSessionExpired)
        return
    }

    var q by remember { mutableStateOf("") }
    var rows by remember { mutableStateOf(listOf<JSONObject>()) }
    var summary by remember { mutableStateOf<JSONObject?>(null) }
    var page by remember { mutableIntStateOf(1) }
    var hasMore by remember { mutableStateOf(false) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var reloadKey by remember { mutableIntStateOf(0) }

    LaunchedEffect(q, page, reloadKey) {
        loading = true
        error = null
        if (q.isNotEmpty()) delay(350)
        val path = "/clients/api/app/renewals/?page=$page&q=" + java.net.URLEncoder.encode(q, "UTF-8")
        when (val r = ApiClient.get(path)) {
            is ApiClient.Result.Ok -> {
                val arr = r.json.optJSONArray("results")
                val newRows = (0 until (arr?.length() ?: 0)).map { arr!!.getJSONObject(it) }
                rows = if (page == 1) newRows else rows + newRows
                hasMore = r.json.optBoolean("has_more")
                summary = r.json.optJSONObject("summary")
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
            Row(verticalAlignment = Alignment.CenterVertically) {
                if (onBack != null) {
                    Text(
                        "← Back",
                        color = MaterialTheme.colorScheme.secondary,
                        fontWeight = FontWeight.SemiBold,
                        modifier = Modifier.clickable(onClick = onBack).padding(end = 12.dp),
                    )
                }
                Text("Renewals", fontSize = 22.sp, fontWeight = FontWeight.Bold)
            }
            Button(onClick = { adding = true }) { Text("＋ Add") }
        }

        summary?.let { s ->
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                MiniStat("This month", rupees(s.optDouble("month_premium", 0.0)), "${s.optInt("month_count")} renewal(s)", Modifier.weight(1f))
                MiniStat("Today", rupees(s.optDouble("today_premium", 0.0)), "collected", Modifier.weight(1f))
            }
            Spacer(Modifier.height(8.dp))
        }

        OutlinedTextField(
            value = q,
            onValueChange = { q = it; page = 1 },
            label = { Text("Search client / product") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
        )

        when {
            error != null -> ErrorBox(error!!) { error = null; reloadKey++ }
            loading && rows.isEmpty() -> LoadingBox()
            else -> LazyColumn(
                Modifier.fillMaxSize(),
                verticalArrangement = Arrangement.spacedBy(8.dp),
                contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 12.dp),
            ) {
                if (rows.isEmpty()) {
                    item { Text("No renewals found.", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp) }
                }
                items(rows) { r ->
                    Card(
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
                    ) {
                        Row(
                            Modifier.fillMaxWidth().padding(14.dp),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Column(Modifier.weight(1f)) {
                                Text(r.optString("client"), fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
                                Text(
                                    "${r.optString("product")} · ${r.optString("frequency")} · due ${r.optString("renewal_date")}",
                                    fontSize = 12.sp,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                                if (r.optString("employee").isNotEmpty()) {
                                    Text(r.optString("employee"), fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                            }
                            Text(rupees(r.optDouble("premium", 0.0)), fontWeight = FontWeight.Bold, fontSize = 15.sp)
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
                item { Spacer(Modifier.height(12.dp)) }
            }
        }
    }
}

@Composable
private fun MiniStat(title: String, value: String, sub: String, modifier: Modifier = Modifier) {
    Card(
        modifier = modifier,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.padding(12.dp)) {
            Text(title, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(value, fontSize = 17.sp, fontWeight = FontWeight.Bold)
            Text(sub, fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun AddRenewalForm(
    modifier: Modifier,
    onDone: () -> Unit,
    onSessionExpired: () -> Unit,
) {
    val scope = rememberCoroutineScope()

    var meta by remember { mutableStateOf<JSONObject?>(null) }
    var clientQuery by remember { mutableStateOf("") }
    var clientResults by remember { mutableStateOf(listOf<Pair<Int, String>>()) }
    var selectedClient by remember { mutableStateOf<Pair<Int, String>?>(null) }
    var selectedProduct by remember { mutableStateOf<Pair<Int, String>?>(null) }
    var productMenuOpen by remember { mutableStateOf(false) }
    var frequency by remember { mutableStateOf<Pair<String, String>?>(null) }
    var frequencyMenuOpen by remember { mutableStateOf(false) }
    var premium by remember { mutableStateOf("") }
    var renewalDate by remember { mutableStateOf("") }
    var notes by remember { mutableStateOf("") }
    var selectedEmployee by remember { mutableStateOf<Pair<Int, String>?>(null) }
    var employeeMenuOpen by remember { mutableStateOf(false) }
    var submitting by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<Pair<Boolean, String>?>(null) }

    LaunchedEffect(Unit) {
        when (val r = ApiClient.get("/clients/api/app/renewal-meta/")) {
            is ApiClient.Result.Ok -> meta = r.json
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> message = false to r.message
        }
    }

    LaunchedEffect(clientQuery) {
        if (selectedClient != null || clientQuery.length < 2) {
            clientResults = emptyList(); return@LaunchedEffect
        }
        delay(350)
        when (val r = ApiClient.get("/clients/search/?q=" + java.net.URLEncoder.encode(clientQuery, "UTF-8"))) {
            is ApiClient.Result.Ok -> {
                val arr = r.json.optJSONArray("results")
                clientResults = (0 until (arr?.length() ?: 0)).map {
                    val o = arr!!.getJSONObject(it)
                    o.getInt("id") to o.optString("text")
                }
            }
            else -> {}
        }
    }

    val m = meta ?: run { LoadingBox(modifier); return }
    val isAdmin = m.optBoolean("is_admin")

    Column(
        modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                "← Back",
                color = MaterialTheme.colorScheme.secondary,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.clickable(onClick = onDone).padding(end = 12.dp),
            )
            Text("Add Renewal", fontSize = 20.sp, fontWeight = FontWeight.Bold)
        }

        if (selectedClient == null) {
            OutlinedTextField(
                value = clientQuery,
                onValueChange = { clientQuery = it },
                label = { Text("Search client") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
            )
            clientResults.forEach { (id, text) ->
                Card(
                    modifier = Modifier.fillMaxWidth().clickable {
                        selectedClient = id to text; clientResults = emptyList()
                    },
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
                ) { Text(text, Modifier.padding(12.dp), fontSize = 14.sp) }
            }
        } else {
            Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer)) {
                Row(
                    Modifier.fillMaxWidth().padding(12.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(selectedClient!!.second, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                    Text(
                        "Change",
                        color = MaterialTheme.colorScheme.secondary, fontSize = 13.sp, fontWeight = FontWeight.SemiBold,
                        modifier = Modifier.clickable { selectedClient = null; clientQuery = "" },
                    )
                }
            }
        }

        PickerField("Product", selectedProduct?.second ?: "", { productMenuOpen = true }) {
            DropdownMenu(expanded = productMenuOpen, onDismissRequest = { productMenuOpen = false }) {
                val ps = m.optJSONArray("products")
                for (i in 0 until (ps?.length() ?: 0)) {
                    val p = ps!!.getJSONObject(i)
                    DropdownMenuItem(
                        text = { Text(p.optString("name")) },
                        onClick = { selectedProduct = p.getInt("id") to p.optString("name"); productMenuOpen = false },
                    )
                }
            }
        }

        PickerField("Frequency", frequency?.second ?: "", { frequencyMenuOpen = true }) {
            DropdownMenu(expanded = frequencyMenuOpen, onDismissRequest = { frequencyMenuOpen = false }) {
                val fs = m.optJSONArray("frequencies")
                for (i in 0 until (fs?.length() ?: 0)) {
                    val f = fs!!.getJSONObject(i)
                    DropdownMenuItem(
                        text = { Text(f.optString("label")) },
                        onClick = { frequency = f.optString("value") to f.optString("label"); frequencyMenuOpen = false },
                    )
                }
            }
        }

        OutlinedTextField(
            value = premium,
            onValueChange = { premium = it.filter { ch -> ch.isDigit() || ch == '.' } },
            label = { Text("Premium amount (₹)") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
        )

        OutlinedTextField(
            value = renewalDate,
            onValueChange = { renewalDate = it },
            label = { Text("Next renewal date (YYYY-MM-DD)") },
            placeholder = { Text("2026-08-15") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
        )

        OutlinedTextField(
            value = notes,
            onValueChange = { notes = it },
            label = { Text("Notes (optional)") },
            modifier = Modifier.fillMaxWidth(),
        )

        if (isAdmin) {
            PickerField("Assign to employee", selectedEmployee?.second ?: "Myself", { employeeMenuOpen = true }) {
                DropdownMenu(expanded = employeeMenuOpen, onDismissRequest = { employeeMenuOpen = false }) {
                    val es = m.optJSONArray("employees")
                    for (i in 0 until (es?.length() ?: 0)) {
                        val e = es!!.getJSONObject(i)
                        DropdownMenuItem(
                            text = { Text(e.optString("name")) },
                            onClick = { selectedEmployee = e.getInt("id") to e.optString("name"); employeeMenuOpen = false },
                        )
                    }
                }
            }
        }

        message?.let { (ok, text) ->
            Text(text, color = if (ok) StatusGreen else StatusRed, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
        }

        Button(
            onClick = {
                message = null
                submitting = true
                scope.launch {
                    val body = JSONObject()
                        .put("client_id", selectedClient?.first ?: 0)
                        .put("product_id", selectedProduct?.first ?: 0)
                        .put("premium_amount", premium)
                        .put("renewal_date", renewalDate.trim())
                        .put("frequency", frequency?.first ?: "")
                        .put("notes", notes)
                    if (selectedEmployee != null) body.put("employee_id", selectedEmployee!!.first)
                    when (val r = ApiClient.post("/clients/api/app/renewals/create/", body)) {
                        is ApiClient.Result.Ok -> {
                            message = true to "Renewal added ✓"
                            selectedClient = null; clientQuery = ""; selectedProduct = null
                            frequency = null; premium = ""; renewalDate = ""; notes = ""
                            selectedEmployee = null
                        }
                        is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                        is ApiClient.Result.Error -> message = false to r.message
                    }
                    submitting = false
                }
            },
            enabled = !submitting && selectedClient != null && selectedProduct != null
                && frequency != null && premium.isNotBlank() && renewalDate.isNotBlank(),
            modifier = Modifier.fillMaxWidth().height(52.dp),
        ) { Text(if (submitting) "Saving…" else "Save Renewal", fontSize = 16.sp) }

        Spacer(Modifier.height(24.dp))
    }
}

@Composable
private fun PickerField(
    label: String,
    value: String,
    onOpen: () -> Unit,
    menu: @Composable () -> Unit,
) {
    Box {
        OutlinedTextField(
            value = value,
            onValueChange = {},
            readOnly = true,
            label = { Text(label) },
            modifier = Modifier.fillMaxWidth().clickable(onClick = onOpen),
            enabled = false,
            colors = OutlinedTextFieldDefaults.colors(
                disabledTextColor = MaterialTheme.colorScheme.onSurface,
                disabledBorderColor = MaterialTheme.colorScheme.outline,
                disabledLabelColor = MaterialTheme.colorScheme.onSurfaceVariant,
            ),
        )
        menu()
    }
}
