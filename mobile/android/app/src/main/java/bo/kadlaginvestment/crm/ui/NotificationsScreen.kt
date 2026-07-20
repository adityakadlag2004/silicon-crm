package bo.kadlaginvestment.crm.ui

import android.content.Intent
import android.net.Uri
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import bo.kadlaginvestment.crm.net.ApiClient
import kotlinx.coroutines.launch
import org.json.JSONObject

/** Native Notifications: same feed as the web bell, tap to open the target. */
@Composable
fun NotificationsScreen(
    modifier: Modifier = Modifier,
    onBack: () -> Unit,
    onSessionExpired: () -> Unit,
    onOpenWeb: (String) -> Unit,
) {
    BackHandler(onBack = onBack)
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var data by remember { mutableStateOf<JSONObject?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var reloadKey by remember { mutableIntStateOf(0) }

    LaunchedEffect(reloadKey) {
        when (val r = ApiClient.get("/clients/api/app/notifications/")) {
            is ApiClient.Result.Ok -> data = r.json
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
    }

    if (error != null) { ErrorBox(error!!, modifier) { error = null; reloadKey++ }; return }
    val d = data ?: run { LoadingBox(modifier); return }
    val arr = d.optJSONArray("results")
    val rows = (0 until (arr?.length() ?: 0)).map { arr!!.getJSONObject(it) }

    LazyColumn(
        modifier.fillMaxSize().padding(horizontal = rdp(16)),
        verticalArrangement = Arrangement.spacedBy(8.dp),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 16.dp),
    ) {
        item {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        "← Back",
                        color = MaterialTheme.colorScheme.secondary,
                        fontWeight = FontWeight.SemiBold,
                        modifier = Modifier.clickable(onClick = onBack).padding(end = 12.dp),
                    )
                    Text("Notifications", fontSize = rsp(22), fontWeight = FontWeight.Bold)
                }
                if (d.optInt("unread") > 0) {
                    TextButton(onClick = {
                        scope.launch {
                            ApiClient.post("/clients/api/app/notifications/read/", JSONObject())
                            data = null; reloadKey++
                        }
                    }) { Text("Mark all read") }
                }
            }
        }

        if (rows.isEmpty()) {
            item { Text("No notifications yet.", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = rsp(13)) }
        }

        items(rows) { n ->
            val unread = !n.optBoolean("is_read")
            Card(
                modifier = Modifier.fillMaxWidth().clickable {
                    // onOpenWeb is the shell's routeLink: handles tel:, native
                    // screens, and web links.
                    n.optString("link").takeIf { it.isNotBlank() }?.let { onOpenWeb(it) }
                },
                colors = CardDefaults.cardColors(
                    containerColor = if (unread) MaterialTheme.colorScheme.primaryContainer
                    else MaterialTheme.colorScheme.surface
                ),
                elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
            ) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        Text(
                            n.optString("title"),
                            fontWeight = if (unread) FontWeight.Bold else FontWeight.SemiBold,
                            fontSize = rsp(14),
                            modifier = Modifier.weight(1f),
                        )
                        Text(
                            n.optString("created_at"),
                            fontSize = rsp(11),
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    Text(n.optString("body"), fontSize = rsp(13), color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }

        item { Spacer(Modifier.height(12.dp)) }
    }
}
