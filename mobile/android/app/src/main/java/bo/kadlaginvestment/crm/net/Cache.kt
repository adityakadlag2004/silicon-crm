package bo.kadlaginvestment.crm.net

import android.content.Context
import org.json.JSONObject

/**
 * Last-good response per GET endpoint.
 *
 * Every screen in this app was network-only: no signal meant a spinner over an
 * empty page, even for data the phone had shown thirty seconds earlier. Field
 * staff work in lifts, basements and villages.
 *
 * Deliberately dumb — a JSON string per path in SharedPreferences, no
 * expiry, no eviction policy beyond a size cap. The screens render the cached
 * copy immediately and replace it when the live fetch lands
 * (stale-while-revalidate), so a stale read is visible for at most one request.
 *
 * ponytail: SharedPreferences, not Room. Swap it if a screen ever needs to
 * query the cache rather than just re-read one blob.
 */
object Cache {

    private const val PREFS = "api_cache"
    private const val MAX_ENTRY_BYTES = 256 * 1024

    private fun prefs(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun put(context: Context, path: String, json: JSONObject) {
        val s = json.toString()
        // A runaway list response must not bloat the prefs file.
        if (s.length > MAX_ENTRY_BYTES) return
        prefs(context).edit().putString(path, s).apply()
    }

    fun get(context: Context, path: String): JSONObject? = try {
        prefs(context).getString(path, null)?.let { JSONObject(it) }
    } catch (_: Exception) {
        null
    }

    /** Wipe on logout — the next user must not see the last one's numbers. */
    fun clear(context: Context) {
        prefs(context).edit().clear().apply()
    }
}
