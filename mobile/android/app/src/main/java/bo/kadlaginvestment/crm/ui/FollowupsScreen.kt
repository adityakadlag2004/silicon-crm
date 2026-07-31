package bo.kadlaginvestment.crm.ui

import android.content.Intent
import android.net.Uri
import android.provider.ContactsContract
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SwipeToDismissBox
import androidx.compose.material3.SwipeToDismissBoxValue
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberSwipeToDismissBoxState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import bo.kadlaginvestment.crm.net.ApiClient
import bo.kadlaginvestment.crm.net.ContactResolver
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import java.util.Calendar
import java.util.Locale

/** CRM client name if present, else the device-saved contact name, else the number. */
private fun displayName(context: android.content.Context, f: JSONObject): String {
    val client = f.optString("client")
    if (client.isNotEmpty()) return client
    val phone = f.optString("phone")
    return ContactResolver.nameFor(context, phone) ?: phone
}

/** "2026-07-21T15:30" → "21/07 15:30" for the dialog's confirmation line. */
private fun prettyIso(iso: String): String =
    if (iso.length >= 16) "${iso.substring(8, 10)}/${iso.substring(5, 7)} ${iso.substring(11, 16)}" else iso

private fun JSONArray?.rows(): List<JSONObject> =
    (0 until (this?.length() ?: 0)).map { this!!.getJSONObject(it) }

/** Local ISO ("2026-07-21T15:30") for a moment the server will accept. */
private fun isoAt(cal: Calendar): String = String.format(
    Locale.US, "%04d-%02d-%02dT%02d:%02d",
    cal.get(Calendar.YEAR), cal.get(Calendar.MONTH) + 1, cal.get(Calendar.DAY_OF_MONTH),
    cal.get(Calendar.HOUR_OF_DAY), cal.get(Calendar.MINUTE),
)

private fun isoInMinutes(minutes: Int): String =
    isoAt(Calendar.getInstance().apply { add(Calendar.MINUTE, minutes) })

/** Day N from now at 10:00 — "in 2 days" at 9 PM must not ring at 9 PM. */
private fun isoDaysAt10(days: Int): String = isoAt(
    Calendar.getInstance().apply {
        add(Calendar.DAY_OF_YEAR, days)
        set(Calendar.HOUR_OF_DAY, 10); set(Calendar.MINUTE, 0)
    }
)

/** The same quick timings as the post-call popup, so both surfaces behave alike. */
private val SNOOZE_CHIPS: List<Pair<String, () -> String>> = listOf(
    "15 min" to { isoInMinutes(15) },
    "1 hr" to { isoInMinutes(60) },
    "3 hr" to { isoInMinutes(180) },
    "Tmrw 10 AM" to { isoDaysAt10(1) },
    "2 days" to { isoDaysAt10(2) },
    "1 week" to { isoDaysAt10(7) },
)

private fun startOfDay(ms: Long): Long = Calendar.getInstance().apply {
    timeInMillis = ms
    set(Calendar.HOUR_OF_DAY, 0); set(Calendar.MINUTE, 0)
    set(Calendar.SECOND, 0); set(Calendar.MILLISECOND, 0)
}.timeInMillis

/** 0 = due now, 1 = today, 2 = tomorrow, 3 = later. */
// ponytail: "tomorrow" is now + 24h, which is wrong on a DST boundary. India
// has none; use a real calendar add if this ever ships outside IST.
private fun bucketOf(ms: Long, now: Long): Int = when {
    ms <= now -> 0
    startOfDay(ms) == startOfDay(now) -> 1
    startOfDay(ms) == startOfDay(now + 86_400_000L) -> 2
    else -> 3
}

private val BUCKET_LABELS = listOf("Due now", "Today", "Tomorrow", "Later")

/** wa.me wants digits with a country code; local numbers are stored bare. */
private fun whatsappNumber(phone: String): String {
    val digits = phone.filter { it.isDigit() }
    return if (digits.length == 10) "91$digits" else digits
}

/** Call Follow-ups: today's personal call performance on top, then the
 * follow-up list — searchable, grouped by when it's due, with what the last
 * call to that number produced. */
