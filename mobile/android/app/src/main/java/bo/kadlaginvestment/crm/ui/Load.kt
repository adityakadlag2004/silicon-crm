package bo.kadlaginvestment.crm.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.State
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import bo.kadlaginvestment.crm.net.ApiClient
import bo.kadlaginvestment.crm.net.Cache
import org.json.JSONObject

/**
 * What every screen was hand-rolling: fetch a JSON endpoint, hold data/error/
 * loading, expose a reload.
 *
 * Two behaviours it adds that none of them had:
 *
 *  * **cache-first** — the last good response paints immediately, then the
 *    live one replaces it. Offline, you still see yesterday's list instead of
 *    a spinner on a blank page.
 *  * **refresh without a blank screen** — the old `data = null; reloadKey++`
 *    dance threw the content away and showed a full-screen spinner on every
 *    pull. Content stays; [Loader.refreshing] drives a thin progress bar.
 */
class Loader(
    val data: JSONObject?,
    val error: String?,
    val refreshing: Boolean,
    val fromCache: Boolean,
    val reload: () -> Unit,
)

@Composable
fun rememberLoader(
    path: String,
    key: Any? = null,
    onSessionExpired: () -> Unit,
    onLoaded: (JSONObject) -> Unit = {},
): Loader {
    val context = LocalContext.current
    val expired by rememberUpdatedState(onSessionExpired)
    val loaded by rememberUpdatedState(onLoaded)

    var data by remember(path) { mutableStateOf(Cache.get(context, path)) }
    var fromCache by remember(path) { mutableStateOf(data != null) }
    var error by remember(path) { mutableStateOf<String?>(null) }
    var refreshing by remember(path) { mutableStateOf(true) }
    var tick by remember(path) { mutableStateOf(0) }

    LaunchedEffect(path, key, tick) {
        refreshing = true
        error = null
        when (val r = ApiClient.get(path)) {
            is ApiClient.Result.Ok -> {
                data = r.json
                fromCache = false
                Cache.put(context, path, r.json)
                loaded(r.json)
            }
            is ApiClient.Result.NotLoggedIn -> expired()
            // Keep showing the cached copy — an error banner beats losing the
            // screen the user was reading.
            is ApiClient.Result.Error -> error = r.message
        }
        refreshing = false
    }

    return Loader(data, error, refreshing, fromCache, reload = { tick++ })
}

/** Convenience for `var x by rememberSaveable` on a nullable Int route. */
@Composable
fun rememberIntRoute(): androidx.compose.runtime.MutableState<Int?> =
    androidx.compose.runtime.saveable.rememberSaveable {
        mutableStateOf<Int?>(null)
    }

/** State that survives rotation AND process death, for plain values. */
@Composable
fun <T : Any> rememberKept(initial: T): androidx.compose.runtime.MutableState<T> =
    androidx.compose.runtime.saveable.rememberSaveable { mutableStateOf(initial) }
