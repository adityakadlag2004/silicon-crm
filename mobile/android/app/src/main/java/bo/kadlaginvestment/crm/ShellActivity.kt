package bo.kadlaginvestment.crm

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.TextButton
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AddCircle
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import androidx.compose.ui.Modifier
import bo.kadlaginvestment.crm.net.ApiClient
import bo.kadlaginvestment.crm.ui.AddSaleScreen
import bo.kadlaginvestment.crm.ui.CallAnalyticsScreen
import bo.kadlaginvestment.crm.ui.ClientsScreen
import bo.kadlaginvestment.crm.ui.DashboardScreen
import bo.kadlaginvestment.crm.ui.FollowupsScreen
import bo.kadlaginvestment.crm.ui.IncentivesScreen
import bo.kadlaginvestment.crm.ui.KadlagTheme
import bo.kadlaginvestment.crm.ui.LeadsScreen
import bo.kadlaginvestment.crm.ui.MenuScreen
import bo.kadlaginvestment.crm.ui.ReportsScreen
import bo.kadlaginvestment.crm.ui.NotificationsScreen
import bo.kadlaginvestment.crm.ui.RenewalsScreen
import bo.kadlaginvestment.crm.ui.ReportsHub
import bo.kadlaginvestment.crm.ui.SalesScreen
import bo.kadlaginvestment.crm.ui.SettingsScreen
import bo.kadlaginvestment.crm.ui.SimSettingsScreen
import bo.kadlaginvestment.crm.ui.TeamScreen
import bo.kadlaginvestment.crm.ui.rsp

@androidx.compose.runtime.Composable
private fun PermCard(title: String, desc: String, cta: String, onFix: () -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth().padding(bottom = 12.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
    ) {
        Column(Modifier.fillMaxWidth().padding(14.dp)) {
            Text(title, fontWeight = FontWeight.SemiBold, fontSize = rsp(15))
            Spacer(Modifier.height(4.dp))
            Text(desc, fontSize = rsp(13), color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(10.dp))
            // Full-width button so it can never be clipped off-screen.
            Button(onClick = onFix, modifier = Modifier.fillMaxWidth().heightIn(min = 46.dp)) { Text(cta) }
        }
    }
}

/**
 * Native app shell: bottom navigation, all tabs rendered in Compose.
 * Screens not yet converted open in [WebActivity] from the Menu tab
 * (see mobile/NATIVE_MIGRATION.md for the conversion order).
 */
class ShellActivity : ComponentActivity() {

    // Bumped on every resume so the permission gate re-checks after the user
    // returns from a settings screen.
    private val permTick = mutableIntStateOf(0)

    private fun callsGranted(): Boolean =
        checkSelfPermission(Manifest.permission.READ_PHONE_STATE) == PackageManager.PERMISSION_GRANTED &&
            checkSelfPermission(Manifest.permission.READ_CALL_LOG) == PackageManager.PERMISSION_GRANTED

    private fun notificationsGranted(): Boolean =
        Build.VERSION.SDK_INT < 33 ||
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    /** Android 14+ lets the user revoke full-screen intents (the ringing
     * follow-up alarm screen); below 14 the manifest permission is enough. */
    private fun fullScreenAlarmsGranted(): Boolean =
        Build.VERSION.SDK_INT < 34 ||
            (getSystemService(android.app.NotificationManager::class.java)?.canUseFullScreenIntent() ?: true)

    private fun reportDeviceStatus() {
        Thread {
            try {
                val version = packageManager.getPackageInfo(packageName, 0).versionName ?: ""
                // Popup/alarm health snapshot — shown per device on the admin's
                // Call Analytics roster, so "why is the popup not appearing?"
                // is answerable without touching the phone.
                val ct = getSharedPreferences("call_tracking", MODE_PRIVATE)
                val diagnostics = org.json.JSONObject()
                    .put("popup_enabled", ct.getBoolean("popup_enabled", true))
                    .put(
                        "popup_window",
                        "${ct.getInt("popup_start_minutes", 540)}-${ct.getInt("popup_end_minutes", 1260)}",
                    )
                    .put("popup_days", ct.getString("popup_days", "0,1,2,3,4,5,6"))
                    .put("sim_configured", ct.getBoolean("sim_configured", false))
                    .put("last_popup_result", ct.getString("last_popup_result", ""))
                    .put("last_popup_at", ct.getLong("last_popup_at", 0L))
                    // Proof the call-end receiver runs at all on this device.
                    .put("last_call_handled_at", ct.getLong("last_call_handled_at", 0L))
                    .put("exact_alarms", FollowupAlarmScheduler.canScheduleExact(this))
                    .put("fullscreen_alarms", fullScreenAlarmsGranted())
                val body = org.json.JSONObject()
                    .put("calls_granted", callsGranted())
                    .put("overlay_granted", Settings.canDrawOverlays(this))
                    .put("notifications_granted", notificationsGranted())
                    .put("app_version", version)
                    .put("diagnostics", diagnostics)
                BackendClient.postJson("/clients/api/app/device-status/", body.toString())
            } catch (_: Exception) {}
        }.start()
    }

