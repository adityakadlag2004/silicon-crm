package bo.kadlaginvestment.crm.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Locale

// ── Priority / status colors (mirror the web design tokens) ──
fun priorityColor(priority: String): Color = when (priority) {
    "critical" -> StatusRed
    "high" -> StatusAmber
    "medium" -> Color(0xFF2563EB)
    else -> Color(0xFF9CA3AF) // low
}

fun statusColor(status: String): Color = when (status) {
    "completed" -> StatusGreen
    "overdue" -> StatusRed
    "in_progress" -> Color(0xFF2563EB)
    "cancelled" -> Color(0xFF9CA3AF)
    else -> Color(0xFF9CA3AF) // pending
}

private val isoFmt = SimpleDateFormat("yyyy-MM-dd", Locale.US)
private val outFmt = SimpleDateFormat("dd MMM", Locale.US)

fun fmtDate(iso: String?): String {
    if (iso.isNullOrBlank()) return ""
    return try {
        outFmt.format(isoFmt.parse(iso)!!)
    } catch (_: Exception) {
        iso
    }
}

private val hm24 = SimpleDateFormat("HH:mm", Locale.US)
private val hm12 = SimpleDateFormat("hh:mm a", Locale.US)

/** "17:00" → "05:00 PM". Returns input unchanged if unparseable. */
fun fmt12h(hhmm: String?): String {
    if (hhmm.isNullOrBlank()) return ""
    return try {
        hm12.format(hm24.parse(hhmm)!!)
    } catch (_: Exception) {
        hhmm
    }
}

@Composable
fun Pill(text: String, color: Color) {
    Box(
        Modifier
            .background(color.copy(alpha = 0.15f), RoundedCornerShape(6.dp))
            .padding(horizontal = 8.dp, vertical = 2.dp)
    ) {
        Text(text, color = color, fontSize = 10.sp, fontWeight = FontWeight.Bold)
    }
}

/** Advanced filter dialog: category + priority. */
@Composable
fun TaskFilterSheet(
    meta: JSONObject,
    category: Pair<Int, String>?,
    priority: String?,
    onApply: (Pair<Int, String>?, String?) -> Unit,
    onClear: () -> Unit,
    onDismiss: () -> Unit,
) {
    var selCat by remember { mutableStateOf(category) }
    var selPri by remember { mutableStateOf(priority) }
    var catOpen by remember { mutableStateOf(false) }
    val cats = meta.optJSONArray("categories")
    val pris = meta.optJSONArray("priorities")

    androidx.compose.material3.AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Filters", fontWeight = FontWeight.Bold) },
        text = {
            Column {
                Text("Category", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Box {
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(8.dp))
                            .background(MaterialTheme.colorScheme.surfaceVariant)
                            .clickable { catOpen = true }
                            .padding(12.dp),
                    ) { Text(selCat?.second ?: "Any category") }
                    androidx.compose.material3.DropdownMenu(catOpen, onDismissRequest = { catOpen = false }) {
                        androidx.compose.material3.DropdownMenuItem(text = { Text("Any category") }, onClick = { selCat = null; catOpen = false })
                        for (i in 0 until (cats?.length() ?: 0)) {
                            val c = cats!!.getJSONObject(i)
                            androidx.compose.material3.DropdownMenuItem(
                                text = { Text(c.optString("name")) },
                                onClick = { selCat = c.getInt("id") to c.optString("name"); catOpen = false },
                            )
                        }
                    }
                }
                Spacer(Modifier.height(12.dp))
                Text("Priority", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    for (i in 0 until (pris?.length() ?: 0)) {
                        val p = pris!!.getJSONObject(i)
                        val v = p.optString("value")
                        Chip(p.optString("label"), selPri == v) { selPri = if (selPri == v) null else v }
                    }
                }
            }
        },
        confirmButton = {
            androidx.compose.material3.TextButton(onClick = { onApply(selCat, selPri) }) { Text("Apply") }
        },
        dismissButton = {
            androidx.compose.material3.TextButton(onClick = onClear) { Text("Clear") }
        },
    )
}

/** One task card for the list. A leading circle toggles completion; the rest
 * of the card opens the detail screen. */
@Composable
fun TaskCard(
    task: JSONObject,
    onOpen: () -> Unit,
    onToggleDone: () -> Unit,
) {
    val status = task.optString("status")
    val priority = task.optString("priority")
    val done = status == "completed"
    val pct = task.optInt("checklist_percent", 0)

    Card(
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
        shape = RoundedCornerShape(12.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(Modifier.padding(12.dp), verticalAlignment = Alignment.Top) {
            // Left accent bar by priority
            Box(
                Modifier
                    .width(4.dp)
                    .height(46.dp)
                    .clip(RoundedCornerShape(2.dp))
                    .background(priorityColor(priority))
            )
            Spacer(Modifier.width(10.dp))

            // Mark-done circle
            Box(
                Modifier
                    .size(26.dp)
                    .clip(CircleShape)
                    .clickable(onClick = onToggleDone),
                contentAlignment = Alignment.Center,
            ) {
                if (done) {
                    Icon(Icons.Filled.CheckCircle, contentDescription = "Completed", tint = StatusGreen)
                } else {
                    Box(
                        Modifier
                            .size(20.dp)
                            .clip(CircleShape)
                            .background(MaterialTheme.colorScheme.surfaceVariant)
                    )
                }
            }
            Spacer(Modifier.width(10.dp))

            Column(Modifier.weight(1f).clickable(onClick = onOpen)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        "#${task.optInt("id")}",
                        fontSize = 11.sp,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.width(6.dp))
                    Text(
                        task.optString("title"),
                        fontWeight = FontWeight.SemiBold,
                        fontSize = 14.sp,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                        textDecoration = if (done) TextDecoration.LineThrough else null,
                        color = if (done) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface,
                        modifier = Modifier.weight(1f),
                    )
                }
                Spacer(Modifier.height(6.dp))
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Pill(task.optString("priority_label"), priorityColor(priority))
                    Pill(task.optString("status_label"), statusColor(status))
                }
                Spacer(Modifier.height(6.dp))
                val meta = buildList {
                    if (task.optString("category").isNotBlank()) add(task.optString("category"))
                    if (task.optString("assignee").isNotBlank()) add("👤 " + task.optString("assignee"))
                    if (task.optString("due_date").isNotBlank()) {
                        val d = fmtDate(task.optString("due_date")) +
                            (task.optString("due_time").takeIf { it.isNotBlank() }?.let { " $it" } ?: "")
                        add("📅 $d")
                    }
                }.joinToString("  ·  ")
                if (meta.isNotBlank()) {
                    Text(
                        meta,
                        fontSize = 11.sp,
                        // `late` also reddens In Progress rows past their deadline.
                        color = if (status == "overdue" || task.optBoolean("late")) StatusRed
                        else MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                if (pct in 1..99) {
                    Spacer(Modifier.height(6.dp))
                    LinearProgressIndicator(
                        progress = { pct / 100f },
                        modifier = Modifier.fillMaxWidth().height(4.dp).clip(RoundedCornerShape(2.dp)),
                        color = MaterialTheme.colorScheme.primary,
                    )
                }
            }
        }
    }
}
