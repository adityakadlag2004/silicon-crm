package bo.kadlaginvestment.crm.ui

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
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import bo.kadlaginvestment.crm.net.ApiClient
import kotlinx.coroutines.launch
import org.json.JSONObject

/** Native Sales list. Admins / authorized managers can approve or reject
 * pending sales inline. */
@Composable
fun SalesScreen(
    modifier: Modifier = Modifier,
    initialStatus: String = "",
    onBack: (() -> Unit)? = null,
    onSessionExpired: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    if (onBack != null) BackHandler(onBack = onBack)

    var status by remember { mutableStateOf(initialStatus) }
    var rows by remember { mutableStateOf(listOf<JSONObject>()) }
    var canApprove by remember { mutableStateOf(false) }
    var canDelete by remember { mutableStateOf(false) }
    var page by remember { mutableIntStateOf(1) }
    var hasMore by remember { mutableStateOf(false) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var reloadKey by remember { mutableIntStateOf(0) }
    var rejecting by remember { mutableStateOf<JSONObject?>(null) }
    var rejectReason by remember { mutableStateOf("") }
    var deleting by remember { mutableStateOf<JSONObject?>(null) }

    LaunchedEffect(status, page, reloadKey) {
        loading = true
        error = null
        when (val r = ApiClient.get("/clients/api/app/sales/?status=$status&page=$page")) {
            is ApiClient.Result.Ok -> {
                val arr = r.json.optJSONArray("results")
                val newRows = (0 until (arr?.length() ?: 0)).map { arr!!.getJSONObject(it) }
                rows = if (page == 1) newRows else rows + newRows
                hasMore = r.json.optBoolean("has_more")
                canApprove = r.json.optBoolean("can_approve")
                canDelete = r.json.optBoolean("can_delete")
            }
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
        loading = false
    }

    fun act(sale: JSONObject, action: String, reason: String = "") {
        scope.launch {
            val body = JSONObject().put("action", action).put("reason", reason)
            when (val r = ApiClient.post("/clients/api/app/sales/${sale.getInt("id")}/action/", body)) {
                is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                is ApiClient.Result.Error -> error = r.message
                is ApiClient.Result.Ok -> { page = 1; reloadKey++ }
            }
        }
    }

    rejecting?.let { sale ->
        AlertDialog(
            onDismissRequest = { rejecting = null },
            title = { Text("Reject sale #${sale.optInt("id")}") },
            text = {
                OutlinedTextField(
                    value = rejectReason,
                    onValueChange = { rejectReason = it },
                    label = { Text("Reason (optional)") },
                    modifier = Modifier.fillMaxWidth(),
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    act(sale, "reject", rejectReason)
                    rejecting = null
                    rejectReason = ""
                }) { Text("Reject", color = StatusRed) }
            },
            dismissButton = { TextButton(onClick = { rejecting = null }) { Text("Cancel") } },
        )
    }

    deleting?.let { sale ->
        AlertDialog(
            onDismissRequest = { deleting = null },
            title = { Text("Delete sale #${sale.optInt("id")}?") },
            text = {
                Text(
                    "This permanently removes the ${rupees(sale.optDouble("amount", 0.0))} " +
                        "${sale.optString("product")} sale for ${sale.optString("client")}. " +
                        "This can't be undone."
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    act(sale, "delete")
                    deleting = null
                }) { Text("Delete", color = StatusRed) }
            },
            dismissButton = { TextButton(onClick = { deleting = null }) { Text("Cancel") } },
        )
    }

    Column(modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        Row(
            Modifier.fillMaxWidth().padding(top = 16.dp, bottom = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (onBack != null) {
                Text(
                    "← Back",
                    color = MaterialTheme.colorScheme.secondary,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.clickable(onClick = onBack).padding(end = 12.dp),
                )
            }
            Text("Sales", fontSize = 22.sp, fontWeight = FontWeight.Bold)
        }

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Chip("All", status == "") { status = ""; page = 1 }
            Chip("Pending", status == "pending") { status = "pending"; page = 1 }
            Chip("Approved", status == "approved") { status = "approved"; page = 1 }
            Chip("Rejected", status == "rejected") { status = "rejected"; page = 1 }
        }

        when {
            error != null -> ErrorBox(error!!) { error = null; reloadKey++ }
            loading && rows.isEmpty() -> LoadingBox()
            else -> LazyColumn(
                Modifier.fillMaxSize(),
                verticalArrangement = Arrangement.spacedBy(8.dp),
                contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 12.dp),
            ) {
                if (rows.isEmpty()) {
                    item {
                        Text("No sales found.", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp)
                    }
                }
                items(rows) { s ->
                    Card(
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
                    ) {
                        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            Row(
                                Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Column(Modifier.weight(1f)) {
                                    Text(s.optString("client"), fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
                                    Text(
                                        "${s.optString("product")} · ${s.optString("employee")} · ${s.optString("date")}",
                                        fontSize = 12.sp,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                }
                                Column(horizontalAlignment = Alignment.End) {
                                    Text(rupees(s.optDouble("amount", 0.0)), fontWeight = FontWeight.Bold, fontSize = 15.sp)
                                    StatusPill(s.optString("status"))
                                }
                            }
                            if (canApprove || canDelete) {
                                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    if (canApprove && s.optString("status") == "pending") {
                                        Button(onClick = { act(s, "approve") }) { Text("Approve") }
                                        OutlinedButton(onClick = { rejecting = s }) { Text("Reject") }
                                    }
                                    if (canDelete) {
                                        OutlinedButton(onClick = { deleting = s }) {
                                            Text("Delete", color = StatusRed)
                                        }
                                    }
                                }
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