    override fun onResume() {
        super.onResume()
        permTick.intValue++
        reportDeviceStatus()
        // Single-SIM phones need no choice — auto-select so tracking is scoped.
        SimHelper.ensureSingleSimConfigured(applicationContext)
    }

    /** Fetch /api/calls/config/ and cache it in SharedPreferences — read by
     * CallTrackerReceiver (windows) and FollowupActivity (quick-chip grid).
     * Blocking; call off the main thread. Re-run after the admin saves the
     * app Settings screen so changes apply without an app restart. */
    fun syncCallConfigBlocking() {
        try {
            val cookies = android.webkit.CookieManager.getInstance().getCookie(BackendClient.BASE_URL)
            if (cookies != null && cookies.contains("sessionid=")) {
                val url = java.net.URL(BackendClient.BASE_URL + "/clients/api/calls/config/")
                val conn = url.openConnection() as java.net.HttpURLConnection
                conn.setRequestProperty("Cookie", cookies)
                conn.setRequestProperty("Accept", "application/json")
                conn.connectTimeout = 8000; conn.readTimeout = 8000
                if (conn.responseCode == 200) {
                    val cfg = org.json.JSONObject(conn.inputStream.bufferedReader().readText())
                    fun daysCsv(key: String, fallback: String): String {
                        val arr = cfg.optJSONArray(key) ?: return fallback
                        return (0 until arr.length()).joinToString(",") { arr.getInt(it).toString() }
                    }
                    getSharedPreferences("call_tracking", MODE_PRIVATE).edit()
                        // tracking window
                        .putBoolean("enabled", cfg.optBoolean("enabled", true))
                        .putInt("work_start_minutes", cfg.optInt("work_start_minutes", 600))
                        .putInt("work_end_minutes", cfg.optInt("work_end_minutes", 1080))
                        .putString("work_days", daysCsv("work_days", "0,1,2,3,4,5"))
                        // follow-up popup window (independent)
                        .putBoolean("popup_enabled", cfg.optBoolean("popup_enabled", true))
                        .putInt("popup_start_minutes", cfg.optInt("popup_start_minutes", 540))
                        .putInt("popup_end_minutes", cfg.optInt("popup_end_minutes", 1260))
                        .putString("popup_days", daysCsv("popup_days", "0,1,2,3,4,5,6"))
                        // admin-configured quick-chip grid for FollowupActivity
                        .putString("popup_choices",
                            (cfg.optJSONArray("popup_choices") ?: org.json.JSONArray()).toString())
                        .apply()
                }
                conn.disconnect()
            }
        } catch (_: Exception) {}
    }

