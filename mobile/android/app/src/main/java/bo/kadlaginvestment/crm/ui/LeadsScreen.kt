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
import androidx.compose.foundation.text.KeyboardOptions
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
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import bo.kadlaginvestment.crm.net.ApiClient
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject

/** Native Leads pipeline: stage chips, lead detail with per-product progress,
 * remarks, convert-to-client, and a create form. */
@Composable
fun LeadsScreen(
    modifier: Modifier = Modifier,
    onBack: (() -> Unit)? = null,
    onSessionExpired: () -> Unit,
) {
    if (onBack != null) BackHandler(onBack = onBack)

    var selectedLeadId by remember { mutableStateOf<Int?>(null) }
    var creating by remember { mutableStateOf(false) }
    var listReload by remember { mutableIntStateOf(0) }

    when {
        creating -> {
            BackHandler { creating = false }
            LeadCreateForm(modifier, onDone = { creating = false; listReload++ }, onSessionExpired = onSessionExpired)
        }
        selectedLeadId != null -> {
            BackHandler { selectedLeadId = null; listReload++ }
            LeadDetail(
                leadId = selectedLeadId!!,
                modifier = modifier,
                onBack = { selectedLeadId = null; listReload++ },
                onSessionExpired = onSessionExpired,
            )
        }
        else -> LeadList(
            modifier = modifier,
            onBack = onBack,
            reloadKey = listReload,
            onOpen = { selectedLeadId = it },
            onCreate = { creating = true },
            onSessionExpired = onSessionExpired,
        )
    }
}

