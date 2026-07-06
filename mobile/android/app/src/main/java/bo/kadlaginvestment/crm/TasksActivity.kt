package bo.kadlaginvestment.crm

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.activity.compose.BackHandler
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.List
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Send
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import bo.kadlaginvestment.crm.net.ApiClient
import bo.kadlaginvestment.crm.ui.AssignTaskScreen
import bo.kadlaginvestment.crm.ui.KadlagTheme
import bo.kadlaginvestment.crm.ui.StatusGreen
import bo.kadlaginvestment.crm.ui.TaskActivitiesScreen
import bo.kadlaginvestment.crm.ui.TaskDashboardScreen
import bo.kadlaginvestment.crm.ui.TaskDetailScreen
import bo.kadlaginvestment.crm.ui.TaskMoreScreen
import bo.kadlaginvestment.crm.ui.TasksListScreen

/** Self-contained native Task module: its own bottom nav, FAB and sub-pages. */
class TasksActivity : ComponentActivity() {

    companion object {
        fun open(context: Context, taskId: Int? = null) {
            val i = Intent(context, TasksActivity::class.java)
            if (taskId != null && taskId > 0) i.putExtra("task_id", taskId)
            context.startActivity(i)
        }
    }

    @OptIn(ExperimentalMaterial3Api::class)
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            KadlagTheme {
                var tab by remember { mutableIntStateOf(0) }       // 0 Dash 1 My 2 MyApps 3 Delegated 4 More
                var subRoute by remember { mutableStateOf<String?>(null) } // all/subscribed/activities from More
                var detailId by remember {
                    mutableStateOf<Int?>(intent?.getIntExtra("task_id", 0)?.takeIf { it > 0 })
                }
                var showAssign by remember { mutableStateOf(false) }
                var reloadSignal by remember { mutableIntStateOf(0) }
                var role by remember { mutableStateOf("") }

                LaunchedEffect(Unit) {
                    when (val r = ApiClient.get("/clients/api/app/dashboard/")) {
                        is ApiClient.Result.Ok -> role = r.json.optString("role")
                        else -> {}
                    }
                }

                val goLogin: () -> Unit = {
                    startActivity(
                        Intent(this, LoginActivity::class.java)
                            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
                    )
                    finish()
                }
                val openWeb: (String) -> Unit = { path -> WebActivity.open(this, path) }

                // Full-screen sub-pages take over the whole activity (no bottom nav).
                when {
                    showAssign -> {
                        AssignTaskScreen(
                            onBack = { showAssign = false },
                            onSessionExpired = goLogin,
                            onCreated = { showAssign = false; reloadSignal++ },
                        )
                        return@KadlagTheme
                    }
                    detailId != null -> {
                        TaskDetailScreen(
                            taskId = detailId!!,
                            onBack = { detailId = null },
                            onSessionExpired = goLogin,
                            onOpenWeb = openWeb,
                            onChanged = { reloadSignal++ },
                        )
                        return@KadlagTheme
                    }
                }

                val isAdminOrManager = role == "admin" || role == "manager"
                val onList = subRoute == "all" || subRoute == "subscribed" ||
                    (subRoute == null && tab in listOf(0, 1, 3))

                Scaffold(
                    topBar = {
                        TopAppBar(
                            title = {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Text("🗂", fontSize = 18.sp, modifier = Modifier.padding(end = 8.dp))
                                    Text("Tasks", fontWeight = FontWeight.Bold)
                                }
                            },
                            actions = {
                                Text(
                                    "↻",
                                    fontSize = 20.sp,
                                    color = MaterialTheme.colorScheme.onSurface,
                                    modifier = Modifier
                                        .padding(end = 16.dp)
                                        .clickable { reloadSignal++ },
                                )
                            },
                            colors = TopAppBarDefaults.topAppBarColors(
                                containerColor = MaterialTheme.colorScheme.surface,
                            ),
                        )
                    },
                    bottomBar = {
                        NavigationBar {
                            data class T(val label: String, val icon: androidx.compose.ui.graphics.vector.ImageVector)
                            val tabs = listOf(
                                T("Dashboard", Icons.Filled.Home),
                                T("My Tasks", Icons.Filled.CheckCircle),
                                T("My Apps", Icons.Filled.List),
                                T("Delegated", Icons.Filled.Send),
                                T("More", Icons.Filled.Menu),
                            )
                            tabs.forEachIndexed { i, t ->
                                NavigationBarItem(
                                    selected = tab == i && subRoute == null,
                                    onClick = { tab = i; subRoute = null },
                                    icon = { Icon(t.icon, contentDescription = t.label) },
                                    label = { Text(t.label, fontSize = 10.sp) },
                                )
                            }
                        }
                    },
                    floatingActionButton = {
                        if (onList) {
                            FloatingActionButton(
                                onClick = { showAssign = true },
                                containerColor = StatusGreen,
                                contentColor = Color.White,
                            ) { Icon(Icons.Filled.Add, contentDescription = "Assign task") }
                        }
                    },
                ) { padding ->
                    val m = Modifier.padding(padding).fillMaxSize()
                    // Sub-routes reached from More get a back gesture returning to More.
                    if (subRoute != null) BackHandler { subRoute = null }

                    Box(m) {
                        when {
                            subRoute == "activities" -> TaskActivitiesScreen(
                                onOpenTask = { detailId = it }, onSessionExpired = goLogin)
                            subRoute == "all" -> TasksListScreen("all", reloadSignal, { detailId = it }, goLogin)
                            subRoute == "subscribed" -> TasksListScreen("subscribed", reloadSignal, { detailId = it }, goLogin)
                            tab == 0 -> TaskDashboardScreen(reloadSignal, { detailId = it }, goLogin)
                            tab == 1 -> TasksListScreen("my", reloadSignal, { detailId = it }, goLogin)
                            tab == 2 -> MyAppsScreen(openWeb)
                            tab == 3 -> TasksListScreen("delegated", reloadSignal, { detailId = it }, goLogin)
                            else -> TaskMoreScreen(
                                isAdminOrManager = isAdminOrManager,
                                onNavigate = { subRoute = it },
                                onOpenWeb = openWeb,
                            )
                        }
                    }
                }
            }
        }
    }
}

@androidx.compose.runtime.Composable
private fun MyAppsScreen(onOpenWeb: (String) -> Unit) {
    Column(
        Modifier.fillMaxSize().padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = androidx.compose.foundation.layout.Arrangement.Center,
    ) {
        Text("🔗", fontSize = 44.sp)
        Text("Business Links", fontWeight = FontWeight.Bold, fontSize = 17.sp)
        Text(
            "Your business links & portals.",
            color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp,
        )
        androidx.compose.foundation.layout.Spacer(Modifier.padding(6.dp))
        androidx.compose.material3.Button(onClick = { onOpenWeb("/clients/links/") }) {
            Text("Open Links")
        }
    }
}
