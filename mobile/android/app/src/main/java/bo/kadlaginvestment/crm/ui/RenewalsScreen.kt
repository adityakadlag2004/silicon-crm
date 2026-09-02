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
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
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

/** Sentinel for the "New policy" radio option in the add-renewal form. */
private const val NEW_POLICY = -1

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

    Column(modifier.fillMaxSize().padding(horizontal = rdp(16))) {
        ScreenHeader("Renewals", onBack = onBack) {
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
                    item {
                        EmptyState(
                            "🔁", "No renewals",
                            if (q.isBlank()) "Nothing due. Add one as policies come up for renewal."
                            else "Nothing matches “$q”.",
                            modifier = Modifier.heightIn(min = 200.dp),
                            actionLabel = if (q.isBlank()) "＋ Add renewal" else null,
                            onAction = if (q.isBlank()) ({ adding = true }) else null,
                        )
                    }
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
                                Text(r.optString("client"), fontWeight = FontWeight.SemiBold, fontSize = rsp(15))
                                Text(
                                    "${r.optString("product")} · ${r.optString("frequency")} · due ${r.optString("renewal_date")}",
                                    fontSize = rsp(12),
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                                if (r.optString("employee").isNotEmpty()) {
                                    Text(r.optString("employee"), fontSize = rsp(11), color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                            }
                            Text(rupees(r.optDouble("premium", 0.0)), fontWeight = FontWeight.Bold, fontSize = rsp(15))
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
            Text(title, fontSize = rsp(11), color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(value, fontSize = rsp(17), fontWeight = FontWeight.Bold)
            Text(sub, fontSize = rsp(10), color = MaterialTheme.colorScheme.onSurfaceVariant)
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
    // Insurance Tracker link: the client's existing Health/Life policies, and
    // which one this renewal belongs to. "New policy" (-1) reveals a number
    // field — that's how the old book, sold before the tracker existed, gets
    // captured the first time it renews.
    var policies by remember { mutableStateOf(listOf<JSONObject>()) }
    var policyId by remember { mutableStateOf<Int?>(null) }
    var newPolicyNumber by remember { mutableStateOf("") }
    var selectedEmployee by remember { mutableStateOf<Pair<Int, String>?>(null) }
    var employeeMenuOpen by remember { mutableStateOf(false) }
    var submitting by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<Pair<Boolean, String>?>(null) }
    var duplicateWarning by remember { mutableStateOf<String?>(null) }

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

    // Client picked → load their tracker policies (same endpoint the web
    // add-renewal page uses).
    LaunchedEffect(selectedClient?.first) {
        val id = selectedClient?.first
        policyId = null
        newPolicyNumber = ""
        if (id == null) { policies = emptyList(); return@LaunchedEffect }
        when (val r = ApiClient.get("/clients/api/client/$id/policies/")) {
            is ApiClient.Result.Ok -> {
                val arr = r.json.optJSONArray("policies")
                policies = (0 until (arr?.length() ?: 0)).map { arr!!.getJSONObject(it) }
            }
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> policies = emptyList()
        }
    }

    val m = meta ?: run { LoadingBox(modifier); return }
    val isAdmin = m.optBoolean("is_admin")

    Column(
        modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        ScreenHeader("Add Renewal", onBack = onDone)

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
                ) { Text(text, Modifier.padding(12.dp), fontSize = rsp(14)) }
            }
        } else {
            Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer)) {
                Row(
                    Modifier.fillMaxWidth().padding(12.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(selectedClient!!.second, fontSize = rsp(14), fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                    Text(
                        "Change",
                        color = MaterialTheme.colorScheme.secondary, fontSize = rsp(13), fontWeight = FontWeight.SemiBold,
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
            onValueChange = { premium = moneyInput(it) },
            label = { Text("Premium amount (₹)") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
        )

        DateField("Next renewal date *", renewalDate) { renewalDate = it }

        // ── Which policy is this a renewal of? ──
        if (selectedClient != null) {
            Text(
                "Which policy is this?",
                fontSize = rsp(13), fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            if (policies.isEmpty()) {
                Text(
                    "No policy on the tracker for this client yet — add the number below and one will be created.",
                    fontSize = rsp(12), color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            policies.forEach { p ->
                val id = p.optInt("id")
                Card(
                    modifier = Modifier.fillMaxWidth().clickable { policyId = id; newPolicyNumber = "" },
                    colors = CardDefaults.cardColors(
                        containerColor = if (policyId == id) MaterialTheme.colorScheme.primaryContainer
                        else MaterialTheme.colorScheme.surface
                    ),
                ) {
                    Row(
                        Modifier.fillMaxWidth().padding(12.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        RadioButton(selected = policyId == id, onClick = { policyId = id; newPolicyNumber = "" })
                        Column(Modifier.weight(1f)) {
                            Text(
                                "${p.optString("type")} · ${p.optString("number")}",
                                fontSize = rsp(13), fontWeight = FontWeight.SemiBold,
                            )
                            Text(
                                listOf(p.optString("insurer"), p.optString("plan"))
                                    .filter { it.isNotEmpty() && it != "—" }.joinToString(" · "),
                                fontSize = rsp(11), color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }
            Card(
                modifier = Modifier.fillMaxWidth().clickable { policyId = NEW_POLICY },
                colors = CardDefaults.cardColors(
                    containerColor = if (policyId == NEW_POLICY) MaterialTheme.colorScheme.primaryContainer
                    else MaterialTheme.colorScheme.surface
                ),
            ) {
                Row(Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                    RadioButton(selected = policyId == NEW_POLICY, onClick = { policyId = NEW_POLICY })
                    Text("New policy — not on the tracker yet", fontSize = rsp(13))
                }
            }
            if (policyId == NEW_POLICY) {
                OutlinedTextField(
                    value = newPolicyNumber,
                    // Raw while typing — rewriting resets the cursor. Normalised
                    // server-side.
                    onValueChange = { newPolicyNumber = it },
                    label = { Text("Policy number") },
                    supportingText = { Text("From the policy document", fontSize = rsp(11)) },
                    keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                        capitalization = androidx.compose.ui.text.input.KeyboardCapitalization.Characters
                    ),
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                )
            }
        }

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
            Text(text, color = if (ok) StatusGreen else StatusRed, fontSize = rsp(14), fontWeight = FontWeight.SemiBold)
        }

        // Posted twice at most: plainly, then again with confirm_duplicate if
        // the server matched a renewal already collected on this policy this
        // cycle and the user says it is a real one. Same idiom as Add Sale.
        suspend fun submit(confirmDuplicate: Boolean) {
            message = null
            submitting = true
            val body = JSONObject()
                .put("client_id", selectedClient?.first ?: 0)
                .put("product_id", selectedProduct?.first ?: 0)
                .put("premium_amount", premium)
                .put("renewal_date", renewalDate.trim())
                .put("frequency", frequency?.first ?: "")
                .put("notes", notes)
            if (confirmDuplicate) body.put("confirm_duplicate", true)
            if (policyId != null && policyId != NEW_POLICY) body.put("policy_id", policyId)
            if (policyId == NEW_POLICY) body.put("policy_number", newPolicyNumber)
            if (selectedEmployee != null) body.put("employee_id", selectedEmployee!!.first)
            when (val r = ApiClient.post("/clients/api/app/renewals/create/", body)) {
                is ApiClient.Result.Ok -> {
                    message = true to "Renewal added ✓"
                    selectedClient = null; clientQuery = ""; selectedProduct = null
                    frequency = null; premium = ""; renewalDate = ""; notes = ""
                    selectedEmployee = null
                    policies = emptyList(); policyId = null; newPolicyNumber = ""
                }
                is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                is ApiClient.Result.Error ->
                    if (r.body?.optBoolean("duplicate") == true) duplicateWarning = r.message
                    else message = false to r.message
            }
            submitting = false
        }

        duplicateWarning?.let { warning ->
            AlertDialog(
                onDismissRequest = { duplicateWarning = null },
                title = { Text("Already added?", fontSize = rsp(18), fontWeight = FontWeight.Bold) },
                text = { Text(warning, fontSize = rsp(14)) },
                confirmButton = {
                    TextButton(onClick = {
                        duplicateWarning = null
                        scope.launch { submit(confirmDuplicate = true) }
                    }) { Text("Add anyway", fontSize = rsp(14)) }
                },
                dismissButton = {
                    TextButton(onClick = { duplicateWarning = null }) {
                        Text("Cancel", fontSize = rsp(14))
                    }
                },
            )
        }

        Button(
            onClick = { scope.launch { submit(confirmDuplicate = false) } },
            enabled = !submitting && selectedClient != null && selectedProduct != null
                && frequency != null && premium.isNotBlank() && renewalDate.isNotBlank(),
            modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
        ) { Text(if (submitting) "Saving…" else "Save Renewal", fontSize = rsp(16)) }

        Spacer(Modifier.height(24.dp))
    }
}

