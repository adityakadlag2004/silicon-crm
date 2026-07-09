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
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import bo.kadlaginvestment.crm.SimHelper

/** View / change which SIM is the office SIM (only its calls are tracked). */
@Composable
fun SimSettingsScreen(
    modifier: Modifier = Modifier,
    onBack: () -> Unit,
    onSessionExpired: () -> Unit,
) {
    BackHandler(onBack = onBack)
    val context = LocalContext.current
    val sims = remember { SimHelper.getSims(context) }
    var selectedSub by remember { mutableIntStateOf(SimHelper.getOfficeSubId(context)) }
    var saved by remember { mutableStateOf(false) }

    Column(modifier.fillMaxSize().padding(16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("← Back", color = MaterialTheme.colorScheme.onSurface, fontWeight = FontWeight.SemiBold,
                modifier = Modifier.clickable(onClick = onBack))
            Spacer(Modifier.padding(6.dp))
            Text("Office SIM", fontSize = 20.sp, fontWeight = FontWeight.Bold)
        }
        Spacer(Modifier.height(8.dp))
        Text(
            "Only calls made or received on the office SIM are tracked and prompt a follow-up. Your personal SIM is ignored.",
            fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(16.dp))

        when {
            sims.isEmpty() -> Text(
                "No SIMs detected, or phone permission is not granted yet. Grant the phone permission and reopen this screen.",
                color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp,
            )
            sims.size == 1 -> {
                Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                    Column(Modifier.padding(14.dp)) {
                        Text(sims[0].label, fontWeight = FontWeight.SemiBold)
                        Text("This is your only SIM — all its calls are tracked.",
                            fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
            else -> {
                sims.forEach { sim ->
                    Card(
                        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp)
                            .clickable { selectedSub = sim.subId; saved = false },
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                    ) {
                        Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                            RadioButton(selected = selectedSub == sim.subId,
                                onClick = { selectedSub = sim.subId; saved = false })
                            Text(sim.label, fontSize = 14.sp, fontWeight = FontWeight.Medium)
                        }
                    }
                }
                Spacer(Modifier.height(12.dp))
                Button(
                    onClick = {
                        val sim = sims.firstOrNull { it.subId == selectedSub }
                        if (sim != null) { SimHelper.saveOfficeSim(context, sim.subId, sim.slot); saved = true }
                    },
                    enabled = sims.any { it.subId == selectedSub },
                    modifier = Modifier.fillMaxWidth(),
                ) { Text(if (saved) "Saved ✓" else "Save office SIM") }
            }
        }
    }
}
