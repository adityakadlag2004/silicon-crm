package bo.kadlaginvestment.crm

import android.content.Intent
import android.os.Bundle
import android.webkit.CookieManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.padding
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
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import bo.kadlaginvestment.crm.ui.AddSaleScreen
import bo.kadlaginvestment.crm.ui.ClientsScreen
import bo.kadlaginvestment.crm.ui.DashboardScreen
import bo.kadlaginvestment.crm.ui.FollowupsScreen
import bo.kadlaginvestment.crm.ui.KadlagTheme
import bo.kadlaginvestment.crm.ui.MenuScreen
import bo.kadlaginvestment.crm.ui.SalesScreen

/**
 * Native app shell: bottom navigation, all tabs rendered in Compose.
 * Screens not yet converted open in [WebActivity] from the Menu tab
 * (see mobile/NATIVE_MIGRATION.md for the conversion order).
 */
class ShellActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        setContent {
            KadlagTheme {
                var selected by remember { mutableIntStateOf(0) }
                var salesOverlay by remember { mutableStateOf<String?>(null) } // initial status filter

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
                        "/clients/sales/approve/" -> salesOverlay = "pending"
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
                                    selected = selected == i && salesOverlay == null,
                                    onClick = { selected = i; salesOverlay = null },
                                    icon = { Icon(tab.icon, contentDescription = tab.label) },
                                    label = { Text(tab.label) },
                                )
                            }
                        }
                    }
                ) { padding ->
                    val m = Modifier.padding(padding)
                    when {
                        salesOverlay != null -> SalesScreen(
                            modifier = m,
                            initialStatus = salesOverlay!!,
                            onBack = { salesOverlay = null },
                            onSessionExpired = goLogin,
                        )
                        selected == 0 -> DashboardScreen(m, onSessionExpired = goLogin, onOpenWeb = smartOpen)
                        selected == 1 -> ClientsScreen(m, onSessionExpired = goLogin, onOpenWeb = openWeb)
                        selected == 2 -> AddSaleScreen(m, onSessionExpired = goLogin)
                        selected == 3 -> FollowupsScreen(m, onSessionExpired = goLogin)
                        else -> MenuScreen(
                            modifier = m,
                            onOpenWeb = openWeb,
                            onOpenSales = { salesOverlay = "" },
                            onLoggedOut = logout,
                            onSessionExpired = goLogin,
                        )
                    }
                }
            }
        }
    }
}
