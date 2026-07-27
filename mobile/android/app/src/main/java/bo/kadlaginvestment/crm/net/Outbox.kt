package bo.kadlaginvestment.crm.net

import android.content.Context
import bo.kadlaginvestment.crm.BackendClient
import org.json.JSONArray
import org.json.JSONObject

/**
 * Writes that must survive a dead network.
 *
 * Staff use this app in lifts, basements and villages. Every write used to be
 * fire-and-forget: a follow-up scheduled from the post-call popup with no
 * signal was simply gone, and "Done" on a follow-up looked like it worked. The
 * outbox parks those requests on the device and replays them on the next app
 * open, call sync, or successful request.
 *
 * Only *idempotent* endpoints may queue — replaying them is harmless. Creating
 * a sale or a client deliberately does NOT queue: a replay there could
 * double-book real business, so those still fail loudly and the user retries.
 */
object Outbox {

    private const val PREFS = "outbox"
    private const val KEY = "pending"
    private const val MAX = 200

    private fun prefs(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** Park one POST. Oldest entries are dropped past [MAX] so a permanently
     * offline device can't grow the queue without bound. */
    @JvmStatic
    fun enqueue(context: Context, path: String, body: String) {
        synchronized(this) {
            val arr = load(context)
            val out = JSONArray()
            val start = (arr.length() - (MAX - 1)).coerceAtLeast(0)
            for (i in start until arr.length()) out.put(arr.get(i))
            out.put(JSONObject().put("path", path).put("body", body).put("at", System.currentTimeMillis()))
            prefs(context).edit().putString(KEY, out.toString()).apply()
        }
    }

    fun size(context: Context): Int = load(context).length()

    /**
     * Replay everything, oldest first. Stops at the first failure so ordering
     * is preserved and nothing is dropped on a flaky connection. Blocking —
     * call from a background thread.
     *
     * Returns the number of requests that got through.
     */
    @JvmStatic
    fun drain(context: Context): Int {
        val queued = synchronized(this) { load(context) }
        if (queued.length() == 0) return 0
        var sent = 0
        val remaining = JSONArray()
        var failed = false
        for (i in 0 until queued.length()) {
            val item = queued.optJSONObject(i) ?: continue
            if (failed) { remaining.put(item); continue }
            val ok = try {
                BackendClient.postJson(item.optString("path"), item.optString("body")) == 200
            } catch (_: Exception) {
                false
            }
            if (ok) sent++ else { failed = true; remaining.put(item) }
        }
        synchronized(this) {
            // Anything enqueued while we were draining stays put: re-read and
            // keep the tail we never looked at.
            val now = load(context)
            for (i in queued.length() until now.length()) remaining.put(now.get(i))
            prefs(context).edit().putString(KEY, remaining.toString()).apply()
        }
        return sent
    }

    private fun load(context: Context): JSONArray = try {
        JSONArray(prefs(context).getString(KEY, "[]") ?: "[]")
    } catch (_: Exception) {
        JSONArray()
    }
}