@Composable
fun FollowupsScreen(
    modifier: Modifier = Modifier,
    onSessionExpired: () -> Unit,
    onOpenWeb: (String) -> Unit = {},
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    val loader = rememberLoader(
        "/clients/api/app/followups/",
        onSessionExpired = onSessionExpired,
        onLoaded = { json ->
            // Keep on-device alarms matched to the server list (arms new
            // follow-ups, drops ones completed on another device/web).
            json.optJSONArray("pending")?.let {
                bo.kadlaginvestment.crm.FollowupAlarmScheduler.syncFromPending(context, it)
            }
            FollowupBadge.set(json.optInt("overdue"))
        },
    )
    fun reloadKey() = loader.reload()

    var showAdd by remember { mutableStateOf(false) }
    var query by rememberSaveable { mutableStateOf("") }
    var showDone by rememberSaveable { mutableStateOf(false) }
    // Follow-up awaiting a dialog: which one, and which dialog.
    var outcomeFor by remember { mutableStateOf<JSONObject?>(null) }
    var laterFor by remember { mutableStateOf<JSONObject?>(null) }
    var noteFor by remember { mutableStateOf<JSONObject?>(null) }

    fun act(
        id: Int,
        action: String,
        at: String? = null,
        outcome: String? = null,
        note: String? = null,
        kind: String = "call",
    ) {
        scope.launch {
            val body = JSONObject().put("action", action).put("kind", kind)
            at?.let { body.put("at", it) }
            outcome?.let { body.put("outcome", it) }
            note?.let { body.put("note", it) }
            // Queued offline: acting on a follow-up is idempotent, so replaying
            // it is safe — and losing it silently (the old `else ->` branch,
            // which treated every failure as success) is not.
            when (val r = ApiClient.post(
                "/clients/api/app/followups/$id/action/", body, offlineQueue = context,
            )) {
                is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                is ApiClient.Result.Error -> AppMessage.show("Couldn't save: ${r.message}")
                is ApiClient.Result.Ok -> {
                    AppMessage.showResult(
                        r.json,
                        when (action) {
                            "done" -> "Marked done"
                            "dismiss" -> "Dismissed"
                            "note" -> "Note saved"
                            else -> "Rescheduled"
                        },
                    )
                    reloadKey()
                }
            }
        }
    }

    fun pushOverdue(iso: String) {
        scope.launch {
            when (val r = ApiClient.post(
                "/clients/api/app/followups/push-overdue/",
                JSONObject().put("at", iso), offlineQueue = context,
            )) {
                is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                is ApiClient.Result.Error -> AppMessage.show("Couldn't move: ${r.message}")
                is ApiClient.Result.Ok -> { AppMessage.showResult(r.json, "Moved"); reloadKey() }
            }
        }
    }

    if (showAdd) {
        AddFollowupDialog(
            onDismiss = { showAdd = false },
            onSave = { phone, note, iso ->
                showAdd = false
                scope.launch {
                    val body = JSONObject()
                        .put("phone", phone).put("note", note).put("custom_at", iso)
                    when (val r = ApiClient.post(
                        "/clients/api/calls/followup/", body, offlineQueue = context,
                    )) {
                        is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                        is ApiClient.Result.Error -> AppMessage.show("Couldn't save: ${r.message}")
                        is ApiClient.Result.Ok -> {
                            AppMessage.showResult(r.json, "Follow-up scheduled")
                            reloadKey()
                        }
                    }
                }
            },
        )
    }

    if (loader.data == null && loader.error != null) {
        ErrorBox(loader.error!!, modifier) { loader.reload() }; return
    }
    val d = loader.data ?: run { LoadingBox(modifier); return }

    outcomeFor?.let { f ->
        OutcomeDialog(
            name = displayName(context, f),
            outcomes = d.optJSONArray("outcomes").rows(),
            onDismiss = { outcomeFor = null },
            onPick = { key -> outcomeFor = null; act(f.getInt("id"), "done", outcome = key, kind = f.optString("kind")) },
        )
    }
    laterFor?.let { f ->
        LaterDialog(
            name = displayName(context, f),
            onDismiss = { laterFor = null },
            onPick = { iso -> laterFor = null; act(f.getInt("id"), "reschedule", at = iso, kind = f.optString("kind")) },
        )
    }
    noteFor?.let { f ->
        NoteDialog(
            initial = f.optString("note"),
            onDismiss = { noteFor = null },
            onSave = { text -> noteFor = null; act(f.getInt("id"), "note", note = text, kind = f.optString("kind")) },
        )
    }

    val stats = d.optJSONObject("stats")
    val allPending = d.optJSONArray("pending").rows()
    val doneToday = d.optJSONArray("done_today").rows()
    val q = query.trim().lowercase()
    val now = System.currentTimeMillis()
    val pendingRows = (if (q.isEmpty()) allPending else allPending.filter {
        displayName(context, it).lowercase().contains(q) ||
            it.optString("phone").contains(q) ||
            it.optString("note").lowercase().contains(q)
    }).sortedWith(
        // Time order, except inside "Due now": the number chased four times
        // is the one to call first, not the one that happened to be booked
        // earliest this morning.
        compareBy({ bucketOf(it.optLong("scheduled_at_ms"), now) },
            { if (bucketOf(it.optLong("scheduled_at_ms"), now) == 0) -it.optInt("attempts", 1) else 0 },
            { it.optLong("scheduled_at_ms") })
    )

    Box(modifier.fillMaxSize()) {
    RefreshableBox(refreshing = loader.refreshing, onRefresh = loader.reload) {
    LazyColumn(
        Modifier.fillMaxSize().padding(horizontal = rdp(16)),
        verticalArrangement = Arrangement.spacedBy(10.dp),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(top = 16.dp, bottom = 88.dp),
    ) {
        item {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("My Calls Today", fontSize = rsp(22), fontWeight = FontWeight.Bold)
            }
        }

        // ── Today's call performance ──
        if (stats != null) {
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    StatBlock("Dialed", "${stats.optInt("calls")}", Modifier.weight(1f))
                    StatBlock(
                        "Talk time",
                        formatMinutes(stats.optDouble("talk_minutes", 0.0)),
                        Modifier.weight(1f),
                    )
                }
            }
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    StatBlock("Connected", "${stats.optInt("connected")}", Modifier.weight(1f), StatusGreen)
                    StatBlock("Serious", "${stats.optInt("serious")}", Modifier.weight(1f), BrandGoldDark, sub = "2½ min+")
                }
            }
        }

        item { ErrorStrip(loader.error) }

        item {
            ActionRow(Modifier.padding(top = 4.dp)) {
                Chip("Pending (${allPending.size})", !showDone) { showDone = false }
                Chip("Done today (${doneToday.size})", showDone) { showDone = true }
            }
        }

        if (showDone) {
            if (doneToday.isEmpty()) {
                item { HintText("Nothing closed yet today.") }
            }
            items(doneToday, key = { it.optString("kind") + it.optInt("id") }) { f -> DoneCard(context, f) }
        } else {
            if (allPending.size > 5) {
                item {
                    OutlinedTextField(
                        query, { query = it },
                        label = { Text("Search name, number or note") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }

            if (pendingRows.isEmpty()) {
                item {
                    HintText(
                        if (allPending.isEmpty())
                            "Nothing pending — schedule from the post-call popup, or tap ＋ to add one yourself."
                        else "No follow-up matches \"$query\"."
                    )
                }
            }

            // Grouped by when it's due; the server already sorts by time, so
            // walking the list and printing a header on each change is enough.
            var lastBucket = -1
            pendingRows.forEach { f ->
                val bucket = bucketOf(f.optLong("scheduled_at_ms"), now)
                if (bucket != lastBucket) {
                    lastBucket = bucket
                    val overdueGroup = bucket == 0
                    item(key = "hdr$bucket") {
                        Row(
                            Modifier.fillMaxWidth().padding(top = 6.dp),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(
                                BUCKET_LABELS[bucket],
                                fontSize = rsp(15), fontWeight = FontWeight.Bold,
                                color = if (overdueGroup) StatusAmber else MaterialTheme.colorScheme.onSurface,
                            )
                            if (overdueGroup) {
                                TextButton(onClick = {
                                    pickDateTime(context, minNow = true) { iso -> pushOverdue(iso) }
                                }) { Text("Move all", fontSize = rsp(13)) }
                            }
                        }
                    }
                }
                item(key = f.optString("kind") + f.optInt("id")) {
                    SwipeableFollowup(
                        onDone = { act(f.getInt("id"), "done", kind = f.optString("kind")) },
                        onDismiss = { act(f.getInt("id"), "dismiss", kind = f.optString("kind")) },
                    ) {
                        FollowupCard(
                            context = context,
                            f = f,
                            onCall = {
                                context.startActivity(
                                    Intent(Intent.ACTION_DIAL, Uri.parse("tel:" + f.optString("phone")))
                                )
                            },
                            onWhatsapp = {
                                try {
                                    context.startActivity(
                                        Intent(
                                            Intent.ACTION_VIEW,
                                            Uri.parse("https://wa.me/" + whatsappNumber(f.optString("phone"))),
                                        )
                                    )
                                } catch (_: Exception) {
                                    AppMessage.show("No app can open WhatsApp")
                                }
                            },
                            onDone = { outcomeFor = f },
                            onLater = { laterFor = f },
                            onNote = { noteFor = f },
                            onDismissFollowup = { act(f.getInt("id"), "dismiss", kind = f.optString("kind")) },
                            onOpenClient = {
                                val id = f.optInt("client_id")
                                if (id > 0) onOpenWeb("/clients/clients/$id/profile/")
                                else AppMessage.show("This number isn't a client yet")
                            },
                        )
                    }
                }
            }
        }

        item { Spacer(Modifier.height(12.dp)) }
    }
    }

        ExtendedFloatingActionButton(
            onClick = { showAdd = true },
            modifier = Modifier.align(Alignment.BottomEnd).padding(16.dp),
        ) { Text("＋  Follow-up") }
    }
}

