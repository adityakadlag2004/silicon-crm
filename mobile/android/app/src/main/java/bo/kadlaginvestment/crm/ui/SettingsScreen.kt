package bo.kadlaginvestment.crm.ui

import android.app.TimePickerDialog
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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import bo.kadlaginvestment.crm.net.ApiClient
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

private val DAY_LABELS = listOf("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

private fun fmt12(hhmm: String): String {
    val parts = hhmm.split(":")
    val h = parts.getOrNull(0)?.toIntOrNull() ?: return hhmm
    val m = parts.getOrNull(1)?.toIntOrNull() ?: 0
    val ampm = if (h < 12) "AM" else "PM"
    val h12 = when { h == 0 -> 12; h > 12 -> h - 12; else -> h }
    return "%d:%02d %s".format(h12, m, ampm)
}

/** Admin: manage module settings (call tracking, follow-up popup incl. its
 * quick-chip grid, task reminders) — the native mirror of the web forms. */
@Composable
fun SettingsScreen(
    modifier: Modifier = Modifier,
    onBack: () -> Unit,
    onSessionExpired: () -> Unit,
    onOpenSim: () -> Unit,
    onSaved: () -> Unit,
) {
    BackHandler(onBack = onBack)
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var savedMsg by remember { mutableStateOf<String?>(null) }

    // ── editable state per section ──
    var trkEnabled by remember { mutableStateOf(true) }
    var workStart by remember { mutableStateOf("10:00") }
    var workEnd by remember { mutableStateOf("18:00") }
    var workDays by remember { mutableStateOf(setOf(0, 1, 2, 3, 4, 5)) }

    var popEnabled by remember { mutableStateOf(true) }
    var popStart by remember { mutableStateOf("09:00") }
    var popEnd by remember { mutableStateOf("21:00") }
    var popDays by remember { mutableStateOf(setOf(0, 1, 2, 3, 4, 5, 6)) }
    var popChoices by remember { mutableStateOf(listOf<String>()) }
    var catalog by remember { mutableStateOf(listOf<Pair<String, String>>()) }

    var remDayBefore by remember { mutableStateOf(true) }
    var remSameDay by remember { mutableStateOf(true) }
    var remHour by remember { mutableStateOf(9) }

    fun applyJson(d: JSONObject) {
        d.optJSONObject("call_tracking")?.let { ct ->
            trkEnabled = ct.optBoolean("enabled", true)
            workStart = ct.optString("work_start", "10:00")
            workEnd = ct.optString("work_end", "18:00")
            workDays = jsonIntSet(ct.optJSONArray("work_days"))
        }
        d.optJSONObject("popup")?.let { p ->
            popEnabled = p.optBoolean("popup_enabled", true)
            popStart = p.optString("popup_start", "09:00")
            popEnd = p.optString("popup_end", "21:00")
            popDays = jsonIntSet(p.optJSONArray("popup_days"))
            popChoices = jsonStrList(p.optJSONArray("popup_choices"))
            val cat = p.optJSONArray("catalog") ?: JSONArray()
            catalog = (0 until cat.length()).map {
                val o = cat.getJSONObject(it)
                o.optString("key") to o.optString("label")
            }
        }
        d.optJSONObject("tasks")?.let { t ->
            remDayBefore = t.optBoolean("remind_day_before", true)
            remSameDay = t.optBoolean("remind_same_day", true)
            remHour = t.optInt("same_day_hour", 9)
        }
    }

    LaunchedEffect(Unit) {
        when (val r = ApiClient.get("/clients/api/app/settings/")) {
            is ApiClient.Result.Ok -> { applyJson(r.json); loading = false }
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> { error = r.message; loading = false }
        }
    }

    fun save(section: String, body: JSONObject) {
        body.put("section", section)
        scope.launch {
            when (val r = ApiClient.post("/clients/api/app/settings/", body)) {
                is ApiClient.Result.Ok -> {
                    applyJson(r.json)
                    savedMsg = "Saved ✓"
                    onSaved()
                }
                is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                is ApiClient.Result.Error -> savedMsg = "Save failed — ${r.message}"
            }
        }
    }

    fun timeRow(label: String, value: String, onPick: (String) -> Unit): @Composable () -> Unit = {
        Row(
            Modifier.fillMaxWidth().padding(vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(label, fontSize = 14.sp, modifier = Modifier.weight(1f))
            Text(
                fmt12(value),
                fontSize = 14.sp, fontWeight = FontWeight.SemiBold,
                color = BrandGoldDark,
                modifier = Modifier.clickable {
                    val parts = value.split(":")
                    TimePickerDialog(context, { _, h, m ->
                        onPick("%02d:%02d".format(h, m))
                    }, parts.getOrNull(0)?.toIntOrNull() ?: 9,
                        parts.getOrNull(1)?.toIntOrNull() ?: 0, false).show()
                },
            )
        }
    }

    Column(modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("← Back", fontWeight = FontWeight.SemiBold,
                modifier = Modifier.clickable(onClick = onBack))
            Spacer(Modifier.width(10.dp))
            Text("App Settings", fontSize = 20.sp, fontWeight = FontWeight.Bold)
        }
        Spacer(Modifier.height(4.dp))
        savedMsg?.let {
            Text(it, fontSize = 12.sp,
                color = if (it.startsWith("Saved")) StatusGreen else StatusRed)
        }
        Spacer(Modifier.height(8.dp))

        when {
            loading -> LoadingBox(Modifier.height(160.dp))
            error != null -> ErrorBox(error ?: "", onRetry = {
                loading = true; error = null
                scope.launch {
                    when (val r = ApiClient.get("/clients/api/app/settings/")) {
                        is ApiClient.Result.Ok -> { applyJson(r.json); loading = false }
                        is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                        is ApiClient.Result.Error -> { error = r.message; loading = false }
                    }
                }
            })
            else -> {
                // ── Call tracking ──
                SettingsCard("📶  Call Tracking", "Which calls sync from devices and count in analytics.") {
                    SwitchRow("Tracking enabled", trkEnabled) { trkEnabled = it }
                    timeRow("Work start", workStart) { workStart = it }()
                    timeRow("Work end", workEnd) { workEnd = it }()
                    DayPicker(workDays) { workDays = it }
                    SaveButton {
                        save("call_tracking", JSONObject()
                            .put("enabled", trkEnabled)
                            .put("work_start", workStart).put("work_end", workEnd)
                            .put("work_days", JSONArray(workDays.sorted())))
                    }
                }

                // ── Follow-up popup ──
                SettingsCard("📞  Follow-up Popup", "The after-call reminder popup on employee phones.") {
                    SwitchRow("Popup enabled", popEnabled) { popEnabled = it }
                    timeRow("Popup from", popStart) { popStart = it }()
                    timeRow("Popup until", popEnd) { popEnd = it }()
                    DayPicker(popDays) { popDays = it }
                    Spacer(Modifier.height(10.dp))
                    Text("Quick options shown on the popup", fontSize = 13.sp,
                        fontWeight = FontWeight.SemiBold)
                    Text("Tap to include/exclude. At least one required.",
                        fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Spacer(Modifier.height(6.dp))
                    catalog.chunked(3).forEach { rowItems ->
                        Row(
                            Modifier.fillMaxWidth().padding(vertical = 2.dp),
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                        ) {
                            rowItems.forEach { (key, label) ->
                                Column(Modifier.weight(1f)) {
                                    Chip(label, selected = key in popChoices) {
                                        popChoices = if (key in popChoices) {
                                            if (popChoices.size > 1) popChoices - key else popChoices
                                        } else popChoices + key
                                    }
                                }
                            }
                            repeat(3 - rowItems.size) { Spacer(Modifier.weight(1f)) }
                        }
                    }
                    SaveButton {
                        // keep catalog order regardless of tap order
                        val ordered = catalog.map { it.first }.filter { it in popChoices }
                        save("popup", JSONObject()
                            .put("popup_enabled", popEnabled)
                            .put("popup_start", popStart).put("popup_end", popEnd)
                            .put("popup_days", JSONArray(popDays.sorted()))
                            .put("popup_choices", JSONArray(ordered)))
                    }
                }

                // ── Task reminders ──
                SettingsCard("✅  Task Reminders", "When employees are reminded about due tasks. Overdue alerts always fire.") {
                    SwitchRow("Remind one day before", remDayBefore) { remDayBefore = it }
                    SwitchRow("Remind on the due day", remSameDay) { remSameDay = it }
                    timeRow("Reminder hour", "%02d:00".format(remHour)) {
                        remHour = it.split(":")[0].toIntOrNull() ?: remHour
                    }()
                    SaveButton {
                        save("tasks", JSONObject()
                            .put("remind_day_before", remDayBefore)
                            .put("remind_same_day", remSameDay)
                            .put("same_day_hour", remHour))
                    }
                }

                // ── Office SIM (per-device, lives on its own screen) ──
                Card(
                    modifier = Modifier.fillMaxWidth().clickable(onClick = onOpenSim),
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                    elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
                ) {
                    Row(
                        Modifier.fillMaxWidth().padding(14.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("📶", fontSize = 18.sp, modifier = Modifier.padding(end = 12.dp))
                        Column(Modifier.weight(1f)) {
                            Text("Office SIM", fontSize = 15.sp, fontWeight = FontWeight.Medium)
                            Text("This device only — pick which SIM's calls are tracked",
                                fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        Text("›", fontSize = 16.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
                Spacer(Modifier.height(24.dp))
            }
        }
    }
}

private fun jsonIntSet(arr: JSONArray?): Set<Int> =
    if (arr == null) emptySet() else (0 until arr.length()).map { arr.getInt(it) }.toSet()

private fun jsonStrList(arr: JSONArray?): List<String> =
    if (arr == null) emptyList() else (0 until arr.length()).map { arr.getString(it) }

@Composable
private fun SettingsCard(title: String, subtitle: String, content: @Composable () -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth().padding(bottom = 12.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.fillMaxWidth().padding(14.dp)) {
            Text(title, fontSize = 15.sp, fontWeight = FontWeight.Bold)
            Text(subtitle, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(8.dp))
            content()
        }
    }
}

@Composable
private fun SwitchRow(label: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(vertical = 2.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, fontSize = 14.sp, modifier = Modifier.weight(1f))
        Switch(checked = checked, onCheckedChange = onChange)
    }
}

@Composable
private fun DayPicker(selected: Set<Int>, onChange: (Set<Int>) -> Unit) {
    Spacer(Modifier.height(6.dp))
    Row(
        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        DAY_LABELS.forEachIndexed { i, label ->
            Chip(label, selected = i in selected) {
                val next = if (i in selected) selected - i else selected + i
                if (next.isNotEmpty()) onChange(next)
            }
        }
    }
}

@Composable
private fun SaveButton(onClick: () -> Unit) {
    Spacer(Modifier.height(10.dp))
    Button(onClick = onClick, modifier = Modifier.fillMaxWidth()) {
        Text("Save")
    }
}
