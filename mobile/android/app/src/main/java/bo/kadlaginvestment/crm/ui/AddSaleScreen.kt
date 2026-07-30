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
    var selectedPpt by remember { mutableStateOf<String?>(null) }
    var pptMenuOpen by remember { mutableStateOf(false) }

    var amount by remember { mutableStateOf("") }
    var coverAmount by remember { mutableStateOf("") }
    var policyType by remember { mutableStateOf("") }
    // Health/Life only: the commencement date + number off the policy document.
    // Renewal reminders are measured from policy_date, never the sale date.
    var policyDate by remember { mutableStateOf("") }
    var policyNumber by remember { mutableStateOf("") }
    var policyYears by remember { mutableStateOf(1) }         // Health multiyear term
    var yearsMenuOpen by remember { mutableStateOf(false) }
    var emiMonths by remember { mutableStateOf(0) }           // 0/5/8/11
    var emiMenuOpen by remember { mutableStateOf(false) }

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
        PickerField("Product *", selectedProduct?.optString("name") ?: "", { productMenuOpen = true }) {
            DropdownMenu(expanded = productMenuOpen, onDismissRequest = { productMenuOpen = false }) {
                for (i in 0 until (products?.length() ?: 0)) {
                    val p = products!!.getJSONObject(i)
                    DropdownMenuItem(
                        text = { Text(p.optString("name")) },
                        onClick = {
                            selectedProduct = p
                            selectedSubproduct = null  // reset dependent choices
                            selectedPpt = null
                            policyType = ""
                            policyDate = ""; policyNumber = ""
                            policyYears = 1
                            emiMonths = 0
                            productMenuOpen = false
                        },
                    )
                }
            }
        }

        // ── Sub-product picker (only when the product has sub-products) ──
        val subproducts = selectedProduct?.optJSONArray("subproducts")
        if (subproducts != null && subproducts.length() > 0) {
            PickerField("Sub-product *", selectedSubproduct?.optString("name") ?: "", { subproductMenuOpen = true }) {
                DropdownMenu(expanded = subproductMenuOpen, onDismissRequest = { subproductMenuOpen = false }) {
                    for (i in 0 until subproducts.length()) {
                        val sp = subproducts.getJSONObject(i)
                        DropdownMenuItem(
                            text = { Text(sp.optString("name")) },
                            onClick = { selectedSubproduct = sp; selectedPpt = null; subproductMenuOpen = false },
                        )
                    }
                }
            }
        }

        // ── PPT picker (PPT-priced life plans only) + admin-only live margin ──
        val effectiveProduct = selectedSubproduct ?: selectedProduct
        val pptOptions = effectiveProduct?.optJSONArray("ppt_options")
        if (pptOptions != null && pptOptions.length() > 0) {
            PickerField(
                "Premium Paying Term (PPT) *",
                selectedPpt?.let { "PPT $it" } ?: "",
                { pptMenuOpen = true },
            ) {
                DropdownMenu(expanded = pptMenuOpen, onDismissRequest = { pptMenuOpen = false }) {
                    for (i in 0 until pptOptions.length()) {
                        val v = pptOptions.optString(i)
                        DropdownMenuItem(
                            text = { Text("PPT $v") },
                            onClick = { selectedPpt = v; pptMenuOpen = false },
                        )
                    }
                }
            }
            // Admin-only: the FYC (sale margin) for the chosen plan + PPT.
            if (isAdmin && selectedPpt != null) {
                val fyc = m.optJSONObject("ppt_fyc")
                    ?.optJSONObject(effectiveProduct?.optString("name") ?: "")
                    ?.optString(selectedPpt!!) ?: ""
                if (fyc.isNotEmpty()) {
                    val desig = if (m.optBoolean("mdrt_active")) "MDRT rate" else "Advisor rate"
                    Text(
                        "Margin (FYC): $fyc%  ·  $desig",
                        color = StatusGreen,
                        fontSize = rsp(13),
                        fontWeight = FontWeight.SemiBold,
                    )
                }
            }
        }

        OutlinedTextField(
            value = amount,
            onValueChange = { amount = moneyInput(it) },
            label = { Text("Business amount (₹)") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
        )

        if (selectedProduct?.optBoolean("is_insurance") == true) {
            OutlinedTextField(
                value = coverAmount,
                onValueChange = { coverAmount = moneyInput(it) },
                label = { Text("Cover amount (₹)") },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
            )
            // Both mandatory server-side: the policy date drives every future
            // renewal reminder, the number links this sale to its tracker policy.
            DateField("Policy start date *", policyDate) { policyDate = it }
            OutlinedTextField(
                value = policyNumber,
                // Do NOT rewrite the text while it is being typed. Handing the
                // field a different string than it holds collapses the selection
                // and throws the cursor to the end, which makes a typo mid-number
                // impossible to fix. The keyboard capitalises instead, and
                // Sale.save() upper-cases and strips server-side anyway.
                onValueChange = { policyNumber = it },
                label = { Text("Policy number *") },
                supportingText = { Text("Read both off the policy document", fontSize = rsp(11)) },
                keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                    capitalization = androidx.compose.ui.text.input.KeyboardCapitalization.Characters
                ),
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

            // Multiyear term: the entered amount is the full multi-year premium.
            PickerField(
                "Policy term",
                if (policyYears <= 1) "Single year" else "$policyYears years",
                { yearsMenuOpen = true },
            ) {
                DropdownMenu(expanded = yearsMenuOpen, onDismissRequest = { yearsMenuOpen = false }) {
                    listOf(1 to "Single year", 2 to "2 years", 3 to "3 years").forEach { (v, lbl) ->
                        DropdownMenuItem(text = { Text(lbl) }, onClick = {
                            policyYears = v
                            if (v <= 1) emiMonths = 0
                            yearsMenuOpen = false
                        })
                    }
                }
            }

            // EMI only for a multiyear (>=2y) term.
            if (policyYears >= 2) {
                PickerField(
                    "Sold on EMI?",
                    if (emiMonths == 0) "Not on EMI" else "$emiMonths months",
                    { emiMenuOpen = true },
                ) {
                    DropdownMenu(expanded = emiMenuOpen, onDismissRequest = { emiMenuOpen = false }) {
                        listOf(0 to "Not on EMI", 5 to "5 months", 8 to "8 months", 11 to "11 months").forEach { (v, lbl) ->
                            DropdownMenuItem(text = { Text(lbl) }, onClick = { emiMonths = v; emiMenuOpen = false })
                        }
                    }
                }
            }
        }

        if (isAdmin) {
            PickerField("Assign to employee", selectedEmployee?.second ?: "Myself", { employeeMenuOpen = true }) {
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
                        .put("ppt", selectedPpt ?: "")
                        .put("amount", amount)
                        .put("cover_amount", coverAmount)
                        .put("policy_type", policyType)
                        .put("policy_date", policyDate)
                        .put("policy_number", policyNumber)
                        .put("policy_years", policyYears)
                        .put("emi_months", emiMonths)
                    if (selectedEmployee != null) body.put("employee_id", selectedEmployee!!.first)
                    when (val r = ApiClient.post("/clients/api/app/sales/create/", body)) {
                        is ApiClient.Result.Ok -> {
                            val approved = r.json.optString("status") == "approved"
                            message = true to if (approved) "Sale added and approved ✓" else "Sale added — pending approval ✓"
                            selectedClient = null; clientQuery = ""
                            selectedProduct = null; selectedSubproduct = null; selectedPpt = null
                            amount = ""; coverAmount = ""; policyType = ""
                            policyDate = ""; policyNumber = ""
                            policyYears = 1; emiMonths = 0
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
                ((selectedProduct?.optJSONArray("subproducts")?.length() ?: 0) == 0 || selectedSubproduct != null) &&
                // A PPT-priced plan requires a PPT.
                ((effectiveProduct?.optJSONArray("ppt_options")?.length() ?: 0) == 0 || selectedPpt != null) &&
                // Insurance: policy date + number are mandatory (server enforces too).
                (selectedProduct?.optBoolean("is_insurance") != true ||
                    (policyDate.isNotBlank() && policyNumber.isNotBlank())) &&
                // Health: Port or Fresh must be chosen.
                (selectedProduct?.optBoolean("is_health") != true || policyType.isNotBlank()),
            modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
        ) {
            Text(if (submitting) "Saving…" else "Save Sale", fontSize = rsp(16))
        }

        Spacer(Modifier.height(24.dp))
    }
}
