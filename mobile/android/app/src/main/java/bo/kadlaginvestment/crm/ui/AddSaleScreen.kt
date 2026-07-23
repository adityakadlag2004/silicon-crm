package bo.kadlaginvestment.crm.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import bo.kadlaginvestment.crm.net.ApiClient
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject

/** Native Add Sale — client search, product picker, conditional insurance fields. */
@Composable
fun AddSaleScreen(
    modifier: Modifier = Modifier,
    onSessionExpired: () -> Unit,
) {
    val scope = rememberCoroutineScope()

    var meta by remember { mutableStateOf<JSONObject?>(null) }
    var metaError by remember { mutableStateOf<String?>(null) }

    var clientQuery by remember { mutableStateOf("") }
    var clientResults by remember { mutableStateOf(listOf<Pair<Int, String>>()) }
    var selectedClient by remember { mutableStateOf<Pair<Int, String>?>(null) }

    var selectedProduct by remember { mutableStateOf<JSONObject?>(null) }
    var productMenuOpen by remember { mutableStateOf(false) }
    var selectedSubproduct by remember { mutableStateOf<JSONObject?>(null) }
    var subproductMenuOpen by remember { mutableStateOf(false) }

    var amount by remember { mutableStateOf("") }
    var coverAmount by remember { mutableStateOf("") }
    var policyType by remember { mutableStateOf("") }

    var selectedEmployee by remember { mutableStateOf<Pair<Int, String>?>(null) }
    var employeeMenuOpen by remember { mutableStateOf(false) }

    var submitting by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<Pair<Boolean, String>?>(null) } // success? to text

    LaunchedEffect(Unit) {
        when (val r = ApiClient.get("/clients/api/app/sale-meta/")) {
            is ApiClient.Result.Ok -> meta = r.json
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> metaError = r.message
        }
    }

    // Debounced client search
    LaunchedEffect(clientQuery) {
        if (selectedClient != null || clientQuery.length < 2) {
            clientResults = emptyList()
            return@LaunchedEffect
        }
        delay(350)
        when (val r = ApiClient.get("/clients/search/?q=" + java.net.URLEncoder.encode(clientQuery, "UTF-8"))) {
            is ApiClient.Result.Ok -> {
                val arr = r.json.optJSONArray("results")
                clientResults = (0 until (arr?.length() ?: 0)).map {
                    val o = arr!!.getJSONObject(it)
                    o.getInt("id") to o.optString("text")
                }
            }
            is ApiClient.Result.NotLoggedIn -> onSessionExpired()
            is ApiClient.Result.Error -> {}
        }
    }

    if (metaError != null) {
        ErrorBox(metaError!!, modifier) { metaError = null; meta = null }
        return
    }
    val m = meta ?: run { LoadingBox(modifier); return }
    val isAdmin = m.optBoolean("is_admin")
    val products = m.optJSONArray("products")

    Column(
        modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Add Sale", fontSize = rsp(22), fontWeight = FontWeight.Bold)

        // ── Client picker ──
        if (selectedClient == null) {
            OutlinedTextField(
                value = clientQuery,
                onValueChange = { clientQuery = it },
                label = { Text("Search client (name / phone / email)") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
            )
            clientResults.forEach { (id, text) ->
                Card(
                    modifier = Modifier.fillMaxWidth().clickable {
                        selectedClient = id to text
                        clientResults = emptyList()
                    },
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
                ) {
                    Text(text, Modifier.padding(12.dp), fontSize = rsp(14))
                }
            }
        } else {
            Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer)) {
                Row(
                    Modifier.fillMaxWidth().padding(12.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(selectedClient!!.second, fontSize = rsp(14), fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                    Text(
                        "Change",
                        color = MaterialTheme.colorScheme.secondary,
                        fontSize = rsp(13),
                        fontWeight = FontWeight.SemiBold,
                        modifier = Modifier.clickable { selectedClient = null; clientQuery = "" },
                    )
                }
            }
        }

        // ── Product picker ──
        Box {
            OutlinedTextField(
                value = selectedProduct?.optString("name") ?: "",
                onValueChange = {},
                readOnly = true,
                label = { Text("Product") },
                modifier = Modifier.fillMaxWidth().clickable { productMenuOpen = true },
                enabled = false,
                colors = androidx.compose.material3.OutlinedTextFieldDefaults.colors(
                    disabledTextColor = MaterialTheme.colorScheme.onSurface,
                    disabledBorderColor = MaterialTheme.colorScheme.outline,
                    disabledLabelColor = MaterialTheme.colorScheme.onSurfaceVariant,
                ),
            )
            DropdownMenu(expanded = productMenuOpen, onDismissRequest = { productMenuOpen = false }) {
                for (i in 0 until (products?.length() ?: 0)) {
                    val p = products!!.getJSONObject(i)
                    DropdownMenuItem(
                        text = { Text(p.optString("name")) },
                        onClick = {
                            selectedProduct = p
                            selectedSubproduct = null  // reset dependent choice
                            productMenuOpen = false
                        },
                    )
                }
            }
        }

        // ── Sub-product picker (only when the product has sub-products) ──
        val subproducts = selectedProduct?.optJSONArray("subproducts")
        if (subproducts != null && subproducts.length() > 0) {
            Box {
                OutlinedTextField(
                    value = selectedSubproduct?.optString("name") ?: "",
                    onValueChange = {},
                    readOnly = true,
                    label = { Text("Sub-product") },
                    modifier = Modifier.fillMaxWidth().clickable { subproductMenuOpen = true },
                    enabled = false,
                    colors = androidx.compose.material3.OutlinedTextFieldDefaults.colors(
                        disabledTextColor = MaterialTheme.colorScheme.onSurface,
                        disabledBorderColor = MaterialTheme.colorScheme.outline,
                        disabledLabelColor = MaterialTheme.colorScheme.onSurfaceVariant,
                    ),
                )
                DropdownMenu(expanded = subproductMenuOpen, onDismissRequest = { subproductMenuOpen = false }) {
                    for (i in 0 until subproducts.length()) {
                        val sp = subproducts.getJSONObject(i)
                        DropdownMenuItem(
                            text = { Text(sp.optString("name")) },
                            onClick = { selectedSubproduct = sp; subproductMenuOpen = false },
                        )
                    }
                }
            }
        }

        OutlinedTextField(
            value = amount,
            onValueChange = { amount = it.filter { ch -> ch.isDigit() || ch == '.' } },
            label = { Text("Business amount (₹)") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
        )

        if (selectedProduct?.optBoolean("is_insurance") == true) {
            OutlinedTextField(
                value = coverAmount,
                onValueChange = { coverAmount = it.filter { ch -> ch.isDigit() || ch == '.' } },
                label = { Text("Cover amount (₹)") },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
            )
        }

        if (selectedProduct?.optBoolean("is_health") == true) {
            Text("Policy type", fontSize = rsp(13), color = MaterialTheme.colorScheme.onSurfaceVariant)
            Row(verticalAlignment = Alignment.CenterVertically) {
                RadioButton(selected = policyType == "fresh", onClick = { policyType = "fresh" })
                Text("Fresh", Modifier.clickable { policyType = "fresh" })
                Spacer(Modifier.height(0.dp))
                RadioButton(selected = policyType == "port", onClick = { policyType = "port" })
                Text("Port (no points)", Modifier.clickable { policyType = "port" })
            }
        }

        if (isAdmin) {
            Box {
                OutlinedTextField(
                    value = selectedEmployee?.second ?: "Myself",
                    onValueChange = {},
                    readOnly = true,
                    label = { Text("Assign to employee") },
                    modifier = Modifier.fillMaxWidth().clickable { employeeMenuOpen = true },
                    enabled = false,
                    colors = androidx.compose.material3.OutlinedTextFieldDefaults.colors(
                        disabledTextColor = MaterialTheme.colorScheme.onSurface,
                        disabledBorderColor = MaterialTheme.colorScheme.outline,
                        disabledLabelColor = MaterialTheme.colorScheme.onSurfaceVariant,
                    ),
                )
                DropdownMenu(expanded = employeeMenuOpen, onDismissRequest = { employeeMenuOpen = false }) {
                    val emps = m.optJSONArray("employees")
                    for (i in 0 until (emps?.length() ?: 0)) {
                        val e = emps!!.getJSONObject(i)
                        DropdownMenuItem(
                            text = { Text(e.optString("name")) },
                            onClick = {
                                selectedEmployee = e.getInt("id") to e.optString("name")
                                employeeMenuOpen = false
                            },
                        )
                    }
                }
            }
        }

        message?.let { (ok, text) ->
            Text(
                text,
                color = if (ok) StatusGreen else StatusRed,
                fontSize = rsp(14),
                fontWeight = FontWeight.SemiBold,
            )
        }

        Button(
            onClick = {
                message = null
                submitting = true
                scope.launch {
                    // The sub-product is the product sold when one is required.
                    val effectiveProductId = selectedSubproduct?.optInt("id")
                        ?: selectedProduct?.optInt("id") ?: 0
                    val body = JSONObject()
                        .put("client_id", selectedClient?.first ?: 0)
                        .put("product_id", effectiveProductId)
                        .put("amount", amount)
                        .put("cover_amount", coverAmount)
                        .put("policy_type", policyType)
                    if (selectedEmployee != null) body.put("employee_id", selectedEmployee!!.first)
                    when (val r = ApiClient.post("/clients/api/app/sales/create/", body)) {
                        is ApiClient.Result.Ok -> {
                            val approved = r.json.optString("status") == "approved"
                            message = true to if (approved) "Sale added and approved ✓" else "Sale added — pending approval ✓"
                            selectedClient = null; clientQuery = ""
                            selectedProduct = null; selectedSubproduct = null
                            amount = ""; coverAmount = ""; policyType = ""
                            selectedEmployee = null
                        }
                        is ApiClient.Result.NotLoggedIn -> onSessionExpired()
                        is ApiClient.Result.Error -> message = false to r.message
                    }
                    submitting = false
                }
            },
            enabled = !submitting && selectedClient != null && selectedProduct != null &&
                amount.isNotBlank() &&
                // A product with sub-products requires one to be chosen.
                ((selectedProduct?.optJSONArray("subproducts")?.length() ?: 0) == 0 || selectedSubproduct != null),
            modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
        ) {
            Text(if (submitting) "Saving…" else "Save Sale", fontSize = rsp(16))
        }

        Spacer(Modifier.height(24.dp))
    }
}
