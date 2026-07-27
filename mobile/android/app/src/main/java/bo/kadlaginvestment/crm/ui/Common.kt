package bo.kadlaginvestment.crm.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.text.NumberFormat
import java.util.Locale

private val inr: NumberFormat = NumberFormat.getCurrencyInstance(Locale("en", "IN")).apply {
    maximumFractionDigits = 0
}

fun rupees(v: Double): String = inr.format(v)

/**
 * One place to say something to the user.
 *
 * Before this, a failed write either replaced the whole screen with an error
 * page or — worse — said nothing at all. Any code can call [AppMessage.show];
 * [AppMessageHost] renders it as a Snackbar over whatever screen is up.
 */
object AppMessage {
    var text by androidx.compose.runtime.mutableStateOf<String?>(null)
        private set

    fun show(message: String) { text = message }
    fun clear() { text = null }

    /** "Saved on this device, will sync" vs a plain confirmation. */
    fun showResult(json: org.json.JSONObject?, okText: String) =
        show(if (json?.optBoolean("queued") == true) "No internet — saved, will sync automatically" else okText)
}

/** Drop once per Activity, inside the theme, above the content. */
@Composable
fun AppMessageHost(modifier: Modifier = Modifier) {
    val host = androidx.compose.runtime.remember { androidx.compose.material3.SnackbarHostState() }
    val msg = AppMessage.text
    androidx.compose.runtime.LaunchedEffect(msg) {
        if (msg != null) {
            host.showSnackbar(msg, withDismissAction = true)
            AppMessage.clear()
        }
    }
    androidx.compose.material3.SnackbarHost(host, modifier)
}

@Composable
fun LoadingBox(modifier: Modifier = Modifier) {
    Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        CircularProgressIndicator()
    }
}

@Composable
fun ErrorBox(message: String, modifier: Modifier = Modifier, onRetry: () -> Unit) {
    Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text("Something went wrong", fontWeight = FontWeight.SemiBold)
            Text(message, color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = rsp(13))
            androidx.compose.foundation.layout.Spacer(Modifier.height(12.dp))
            Button(onClick = onRetry) { Text("Retry") }
        }
    }
}

@Composable
fun StatusPill(status: String) {
    val (bg, label) = when (status) {
        "approved", "done" -> StatusGreen to status.replaceFirstChar { it.uppercase() }
        "rejected", "dismissed" -> StatusRed to status.replaceFirstChar { it.uppercase() }
        else -> StatusAmber to status.replaceFirstChar { it.uppercase() }
    }
    Box(
        Modifier
            .background(bg.copy(alpha = 0.14f), RoundedCornerShape(8.dp))
            .padding(horizontal = 8.dp, vertical = 2.dp)
    ) {
        Text(label, fontSize = rsp(10), color = bg, fontWeight = FontWeight.SemiBold)
    }
}

/**
 * Empty state with a reason and, where there is one, a way out.
 *
 * Most lists in the app said nothing at all, or one grey sentence. An empty
 * screen with no explanation reads as a bug.
 */
@Composable
fun EmptyState(
    icon: String,
    title: String,
    detail: String,
    modifier: Modifier = Modifier,
    actionLabel: String? = null,
    onAction: (() -> Unit)? = null,
) {
    Box(modifier.fillMaxSize().padding(24.dp), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(icon, fontSize = rsp(36))
            androidx.compose.foundation.layout.Spacer(Modifier.height(8.dp))
            Text(title, fontWeight = FontWeight.Bold, fontSize = rsp(16))
            Text(
                detail,
                fontSize = rsp(13),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = androidx.compose.ui.text.style.TextAlign.Center,
            )
            if (actionLabel != null && onAction != null) {
                androidx.compose.foundation.layout.Spacer(Modifier.height(14.dp))
                Button(onClick = onAction) { Text(actionLabel) }
            }
        }
    }
}

@Composable
fun SectionTitle(text: String) {
    Text(text, fontSize = rsp(16), fontWeight = FontWeight.Bold, modifier = Modifier.padding(vertical = 6.dp))
}

@Composable
fun Chip(text: String, selected: Boolean, onClick: () -> Unit) {
    // Selected: dark text on gold (readable) instead of low-contrast white-on-gold.
    val bg = if (selected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceVariant
    val fg = if (selected) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurface
    Box(
        Modifier
            .background(bg, RoundedCornerShape(20.dp))
            .clickable(onClick = onClick)
            .padding(horizontal = 14.dp, vertical = 7.dp)
    ) {
        Text(text, color = fg, fontSize = rsp(13), fontWeight = FontWeight.SemiBold)
    }
}
