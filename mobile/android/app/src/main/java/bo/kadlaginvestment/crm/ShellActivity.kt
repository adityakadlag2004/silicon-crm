package bo.kadlaginvestment.crm

import android.content.Intent
import android.os.Bundle
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
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import bo.kadlaginvestment.crm.ui.DashboardScreen
import bo.kadlaginvestment.crm.ui.KadlagTheme

/**
 * Native app shell: bottom navigation with the converted screens rendered
 * in Compose. Tabs not yet converted open the corresponding web page in
 * [WebActivity] (see mobile/NATIVE_MIGRATION.md for the conversion order).
 */
class ShellActivity : ComponentActivity() {

    private data class Tab(val label: String, val icon: ImageVector, val webPath: String?)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val tabs = listOf(
            Tab("Home", Icons.Filled.Home, null), // native
            Tab("Clients", Icons.Filled.Person, "/clients/my/"),
            Tab("Add Sale", Icons.Filled.AddCircle, "/clients/sales/add/"),
            Tab("Calls", Icons.Filled.Call, "/clients/calls/followups/"),
            Tab("Menu", Icons.Filled.Menu, "/clients/dashboard/employee/"),
        )

        setContent {
            KadlagTheme {
                var selected by remember { mutableIntStateOf(0) }
                Scaffold(
                    bottomBar = {
                        NavigationBar {
                            tabs.forEachIndexed { i, tab ->
                                NavigationBarItem(
                                    selected = selected == i,
                                    onClick = {
                                        if (tab.webPath == null) {
                                            selected = i
                                        } else {
                                            WebActivity.open(this@ShellActivity, tab.webPath)
                                        }
                                    },
                                    icon = { Icon(tab.icon, contentDescription = tab.label) },
                                    label = { Text(tab.label) },
                                )
                            }
                        }
                    }
                ) { padding ->
                    // Only "Home" is native so far; other tabs launch WebActivity.
                    DashboardScreen(
                        modifier = Modifier.padding(padding),
                        onSessionExpired = {
                            startActivity(
                                Intent(this, MainActivity::class.java)
                                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
                            )
                            finish()
                        },
                        onOpenWeb = { path -> WebActivity.open(this, path) },
                    )
                }
            }
        }
    }
}