    /** Bootstraps that used to run in the WebView login's JS: sync the
     * admin call-tracking window to the device, and register this device's
     * FCM push token. Best-effort, off the main thread. */
    private fun bootstrapNative() {
        Thread {
            // 1) Call-tracking config → SharedPreferences (read by CallTrackerReceiver)
            syncCallConfigBlocking()

            // 1b) Arm on-device alarms for every pending follow-up + the daily
            // digest, so reminders ring even if FCM never arrives.
            try {
                BackendClient.getJson("/clients/api/app/followups/")?.let { body ->
                    org.json.JSONObject(body).optJSONArray("pending")?.let {
                        FollowupAlarmScheduler.syncFromPending(applicationContext, it)
                    }
                }
            } catch (_: Exception) {}

            // 1c) …and exact due-time alarms for the user's open tasks.
            TaskAlarmScheduler.syncBlocking(applicationContext)

            // 2) FCM push token → register with the server (needs Firebase configured)
            try {
                com.google.firebase.messaging.FirebaseMessaging.getInstance().token
                    .addOnSuccessListener { token ->
                        Thread {
                            try {
                                val body = org.json.JSONObject()
                                    .put("token", token).put("platform", "android")
                                BackendClient.postJson("/clients/api/push/register/", body.toString())
                            } catch (_: Exception) {}
                        }.start()
                    }
            } catch (_: Throwable) {
                // Firebase not configured / not available — push simply stays off.
            }
        }.start()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Ensure the push notification channel exists before any FCM arrives.
        KadlagMessagingService.ensureChannel(this)
        // …and the ringing follow-up alarm channel before any alarm fires.
        FollowupAlarmNotifier.ensureChannel(this)

        // Catch-up sync: uploads any calls that couldn't be synced when they
        // ended (no internet at the time). Server-side dedup makes this safe.
        Thread { CallSyncManager.syncRecentCalls(applicationContext) }.start()
        bootstrapNative()

        setContent {
            KadlagTheme {
                var selected by remember { mutableIntStateOf(0) }
                var permDialogDismissed by remember { mutableStateOf(false) }
                var simDialogDismissed by remember { mutableStateOf(false) }
                var pickedSub by remember { mutableIntStateOf(-1) }

                // ── Self-hosted update check ──
                var update by remember { mutableStateOf<UpdateManager.UpdateInfo?>(null) }
                LaunchedEffect(Unit) {
                    update = withContext(Dispatchers.IO) { UpdateManager.checkForUpdate(this@ShellActivity) }
                }
                update?.let { info ->
                    AlertDialog(
                        onDismissRequest = { update = null },
                        title = { Text("Update available — v${info.versionName}") },
                        text = { Text(info.notes.ifEmpty { "A new version of the app is ready." }) },
                        confirmButton = {
                            Button(onClick = {
                                UpdateManager.downloadAndInstall(this@ShellActivity, info)
                                update = null
                            }) { Text("Update now") }
                        },
                        dismissButton = {
                            TextButton(onClick = { update = null }) { Text("Later") }
                        },
                    )
                }
                // Reading permTick subscribes this composition to onResume bumps,
                // so the checks below re-run after returning from Settings.
                @Suppress("UNUSED_VARIABLE") val tick = permTick.intValue

                val needCalls = !callsGranted()
                val needOverlay = !Settings.canDrawOverlays(this)
                val needNotifications = !notificationsGranted()
                val needExactAlarms = !FollowupAlarmScheduler.canScheduleExact(this)
                val needFullScreen = !fullScreenAlarmsGranted()

                // Full-screen permission gate. A plain AlertDialog clipped the
                // Allow buttons on some devices (they sat inside the scrolling
                // text slot); this uses a wide, scrollable Surface with
                // full-width buttons that are always visible. It reappears on
                // every launch while any permission is still missing.
                if (!permDialogDismissed &&
                    (needCalls || needOverlay || needNotifications || needExactAlarms || needFullScreen)
                ) {
                    Dialog(
                        onDismissRequest = { permDialogDismissed = true },
                        properties = DialogProperties(usePlatformDefaultWidth = false),
                    ) {
                        Surface(
                            modifier = Modifier.fillMaxWidth(0.94f).heightIn(max = 640.dp),
                            shape = RoundedCornerShape(20.dp),
                            color = MaterialTheme.colorScheme.surface,
                        ) {
                            Column(
                                Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(24.dp),
                            ) {
                                Text("Permissions needed", fontSize = rsp(20), fontWeight = FontWeight.Bold)
                                Spacer(Modifier.height(10.dp))
                                Text(
                                    "These let call tracking and follow-up reminders work. The app " +
                                        "keeps asking each time you open it until they're allowed.",
                                    fontSize = rsp(14),
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                                Spacer(Modifier.height(18.dp))
                                if (needCalls) PermCard(
                                    "Call tracking",
                                    "Phone state, call log & contacts — needed to record office calls.",
                                    "Allow",
                                ) {
                                    requestPermissions(
                                        arrayOf(
                                            Manifest.permission.READ_PHONE_STATE,
                                            Manifest.permission.READ_CALL_LOG,
                                            Manifest.permission.READ_CONTACTS,
                                        ), 100,
                                    )
                                }
                                if (needOverlay) PermCard(
                                    "Follow-up popup",
                                    "Display over other apps — shows the after-call follow-up popup.",
                                    "Open settings",
                                ) {
                                    startActivity(
                                        Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName"))
                                    )
                                }
                                if (needNotifications) PermCard(
                                    "Notifications",
                                    "Show reminders and alerts sent from the office.",
                                    "Allow",
                                ) {
                                    if (Build.VERSION.SDK_INT >= 33) {
                                        requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 101)
                                    }
                                }
                                if (needExactAlarms) PermCard(
                                    "Follow-up alarms",
                                    "Alarms & reminders — makes follow-up reminders ring exactly on time, like an alarm clock.",
                                    "Open settings",
                                ) {
                                    if (Build.VERSION.SDK_INT >= 31) {
                                        startActivity(
                                            Intent(
                                                Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM,
                                                Uri.parse("package:$packageName"),
                                            )
                                        )
                                    }
                                }
                                if (needFullScreen) PermCard(
                                    "Ring on lock screen",
                                    "Full-screen reminders — shows the ringing follow-up alarm even when the phone is locked.",
                                    "Open settings",
                                ) {
                                    if (Build.VERSION.SDK_INT >= 34) {
                                        startActivity(
                                            Intent(
                                                Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT,
                                                Uri.parse("package:$packageName"),
                                            )
                                        )
                                    }
                                }
                                Spacer(Modifier.height(6.dp))
                                TextButton(
                                    onClick = { permDialogDismissed = true },
                                    modifier = Modifier.align(androidx.compose.ui.Alignment.End),
                                ) { Text("Continue for now") }
                            }
                        }
                    }
                }

                // ── First-run office-SIM picker (dual-SIM employees) ──
                if (!simDialogDismissed && !needCalls && SimHelper.needsSetup(this)) {
                    val sims = SimHelper.getSims(this)
                    if (pickedSub == -1 && sims.isNotEmpty()) pickedSub = sims[0].subId
                    AlertDialog(
                        onDismissRequest = { },
                        title = { Text("Which SIM is your office SIM?") },
                        text = {
                            Column {
                                Text("Only this SIM's calls will be tracked. Your personal SIM is ignored. You can change it later in Menu → Office SIM.")
                                sims.forEach { sim ->
                                    Row(
                                        Modifier.fillMaxWidth().padding(top = 8.dp)
                                            .clickable { pickedSub = sim.subId },
                                        verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
                                    ) {
                                        androidx.compose.material3.RadioButton(
                                            selected = pickedSub == sim.subId,
                                            onClick = { pickedSub = sim.subId },
                                        )
                                        Text(sim.label)
                                    }
                                }
                            }
                        },
                        confirmButton = {
                            Button(onClick = {
                                sims.firstOrNull { it.subId == pickedSub }?.let {
                                    SimHelper.saveOfficeSim(this, it.subId, it.slot)
                                }
                                simDialogDismissed = true
                            }) { Text("Track this SIM") }
                        },
                    )
                }
                // Native routes layered above the tabs: "sales", "sales_pending",
                // "renewals", "notifications".
                var overlay by remember { mutableStateOf<String?>(null) }

                // Session rejected by the server: drop the dead cookie before
                // leaving, or LoginActivity sees it, bounces back here, this
                // screen fails again… (the v4.11.0 reload loop).
                val goLogin: () -> Unit = {
                    ApiClient.clearSession()
                    startActivity(LoginActivity.expiredIntent(this))
                    finish()
                }
                val logout: () -> Unit = goLogin
                val openWeb: (String) -> Unit = { path -> WebActivity.open(this, path) }
                // Route a notification/shortcut link to the right destination —
                // native screens where they exist, else the web page or dialer.
                val routeLink: (String) -> Unit = { raw ->
                    val link = raw.trim()
                    when {
                        link.isEmpty() -> {}
                        link.startsWith("tel:") ->
                            startActivity(Intent(Intent.ACTION_DIAL, Uri.parse(link)))
                        link.contains("/sales/approve/") -> { overlay = "sales_pending" }
                        link.contains("/sales/") -> { overlay = "sales" }
                        link.contains("/tasks/") -> {
                            val id = Regex("/tasks/(\\d+)/").find(link)?.groupValues?.get(1)?.toIntOrNull()
                            TasksActivity.open(this, id)
                        }
                        link.contains("/calls/followups") -> { overlay = null; selected = 3 }
                        link.startsWith("/") -> openWeb(link)
                    }
                }
                // Apply a route passed in from a push-notification tap (once).
                LaunchedEffect(Unit) {
                    intent?.getStringExtra("route")?.let { if (it.isNotBlank()) routeLink(it) }
                }

                data class Tab(val label: String, val icon: androidx.compose.ui.graphics.vector.ImageVector)
                val tabs = listOf(
                    Tab("Home", Icons.Filled.Home),
                    Tab("Tasks", Icons.Filled.CheckCircle),
                    Tab("Add Sale", Icons.Filled.AddCircle),
                    Tab("Calls", Icons.Filled.Call),
                    Tab("Menu", Icons.Filled.Menu),
                )

                Scaffold(
                    bottomBar = {
                        NavigationBar {
                            tabs.forEachIndexed { i, tab ->
                                NavigationBarItem(
                                    selected = selected == i && overlay == null,
                                    onClick = {
                                        // Tasks is a self-contained module (its own bottom
                                        // nav) — open it as a full-screen activity instead
                                        // of rendering it inline as a tab.
                                        if (tab.label == "Tasks") TasksActivity.open(this@ShellActivity)
                                        else { selected = i; overlay = null }
                                    },
                                    icon = { Icon(tab.icon, contentDescription = tab.label) },
                                    label = { Text(tab.label) },
                                )
                            }
                        }
                    }
                ) { padding ->
                    val m = Modifier.padding(padding)
                    when {
                        overlay == "sales" || overlay == "sales_pending" -> SalesScreen(
                            modifier = m,
                            initialStatus = if (overlay == "sales_pending") "pending" else "",
                            onBack = { overlay = null },
                            onSessionExpired = goLogin,
                        )
                        overlay == "renewals" -> RenewalsScreen(
                            modifier = m,
                            onBack = { overlay = null },
                            onSessionExpired = goLogin,
                        )
                        overlay == "notifications" -> NotificationsScreen(
                            modifier = m,
                            onBack = { overlay = null },
                            onSessionExpired = goLogin,
                            onOpenWeb = routeLink,
                        )
                        overlay == "leads" -> LeadsScreen(
                            modifier = m,
                            onBack = { overlay = null },
                            onSessionExpired = goLogin,
                        )
                        overlay == "reports" -> ReportsHub(
                            modifier = m,
                            onBack = { overlay = null },
                            onSessionExpired = goLogin,
                            onOpenWeb = openWeb,
                        )
                        overlay == "call_analytics" -> CallAnalyticsScreen(
                            modifier = m,
                            onBack = { overlay = null },
                            onSessionExpired = goLogin,
                        )
                        overlay == "team" -> TeamScreen(
                            modifier = m,
                            onBack = { overlay = null },
                            onSessionExpired = goLogin,
                        )
                        overlay == "incentives" -> IncentivesScreen(
                            modifier = m,
                            onBack = { overlay = null },
                            onSessionExpired = goLogin,
                        )
                        overlay == "sim" -> SimSettingsScreen(
                            modifier = m,
                            onBack = { overlay = null },
                            onSessionExpired = goLogin,
                        )
                        overlay == "settings" -> SettingsScreen(
                            modifier = m,
                            onBack = { overlay = null },
                            onSessionExpired = goLogin,
                            onOpenSim = { overlay = "sim" },
                            // re-cache the popup grid/windows so changes apply now
                            onSaved = { Thread { syncCallConfigBlocking() }.start() },
                        )
                        overlay == "clients" -> ClientsScreen(
                            modifier = m,
                            onBack = { overlay = null },
                            onSessionExpired = goLogin,
                            onOpenWeb = openWeb,
                        )
                        overlay == "today" -> bo.kadlaginvestment.crm.ui.TodayScreen(
                            modifier = m,
                            onBack = { overlay = null },
                            onSessionExpired = goLogin,
                        )
                        selected == 0 -> DashboardScreen(m, onSessionExpired = goLogin, onOpenWeb = routeLink)
                        selected == 2 -> AddSaleScreen(m, onSessionExpired = goLogin)
                        selected == 3 -> FollowupsScreen(m, onSessionExpired = goLogin)
                        else -> MenuScreen(
                            modifier = m,
                            onOpenWeb = openWeb,
                            onOpenNative = { route -> overlay = route },
                            onLoggedOut = logout,
                            onSessionExpired = goLogin,
                        )
                    }
                }
            }
        }
    }
}
