package bo.kadlaginvestment.crm.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
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
import bo.kadlaginvestment.crm.net.ApiClient
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

/**
 * Insurance on the phone: the policy tracker, and the claim workflow.
 *
 * A claim is intimated over the phone, from wherever the client is, and its
 * papers are photographed on the spot — the one module that was web-only was
 * the one nobody is at a desk for. Stages, modes and document kinds all come
 * from `/insurance-meta/`: the server owns the workflow, the app draws it.
 *
 * `start` picks which list opens ("policies" or "claims"); everything below
 * is one screen stack so a claim reached from its policy behaves the same as
 * one reached from the claim list.
 */
@Composable
fun InsuranceScreen(
    modifier: Modifier = Modifier,
    start: String = "policies",
    onBack: (() -> Unit)? = null,
    onSessionExpired: () -> Unit,
    onOpenWeb: (String) -> Unit,
) {
    if (onBack != null) BackHandler(onBack = onBack)

    var meta by remember { mutableStateOf<JSONObject?>(null) }
    var metaError by remember { mutableStateOf<String?>(null) }
    var metaReload by remember { mutableIntStateOf(0) }
    var policyId by remember { mutableStateOf<Int?>(null) }
    var claimId by remember { mutableStateOf<Int?>(null) }
    // Non-null while raising a claim: 0 = pick the policy, else pre-filled.
    var raisingFor by remember { mutableStateOf<Int?>(null) }
    var listReload by remember { mutableIntStateOf(0) }

    LaunchedEffect(metaReload) {
        when (val r = ApiClient.get("/clients/api/app/insurance-meta/")) {
            is ApiClient.Result.Ok -> meta = r.json
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> metaError = r.message
        }
    }

    if (metaError != null) {
        ErrorBox(metaError!!, modifier) { metaError = null; metaReload++ }
        return
    }
    val m = meta ?: run { LoadingBox(modifier); return }

    when {
        raisingFor != null -> {
            BackHandler { raisingFor = null }
            RaiseClaimForm(
                meta = m,
                policyId = raisingFor!!.takeIf { it > 0 },
                modifier = modifier,
                onBack = { raisingFor = null },
                onRaised = { id -> raisingFor = null; claimId = id; listReload++ },
                onSessionExpired = onSessionExpired,
            )
        }
        claimId != null -> {
            BackHandler { claimId = null; listReload++ }
            ClaimDetail(
                claimId = claimId!!,
                meta = m,
                modifier = modifier,
                onBack = { claimId = null; listReload++ },
                onOpenPolicy = { policyId = it; claimId = null },
                onSessionExpired = onSessionExpired,
                onOpenWeb = onOpenWeb,
            )
        }
        policyId != null -> {
            BackHandler { policyId = null }
            PolicyDetail(
                policyId = policyId!!,
                modifier = modifier,
                onBack = { policyId = null },
                onOpenClaim = { claimId = it },
                onRaiseClaim = { raisingFor = policyId },
                onSessionExpired = onSessionExpired,
            )
        }
        start == "claims" -> ClaimList(
            meta = m, modifier = modifier, onBack = onBack, reloadKey = listReload,
            onOpen = { claimId = it }, onRaise = { raisingFor = 0 },
            onSessionExpired = onSessionExpired,
        )
        else -> PolicyList(
            meta = m, modifier = modifier, onBack = onBack, reloadKey = listReload,
            onOpen = { policyId = it }, onSessionExpired = onSessionExpired,
        )
    }
}

private fun JSONObject.pairs(key: String): List<Pair<String, String>> {
    val arr = optJSONArray(key) ?: JSONArray()
    return (0 until arr.length()).map {
        val o = arr.getJSONObject(it)
        o.optString("value") to o.optString("label")
    }
}

private fun JSONObject.strings(key: String): List<String> {
    val arr = optJSONArray(key) ?: JSONArray()
    return (0 until arr.length()).map { arr.optString(it) }
}

// ───────────────────────────── policies ─────────────────────────────

