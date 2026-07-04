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
import androidx.compose.runtime.mutableStateMapOf
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
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

/** Native Lead Records (sheets): sheet list → record cards → dynamic
 * add/edit form built from the sheet's own column definitions. */
@Composable
fun SheetsScreen(
    modifier: Modifier = Modifier,
    onBack: () -> Unit,
    onSessionExpired: () -> Unit,
    onOpenWeb: (String) -> Unit,
) {
    BackHandler(onBack = onBack)
    var openSheetId by remember { mutableStateOf<Int?>(null) }

    if (openSheetId != null) {
        BackHandler { openSheetId = null }
        SheetRecords(
            sheetId = openSheetId!!,
            modifier = modifier,
            onBack = { openSheetId = null },
            onSessionExpired = onSessionExpired,
            onOpenWeb = onOpenWeb,
        )
        return
    }

    var rows by remember { mutableStateOf<List<JSONObject>?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var reloadKey by remember { mutableIntStateOf(0) }

    LaunchedEffect(reloadKey) {
        when (val r = ApiClient.get("/clients/api/app/sheets/")) {
            is ApiClient.Result.Ok -> {
                val arr = r.json.optJSONArray("results")
                rows = (0 until (arr?.length() ?: 0)).map { arr!!.getJSONObject(it) }
            }
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
    }

    Column(modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        Row(
            Modifier.fillMaxWidth().padding(top = 16.dp, bottom = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "← Back",
                color = MaterialTheme.colorScheme.secondary,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.clickable(onClick = onBack).padding(end = 12.dp),
            )
            Text("Lead Records", fontSize = 22.sp, fontWeight = FontWeight.Bold)
        }
        Text(
            "Sheet setup (columns, sharing, public form) is on the web — records work here.",
            fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(8.dp))

        when {
            error != null -> ErrorBox(error!!) { error = null; reloadKey++ }
            rows == null -> LoadingBox()
            else -> LazyColumn(
                Modifier.fillMaxSize(),
                verticalArrangement = Arrangement.spacedBy(8.dp),
                contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 8.dp),
            ) {
                if (rows!!.isEmpty()) {
                    item { Text("No sheets shared with you yet.", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp) }
                }
                items(rows!!) { s ->
                    Card(
                        modifier = Modifier.fillMaxWidth().clickable { openSheetId = s.getInt("id") },
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
                    ) {
                        Row(
                            Modifier.fillMaxWidth().padding(14.dp),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Column(Modifier.weight(1f)) {
                                Text(s.optString("name"), fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
                                Text(
                                    listOf(
                                        s.optString("product"),
                                        if (s.optBoolean("is_private")) "private" else "",
                                    ).filter { it.isNotEmpty() }.joinToString(" · "),
                                    fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            Text(
                                "${s.optInt("record_count")} rows",
                                fontSize = 13.sp, fontWeight = FontWeight.Bold,
                            )
                        }
                    }
                }
                item { Spacer(Modifier.height(12.dp)) }
            }
        }
    }
}

@Composable
private fun SheetRecords(
    sheetId: Int,
    modifier: Modifier,
    onBack: () -> Unit,
    onSessionExpired: () -> Unit,
    onOpenWeb: (String) -> Unit,
) {
    var q by remember { mutableStateOf("") }
    var data by remember { mutableStateOf<JSONObject?>(null) }
    var rows by remember { mutableStateOf(listOf<JSONObject>()) }
    var page by remember { mutableIntStateOf(1) }
    var hasMore by remember { mutableStateOf(false) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var reloadKey by remember { mutableIntStateOf(0) }
    var editing by remember { mutableStateOf<JSONObject?>(null) }  // record or empty JSONObject for new

    LaunchedEffect(q, page, reloadKey) {
        loading = true
        error = null
        if (q.isNotEmpty()) delay(350)
        val path = "/clients/api/app/sheets/$sheetId/?page=$page&q=" +
            java.net.URLEncoder.encode(q, "UTF-8")
        when (val r = ApiClient.get(path)) {
            is ApiClient.Result.Ok -> {
                data = r.json
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

    val d = data
    val columns = d?.optJSONArray("columns")
    val canEdit = d?.optJSONObject("sheet")?.optBoolean("can_edit") == true

    editing?.let { record ->
        if (columns != null) {
            BackHandler { editing = null }
            RecordEditor(
                sheetId = sheetId,
                columns = columns,
                record = record.takeIf { it.has("id") },
                modifier = modifier,
                onDone = { editing = null; page = 1; reloadKey++ },
                onSessionExpired = onSessionExpired,
            )
            return
        }
    }

    Column(modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        Row(
            Modifier.fillMaxWidth().padding(top = 16.dp, bottom = 8.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.weight(1f)) {
                Text(
                    "← Back",
                    color = MaterialTheme.colorScheme.secondary,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.clickable(onClick = onBack).padding(end = 12.dp),
                )
                Text(
                    d?.optJSONObject("sheet")?.optString("name") ?: "Sheet",
                    fontSize = 19.sp, fontWeight = FontWeight.Bold,
                    maxLines = 1,
                )
            }
            if (canEdit) {
                Button(onClick = { editing = JSONObject() }) { Text("＋ Add") }
            }
        }

        OutlinedTextField(
            value = q,
            onValueChange = { q = it; page = 1 },
            label = { Text("Search rows") },
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
                    item { Text("No rows here yet.", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp) }
                }
                items(rows) { r ->
                    val vals = r.optJSONObject("values") ?: JSONObject()
                    // Headline: first non-empty value; sub: next two.
                    val ordered = (0 until (columns?.length() ?: 0)).map { columns!!.getJSONObject(it) }
                    val headline = ordered.firstNotNullOfOrNull { c ->
                        vals.optString(c.optString("key")).takeIf { it.isNotEmpty() }
                    } ?: "Row #${r.optInt("id")}"
                    val sub = ordered.mapNotNull { c ->
                        val v = vals.optString(c.optString("key"))
                        if (v.isNotEmpty() && v != headline) "${c.optString("name")}: $v" else null
                    }.take(3).joinToString(" · ")

                    Card(
                        modifier = Modifier.fillMaxWidth().clickable(enabled = canEdit) { editing = r },
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
                    ) {
                        Column(Modifier.padding(12.dp)) {
                            Row(
                                Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Text(headline, fontWeight = FontWeight.SemiBold, fontSize = 14.sp, modifier = Modifier.weight(1f))
                                if (r.optBoolean("converted")) StatusPill("done")
                            }
                            if (sub.isNotEmpty()) {
                                Text(sub, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            val extra = listOf(
                                r.optString("assigned_to"),
                                (0 until (r.optJSONArray("tags")?.length() ?: 0))
                                    .joinToString(" ") { "#" + r.optJSONArray("tags")!!.getString(it) },
                            ).filter { it.isNotEmpty() }.joinToString(" · ")
                            if (extra.isNotEmpty()) {
                                Text(extra, fontSize = 11.sp, color = BrandGoldDark)
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
private fun RecordEditor(
    sheetId: Int,
    columns: JSONArray,
    record: JSONObject?,   // null = new row
    modifier: Modifier,
    onDone: () -> Unit,
    onSessionExpired: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    val values = remember {
        mutableStateMapOf<String, String>().apply {
            val existing = record?.optJSONObject("values")
            for (i in 0 until columns.length()) {
                val key = columns.getJSONObject(i).optString("key")
                put(key, existing?.optString(key) ?: "")
            }
        }
    }
    var openMenuFor by remember { mutableStateOf<String?>(null) }
    var submitting by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }

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
            Text(if (record == null) "New Row" else "Edit Row", fontSize = 20.sp, fontWeight = FontWeight.Bold)
        }

        for (i in 0 until columns.length()) {
            val c = columns.getJSONObject(i)
            val key = c.optString("key")
            val label = c.optString("name") + if (c.optBoolean("required")) " *" else ""
            val type = c.optString("type")
            if (type == "select" || type == "status") {
                Box {
                    OutlinedTextField(
                        value = values[key] ?: "", onValueChange = {}, readOnly = true, enabled = false,
                        label = { Text(label) },
                        modifier = Modifier.fillMaxWidth().clickable { openMenuFor = key },
                        colors = OutlinedTextFieldDefaults.colors(
                            disabledTextColor = MaterialTheme.colorScheme.onSurface,
                            disabledBorderColor = MaterialTheme.colorScheme.outline,
                            disabledLabelColor = MaterialTheme.colorScheme.onSurfaceVariant,
                        ),
                    )
                    DropdownMenu(expanded = openMenuFor == key, onDismissRequest = { openMenuFor = null }) {
                        val opts = c.optJSONArray("options") ?: JSONArray()
                        for (j in 0 until opts.length()) {
                            DropdownMenuItem(
                                text = { Text(opts.getString(j)) },
                                onClick = { values[key] = opts.getString(j); openMenuFor = null },
                            )
                        }
                    }
                }
            } else {
                OutlinedTextField(
                    value = values[key] ?: "",
                    onValueChange = { values[key] = it },
                    label = { Text(label) },
                    placeholder = if (type == "date") {
                        { Text("YYYY-MM-DD") }
                    } else null,
                    keyboardOptions = when (type) {
                        "number" -> androidx.compose.foundation.text.KeyboardOptions(
                            keyboardType = androidx.compose.ui.text.input.KeyboardType.Decimal)
                        "phone" -> androidx.compose.foundation.text.KeyboardOptions(
                            keyboardType = androidx.compose.ui.text.input.KeyboardType.Phone)
                        "email" -> androidx.compose.foundation.text.KeyboardOptions(
                            keyboardType = androidx.compose.ui.text.input.KeyboardType.Email)
                        else -> androidx.compose.foundation.text.KeyboardOptions.Default
                    },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = type != "text",
                )
            }
        }

        message?.let { Text(it, color = StatusRed, fontSize = 13.sp, fontWeight = FontWeight.SemiBold) }

        Button(
            onClick = {
                submitting = true
                message = null
                scope.launch {
                    val vjson = JSONObject()
                    values.forEach { (k, v) -> vjson.put(k, v) }
                    val body = JSONObject().put("values", vjson)
                    if (record != null) body.put("record_id", record.optInt("id"))
                    when (val r = ApiClient.post("/clients/api/app/sheets/$sheetId/save/", body)) {
                        is ApiClient.Result.Ok -> onDone()
                        is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                        is ApiClient.Result.Error -> message = r.message
                    }
                    submitting = false
                }
            },
            enabled = !submitting,
            modifier = Modifier.fillMaxWidth().height(52.dp),
        ) { Text(if (submitting) "Saving…" else "Save Row", fontSize = 16.sp) }

        Spacer(Modifier.height(24.dp))
    }
}
