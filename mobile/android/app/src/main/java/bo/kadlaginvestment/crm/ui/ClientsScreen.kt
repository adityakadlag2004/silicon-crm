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
import androidx.compose.foundation.layout.heightIn
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
import kotlinx.coroutines.launch
import org.json.JSONObject

/** Native Clients: My/All list with search → tap for the client profile. */
@Composable
fun ClientsScreen(
    modifier: Modifier = Modifier,
    onSessionExpired: () -> Unit,
    onOpenWeb: (String) -> Unit,
    onBack: (() -> Unit)? = null,
) {
    var selectedClientId by remember { mutableStateOf<Int?>(null) }
    var creating by remember { mutableStateOf(false) }
    var createdReload by remember { mutableStateOf(0) }

    if (creating) {
        BackHandler { creating = false }
        AddClientForm(
            modifier = modifier,
            onDone = { newId ->
                creating = false
                createdReload++
                if (newId != null) selectedClientId = newId
            },
            onSessionExpired = onSessionExpired,
        )
        return
    }

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

    LaunchedEffect(scopeMy, q, page, reloadKey, createdReload) {
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

    if (onBack != null) BackHandler(onBack = onBack)

    Column(modifier.fillMaxSize().padding(horizontal = rdp(16))) {
        ScreenHeader("Clients", onBack = onBack) {
Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                Chip("My", scopeMy) { scopeMy = true; page = 1 }
                Chip("All", !scopeMy) { scopeMy = false; page = 1 }
                Button(onClick = { creating = true }) { Text("＋ Add") }
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
                        EmptyState(
                            "👥",
                            if (q.isNotBlank()) "No matches" else "No clients yet",
                            when {
                                q.isNotBlank() -> "Nothing matches “$q”. Try a phone number or PAN."
                                scopeMy -> "No clients are mapped to you yet. Switch to All to see the firm's book."
                                else -> "Add your first client to get started."
                            },
                            modifier = Modifier.heightIn(min = 220.dp),
                            actionLabel = if (q.isBlank()) "＋ Add client" else null,
                            onAction = if (q.isBlank()) ({ creating = true }) else null,
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
                                Text(c.optString("name"), fontWeight = FontWeight.SemiBold, fontSize = rsp(15))
                                Text(
                                    listOf(c.optString("phone"), c.optString("mapped_to"))
                                        .filter { it.isNotEmpty() }.joinToString(" · "),
                                    fontSize = rsp(12),
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            Text("#${c.optInt("id")}", fontSize = rsp(12), color = MaterialTheme.colorScheme.onSurfaceVariant)
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
        ScreenHeader(d.optString("name"), onBack = onBack)

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
            Text("No sales for this client yet.", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = rsp(13))
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
                            Text(s.optString("product"), fontWeight = FontWeight.SemiBold, fontSize = rsp(14))
                            Text(s.optString("date"), fontSize = rsp(12), color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        Column(horizontalAlignment = Alignment.End) {
                            Text(rupees(s.optDouble("amount", 0.0)), fontWeight = FontWeight.Bold, fontSize = rsp(14))
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
        Text(label, fontSize = rsp(13), color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(0.35f))
        Text(if (value.isEmpty()) "—" else value, fontSize = rsp(13), modifier = Modifier.weight(0.65f))
    }
}

@Composable
private fun AddClientForm(
    modifier: Modifier,
    onDone: (Int?) -> Unit,
    onSessionExpired: () -> Unit,
) {
    val scope = androidx.compose.runtime.rememberCoroutineScope()
    var meta by remember { mutableStateOf<JSONObject?>(null) }
    var name by remember { mutableStateOf("") }
    var phone by remember { mutableStateOf("") }
    var email by remember { mutableStateOf("") }
    var pan by remember { mutableStateOf("") }
    var address by remember { mutableStateOf("") }
    var dob by remember { mutableStateOf("") }
    var mappedTo by remember { mutableStateOf<Pair<Int, String>?>(null) } // null = Myself/Unmapped label below
    var mappedUnset by remember { mutableStateOf(false) }                 // admin chose "Unmapped"
    var mapMenuOpen by remember { mutableStateOf(false) }
    var submitting by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(Unit) {
        when (val r = bo.kadlaginvestment.crm.net.ApiClient.get("/clients/api/app/sale-meta/")) {
            is bo.kadlaginvestment.crm.net.ApiClient.Result.Ok -> meta = r.json
            is bo.kadlaginvestment.crm.net.ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is bo.kadlaginvestment.crm.net.ApiClient.Result.Error -> meta = JSONObject()
        }
    }
    val m = meta ?: run { LoadingBox(modifier); return }
    val isAdmin = m.optBoolean("is_admin")

    Column(
        modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        ScreenHeader("Add Client", onBack = { onDone(null) })

        OutlinedTextField(value = name, onValueChange = { name = it }, label = { Text("Full name *") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
        OutlinedTextField(
            value = phone, onValueChange = { phone = it },
            label = { Text("Phone *") },
            keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                keyboardType = androidx.compose.ui.text.input.KeyboardType.Phone
            ),
            modifier = Modifier.fillMaxWidth(), singleLine = true,
        )
        OutlinedTextField(
            value = email, onValueChange = { email = it }, label = { Text("Email") },
            keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                keyboardType = androidx.compose.ui.text.input.KeyboardType.Email
            ),
            modifier = Modifier.fillMaxWidth(), singleLine = true,
        )
        // Raw while typing — uppercasing each keystroke reset the cursor to the
        // end. validate_pan() strips and upper-cases it server-side.
        OutlinedTextField(
            value = pan, onValueChange = { pan = it }, label = { Text("PAN") },
            keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                capitalization = androidx.compose.ui.text.input.KeyboardCapitalization.Characters
            ),
            modifier = Modifier.fillMaxWidth(), singleLine = true,
        )
        OutlinedTextField(value = address, onValueChange = { address = it }, label = { Text("Address") }, modifier = Modifier.fillMaxWidth())
        // A birthday can't be in the future, and typing "YYYY-MM-DD" on a phone
        // keyboard was a guaranteed source of 400s.
        DateField("Date of birth (optional)", dob, maxToday = true) { dob = it }

        if (isAdmin) {
            androidx.compose.foundation.layout.Box {
                OutlinedTextField(
                    value = when {
                        mappedUnset -> "Unmapped"
                        mappedTo != null -> mappedTo!!.second
                        else -> "Myself"
                    },
                    onValueChange = {}, readOnly = true, enabled = false,
                    label = { Text("Map to employee") },
                    modifier = Modifier.fillMaxWidth().clickable { mapMenuOpen = true },
                    colors = androidx.compose.material3.OutlinedTextFieldDefaults.colors(
                        disabledTextColor = MaterialTheme.colorScheme.onSurface,
                        disabledBorderColor = MaterialTheme.colorScheme.outline,
                        disabledLabelColor = MaterialTheme.colorScheme.onSurfaceVariant,
                    ),
                )
                androidx.compose.material3.DropdownMenu(
                    expanded = mapMenuOpen, onDismissRequest = { mapMenuOpen = false },
                ) {
                    androidx.compose.material3.DropdownMenuItem(
                        text = { Text("Unmapped") },
                        onClick = { mappedUnset = true; mappedTo = null; mapMenuOpen = false },
                    )
                    val es = m.optJSONArray("employees")
                    for (i in 0 until (es?.length() ?: 0)) {
                        val e = es!!.getJSONObject(i)
                        androidx.compose.material3.DropdownMenuItem(
                            text = { Text(e.optString("name")) },
                            onClick = {
                                mappedTo = e.getInt("id") to e.optString("name")
                                mappedUnset = false
                                mapMenuOpen = false
                            },
                        )
                    }
                }
            }
        }

        message?.let { Text(it, color = StatusRed, fontSize = rsp(13), fontWeight = FontWeight.SemiBold) }

        Button(
            onClick = {
                submitting = true
                message = null
                scope.launch {
                    val body = JSONObject()
                        .put("name", name).put("phone", phone).put("email", email)
                        .put("pan", pan).put("address", address).put("date_of_birth", dob.trim())
                    if (isAdmin) {
                        when {
                            mappedUnset -> body.put("mapped_to_id", "")
                            mappedTo != null -> body.put("mapped_to_id", mappedTo!!.first)
                            else -> body.put("mapped_to_id", m.optInt("employee_id"))
                        }
                    }
                    when (val r = bo.kadlaginvestment.crm.net.ApiClient.post("/clients/api/app/clients/create/", body)) {
                        is bo.kadlaginvestment.crm.net.ApiClient.Result.Ok -> onDone(r.json.optInt("id"))
                        is bo.kadlaginvestment.crm.net.ApiClient.Result.NotLoggedIn -> onSessionExpired()
                        is bo.kadlaginvestment.crm.net.ApiClient.Result.Error -> message = r.message
                    }
                    submitting = false
                }
            },
            enabled = !submitting && name.isNotBlank() && phone.isNotBlank(),
            modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
        ) { Text(if (submitting) "Saving…" else "Save Client", fontSize = rsp(16)) }

        Spacer(Modifier.height(24.dp))
    }
}
