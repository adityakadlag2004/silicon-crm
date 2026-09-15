package bo.kadlaginvestment.crm.ui

import android.content.Intent
import android.net.Uri
import androidx.activity.compose.BackHandler
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
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import bo.kadlaginvestment.crm.net.ApiClient
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

/** Native SPANCO leads pipeline: stage-filtered list, a lead's stepper +
 * requirements, remarks, stage history, convert-to-client, and a create form.
 *
 * Stages come from `/lead-meta/` rather than being hard-coded here — the
 * server owns the method (labels, order and the meaning of each step), the
 * app just draws it, so a change to SPANCO never needs an APK. */
@Composable
fun LeadsScreen(
    modifier: Modifier = Modifier,
    initialLeadId: Int? = null,
    initialStage: String = "",
    onBack: (() -> Unit)? = null,
    onSessionExpired: () -> Unit,
) {
    if (onBack != null) BackHandler(onBack = onBack)

    // Opening straight onto a lead is how the dashboard's "needs you" list
    // hands over. Keyed on the id so a second hand-over lands on that lead
    // rather than being swallowed by the remembered state.
    var selectedLeadId by remember(initialLeadId) { mutableStateOf(initialLeadId) }
    var creating by remember { mutableStateOf(false) }
    var listReload by remember { mutableIntStateOf(0) }
    var meta by remember { mutableStateOf<JSONObject?>(null) }
    var metaError by remember { mutableStateOf<String?>(null) }
    var metaReload by remember { mutableIntStateOf(0) }

    LaunchedEffect(metaReload) {
        when (val r = ApiClient.get("/clients/api/app/lead-meta/")) {
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
        creating -> {
            BackHandler { creating = false }
            LeadCreateForm(m, modifier, onDone = { creating = false; listReload++ }, onSessionExpired = onSessionExpired)
        }
        selectedLeadId != null -> {
            BackHandler { selectedLeadId = null; listReload++ }
            LeadDetail(
                leadId = selectedLeadId!!,
                meta = m,
                modifier = modifier,
                onBack = { selectedLeadId = null; listReload++ },
                onSessionExpired = onSessionExpired,
            )
        }
        else -> LeadList(
            meta = m,
            modifier = modifier,
            initialStage = initialStage,
            onBack = onBack,
            reloadKey = listReload,
            onOpen = { selectedLeadId = it },
            onCreate = { creating = true },
            onSessionExpired = onSessionExpired,
        )
    }
}

/** [{value,label,help}] straight off the meta payload. */
private fun JSONObject.stageList(): List<Triple<String, String, String>> {
    val arr = optJSONArray("stages") ?: JSONArray()
    return (0 until arr.length()).map {
        val o = arr.getJSONObject(it)
        Triple(o.optString("value"), o.optString("label"), o.optString("help"))
    }
}

private fun JSONObject.productList(): List<Pair<Int, String>> {
    val arr = optJSONArray("products") ?: JSONArray()
    return (0 until arr.length()).map {
        val o = arr.getJSONObject(it)
        o.getInt("id") to o.optString("name")
    }
}

@Composable
private fun LeadList(
    meta: JSONObject,
    modifier: Modifier,
    initialStage: String,
    onBack: (() -> Unit)?,
    reloadKey: Int,
    onOpen: (Int) -> Unit,
    onCreate: () -> Unit,
    onSessionExpired: () -> Unit,
) {
    var stage by remember(initialStage) { mutableStateOf(initialStage) }
    var q by remember { mutableStateOf("") }
    var rows by remember { mutableStateOf(listOf<JSONObject>()) }
    var counts by remember { mutableStateOf<JSONObject?>(null) }
    var page by remember { mutableIntStateOf(1) }
    var hasMore by remember { mutableStateOf(false) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var localReload by remember { mutableIntStateOf(0) }
    val stageOrder = remember(meta) { meta.stageList().map { it.first } }

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

    Column(modifier.fillMaxSize().padding(horizontal = rdp(16))) {
        ScreenHeader("Leads", onBack = onBack) {
            Button(onClick = onCreate) { Text("＋ Add") }
        }

        val c = counts
        Row(
            Modifier.horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(rdp(6)),
        ) {
            Chip("All", stage == "") { stage = ""; page = 1 }
            meta.stageList().forEach { (value, label, _) ->
                Chip("$label ${c?.optInt(value) ?: ""}".trim(), stage == value) { stage = value; page = 1 }
            }
            Chip("Lost ${c?.optInt("lost") ?: ""}".trim(), stage == "lost") { stage = "lost"; page = 1 }
        }
        Spacer(Modifier.height(rdp(8)))

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
                verticalArrangement = Arrangement.spacedBy(rdp(8)),
                contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = rdp(12)),
            ) {
                if (rows.isEmpty()) {
                    item {
                        EmptyState(
                            "🫧", "No leads here",
                            if (stage.isBlank()) "Add a lead to start working the pipeline."
                            else "Nothing at this stage right now.",
                            modifier = Modifier.heightIn(min = rdp(200)),
                            actionLabel = if (stage.isBlank()) "＋ Add lead" else null,
                            onAction = if (stage.isBlank()) onCreate else null,
                        )
                    }
                }
                items(rows) { l ->
                    Card(
                        modifier = Modifier.fillMaxWidth().clickable { onOpen(l.getInt("id")) },
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
                    ) {
                        Row(
                            Modifier.fillMaxWidth().padding(rdp(14)),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Column(Modifier.weight(1f)) {
                                Text(l.optString("name"), fontWeight = FontWeight.SemiBold, fontSize = rsp(15))
                                Text(
                                    listOf(l.optString("phone"), l.optString("assigned_to"))
                                        .filter { it.isNotEmpty() }.joinToString(" · "),
                                    fontSize = rsp(12), color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                                if (l.optString("interests").isNotEmpty()) {
                                    Text(
                                        l.optString("interests"),
                                        fontSize = rsp(11), color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                }
                            }
                            Column(horizontalAlignment = Alignment.End) {
                                LeadStagePill(
                                    l.optString("stage_label"),
                                    l.optBoolean("is_discarded"),
                                    l.optBoolean("converted"),
                                )
                                // Position on the roadmap, so a list scan shows
                                // how far each lead has actually come.
                                val step = stageOrder.indexOf(l.optString("stage")) + 1
                                Text(
                                    if (step > 0) "Step $step of ${stageOrder.size} · ${l.optInt("days_in_stage")}d"
                                    else "${l.optInt("days_in_stage")}d here",
                                    fontSize = rsp(11), color = MaterialTheme.colorScheme.onSurfaceVariant,
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
                item { Spacer(Modifier.height(rdp(12))) }
            }
        }
    }
}

@Composable
private fun LeadStagePill(stageLabel: String, lost: Boolean, converted: Boolean) {
    val (color, label) = when {
        converted -> StatusGreen to "Converted"
        lost -> StatusRed to "Lost"
        stageLabel == "Order" -> StatusGreen to stageLabel
        else -> BrandMuted to stageLabel
    }
    Text(label, fontSize = rsp(11), color = color, fontWeight = FontWeight.Bold)
}

@Composable
private fun LeadDetail(
    leadId: Int,
    meta: JSONObject,
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
    var stageNote by remember { mutableStateOf("") }
    var followupAt by remember { mutableStateOf("") }
    var followupNote by remember { mutableStateOf("") }
    var correcting by remember { mutableStateOf(false) }
    var productMenuOpen by remember { mutableStateOf(false) }
    var reloadKey by remember { mutableIntStateOf(0) }
    var docs by remember { mutableStateOf<JSONObject?>(null) }
    var docsKey by remember { mutableIntStateOf(0) }
    var uploading by remember { mutableStateOf(false) }
    // The question to ask, and the body to post once the user says yes.
    var confirmDelete by remember { mutableStateOf<Pair<String, JSONObject>?>(null) }

    LaunchedEffect(leadId, reloadKey) {
        when (val r = ApiClient.get("/clients/api/app/leads/$leadId/")) {
            is ApiClient.Result.Ok -> data = r.json
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
    }

    // Documents come out of the lead's Drive folder in their own request, so
    // a slow Drive never holds up the lead itself.
    LaunchedEffect(leadId, docsKey) {
        when (val r = ApiClient.get("/clients/api/app/leads/$leadId/documents/")) {
            is ApiClient.Result.Ok -> docs = r.json
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> docs = JSONObject().put("error", r.message)
        }
    }

    val attacher = rememberAttacher { uri ->
        uploading = true
        Thread {
            val why = uploadUri(context, uri, "/clients/api/app/leads/$leadId/documents/")
            uploading = false
            AppMessage.show(why ?: "Document uploaded")
            docsKey++
        }.start()
    }

    /** Straight into Downloads with the session cookie — the same hand-off
     * WebActivity makes for report exports. */
    fun download(path: String, name: String) {
        try {
            val url = bo.kadlaginvestment.crm.BackendClient.BASE_URL + path + "?download=1"
            val req = android.app.DownloadManager.Request(Uri.parse(url))
                .addRequestHeader("Cookie", android.webkit.CookieManager.getInstance().getCookie(url) ?: "")
                .setTitle(name)
                .setNotificationVisibility(
                    android.app.DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalPublicDir(android.os.Environment.DIRECTORY_DOWNLOADS, name)
            (context.getSystemService(android.content.Context.DOWNLOAD_SERVICE)
                as android.app.DownloadManager).enqueue(req)
            AppMessage.show("Downloading $name…")
        } catch (e: Exception) {
            AppMessage.show("Could not start the download")
        }
    }

    /** `queue` parks the write offline when there is no signal. Only pass it
     * for a write a replay cannot duplicate — a stage move is deduped server
     * side (`services.leads.set_stage`); a remark or an interest is not. */
    fun post(
        path: String,
        body: JSONObject,
        queue: android.content.Context? = null,
        then: () -> Unit = { data = null; reloadKey++ },
    ) {
        scope.launch {
            actionError = null
            when (val r = ApiClient.post(path, body, queue)) {
                is ApiClient.Result.Ok ->
                    // Parked offline: say so and leave the screen alone. The
                    // reload `then` does would only fail on the same dead
                    // network and swap the lead for an error box.
                    if (r.json.optBoolean("queued")) {
                        AppMessage.show("No signal — saved on the phone, it will sync.")
                    } else then()
                is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                is ApiClient.Result.Error -> actionError = r.message
            }
        }
    }

    if (error != null) { ErrorBox(error!!, modifier) { error = null; reloadKey++ }; return }
    val d = data ?: run { LoadingBox(modifier); return }
    val stages = meta.stageList()
    val currentStage = d.optString("stage")

    Column(
        modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(rdp(16)),
        verticalArrangement = Arrangement.spacedBy(rdp(12)),
    ) {
        ScreenHeader(d.optString("name"), onBack = onBack) {
            LeadStagePill(
                d.optString("stage_label"),
                d.optBoolean("is_discarded"),
                d.optInt("converted_client_id") > 0,
            )
        }

        val phone = d.optString("phone")
        ActionRow {
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
                    post(
                        "/clients/api/app/leads/$leadId/action/",
                        JSONObject().put("action", "discard").put("reason", stageNote),
                    )
                }) { Text("Mark lost") }
            }
        }

        actionError?.let { Text(it, color = StatusRed, fontSize = rsp(13), fontWeight = FontWeight.SemiBold) }
        if (d.optInt("converted_client_id") > 0) {
            Text(
                "Converted to client #${d.optInt("converted_client_id")}",
                color = StatusGreen, fontSize = rsp(13), fontWeight = FontWeight.SemiBold,
            )
        }

        SectionTitle("SPANCO stage")
        Stepper(stages.map { it.second }, stages.indexOfFirst { it.first == currentStage })
        Text(
            "${d.optString("stage_label")} · ${d.optInt("days_in_stage")} days here",
            fontSize = rsp(13), fontWeight = FontWeight.SemiBold,
        )
        Text(
            d.optString("stage_help"),
            fontSize = rsp(12), color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        OutlinedTextField(
            value = stageNote,
            onValueChange = { stageNote = it },
            label = { Text("What happened? (saved with the move)") },
            modifier = Modifier.fillMaxWidth(),
        )

        fun move(stage: String) {
            val note = stageNote
            stageNote = ""
            // The one write that happens standing in a client's living room,
            // where the signal is worst.
            post(
                "/clients/api/app/leads/$leadId/stage/",
                JSONObject().put("stage", stage).put("note", note),
                queue = context,
            )
        }

        // Advancing one step is the whole job; correcting the stage is the
        // exception, so it sits behind a disclosure rather than six equal chips.
        val next = d.optString("next_stage")
        if (next.isNotEmpty()) {
            val nextLabel = stages.firstOrNull { it.first == next }
            Button(
                onClick = { move(next) },
                modifier = Modifier.fillMaxWidth().heightIn(min = rdp(52)),
            ) { Text("Move to ${nextLabel?.second ?: next} →", fontSize = rsp(15)) }
            Text(
                nextLabel?.third ?: "",
                fontSize = rsp(11), color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        TextButton(onClick = { correcting = !correcting }) {
            Text(if (correcting) "Hide stage picker" else "Set a different stage")
        }
        if (correcting) {
            ActionRow {
                stages.forEach { (value, label, _) ->
                    Chip(label, currentStage == value) {
                        if (currentStage != value) move(value)
                    }
                }
            }
        }

        SectionTitle("Requirements")
        val interests = d.optJSONArray("interests")
        if ((interests?.length() ?: 0) == 0) {
            Text(
                "Nothing captured yet — add only what this lead actually needs.",
                fontSize = rsp(12), color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        for (i in 0 until (interests?.length() ?: 0)) {
            val p = interests!!.getJSONObject(i)
            Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                Row(
                    Modifier.fillMaxWidth().padding(rdp(12)),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(Modifier.weight(1f)) {
                        Text(p.optString("label"), fontWeight = FontWeight.SemiBold, fontSize = rsp(14))
                        val amount = p.optDouble("amount", 0.0)
                        if (amount > 0) {
                            Text(
                                rupees(amount), fontSize = rsp(12),
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        if (p.optString("note").isNotEmpty()) {
                            Text(
                                p.optString("note"), fontSize = rsp(11),
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                    TextButton(onClick = {
                        post(
                            "/clients/api/app/leads/$leadId/interest/",
                            JSONObject().put("remove", p.optInt("id")),
                        )
                    }) { Text("Remove") }
                }
            }
        }
        PickerField(
            label = "Add a product this lead needs",
            value = "Pick a product",
            onOpen = { productMenuOpen = true },
        ) {
            DropdownMenu(expanded = productMenuOpen, onDismissRequest = { productMenuOpen = false }) {
                meta.productList().forEach { (id, name) ->
                    DropdownMenuItem(
                        text = { Text(name) },
                        onClick = {
                            productMenuOpen = false
                            post(
                                "/clients/api/app/leads/$leadId/interest/",
                                JSONObject().put("product_id", id),
                            )
                        },
                    )
                }
            }
        }

        // ── Follow-ups ──
        // A follow-up IS a task, so these rows are tasks and tapping one opens
        // it in the Tasks module — closing and rescheduling live there, and a
        // second copy of those buttons here would be a second code path.
        val followups = d.optJSONArray("followups")
        SectionTitle("Follow-ups (${followups?.length() ?: 0})")
        if ((followups?.length() ?: 0) == 0) {
            Text(
                "Nothing scheduled — this lead is not being chased.",
                fontSize = rsp(13), color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        for (i in 0 until (followups?.length() ?: 0)) {
            val f = followups!!.getJSONObject(i)
            Row(
                Modifier
                    .fillMaxWidth()
                    .heightIn(min = rdp(44))
                    .clickable(role = androidx.compose.ui.semantics.Role.Button) {
                        bo.kadlaginvestment.crm.TasksActivity.open(context, f.optInt("id"))
                    },
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text(
                        f.optString("note").ifBlank { "Follow-up" },
                        fontSize = rsp(13),
                    )
                    Text(
                        listOf(
                            fmtDate(f.optString("due")),
                            f.optString("due_time"),
                            f.optString("assigned_to"),
                        ).filter { it.isNotBlank() }.joinToString(" · "),
                        fontSize = rsp(11), color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Text(
                    f.optString("status_label"),
                    fontSize = rsp(11), fontWeight = FontWeight.SemiBold,
                    color = if (f.optBoolean("open")) StatusAmber else StatusGreen,
                )
            }
        }

        DateTimeField("Follow up on", followupAt) { followupAt = it }
        OutlinedTextField(
            value = followupNote,
            onValueChange = { followupNote = it },
            label = { Text("What is the follow-up for?") },
            modifier = Modifier.fillMaxWidth(),
        )
        Button(
            onClick = {
                val at = followupAt
                val note = followupNote
                followupAt = ""; followupNote = ""
                post(
                    "/clients/api/app/leads/$leadId/followup/",
                    JSONObject().put("when", at).put("note", note),
                )
            },
            enabled = followupAt.isNotBlank(),
            modifier = Modifier.fillMaxWidth().heightIn(min = rdp(52)),
        ) { Text("Schedule follow-up", fontSize = rsp(15)) }

        // ── Documents: quotations and papers in the lead's Drive folder ──
        val files = docs?.optJSONArray("files")
        SectionTitle("Documents (${files?.length() ?: 0})")
        docs?.optString("error")?.takeIf { it.isNotBlank() }?.let {
            Text(it, color = StatusRed, fontSize = rsp(12))
        }
        ActionRow(Modifier.fillMaxWidth()) {
            OutlinedButton(onClick = { if (!uploading) attacher.takePhoto() }) {
                Text(if (uploading) "Uploading…" else "Take photo", fontSize = rsp(13))
            }
            OutlinedButton(onClick = { if (!uploading) attacher.pickFile() }) {
                Text("Choose file", fontSize = rsp(13))
            }
        }
        if (docs != null && (files?.length() ?: 0) == 0) {
            Text(
                "No documents yet — upload the quotations sent to this lead.",
                fontSize = rsp(12), color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        for (i in 0 until (files?.length() ?: 0)) {
            val f = files!!.getJSONObject(i)
            val name = f.optString("name")
            val path = f.optString("url")
            Column(Modifier.fillMaxWidth().padding(vertical = rdp(2))) {
                Text(name, fontSize = rsp(13), fontWeight = FontWeight.SemiBold)
                Text(
                    listOf(f.optString("size_label"), f.optString("modified"))
                        .filter { it.isNotBlank() }.joinToString(" · "),
                    fontSize = rsp(11), color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                ActionRow {
                    TextButton(onClick = { bo.kadlaginvestment.crm.WebActivity.open(context, path) }) {
                        Text("View", fontSize = rsp(13))
                    }
                    TextButton(onClick = { download(path, name) }) {
                        Text("Download", fontSize = rsp(13))
                    }
                    TextButton(onClick = {
                        confirmDelete = "Delete “$name” from Drive?" to
                            JSONObject().put("file_id", f.optString("id"))
                    }) { Text("Delete", fontSize = rsp(13), color = StatusRed) }
                }
            }
        }
        if (docs?.optBoolean("has_folder") == true && docs?.optBoolean("can_delete_folder") == true) {
            OutlinedButton(onClick = {
                confirmDelete = "Permanently delete this lead's Drive folder and every file in it? " +
                    "This cannot be undone." to JSONObject().put("folder", true)
            }) { Text("Delete Drive folder", fontSize = rsp(13), color = StatusRed) }
        }
        confirmDelete?.let { (question, body) ->
            AlertDialog(
                onDismissRequest = { confirmDelete = null },
                title = { Text("Delete?", fontSize = rsp(18), fontWeight = FontWeight.Bold) },
                text = { Text(question, fontSize = rsp(14)) },
                confirmButton = {
                    TextButton(onClick = {
                        confirmDelete = null
                        post("/clients/api/app/leads/$leadId/documents/delete/", body,
                             then = { AppMessage.show("Deleted"); docsKey++ })
                    }) { Text("Delete", fontSize = rsp(14), color = StatusRed) }
                },
                dismissButton = {
                    TextButton(onClick = { confirmDelete = null }) { Text("Cancel", fontSize = rsp(14)) }
                },
            )
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
            Column(Modifier.fillMaxWidth().padding(vertical = rdp(2))) {
                Text(r.optString("text"), fontSize = rsp(13))
                Text(
                    "${r.optString("by")} · ${r.optString("at")}",
                    fontSize = rsp(11), color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }

        SectionTitle("Stage history")
        val timeline = d.optJSONArray("timeline")
        for (i in 0 until (timeline?.length() ?: 0)) {
            val e = timeline!!.getJSONObject(i)
            Column(Modifier.fillMaxWidth().padding(vertical = rdp(2))) {
                Text(
                    "${e.optString("from")} → ${e.optString("to")}" +
                        if (e.optString("note").isNotEmpty()) " · ${e.optString("note")}" else "",
                    fontSize = rsp(12), fontWeight = FontWeight.SemiBold,
                )
                Text(
                    "${e.optString("by")} · ${e.optString("at")}",
                    fontSize = rsp(11), color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }

        Spacer(Modifier.height(rdp(20)))
    }
}

@Composable
private fun LeadCreateForm(
    meta: JSONObject,
    modifier: Modifier,
    onDone: () -> Unit,
    onSessionExpired: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    val stages = meta.stageList()
    val products = meta.productList()

    var name by remember { mutableStateOf("") }
    var phone by remember { mutableStateOf("") }
    var pickedName by remember { mutableStateOf("") }
    var email by remember { mutableStateOf("") }
    var income by remember { mutableStateOf("") }
    var notes by remember { mutableStateOf("") }
    var stage by remember { mutableStateOf(stages.firstOrNull()?.first ?: "suspect") }
    var stageMenuOpen by remember { mutableStateOf(false) }
    var pickedProducts by remember { mutableStateOf(setOf<Int>()) }
    var selectedEmployee by remember { mutableStateOf<Pair<Int, String>?>(null) }
    var employeeMenuOpen by remember { mutableStateOf(false) }
    var submitting by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }

    Column(
        modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(rdp(16)),
        verticalArrangement = Arrangement.spacedBy(rdp(12)),
    ) {
        ScreenHeader("New Lead", onBack = onDone)

        OutlinedTextField(
            value = name, onValueChange = { name = it }, label = { Text("Customer name *") },
            keyboardOptions = KeyboardOptions(capitalization = androidx.compose.ui.text.input.KeyboardCapitalization.Words),
            modifier = Modifier.fillMaxWidth(), singleLine = true,
        )
        OutlinedTextField(
            value = phone, onValueChange = { phone = it; pickedName = "" }, label = { Text("Phone") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone),
            modifier = Modifier.fillMaxWidth(), singleLine = true,
        )
        // A lead is usually created with the person's card already open in the
        // phonebook: one tap fills the number and the name it belongs to.
        ContactPickButton(
            label = if (pickedName.isEmpty()) "\uD83D\uDC64 Pick from contacts" else "\uD83D\uDC64 $pickedName",
            modifier = Modifier.fillMaxWidth(),
        ) { contactName, contactPhone ->
            phone = contactPhone
            pickedName = contactName
            // Never overwrite a name already typed — the picker is a shortcut.
            if (name.isBlank()) name = contactName
        }
        OutlinedTextField(
            value = email, onValueChange = { email = it }, label = { Text("Email") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email),
            modifier = Modifier.fillMaxWidth(), singleLine = true,
        )
        OutlinedTextField(
            value = income, onValueChange = { income = moneyInput(it) },
            label = { Text("Annual income (₹, optional)") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
            modifier = Modifier.fillMaxWidth(), singleLine = true,
        )
        OutlinedTextField(value = notes, onValueChange = { notes = it }, label = { Text("Notes") }, modifier = Modifier.fillMaxWidth())

        PickerField(
            label = "Starting stage",
            value = stages.firstOrNull { it.first == stage }?.second ?: "Suspect",
            onOpen = { stageMenuOpen = true },
        ) {
            DropdownMenu(expanded = stageMenuOpen, onDismissRequest = { stageMenuOpen = false }) {
                stages.forEach { (value, label, _) ->
                    DropdownMenuItem(
                        text = { Text(label) },
                        onClick = { stage = value; stageMenuOpen = false },
                    )
                }
            }
        }
        Text(
            stages.firstOrNull { it.first == stage }?.third ?: "",
            fontSize = rsp(11), color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        SectionTitle("What do they need?")
        Text(
            "Optional — tap only the products this lead is actually after.",
            fontSize = rsp(11), color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        ActionRow {
            products.forEach { (id, label) ->
                Chip(label, pickedProducts.contains(id)) {
                    pickedProducts = if (pickedProducts.contains(id)) pickedProducts - id else pickedProducts + id
                }
            }
        }

        if (meta.optBoolean("can_assign")) {
            PickerField(
                label = "Assign to",
                value = selectedEmployee?.second ?: "Myself",
                onOpen = { employeeMenuOpen = true },
            ) {
                DropdownMenu(expanded = employeeMenuOpen, onDismissRequest = { employeeMenuOpen = false }) {
                    val es = meta.optJSONArray("employees")
                    for (i in 0 until (es?.length() ?: 0)) {
                        val e = es!!.getJSONObject(i)
                        DropdownMenuItem(
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

        message?.let { Text(it, color = StatusRed, fontSize = rsp(13), fontWeight = FontWeight.SemiBold) }

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
                        .put("stage", stage)
                        .put("product_ids", JSONArray(pickedProducts.toList()))
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
            modifier = Modifier.fillMaxWidth().heightIn(min = rdp(52)),
        ) { Text(if (submitting) "Saving…" else "Create Lead", fontSize = rsp(16)) }

        Spacer(Modifier.height(rdp(24)))
    }
}