/** Swipe right = done, swipe left = dismiss. The buttons stay for anyone who
 * doesn't know that; swiping is the fast path through a long list. */
@Composable
private fun SwipeableFollowup(
    onDone: () -> Unit,
    onDismiss: () -> Unit,
    content: @Composable () -> Unit,
) {
    val state = rememberSwipeToDismissBoxState(
        confirmValueChange = { value ->
            when (value) {
                SwipeToDismissBoxValue.StartToEnd -> { onDone(); true }
                SwipeToDismissBoxValue.EndToStart -> { onDismiss(); true }
                SwipeToDismissBoxValue.Settled -> false
            }
        }
    )
    SwipeToDismissBox(
        state = state,
        backgroundContent = {
            val done = state.dismissDirection == SwipeToDismissBoxValue.StartToEnd
            Box(
                Modifier
                    .fillMaxSize()
                    .background(
                        (if (done) StatusGreen else StatusAmber).copy(alpha = 0.18f),
                        RoundedCornerShape(12.dp),
                    )
                    .padding(horizontal = 20.dp),
                contentAlignment = if (done) Alignment.CenterStart else Alignment.CenterEnd,
            ) {
                Text(
                    if (done) "✓ Done" else "Dismiss",
                    fontSize = rsp(14), fontWeight = FontWeight.Bold,
                )
            }
        },
        content = { content() },
    )
}

