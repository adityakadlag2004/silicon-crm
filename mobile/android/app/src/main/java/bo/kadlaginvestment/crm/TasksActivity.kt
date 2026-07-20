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
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Notifications
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
import bo.kadlaginvestment.crm.ui.AssignTaskSheet
import bo.kadlaginvestment.crm.ui.KadlagTheme
import bo.kadlaginvestment.crm.ui.StatusGreen
import bo.kadlaginvestment.crm.ui.TaskActivitiesScreen
import bo.kadlaginvestment.crm.ui.TaskDetailScreen
import bo.kadlaginvestment.crm.ui.TaskMoreScreen
import bo.kadlaginvestment.crm.ui.TaskPagerScreen
import bo.kadlaginvestment.crm.ui.rsp

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
        // Keep due-time task alarms matched to the server whenever the module opens.
        Thread { TaskAlarmScheduler.syncBlocking(applicationContext) }.start()
        setContent {
            KadlagTheme {
                var tab by remember { mutableIntStateOf(0) }       // 0 Dash 1 My 2 Delegated 3 Subscribed 4 More
                var subRoute by remember { mutableStateOf<String?>(null) } // all/subscribed/activities from More
                var detailId by remember {
                    mutableStateOf<Int?>(intent?.getIntExtra("task_id", 0)?.takeIf { it > 0 })
                }
                var sheetOpen by remember { mutableStateOf(false) }
                var sheetEdit by remember { mutableStateOf<org.json.JSONObject?>(null) }
                var reloadSignal by remember { mutableIntStateOf(0) }
                var role by remember { mutableStateOf("") }

                LaunchedEffect(Unit) {
                    when (val r = ApiClient.get("/clients/api/app/dashboard/")) {
                        is ApiClient.Result.Ok -> role = r.json.optString("role")
                        else -> {}
                    }
                }

                val goLogin: () -> Unit = {
                    ApiClient.clearSession()
                    startActivity(LoginActivity.expiredIntent(this))
                    finish()
                }
                val openWeb: (String) -> Unit = { path -> WebActivity.open(this, path) }

                val isAdminOrManager = role == "admin" || role == "manager"
                val onList = subRoute == "all" ||
                    (subRoute == null && tab in listOf(0, 1, 2, 3))

                val openSheet: (org.json.JSONObject?) -> Unit = { edit -> sheetEdit = edit; sheetOpen = true }

                Box(Modifier.fillMaxSize()) {
                if (detailId != null) {
                    TaskDetailScreen(
                        taskId = detailId!!,
                        reloadSignal = reloadSignal,
                        onBack = { detailId = null },
                        onSessionExpired = goLogin,
                        onOpenWeb = openWeb,
                        onChanged = { reloadSignal++ },
                        onEdit = { openSheet(it) },
                    )
                } else
                Scaffold(
                    topBar = {
                        TopAppBar(
                            title = {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Text("🗂", fontSize = rsp(18), modifier = Modifier.padding(end = 8.dp))
                                    Text("Tasks", fontWeight = FontWeight.Bold)
                                }
                            },
                            actions = {
                                Text(
                                    "↻",
                                    fontSize = rsp(20),
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
                                T("Delegated", Icons.Filled.Send),
                                T("Subscribed", Icons.Filled.Notifications),
                                T("More", Icons.Filled.Menu),
                            )
                            tabs.forEachIndexed { i, t ->
                                NavigationBarItem(
                                    selected = tab == i && subRoute == null,
                                    onClick = { tab = i; subRoute = null },
                                    icon = { Icon(t.icon, contentDescription = t.label) },
                                    label = { Text(t.label, fontSize = rsp(10)) },
                                )
                            }
                        }
                    },
                    floatingActionButton = {
                        if (onList) {
                            FloatingActionButton(
                                onClick = { openSheet(null) },
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
                            subRoute == "all" -> TaskPagerScreen("all", false, reloadSignal, { detailId = it }, goLogin)
                            tab == 0 -> TaskPagerScreen("all", true, reloadSignal, { detailId = it }, goLogin)
                            tab == 1 -> TaskPagerScreen("my", false, reloadSignal, { detailId = it }, goLogin)
                            tab == 2 -> TaskPagerScreen("delegated", false, reloadSignal, { detailId = it }, goLogin)
                            tab == 3 -> TaskPagerScreen("subscribed", false, reloadSignal, { detailId = it }, goLogin)
                            else -> TaskMoreScreen(
                                isAdminOrManager = isAdminOrManager,
                                onNavigate = { subRoute = it },
                                onOpenWeb = openWeb,
                            )
                        }
                    }
                }

                if (sheetOpen) {
                    AssignTaskSheet(
                        editTask = sheetEdit,
                        onDismiss = { sheetOpen = false },
                        onDone = { sheetOpen = false; reloadSignal++ },
                        onSessionExpired = goLogin,
                    )
                }
                } // Box
            }
        }
    }
}
