package bo.kadlaginvestment.crm.ui

import androidx.activity.compose.BackHandler
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
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
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import bo.kadlaginvestment.crm.net.ApiClient
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

/** Native Team management (admin-only): roster, member detail with edit,
 * activate/deactivate, password reset, and add-member form. */
@Composable
fun TeamScreen(
    modifier: Modifier = Modifier,
    onBack: () -> Unit,
    onSessionExpired: () -> Unit,
) {
    BackHandler(onBack = onBack)
    var selectedId by remember { mutableStateOf<Int?>(null) }
    var creating by remember { mutableStateOf(false) }
    var roles by remember { mutableStateOf<JSONArray?>(null) }
    var listReload by remember { mutableIntStateOf(0) }

    when {
        creating -> {
            BackHandler { creating = false }
            TeamCreateForm(modifier, roles, onDone = { creating = false; listReload++ }, onSessionExpired = onSessionExpired)
        }
        selectedId != null -> {
            BackHandler { selectedId = null; listReload++ }
            TeamDetail(
                employeeId = selectedId!!,
                roles = roles,
                modifier = modifier,
                onBack = { selectedId = null; listReload++ },
                onSessionExpired = onSessionExpired,
            )
        }
        else -> TeamList(
            modifier = modifier,
            reloadKey = listReload,
            onBack = onBack,
            onOpen = { selectedId = it },
            onCreate = { creating = true },
            onRoles = { roles = it },
            onSessionExpired = onSessionExpired,
        )
    }
}