@Composable
private fun LeadList(
    modifier: Modifier,
    onBack: (() -> Unit)?,
    reloadKey: Int,
    onOpen: (Int) -> Unit,
    onCreate: () -> Unit,
    onSessionExpired: () -> Unit,
) {
    var stage by remember { mutableStateOf("") }
    var q by remember { mutableStateOf("") }
    var rows by remember { mutableStateOf(listOf<JSONObject>()) }
    var counts by remember { mutableStateOf<JSONObject?>(null) }
    var page by remember { mutableIntStateOf(1) }
    var hasMore by remember { mutableStateOf(false) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var localReload by remember { mutableIntStateOf(0) }

    LaunchedEffect(stage, q, page, reloadKey, localReload) {
        loading = true
        error = null
        if (q.isNotEmpty()) delay(350)
        val path = "/clients/api/app/leads/?stage=$stage&page=$page&q=" +
            java.net.URLEncoder.encode(q, "UTF-8")
        when (val r = ApiClient.get(path)) {
            is ApiClient.Result.Ok -> {
                val arr = r.json.optJSONArray("results")
                val newRows = (0 until (arr?.length() ?: 0)).map { arr!!.getJSONObject(it) }
                rows = if (page == 1) newRows else rows + newRows
                hasMore = r.json.optBoolean("has_more")
                counts = r.json.optJSONObject("counts")
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
                Text("Leads", fontSize = 22.sp, fontWeight = FontWeight.Bold)
            }
            Button(onClick = onCreate) { Text("＋ Add") }
        }

        val c = counts
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Chip("All", stage == "") { stage = ""; page = 1 }
            Chip("Pending ${c?.optInt("pending") ?: ""}", stage == "pending") { stage = "pending"; page = 1 }
            Chip("Half ${c?.optInt("half_sold") ?: ""}", stage == "half_sold") { stage = "half_sold"; page = 1 }
            Chip("Done ${c?.optInt("processed") ?: ""}", stage == "processed") { stage = "processed"; page = 1 }
            Chip("Bin", stage == "discarded") { stage = "discarded"; page = 1 }
        }
        Spacer(Modifier.height(8.dp))

        OutlinedTextField(
            value = q,
            onValueChange = { q = it; page = 1 },
            label = { Text("Search name / phone") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
        )

        when {
            error != null -> ErrorBox(error!!) { error = null; localReload++ }
            loading && rows.isEmpty() -> LoadingBox()
            else -> LazyColumn(
                Modifier.fillMaxSize(),
                verticalArrangement = Arrangement.spacedBy(8.dp),
                contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 12.dp),
            ) {
                if (rows.isEmpty()) {
                    item { Text("No leads here.", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp) }
                }
                items(rows) { l ->
                    Card(
                        modifier = Modifier.fillMaxWidth().clickable { onOpen(l.getInt("id")) },
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
                    ) {
                        Row(
                            Modifier.fillMaxWidth().padding(14.dp),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Column(Modifier.weight(1f)) {
                                Text(l.optString("name"), fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
                                Text(
                                    listOf(l.optString("phone"), l.optString("assigned_to"))
                                        .filter { it.isNotEmpty() }.joinToString(" · "),
                                    fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            Column(horizontalAlignment = Alignment.End) {
                                LeadStagePill(l.optString("stage"), l.optBoolean("converted"))
                                Text(
                                    l.optString("progress"),
                                    fontSize = 12.sp,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
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
private fun LeadStagePill(stage: String, converted: Boolean) {
    val (color, label) = when {
        converted -> StatusGreen to "Converted"
        stage == "processed" -> StatusGreen to "Processed"
        stage == "half_sold" -> StatusAmber to "Half Sold"
        else -> BrandMuted to "Pending"
    }
    Text(label, fontSize = 11.sp, color = color, fontWeight = FontWeight.Bold)
}

@Composable
private fun LeadDetail(
    leadId: Int,
    modifier: Modifier,
    onBack: () -> Unit,
    onSessionExpired: () -> Unit,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var data by remember { mutableStateOf<JSONObject?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var actionError by remember { mutableStateOf<String?>(null) }
    var remarkText by remember { mutableStateOf("") }
    var reloadKey by remember { mutableIntStateOf(0) }

    LaunchedEffect(leadId, reloadKey) {
        when (val r = ApiClient.get("/clients/api/app/leads/$leadId/")) {
            is ApiClient.Result.Ok -> data = r.json
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
    }

    fun post(path: String, body: JSONObject, then: () -> Unit = { data = null; reloadKey++ }) {
        scope.launch {
            actionError = null
            when (val r = ApiClient.post(path, body)) {
                is ApiClient.Result.Ok -> then()
                is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                is ApiClient.Result.Error -> actionError = r.message
            }
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
            Text(d.optString("name"), fontSize = 20.sp, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
            LeadStagePill(d.optString("stage"), d.optInt("converted_client_id") > 0)
        }

        val phone = d.optString("phone")
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (phone.isNotEmpty()) {
                Button(onClick = {
                    context.startActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:$phone")))
                }) { Text("📞 Call") }
            }
            if (d.optBoolean("can_convert")) {
                Button(onClick = {
                    post("/clients/api/app/leads/$leadId/action/", JSONObject().put("action", "convert"))
                }) { Text("Convert → Client") }
            }
            if (d.optBoolean("is_discarded")) {
                OutlinedButton(onClick = {
                    post("/clients/api/app/leads/$leadId/action/", JSONObject().put("action", "undiscard"))
                }) { Text("Reopen") }
            } else {
                OutlinedButton(onClick = {
                    post("/clients/api/app/leads/$leadId/action/", JSONObject().put("action", "discard"))
                }) { Text("Discard") }
            }
        }

        actionError?.let { Text(it, color = StatusRed, fontSize = 13.sp, fontWeight = FontWeight.SemiBold) }
        if (d.optInt("converted_client_id") > 0) {
            Text("Converted to client #${d.optInt("converted_client_id")}", color = StatusGreen, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
        }

        SectionTitle("Product progress")
        val progress = d.optJSONArray("progress")
        for (i in 0 until (progress?.length() ?: 0)) {
            val p = progress!!.getJSONObject(i)
            Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        Text(p.optString("label"), fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                        val achieved = p.optDouble("achieved", 0.0)
                        if (achieved > 0) Text(rupees(achieved), fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        val current = p.optString("status")
                        listOf("pending" to "Pending", "half_sold" to "Half", "processed" to "Processed").forEach { (v, label) ->
                            Chip(label, current == v) {
                                if (current != v) {
                                    post(
                                        "/clients/api/app/leads/$leadId/progress/",
                                        JSONObject().put("product", p.optString("product")).put("status", v),
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }

        SectionTitle("Remarks")
        OutlinedTextField(
            value = remarkText,
            onValueChange = { remarkText = it },
            label = { Text("Add a remark") },
            modifier = Modifier.fillMaxWidth(),
            trailingIcon = {
                TextButton(
                    onClick = {
                        if (remarkText.isNotBlank()) {
                            val t = remarkText
                            remarkText = ""
                            post("/clients/api/app/leads/$leadId/remark/", JSONObject().put("text", t))
                        }
                    },
                ) { Text("Save") }
            },
        )
        val remarks = d.optJSONArray("remarks")
        for (i in 0 until (remarks?.length() ?: 0)) {
            val r = remarks!!.getJSONObject(i)
            Column(Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
                Text(r.optString("text"), fontSize = 13.sp)
                Text(
                    "${r.optString("by")} · ${r.optString("at")}",
                    fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }

        Spacer(Modifier.height(20.dp))
    }
}

@Composable
private fun LeadCreateForm(
    modifier: Modifier,
    onDone: () -> Unit,
    onSessionExpired: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    var meta by remember { mutableStateOf<JSONObject?>(null) }
    var name by remember { mutableStateOf("") }
    var phone by remember { mutableStateOf("") }
    var email by remember { mutableStateOf("") }
    var income by remember { mutableStateOf("") }
    var notes by remember { mutableStateOf("") }
    var selectedEmployee by remember { mutableStateOf<Pair<Int, String>?>(null) }
    var employeeMenuOpen by remember { mutableStateOf(false) }
    var submitting by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(Unit) {
        when (val r = ApiClient.get("/clients/api/app/lead-meta/")) {
            is ApiClient.Result.Ok -> meta = r.json
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> message = r.message
        }
    }
    val m = meta ?: run { LoadingBox(modifier); return }

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
            Text("New Lead", fontSize = 20.sp, fontWeight = FontWeight.Bold)
        }

        OutlinedTextField(value = name, onValueChange = { name = it }, label = { Text("Customer name *") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
        OutlinedTextField(
            value = phone, onValueChange = { phone = it }, label = { Text("Phone") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone),
            modifier = Modifier.fillMaxWidth(), singleLine = true,
        )
        OutlinedTextField(
            value = email, onValueChange = { email = it }, label = { Text("Email") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email),
            modifier = Modifier.fillMaxWidth(), singleLine = true,
        )
        OutlinedTextField(
            value = income, onValueChange = { income = it.filter { ch -> ch.isDigit() || ch == '.' } },
            label = { Text("Annual income (₹, optional)") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
            modifier = Modifier.fillMaxWidth(), singleLine = true,
        )
        OutlinedTextField(value = notes, onValueChange = { notes = it }, label = { Text("Notes") }, modifier = Modifier.fillMaxWidth())

        if (m.optBoolean("can_assign")) {
            androidx.compose.foundation.layout.Box {
                OutlinedTextField(
                    value = selectedEmployee?.second ?: "Myself",
                    onValueChange = {}, readOnly = true, enabled = false,
                    label = { Text("Assign to") },
                    modifier = Modifier.fillMaxWidth().clickable { employeeMenuOpen = true },
                    colors = androidx.compose.material3.OutlinedTextFieldDefaults.colors(
                        disabledTextColor = MaterialTheme.colorScheme.onSurface,
                        disabledBorderColor = MaterialTheme.colorScheme.outline,
                        disabledLabelColor = MaterialTheme.colorScheme.onSurfaceVariant,
                    ),
                )
                androidx.compose.material3.DropdownMenu(
                    expanded = employeeMenuOpen,
                    onDismissRequest = { employeeMenuOpen = false },
                ) {
                    val es = m.optJSONArray("employees")
                    for (i in 0 until (es?.length() ?: 0)) {
                        val e = es!!.getJSONObject(i)
                        androidx.compose.material3.DropdownMenuItem(
                            text = { Text(e.optString("name")) },
                            onClick = {
                                selectedEmployee = e.getInt("id") to e.optString("name")
                                employeeMenuOpen = false
                            },
                        )
                    }
                }
            }
        }

        message?.let { Text(it, color = StatusRed, fontSize = 13.sp, fontWeight = FontWeight.SemiBold) }

        Button(
            onClick = {
                submitting = true
                message = null
                scope.launch {
                    val body = JSONObject()
                        .put("customer_name", name)
                        .put("phone", phone)
                        .put("email", email)
                        .put("income", income)
                        .put("notes", notes)
                    if (selectedEmployee != null) body.put("assigned_to_id", selectedEmployee!!.first)
                    when (val r = ApiClient.post("/clients/api/app/leads/create/", body)) {
                        is ApiClient.Result.Ok -> onDone()
                        is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                        is ApiClient.Result.Error -> message = r.message
                    }
                    submitting = false
                }
            },
            enabled = !submitting && name.isNotBlank(),
            modifier = Modifier.fillMaxWidth().height(52.dp),
        ) { Text(if (submitting) "Saving…" else "Create Lead", fontSize = 16.sp) }

        Spacer(Modifier.height(24.dp))
    }
}