@Composable
private fun PolicyList(
    meta: JSONObject,
    modifier: Modifier,
    onBack: (() -> Unit)?,
    reloadKey: Int,
    onOpen: (Int) -> Unit,
    onSessionExpired: () -> Unit,
) {
    var status by remember { mutableStateOf("") }
    var q by remember { mutableStateOf("") }
    var rows by remember { mutableStateOf(listOf<JSONObject>()) }
    var counts by remember { mutableStateOf<JSONObject?>(null) }
    var page by remember { mutableIntStateOf(1) }
    var hasMore by remember { mutableStateOf(false) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(status, q, page, reloadKey) {
        loading = true
        error = null
        if (q.isNotEmpty()) delay(350)
        val path = "/clients/api/app/policies/?status=$status&page=$page&q=" +
            java.net.URLEncoder.encode(q, "UTF-8")
        when (val r = ApiClient.get(path)) {
            is ApiClient.Result.Ok -> {
                val arr = r.json.optJSONArray("results")
                val fresh = (0 until (arr?.length() ?: 0)).map { arr!!.getJSONObject(it) }
                rows = if (page == 1) fresh else rows + fresh
                hasMore = r.json.optBoolean("has_more")
                counts = r.json.optJSONObject("counts")
            }
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
        loading = false
    }

    Column(modifier.fillMaxSize().padding(horizontal = rdp(16))) {
        ScreenHeader("Policies", onBack = onBack)

        val c = counts
        Row(
            Modifier.horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(rdp(6)),
        ) {
            Chip("All ${c?.optInt("total") ?: ""}".trim(), status == "") { status = ""; page = 1 }
            // Expiring is a date window, not a status — it is the reason to
            // open this screen at all, so it sits second.
            Chip("Expiring ≤30d ${c?.optInt("expiring") ?: ""}".trim(), status == "expiring") {
                status = "expiring"; page = 1
            }
            meta.pairs("policy_statuses").forEach { (value, label) ->
                Chip(label, status == value) { status = value; page = 1 }
            }
        }
        Spacer(Modifier.height(rdp(8)))

        OutlinedTextField(
            value = q,
            onValueChange = { q = it; page = 1 },
            label = { Text("Search policy no., client, insurer") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(rdp(8)))

        if (error != null) {
            ErrorStrip(error)
        }
        if (loading && rows.isEmpty()) {
            LoadingBox()
        } else if (rows.isEmpty()) {
            EmptyState("📄", "No policies here",
                if (q.isBlank()) "Nothing on the tracker for this filter."
                else "No policy matches “$q”.")
        } else {
            LazyColumn(verticalArrangement = Arrangement.spacedBy(rdp(8))) {
                items(rows) { p -> PolicyCard(p) { onOpen(p.optInt("id")) } }
                if (hasMore) {
                    item {
                        TextButton(onClick = { page++ }, modifier = Modifier.fillMaxWidth()) {
                            Text(if (loading) "Loading…" else "Load more")
                        }
                    }
                }
                item { Spacer(Modifier.height(rdp(16))) }
            }
        }
    }
}

@Composable
private fun PolicyCard(p: JSONObject, onClick: () -> Unit) {
    val days = if (p.isNull("days_to_expiry")) null else p.optInt("days_to_expiry")
    Card(
        modifier = Modifier.fillMaxWidth().clickable(onClick = onClick),
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.fillMaxWidth().padding(rdp(14))) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text(p.optString("client"), fontSize = rsp(15), fontWeight = FontWeight.SemiBold,
                     modifier = Modifier.weight(1f))
                StatusPill(p.optString("status"))
            }
            Text(
                "${p.optString("number")} · ${p.optString("insurer")} · ${p.optString("type")}",
                fontSize = rsp(12), color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Row(Modifier.fillMaxWidth().padding(top = rdp(4)),
                horizontalArrangement = Arrangement.SpaceBetween) {
                Text("Cover ${rupees(p.optDouble("sum_insured", 0.0))}", fontSize = rsp(12))
                if (days != null) {
                    Text(
                        when {
                            days < 0 -> "expired ${-days}d ago"
                            days == 0 -> "expires today"
                            else -> "expires in ${days}d"
                        },
                        fontSize = rsp(12), fontWeight = FontWeight.SemiBold,
                        color = if (days <= 30) StatusAmber else MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

@Composable
private fun PolicyDetail(
    policyId: Int,
    modifier: Modifier,
    onBack: () -> Unit,
    onOpenClaim: (Int) -> Unit,
    onRaiseClaim: () -> Unit,
    onSessionExpired: () -> Unit,
) {
    val loader = rememberLoader("/clients/api/app/policies/$policyId/",
                                onSessionExpired = onSessionExpired)
    val d = loader.data ?: run {
        if (loader.error != null) ErrorBox(loader.error!!, modifier, loader.reload)
        else LoadingBox(modifier)
        return
    }

    Column(
        modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(rdp(16)),
        verticalArrangement = Arrangement.spacedBy(rdp(10)),
    ) {
        ScreenHeader(d.optString("client"), onBack = onBack) {
            StatusPill(d.optString("status"))
        }
        Text("${d.optString("number")} · ${d.optString("insurer")}",
             fontSize = rsp(13), color = MaterialTheme.colorScheme.onSurfaceVariant)

        FieldGrid(
            listOf(
                "Plan" to d.optString("plan").ifBlank { d.optString("type") },
                "Cover" to rupees(d.optDouble("sum_insured", 0.0)),
                "Premium" to rupees(d.optDouble("premium", 0.0)),
                "Start" to fmtDateOr(d.optString("start_date")),
                "End" to fmtDateOr(d.optString("end_date")),
                "Nominee" to d.optString("nominee").ifBlank { "—" },
                "RM" to d.optString("manager").ifBlank { "—" },
            )
        )

        Button(
            onClick = onRaiseClaim,
            modifier = Modifier.fillMaxWidth().heightIn(min = rdp(52)),
        ) { Text("Raise a claim on this policy", fontSize = rsp(15)) }

        val claims = d.optJSONArray("claims")
        SectionTitle("Claims (${claims?.length() ?: 0})")
        if ((claims?.length() ?: 0) == 0) {
            Text("No claim has been raised on this policy.",
                 fontSize = rsp(13), color = MaterialTheme.colorScheme.onSurfaceVariant)
        } else {
            for (i in 0 until claims!!.length()) {
                val c = claims.getJSONObject(i)
                ClaimCard(c, showClient = false) { onOpenClaim(c.optInt("id")) }
            }
        }

        val renewals = d.optJSONArray("renewals")
        if ((renewals?.length() ?: 0) > 0) {
            SectionTitle("Renewal history")
            for (i in 0 until renewals!!.length()) {
                val r = renewals.getJSONObject(i)
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text(fmtDateOr(r.optString("date")), fontSize = rsp(13))
                    Text(rupees(r.optDouble("premium", 0.0)),
                         fontSize = rsp(13), fontWeight = FontWeight.SemiBold)
                }
            }
        }
        Spacer(Modifier.height(rdp(16)))
    }
}

// ───────────────────────────── claims ─────────────────────────────

@Composable
private fun ClaimList(
    meta: JSONObject,
    modifier: Modifier,
    onBack: (() -> Unit)?,
    reloadKey: Int,
    onOpen: (Int) -> Unit,
    onRaise: () -> Unit,
    onSessionExpired: () -> Unit,
) {
    var status by remember { mutableStateOf("open") }
    var q by remember { mutableStateOf("") }
    var rows by remember { mutableStateOf(listOf<JSONObject>()) }
    var counts by remember { mutableStateOf<JSONObject?>(null) }
    var page by remember { mutableIntStateOf(1) }
    var hasMore by remember { mutableStateOf(false) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(status, q, page, reloadKey) {
        loading = true
        error = null
        if (q.isNotEmpty()) delay(350)
        val path = "/clients/api/app/claims/?status=$status&page=$page&q=" +
            java.net.URLEncoder.encode(q, "UTF-8")
        when (val r = ApiClient.get(path)) {
            is ApiClient.Result.Ok -> {
                val arr = r.json.optJSONArray("results")
                val fresh = (0 until (arr?.length() ?: 0)).map { arr!!.getJSONObject(it) }
                rows = if (page == 1) fresh else rows + fresh
                hasMore = r.json.optBoolean("has_more")
                counts = r.json.optJSONObject("counts")
            }
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
        loading = false
    }

    Column(modifier.fillMaxSize().padding(horizontal = rdp(16))) {
        ScreenHeader("Claims", onBack = onBack) {
            Button(onClick = onRaise) { Text("＋ Raise") }
        }

        val c = counts
        Row(
            Modifier.horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(rdp(6)),
        ) {
            // Open first and selected by default: a settled claim is history,
            // an open one is work.
            Chip("Open ${c?.optInt("open") ?: ""}".trim(), status == "open") { status = "open"; page = 1 }
            Chip("All ${c?.optInt("total") ?: ""}".trim(), status == "") { status = ""; page = 1 }
            meta.pairs("claim_statuses").forEach { (value, label) ->
                Chip(label, status == value) { status = value; page = 1 }
            }
        }
        Spacer(Modifier.height(rdp(8)))

        OutlinedTextField(
            value = q,
            onValueChange = { q = it; page = 1 },
            label = { Text("Search client, policy no., type") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(rdp(8)))

        if (error != null) ErrorStrip(error)
        if (loading && rows.isEmpty()) {
            LoadingBox()
        } else if (rows.isEmpty()) {
            EmptyState("🏥", "No claims here",
                if (status == "open") "Nothing open — every claim is settled or rejected."
                else "Nothing matches this filter.",
                actionLabel = "＋ Raise a claim", onAction = onRaise)
        } else {
            LazyColumn(verticalArrangement = Arrangement.spacedBy(rdp(8))) {
                items(rows) { c2 -> ClaimCard(c2) { onOpen(c2.optInt("id")) } }
                if (hasMore) {
                    item {
                        TextButton(onClick = { page++ }, modifier = Modifier.fillMaxWidth()) {
                            Text(if (loading) "Loading…" else "Load more")
                        }
                    }
                }
                item { Spacer(Modifier.height(rdp(16))) }
            }
        }
    }
}

@Composable
private fun ClaimCard(c: JSONObject, showClient: Boolean = true, onClick: () -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth().clickable(onClick = onClick),
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.fillMaxWidth().padding(rdp(14))) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text(
                    if (showClient) c.optString("client") else c.optString("claim_type"),
                    fontSize = rsp(15), fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.weight(1f),
                )
                Text(
                    c.optString("status_label"),
                    fontSize = rsp(11), fontWeight = FontWeight.SemiBold,
                    color = if (c.optBoolean("is_open")) StatusAmber else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Text(
                if (showClient) "${c.optString("claim_type")} · ${c.optString("policy_number")}"
                else c.optString("policy_number"),
                fontSize = rsp(12), color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Row(Modifier.fillMaxWidth().padding(top = rdp(4)),
                horizontalArrangement = Arrangement.SpaceBetween) {
                Text("Claimed ${rupees(c.optDouble("claimed", 0.0))}", fontSize = rsp(12))
                if (c.optDouble("settled", 0.0) > 0) {
                    Text("Settled ${rupees(c.optDouble("settled", 0.0))}",
                         fontSize = rsp(12), color = StatusGreen, fontWeight = FontWeight.SemiBold)
                }
            }
        }
    }
}

@Composable
private fun ClaimDetail(
    claimId: Int,
    meta: JSONObject,
    modifier: Modifier,
    onBack: () -> Unit,
    onOpenPolicy: (Int) -> Unit,
    onSessionExpired: () -> Unit,
    onOpenWeb: (String) -> Unit,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var data by remember { mutableStateOf<JSONObject?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var actionError by remember { mutableStateOf<String?>(null) }
    var reloadKey by remember { mutableIntStateOf(0) }
    var note by remember { mutableStateOf("") }
    var settled by remember { mutableStateOf("") }
    var reminderAt by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var uploading by remember { mutableStateOf(false) }
    var docKind by remember { mutableStateOf("other") }
    var kindMenu by remember { mutableStateOf(false) }
    var stageMenu by remember { mutableStateOf(false) }

    LaunchedEffect(claimId, reloadKey) {
        when (val r = ApiClient.get("/clients/api/app/claims/$claimId/")) {
            is ApiClient.Result.Ok -> data = r.json
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
    }

    val attacher = rememberAttacher { uri ->
        uploading = true
        Thread {
            val why = uploadUri(
                context, uri, "/clients/api/app/claims/$claimId/document/?kind=$docKind")
            uploading = false
            AppMessage.show(why ?: "Document uploaded")
            reloadKey++
        }.start()
    }

    fun submit(status: String?) {
        if (busy) return
        busy = true
        val body = JSONObject()
            .put("note", note)
            .put("reminder_at", reminderAt)
            .put("reminder_note", note)
        if (status != null) {
            body.put("status", status)
            if (settled.isNotBlank()) body.put("settled_amount", settled)
        }
        scope.launch {
            actionError = null
            when (val r = ApiClient.post("/clients/api/app/claims/$claimId/update/", body)) {
                is ApiClient.Result.Ok -> {
                    note = ""; reminderAt = ""; settled = ""
                    data = null; reloadKey++
                }
                is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                is ApiClient.Result.Error -> actionError = r.message
            }
            busy = false
        }
    }

    if (error != null) { ErrorBox(error!!, modifier) { error = null; reloadKey++ }; return }
    val d = data ?: run { LoadingBox(modifier); return }

    val stages = meta.strings("claim_stages")
    val statusLabels = meta.pairs("claim_statuses").toMap()
    val current = d.optString("status")

    Column(
        modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(rdp(16)),
        verticalArrangement = Arrangement.spacedBy(rdp(10)),
    ) {
        ScreenHeader(d.optString("client"), onBack = onBack)
        Text(
            "${d.optString("claim_type")} · ${d.optString("policy_number")} · ${d.optString("mode")}",
            fontSize = rsp(13), color = MaterialTheme.colorScheme.primary,
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = rdp(44))
                .clickable(role = androidx.compose.ui.semantics.Role.Button) {
                    onOpenPolicy(d.optInt("policy_id"))
                },
        )
        if (actionError != null) ErrorStrip(actionError)

        Stepper(stages.map { statusLabels[it] ?: it }, stages.indexOf(current))
        Text(
            d.optString("status_label"),
            fontSize = rsp(14), fontWeight = FontWeight.Bold,
        )

        FieldGrid(
            listOf(
                "Claimed" to rupees(d.optDouble("claimed", 0.0)),
                "Settled" to rupees(d.optDouble("settled", 0.0)),
                "Shortfall" to rupees(d.optDouble("shortfall", 0.0)),
                "Intimated" to fmtDateOr(d.optString("intimation_date")),
                "Submitted" to fmtDateOr(d.optString("submission_date")),
                "Handler" to d.optString("handler").ifBlank { "—" },
            )
        )

        // ── one submit: a stage move, a note, and a follow-up ──
        SectionTitle("Update")
        OutlinedTextField(
            value = note,
            onValueChange = { note = it },
            label = { Text("What happened? (saved on the timeline)") },
            modifier = Modifier.fillMaxWidth(),
        )
        DateTimeField("Follow-up reminder (optional)", reminderAt) { reminderAt = it }

        val next = stages.getOrNull(stages.indexOf(current) + 1)
        if (next == "settled" || current == "settled") {
            OutlinedTextField(
                value = settled,
                onValueChange = { settled = moneyInput(it) },
                label = { Text("Settled amount") },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                modifier = Modifier.fillMaxWidth(),
            )
        }

        ActionRow(Modifier.fillMaxWidth()) {
            if (next != null) {
                Button(onClick = { submit(next) }, enabled = !busy) {
                    Text("Move to ${statusLabels[next] ?: next} →", fontSize = rsp(14))
                }
            }
            OutlinedButton(onClick = { submit(null) }, enabled = !busy && (note.isNotBlank() || reminderAt.isNotBlank())) {
                Text("Save note", fontSize = rsp(14))
            }
            if (d.optBoolean("is_open")) {
                OutlinedButton(onClick = { stageMenu = true }, enabled = !busy) {
                    Text("Other stage", fontSize = rsp(14))
                }
                DropdownMenu(expanded = stageMenu, onDismissRequest = { stageMenu = false }) {
                    meta.pairs("claim_statuses").forEach { (value, label) ->
                        DropdownMenuItem(
                            text = { Text(label) },
                            onClick = { stageMenu = false; submit(value) },
                        )
                    }
                }
            }
        }

        // ── documents ──
        val docs = d.optJSONArray("documents")
        SectionTitle("Documents (${docs?.length() ?: 0})")
        PickerField(
            label = "Kind of document",
            value = meta.pairs("document_kinds").firstOrNull { it.first == docKind }?.second ?: "Other",
            onOpen = { kindMenu = true },
        ) {
            DropdownMenu(expanded = kindMenu, onDismissRequest = { kindMenu = false }) {
                meta.pairs("document_kinds").forEach { (value, label) ->
                    DropdownMenuItem(
                        text = { Text(label) },
                        onClick = { docKind = value; kindMenu = false },
                    )
                }
            }
        }
        ActionRow(Modifier.fillMaxWidth()) {
            // The bill is photographed in the hospital corridor, so the camera
            // is a button, not a trip through the gallery.
            OutlinedButton(onClick = { if (!uploading) attacher.takePhoto() }) {
                Text(if (uploading) "Uploading…" else "Take photo", fontSize = rsp(13))
            }
            OutlinedButton(onClick = { if (!uploading) attacher.pickFile() }) {
                Text("Choose file", fontSize = rsp(13))
            }
        }
        for (i in 0 until (docs?.length() ?: 0)) {
            val doc = docs!!.getJSONObject(i)
            Row(
                Modifier.fillMaxWidth()
                    .heightIn(min = rdp(44))
                    .clickable { onOpenWeb(doc.optString("url")) },
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text(doc.optString("filename"), fontSize = rsp(13))
                    Text(doc.optString("kind"), fontSize = rsp(11),
                         color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Text("open ›", fontSize = rsp(12), color = MaterialTheme.colorScheme.primary)
            }
        }

        // ── follow-ups (they are tasks) ──
        val reminders = d.optJSONArray("reminders")
        if ((reminders?.length() ?: 0) > 0) {
            SectionTitle("Follow-ups")
            for (i in 0 until reminders!!.length()) {
                val t = reminders.getJSONObject(i)
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text(t.optString("title"), fontSize = rsp(13), modifier = Modifier.weight(1f))
                    Text(
                        fmtDateOr(t.optString("due")),
                        fontSize = rsp(12),
                        color = if (t.optBoolean("open")) StatusAmber else StatusGreen,
                    )
                }
            }
            Text("Worked on the Tasks screen — a follow-up is a task.",
                 fontSize = rsp(11), color = MaterialTheme.colorScheme.onSurfaceVariant)
        }

        // ── timeline ──
        val acts = d.optJSONArray("activities")
        SectionTitle("Timeline")
        for (i in 0 until (acts?.length() ?: 0)) {
            val a = acts!!.getJSONObject(i)
            Column(Modifier.fillMaxWidth().padding(bottom = rdp(6))) {
                Text(a.optString("detail").ifBlank { a.optString("action") }, fontSize = rsp(13))
                Text("${a.optString("action")} · ${a.optString("actor")} · ${a.optString("at")}",
                     fontSize = rsp(11), color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        Spacer(Modifier.height(rdp(16)))
    }
}

// ───────────────────────────── raise a claim ─────────────────────────────

@Composable
private fun RaiseClaimForm(
    meta: JSONObject,
    policyId: Int?,
    modifier: Modifier,
    onBack: () -> Unit,
    onRaised: (Int) -> Unit,
    onSessionExpired: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    var picked by remember { mutableStateOf<JSONObject?>(null) }
    var q by remember { mutableStateOf("") }
    var found by remember { mutableStateOf(listOf<JSONObject>()) }
    var claimType by remember { mutableStateOf("Hospitalisation Claim") }
    var mode by remember { mutableStateOf(meta.pairs("claim_modes").firstOrNull()?.first ?: "cashless") }
    var modeMenu by remember { mutableStateOf(false) }
    var amount by remember { mutableStateOf("") }
    var admission by remember { mutableStateOf("") }
    var note by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    // Reached from a policy: fetch it so the form can name it.
    LaunchedEffect(policyId) {
        if (policyId == null) return@LaunchedEffect
        when (val r = ApiClient.get("/clients/api/app/policies/$policyId/")) {
            is ApiClient.Result.Ok -> picked = r.json
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
    }

    LaunchedEffect(q) {
        if (picked != null || q.length < 2) return@LaunchedEffect
        delay(350)
        val path = "/clients/api/app/policies/?q=" + java.net.URLEncoder.encode(q, "UTF-8")
        when (val r = ApiClient.get(path)) {
            is ApiClient.Result.Ok -> {
                val arr = r.json.optJSONArray("results")
                found = (0 until (arr?.length() ?: 0)).map { arr!!.getJSONObject(it) }
            }
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
    }

    Column(
        modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(rdp(16)),
        verticalArrangement = Arrangement.spacedBy(rdp(10)),
    ) {
        ScreenHeader("Raise claim", onBack = onBack)
        if (error != null) ErrorStrip(error)

        val p = picked
        if (p == null) {
            OutlinedTextField(
                value = q,
                onValueChange = { q = it },
                label = { Text("Search the policy (client or policy no.)") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            found.forEach { row ->
                Card(
                    modifier = Modifier.fillMaxWidth().clickable { picked = row },
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                ) {
                    Column(Modifier.padding(rdp(12))) {
                        Text(row.optString("client"), fontSize = rsp(14), fontWeight = FontWeight.SemiBold)
                        Text("${row.optString("number")} · ${row.optString("insurer")}",
                             fontSize = rsp(12), color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
            return@Column
        }

        Text("${p.optString("client")} · ${p.optString("number")}",
             fontSize = rsp(15), fontWeight = FontWeight.SemiBold)
        if (policyId == null) {
            TextButton(onClick = { picked = null }) { Text("Choose a different policy") }
        }

        OutlinedTextField(
            value = claimType,
            onValueChange = { claimType = it },
            label = { Text("Claim type") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        PickerField(
            label = "Cashless or reimbursement",
            value = meta.pairs("claim_modes").firstOrNull { it.first == mode }?.second ?: mode,
            onOpen = { modeMenu = true },
        ) {
            DropdownMenu(expanded = modeMenu, onDismissRequest = { modeMenu = false }) {
                meta.pairs("claim_modes").forEach { (value, label) ->
                    DropdownMenuItem(text = { Text(label) },
                                     onClick = { mode = value; modeMenu = false })
                }
            }
        }
        OutlinedTextField(
            value = amount,
            onValueChange = { amount = moneyInput(it) },
            label = { Text("Claimed amount") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
            modifier = Modifier.fillMaxWidth(),
        )
        DateField("Admission date (optional)", admission, maxToday = true) { admission = it }
        OutlinedTextField(
            value = note,
            onValueChange = { note = it },
            label = { Text("First note (optional)") },
            modifier = Modifier.fillMaxWidth(),
        )

        Button(
            onClick = {
                if (busy) return@Button
                busy = true
                scope.launch {
                    error = null
                    val body = JSONObject()
                        .put("policy_id", p.optInt("id"))
                        .put("claim_type", claimType)
                        .put("claim_mode", mode)
                        .put("claimed_amount", amount)
                        .put("admission_date", admission)
                        .put("note", note)
                    when (val r = ApiClient.post("/clients/api/app/claims/create/", body)) {
                        is ApiClient.Result.Ok -> onRaised(r.json.optInt("id"))
                        is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                        is ApiClient.Result.Error -> error = r.message
                    }
                    busy = false
                }
            },
            enabled = !busy,
            modifier = Modifier.fillMaxWidth().heightIn(min = rdp(52)),
        ) { Text(if (busy) "Raising…" else "Raise claim", fontSize = rsp(15)) }
        Spacer(Modifier.height(rdp(16)))
    }
}

// ───────────────────────────── small shared bits ─────────────────────────────

/** Label/value pairs, two to a row — the phone version of the web field grid. */
@Composable
private fun FieldGrid(fields: List<Pair<String, String>>) {
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(rdp(6))) {
        fields.chunked(2).forEach { pair ->
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(rdp(10))) {
                pair.forEach { (label, value) ->
                    Column(Modifier.weight(1f)) {
                        Text(label, fontSize = rsp(11),
                             color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text(value, fontSize = rsp(14), fontWeight = FontWeight.SemiBold)
                    }
                }
                if (pair.size == 1) Spacer(Modifier.weight(1f))
            }
        }
    }
}

private fun fmtDateOr(iso: String?): String =
    if (iso.isNullOrBlank() || iso == "null") "—" else fmtDate(iso)