@Composable
private fun TeamList(
    modifier: Modifier,
    reloadKey: Int,
    onBack: () -> Unit,
    onOpen: (Int) -> Unit,
    onCreate: () -> Unit,
    onRoles: (JSONArray?) -> Unit,
    onSessionExpired: () -> Unit,
) {
    var rows by remember { mutableStateOf<List<JSONObject>?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var localReload by remember { mutableIntStateOf(0) }

    LaunchedEffect(reloadKey, localReload) {
        when (val r = ApiClient.get("/clients/api/app/team/")) {
            is ApiClient.Result.Ok -> {
                val arr = r.json.optJSONArray("results")
                rows = (0 until (arr?.length() ?: 0)).map { arr!!.getJSONObject(it) }
                onRoles(r.json.optJSONArray("roles"))
            }
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
    }

    Column(modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        Row(
            Modifier.fillMaxWidth().padding(top = 16.dp, bottom = 8.dp),
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
                Text("Team", fontSize = 22.sp, fontWeight = FontWeight.Bold)
            }
            Button(onClick = onCreate) { Text("＋ Add") }
        }

        when {
            error != null -> ErrorBox(error!!) { error = null; localReload++ }
            rows == null -> LoadingBox()
            else -> LazyColumn(
                Modifier.fillMaxSize(),
                verticalArrangement = Arrangement.spacedBy(8.dp),
                contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 8.dp),
            ) {
                items(rows!!) { e ->
                    Card(
                        modifier = Modifier.fillMaxWidth().clickable { onOpen(e.getInt("id")) },
                        colors = CardDefaults.cardColors(
                            containerColor = if (e.optBoolean("active")) MaterialTheme.colorScheme.surface
                            else MaterialTheme.colorScheme.surfaceVariant
                        ),
                        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
                    ) {
                        Row(
                            Modifier.fillMaxWidth().padding(14.dp),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Column(Modifier.weight(1f)) {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Text(e.optString("name"), fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
                                    Spacer(Modifier.padding(horizontal = 4.dp))
                                    RolePill(e.optString("role"))
                                    if (!e.optBoolean("active")) {
                                        Spacer(Modifier.padding(horizontal = 3.dp))
                                        Text("INACTIVE", fontSize = 9.sp, color = StatusRed, fontWeight = FontWeight.Bold)
                                    }
                                }
                                Text(
                                    listOf(
                                        e.optString("employee_number"),
                                        "${e.optInt("client_count")} clients",
                                    ).filter { it.isNotEmpty() }.joinToString(" · "),
                                    fontSize = 12.sp,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            Column(horizontalAlignment = Alignment.End) {
                                Text(rupees(e.optDouble("month_amount", 0.0)), fontWeight = FontWeight.Bold, fontSize = 14.sp)
                                Text(
                                    "${e.optInt("month_sales")} sale(s) this month",
                                    fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                    }
                }
                item { Spacer(Modifier.height(12.dp)) }
            }
        }
    }
}

@Composable
private fun RolePill(role: String) {
    val color = when (role) {
        "admin" -> StatusRed
        "manager" -> StatusAmber
        else -> StatusGreen
    }
    Text(role.uppercase(), fontSize = 9.sp, color = color, fontWeight = FontWeight.Bold)
}

@Composable
private fun TeamDetail(
    employeeId: Int,
    roles: JSONArray?,
    modifier: Modifier,
    onBack: () -> Unit,
    onSessionExpired: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    var data by remember { mutableStateOf<JSONObject?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var message by remember { mutableStateOf<Pair<Boolean, String>?>(null) }
    var reloadKey by remember { mutableIntStateOf(0) }

    var firstName by remember { mutableStateOf("") }
    var lastName by remember { mutableStateOf("") }
    var email by remember { mutableStateOf("") }
    var role by remember { mutableStateOf("") }
    var salary by remember { mutableStateOf("") }
    var empNumber by remember { mutableStateOf("") }
    var roleMenuOpen by remember { mutableStateOf(false) }
    var showReset by remember { mutableStateOf(false) }
    var newPassword by remember { mutableStateOf("") }
    var confirmToggle by remember { mutableStateOf(false) }

    LaunchedEffect(employeeId, reloadKey) {
        when (val r = ApiClient.get("/clients/api/app/team/$employeeId/")) {
            is ApiClient.Result.Ok -> {
                data = r.json
                firstName = r.json.optString("first_name")
                lastName = r.json.optString("last_name")
                email = r.json.optString("email")
                role = r.json.optString("role")
                salary = "%.0f".format(r.json.optDouble("salary", 0.0))
                empNumber = r.json.optString("employee_number")
            }
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> error = r.message
        }
    }

    if (error != null) { ErrorBox(error!!, modifier) { error = null; reloadKey++ }; return }
    val d = data ?: run { LoadingBox(modifier); return }
    val active = d.optBoolean("active")

    if (showReset) {
        AlertDialog(
            onDismissRequest = { showReset = false },
            title = { Text("Reset password — ${d.optString("username")}") },
            text = {
                OutlinedTextField(
                    value = newPassword,
                    onValueChange = { newPassword = it },
                    label = { Text("New password") },
                    visualTransformation = PasswordVisualTransformation(),
                    modifier = Modifier.fillMaxWidth(),
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    scope.launch {
                        when (val r = ApiClient.post(
                            "/clients/api/app/team/$employeeId/reset-password/",
                            JSONObject().put("new_password", newPassword),
                        )) {
                            is ApiClient.Result.Ok -> { message = true to "Password reset ✓"; showReset = false; newPassword = "" }
                            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                            is ApiClient.Result.Error -> message = false to r.message
                        }
                    }
                }) { Text("Reset") }
            },
            dismissButton = { TextButton(onClick = { showReset = false }) { Text("Cancel") } },
        )
    }

    if (confirmToggle) {
        AlertDialog(
            onDismissRequest = { confirmToggle = false },
            title = { Text(if (active) "Deactivate ${d.optString("username")}?" else "Reactivate ${d.optString("username")}?") },
            text = {
                Text(
                    if (active) "Their login stops working and their clients are redistributed to active employees. Sales history is kept."
                    else "Their login starts working again."
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    confirmToggle = false
                    scope.launch {
                        when (val r = ApiClient.post("/clients/api/app/team/$employeeId/toggle/", JSONObject())) {
                            is ApiClient.Result.Ok -> {
                                val n = r.json.optInt("reassigned")
                                message = true to if (r.json.optBoolean("active")) "Reactivated ✓"
                                else "Deactivated ✓" + if (n > 0) " — $n client(s) reassigned" else ""
                                data = null; reloadKey++
                            }
                            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                            is ApiClient.Result.Error -> message = false to r.message
                        }
                    }
                }) { Text(if (active) "Deactivate" else "Reactivate", color = if (active) StatusRed else StatusGreen) }
            },
            dismissButton = { TextButton(onClick = { confirmToggle = false }) { Text("Cancel") } },
        )
    }

    Column(
        modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                "← Back",
                color = MaterialTheme.colorScheme.secondary,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.clickable(onClick = onBack).padding(end = 12.dp),
            )
            Text(d.optString("username"), fontSize = 20.sp, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
            RolePill(d.optString("role"))
        }

        val s = d.optJSONObject("stats") ?: JSONObject()
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            MiniStatCard("Month", rupees(s.optDouble("month_amount", 0.0)), Modifier.weight(1f))
            MiniStatCard("Total", rupees(s.optDouble("total_amount", 0.0)), Modifier.weight(1f))
            MiniStatCard("Clients", "${s.optInt("clients")}", Modifier.weight(1f))
            MiniStatCard("Points", "%.1f".format(s.optDouble("total_points", 0.0)), Modifier.weight(1f))
        }

        SectionTitle("Profile")
        OutlinedTextField(value = firstName, onValueChange = { firstName = it }, label = { Text("First name") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
        OutlinedTextField(value = lastName, onValueChange = { lastName = it }, label = { Text("Last name") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
        OutlinedTextField(
            value = email, onValueChange = { email = it }, label = { Text("Email") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email),
            modifier = Modifier.fillMaxWidth(), singleLine = true,
        )
        Box {
            OutlinedTextField(
                value = role, onValueChange = {}, readOnly = true, enabled = false,
                label = { Text("Role") },
                modifier = Modifier.fillMaxWidth().clickable { roleMenuOpen = true },
                colors = OutlinedTextFieldDefaults.colors(
                    disabledTextColor = MaterialTheme.colorScheme.onSurface,
                    disabledBorderColor = MaterialTheme.colorScheme.outline,
                    disabledLabelColor = MaterialTheme.colorScheme.onSurfaceVariant,
                ),
            )
            DropdownMenu(expanded = roleMenuOpen, onDismissRequest = { roleMenuOpen = false }) {
                for (i in 0 until (roles?.length() ?: 0)) {
                    val r = roles!!.getJSONObject(i)
                    DropdownMenuItem(
                        text = { Text(r.optString("label")) },
                        onClick = { role = r.optString("value"); roleMenuOpen = false },
                    )
                }
            }
        }
        OutlinedTextField(
            value = salary, onValueChange = { salary = it.filter { ch -> ch.isDigit() || ch == '.' } },
            label = { Text("Salary (₹/month)") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
            modifier = Modifier.fillMaxWidth(), singleLine = true,
        )
        OutlinedTextField(value = empNumber, onValueChange = { empNumber = it }, label = { Text("Employee number") }, modifier = Modifier.fillMaxWidth(), singleLine = true)

        message?.let { (ok, text) ->
            Text(text, color = if (ok) StatusGreen else StatusRed, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
        }

        Button(
            onClick = {
                scope.launch {
                    message = null
                    val body = JSONObject()
                        .put("first_name", firstName).put("last_name", lastName)
                        .put("email", email).put("role", role)
                        .put("salary", salary).put("employee_number", empNumber)
                    when (val r = ApiClient.post("/clients/api/app/team/$employeeId/update/", body)) {
                        is ApiClient.Result.Ok -> message = true to "Saved ✓"
                        is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                        is ApiClient.Result.Error -> message = false to r.message
                    }
                }
            },
            modifier = Modifier.fillMaxWidth().height(50.dp),
        ) { Text("Save changes") }

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = { showReset = true }, modifier = Modifier.weight(1f)) { Text("Reset password") }
            OutlinedButton(onClick = { confirmToggle = true }, modifier = Modifier.weight(1f)) {
                Text(if (active) "Deactivate" else "Reactivate", color = if (active) StatusRed else StatusGreen)
            }
        }
        Spacer(Modifier.height(20.dp))
    }
}

@Composable
private fun MiniStatCard(title: String, value: String, modifier: Modifier = Modifier) {
    Card(
        modifier = modifier,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.padding(10.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Text(value, fontSize = 14.sp, fontWeight = FontWeight.Bold)
            Text(title, fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun TeamCreateForm(
    modifier: Modifier,
    roles: JSONArray?,
    onDone: () -> Unit,
    onSessionExpired: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var firstName by remember { mutableStateOf("") }
    var lastName by remember { mutableStateOf("") }
    var email by remember { mutableStateOf("") }
    var role by remember { mutableStateOf("employee") }
    var salary by remember { mutableStateOf("") }
    var roleMenuOpen by remember { mutableStateOf(false) }
    var submitting by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }

    Column(
        modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                "← Back",
                color = MaterialTheme.colorScheme.secondary,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.clickable(onClick = onDone).padding(end = 12.dp),
            )
            Text("Add Team Member", fontSize = 20.sp, fontWeight = FontWeight.Bold)
        }

        OutlinedTextField(value = username, onValueChange = { username = it }, label = { Text("Username *") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
        OutlinedTextField(
            value = password, onValueChange = { password = it }, label = { Text("Password *") },
            visualTransformation = PasswordVisualTransformation(),
            modifier = Modifier.fillMaxWidth(), singleLine = true,
        )
        OutlinedTextField(value = firstName, onValueChange = { firstName = it }, label = { Text("First name") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
        OutlinedTextField(value = lastName, onValueChange = { lastName = it }, label = { Text("Last name") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
        OutlinedTextField(
            value = email, onValueChange = { email = it }, label = { Text("Email") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email),
            modifier = Modifier.fillMaxWidth(), singleLine = true,
        )
        Box {
            OutlinedTextField(
                value = role, onValueChange = {}, readOnly = true, enabled = false,
                label = { Text("Role") },
                modifier = Modifier.fillMaxWidth().clickable { roleMenuOpen = true },
                colors = OutlinedTextFieldDefaults.colors(
                    disabledTextColor = MaterialTheme.colorScheme.onSurface,
                    disabledBorderColor = MaterialTheme.colorScheme.outline,
                    disabledLabelColor = MaterialTheme.colorScheme.onSurfaceVariant,
                ),
            )
            DropdownMenu(expanded = roleMenuOpen, onDismissRequest = { roleMenuOpen = false }) {
                for (i in 0 until (roles?.length() ?: 0)) {
                    val r = roles!!.getJSONObject(i)
                    DropdownMenuItem(
                        text = { Text(r.optString("label")) },
                        onClick = { role = r.optString("value"); roleMenuOpen = false },
                    )
                }
            }
        }
        OutlinedTextField(
            value = salary, onValueChange = { salary = it.filter { ch -> ch.isDigit() || ch == '.' } },
            label = { Text("Salary (₹/month)") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
            modifier = Modifier.fillMaxWidth(), singleLine = true,
        )

        message?.let { Text(it, color = StatusRed, fontSize = 13.sp, fontWeight = FontWeight.SemiBold) }

        Button(
            onClick = {
                submitting = true
                message = null
                scope.launch {
                    val body = JSONObject()
                        .put("username", username).put("password", password)
                        .put("first_name", firstName).put("last_name", lastName)
                        .put("email", email).put("role", role).put("salary", salary.ifEmpty { "0" })
                    when (val r = ApiClient.post("/clients/api/app/team/create/", body)) {
                        is ApiClient.Result.Ok -> onDone()
                        is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                        is ApiClient.Result.Error -> message = r.message
                    }
                    submitting = false
                }
            },
            enabled = !submitting && username.isNotBlank() && password.isNotBlank(),
            modifier = Modifier.fillMaxWidth().height(52.dp),
        ) { Text(if (submitting) "Creating…" else "Create Member", fontSize = 16.sp) }

        Spacer(Modifier.height(24.dp))
    }
}
