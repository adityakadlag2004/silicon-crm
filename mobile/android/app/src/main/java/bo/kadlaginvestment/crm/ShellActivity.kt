package bo.kadlaginvestment.crm

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.webkit.CookieManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.TextButton
import androidx.compose.ui.unit.dp
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AddCircle
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Person
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
import bo.kadlaginvestment.crm.ui.AddSaleScreen
import bo.kadlaginvestment.crm.ui.CallAnalyticsScreen
import bo.kadlaginvestment.crm.ui.ClientsScreen
import bo.kadlaginvestment.crm.ui.DashboardScreen
import bo.kadlaginvestment.crm.ui.FollowupsScreen
import bo.kadlaginvestment.crm.ui.KadlagTheme
import bo.kadlaginvestment.crm.ui.LeadsScreen
import bo.kadlaginvestment.crm.ui.MenuScreen
import bo.kadlaginvestment.crm.ui.ReportsScreen
import bo.kadlaginvestment.crm.ui.NotificationsScreen
import bo.kadlaginvestment.crm.ui.RenewalsScreen
import bo.kadlaginvestment.crm.ui.SalesScreen
import bo.kadlaginvestment.crm.ui.TeamScreen

@androidx.compose.runtime.Composable
private fun PermRow(label: String, onFix: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(top = 10.dp),
        horizontalArrangement = androidx.compose.foundation.layout.Arrangement.SpaceBetween,
        verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
    ) {
        Text(label, modifier = Modifier.padding(end = 8.dp))
        Button(onClick = onFix) { Text("Allow") }
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

    private fun reportDeviceStatus() {
        Thread {
            try {
                val version = packageManager.getPackageInfo(packageName, 0).versionName ?: ""
                val body = org.json.JSONObject()
                    .put("calls_granted", callsGranted())
                    .put("overlay_granted", Settings.canDrawOverlays(this))
                    .put("notifications_granted", notificationsGranted())
                    .put("app_version", version)
                BackendClient.postJson("/clients/api/app/device-status/", body.toString())
            } catch (_: Exception) {}
        }.start()
    }

    override fun onResume() {
        super.onResume()
        permTick.intValue++
        reportDeviceStatus()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Catch-up sync: uploads any calls that couldn't be synced when they
        // ended (no internet at the time). Server-side dedup makes this safe.
        Thread { CallSyncManager.syncRecentCalls(applicationContext) }.start()

        setContent {
            KadlagTheme {
                var selected by remember { mutableIntStateOf(0) }
                var permDialogDismissed by remember { mutableStateOf(false) }

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

                if (!permDialogDismissed && (needCalls || needOverlay || needNotifications)) {
                    AlertDialog(
                        onDismissRequest = { permDialogDismissed = true },
                        title = { Text("Setup needed") },
                        text = {
                            Column {
                                Text("Some permissions are missing — call tracking and follow-up popups won't work until these are allowed:")
                                if (needCalls) PermRow("Call tracking (phone + call log)") {
                                    requestPermissions(
                                        arrayOf(Manifest.permission.READ_PHONE_STATE, Manifest.permission.READ_CALL_LOG), 100,
                                    )
                                }
                                if (needOverlay) PermRow("Follow-up popup (display over apps)") {
                                    startActivity(
                                        Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName"))
                                    )
                                }
                                if (needNotifications) PermRow("Notifications") {
                                    if (Build.VERSION.SDK_INT >= 33) {
                                        requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 101)
                                    }
                                }
                            }
                        },
                        confirmButton = {},
                        dismissButton = {
                            TextButton(onClick = { permDialogDismissed = true }) { Text("Later") }
                        },
                    )
                }
                // Native routes layered above the tabs: "sales", "sales_pending",
                // "renewals", "notifications".
                var overlay by remember { mutableStateOf<String?>(null) }

                val goLogin: () -> Unit = {
                    startActivity(
                        Intent(this, MainActivity::class.java)
                            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
                    )
                    finish()
                }
                val logout: () -> Unit = {
                    CookieManager.getInstance().removeAllCookies(null)
                    CookieManager.getInstance().flush()
                    goLogin()
                }
                val openWeb: (String) -> Unit = { path -> WebActivity.open(this, path) }
                // Dashboard shortcuts route to native screens where they exist.
                val smartOpen: (String) -> Unit = { path ->
                    when (path) {
                        "/clients/sales/approve/" -> overlay = "sales_pending"
                        "/clients/calls/followups/" -> selected = 3
                        else -> openWeb(path)
                    }
                }

                data class Tab(val label: String, val icon: androidx.compose.ui.graphics.vector.ImageVector)
                val tabs = listOf(
                    Tab("Home", Icons.Filled.Home),
                    Tab("Clients", Icons.Filled.Person),
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
                                    onClick = { selected = i; overlay = null },
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
                            onOpenWeb = openWeb,
                        )
                        overlay == "leads" -> LeadsScreen(
                            modifier = m,
                            onBack = { overlay = null },
                            onSessionExpired = goLogin,
                        )
                        overlay == "reports" -> ReportsScreen(
                            modifier = m,
                            onBack = { overlay = null },
                            onSessionExpired = goLogin,
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
                        selected == 0 -> DashboardScreen(m, onSessionExpired = goLogin, onOpenWeb = smartOpen)
                        selected == 1 -> ClientsScreen(m, onSessionExpired = goLogin, onOpenWeb = openWeb)
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