@Composable
private fun FollowupCard(
    context: android.content.Context,
    f: JSONObject,
    onCall: () -> Unit,
    onWhatsapp: () -> Unit,
    onDone: () -> Unit,
    onLater: () -> Unit,
    onNote: () -> Unit,
    onDismissFollowup: () -> Unit,
    onOpenClient: () -> Unit,
) {
    val overdue = f.optBoolean("overdue")
    var menuOpen by remember { mutableStateOf(false) }
    Card(
        colors = CardDefaults.cardColors(
            containerColor = if (overdue) MaterialTheme.colorScheme.primaryContainer
            else MaterialTheme.colorScheme.surface
        ),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
        modifier = Modifier.clickable(onClick = onOpenClient),
    ) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Column(Modifier.weight(1f)) {
                    Text(
                        displayName(context, f),
                        fontWeight = FontWeight.SemiBold, fontSize = rsp(15),
                    )
                    Text(
                        f.optString("scheduled_at") + if (overdue) "  · DUE" else "",
                        fontSize = rsp(12),
                        color = if (overdue) StatusAmber else MaterialTheme.colorScheme.onSurfaceVariant,
                        fontWeight = if (overdue) FontWeight.Bold else FontWeight.Normal,
                    )
                    Text(
                        f.optString("phone"),
                        fontSize = rsp(12),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Box {
                    IconButton(onClick = { menuOpen = true }) {
                        Icon(Icons.Filled.MoreVert, contentDescription = "More options")
                    }
                    DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                        DropdownMenuItem(
                            text = { Text("Edit note") },
                            onClick = { menuOpen = false; onNote() },
                        )
                        DropdownMenuItem(
                            text = { Text("Open client") },
                            onClick = { menuOpen = false; onOpenClient() },
                        )
                        DropdownMenuItem(
                            text = { Text("Dismiss") },
                            onClick = { menuOpen = false; onDismissFollowup() },
                        )
                    }
                }
            }

            val attempts = f.optInt("attempts", 1)
            val lastCall = f.optString("last_call")
            val isLead = f.optString("kind") == "lead"
            if (attempts > 1 || lastCall.isNotEmpty() || isLead) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (isLead) {
                        Text(
                            "LEAD",
                            fontSize = rsp(11),
                            fontWeight = FontWeight.Bold,
                            color = BrandGoldDark,
                            modifier = Modifier
                                .background(BrandGoldDark.copy(alpha = 0.15f), RoundedCornerShape(10.dp))
                                .padding(horizontal = 8.dp, vertical = 2.dp),
                        )
                    }
                    if (attempts > 1) {
                        Text(
                            "Attempt $attempts",
                            fontSize = rsp(11),
                            fontWeight = FontWeight.Bold,
                            color = StatusAmber,
                            modifier = Modifier
                                .background(StatusAmber.copy(alpha = 0.15f), RoundedCornerShape(10.dp))
                                .padding(horizontal = 8.dp, vertical = 2.dp),
                        )
                    }
                    if (lastCall.isNotEmpty()) {
                        Text(
                            lastCall,
                            fontSize = rsp(11),
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }

            if (f.optString("note").isNotEmpty()) {
                Text(f.optString("note"), fontSize = rsp(13))
            }
            ActionRow {
                // A lead can be saved without a number; don't offer a dead dialler.
                if (f.optString("phone").isNotEmpty()) {
                    Button(onClick = onCall) { Text("📞 Call") }
                    OutlinedButton(onClick = onWhatsapp) { Text("💬 WhatsApp", fontSize = rsp(13)) }
                }
                OutlinedButton(onClick = onDone) { Text("Done") }
                OutlinedButton(onClick = onLater) { Text("🕑 Later") }
            }
        }
    }
}

