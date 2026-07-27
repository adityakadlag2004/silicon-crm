package bo.kadlaginvestment.crm.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp

/**
 * Pull-to-refresh, the way every other Android app does it.
 *
 * The app had none: refresh was a `↻` glyph rendered as clickable text with
 * about 28dp of tap area, and pressing it blanked the screen to a spinner
 * while the request ran. Here the content stays put, a thin bar shows the
 * refresh in flight, and a failed refresh explains itself in a strip rather
 * than replacing what the user was reading.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RefreshableBox(
    refreshing: Boolean,
    onRefresh: () -> Unit,
    modifier: Modifier = Modifier,
    content: @Composable () -> Unit,
) {
    PullToRefreshBox(
        isRefreshing = refreshing,
        onRefresh = onRefresh,
        modifier = modifier.fillMaxSize(),
    ) { content() }
}

/** Thin top bar shown while a refresh runs over existing content. */
@Composable
fun RefreshingBar(refreshing: Boolean) {
    if (refreshing) {
        LinearProgressIndicator(Modifier.fillMaxWidth().height(2.dp))
    }
}

/** Non-destructive error strip: the content below it stays on screen. */
@Composable
fun ErrorStrip(message: String?, modifier: Modifier = Modifier) {
    if (message.isNullOrBlank()) return
    Box(
        modifier
            .fillMaxWidth()
            .background(StatusRed.copy(alpha = 0.10f))
            .padding(horizontal = 12.dp, vertical = 6.dp)
    ) {
        Text(
            "Showing saved data — $message",
            color = StatusRed,
            fontSize = rsp(11),
            textAlign = TextAlign.Center,
            modifier = Modifier.fillMaxWidth(),
        )
    }
}