/** A follow-up closed today: what happened, not what to do. */
@Composable
private fun DoneCard(context: android.content.Context, f: JSONObject) {
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
                Text(displayName(context, f), fontWeight = FontWeight.SemiBold, fontSize = rsp(14))
                Text(
                    f.optString("phone"),
                    fontSize = rsp(12), color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Text(
                f.optString("outcome_label").ifEmpty {
                    if (f.optString("status") == "dismissed") "Dismissed" else "Done"
                },
                fontSize = rsp(12),
                fontWeight = FontWeight.SemiBold,
                color = if (f.optString("status") == "dismissed") MaterialTheme.colorScheme.onSurfaceVariant
                else StatusGreen,
            )
        }
    }
}

@Composable
private fun HintText(text: String) {
    Text(text, color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = rsp(13))
}

/** What the call produced. Without it "Done" records nothing — forty calls a
 * day and no answer to what they were worth. */
@Composable
private fun OutcomeDialog(
    name: String,
    outcomes: List<JSONObject>,
    onDismiss: () -> Unit,
    onPick: (String) -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("How did it go?") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(name, fontSize = rsp(13), color = MaterialTheme.colorScheme.onSurfaceVariant)
                ActionRow { outcomes.forEach { o -> Chip(o.optString("label"), false) { onPick(o.optString("key")) } } }
            }
        },
        confirmButton = { TextButton(onClick = { onPick("") }) { Text("Just mark done") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

/** Reschedule in one tap. The full picker is still there for a real date. */
@Composable
private fun LaterDialog(name: String, onDismiss: () -> Unit, onPick: (String) -> Unit) {
    val context = LocalContext.current
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Call later") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(name, fontSize = rsp(13), color = MaterialTheme.colorScheme.onSurfaceVariant)
                ActionRow { SNOOZE_CHIPS.forEach { (label, at) -> Chip(label, false) { onPick(at()) } } }
            }
        },
        confirmButton = {
            TextButton(onClick = {
                pickDateTime(context, minNow = true) { iso -> onPick(iso) }
            }) { Text("Pick date & time") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
private fun NoteDialog(initial: String, onDismiss: () -> Unit, onSave: (String) -> Unit) {
    var text by remember { mutableStateOf(initial) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Note") },
        text = {
            OutlinedTextField(
                text, { text = it },
                label = { Text("What is this call about?") },
                modifier = Modifier.fillMaxWidth(),
            )
        },
        confirmButton = { TextButton(onClick = { onSave(text.trim()) }) { Text("Save") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

/** Manual follow-up: a number and a moment, no call required. */
@Composable
private fun AddFollowupDialog(
    onDismiss: () -> Unit,
    onSave: (phone: String, note: String, iso: String) -> Unit,
) {
    val context = LocalContext.current
    var phone by remember { mutableStateOf("") }
    var pickedName by remember { mutableStateOf("") }
    var note by remember { mutableStateOf("") }
    var iso by remember { mutableStateOf("") }

    // ACTION_PICK on the Phone table: the user picks one *number*, so contacts
    // with several numbers resolve themselves, and the returned row is readable
    // without READ_CONTACTS (the picker grants access to just that row).
    val pickContact = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        val uri = result.data?.data ?: return@rememberLauncherForActivityResult
        try {
            context.contentResolver.query(
                uri,
                arrayOf(
                    ContactsContract.CommonDataKinds.Phone.NUMBER,
                    ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
                ),
                null, null, null,
            )?.use { c ->
                if (c.moveToFirst()) {
                    phone = c.getString(0).orEmpty().filterNot { it.isWhitespace() }
                    pickedName = c.getString(1).orEmpty()
                }
            }
        } catch (_: Exception) {
            // provider hiccup → the number can still be typed in by hand
        }
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("New follow-up") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedButton(
                    onClick = {
                        pickContact.launch(
                            Intent(
                                Intent.ACTION_PICK,
                                ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
                            )
                        )
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) { Text(if (pickedName.isEmpty()) "👤 Pick from contacts" else "👤 $pickedName") }
                OutlinedTextField(
                    phone, { phone = it; pickedName = "" },
                    label = { Text("Phone number") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    note, { note = it },
                    label = { Text("Note (optional)") },
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedButton(
                    onClick = { pickDateTime(context, minNow = true) { iso = it } },
                    modifier = Modifier.fillMaxWidth(),
                ) { Text(if (iso.isEmpty()) "Pick date & time" else "🕑 ${prettyIso(iso)}") }
            }
        },
        confirmButton = {
            TextButton(
                onClick = { onSave(phone.trim(), note.trim(), iso) },
                enabled = phone.isNotBlank() && iso.isNotEmpty(),
            ) { Text("Save") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
private fun StatBlock(
    title: String,
    value: String,
    modifier: Modifier = Modifier,
    accent: Color = Color.Unspecified,
    sub: String? = null,
) {
    Card(
        modifier = modifier,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.padding(vertical = 14.dp, horizontal = 12.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Text(
                value,
                fontSize = rsp(24),
                fontWeight = FontWeight.Bold,
                color = if (accent == Color.Unspecified) MaterialTheme.colorScheme.onSurface else accent,
                textAlign = TextAlign.Center,
            )
            Text(title, fontSize = rsp(12), color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (sub != null) {
                Text(sub, fontSize = rsp(10), color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

private fun formatMinutes(totalMinutes: Double): String {
    val totalSec = (totalMinutes * 60).toInt()
    val h = totalSec / 3600
    val m = (totalSec % 3600) / 60
    val s = totalSec % 60
    return when {
        h > 0 -> "${h}h ${m}m"
        m > 0 -> "${m}m ${s}s"
        else -> "${s}s"
    }
}
