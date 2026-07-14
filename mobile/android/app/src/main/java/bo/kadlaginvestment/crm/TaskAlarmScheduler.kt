package bo.kadlaginvestment.crm

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import org.json.JSONArray
import org.json.JSONObject

/**
 * On-device exact alarms for TASK deadlines — a task due at 15:00 rings at
 * 15:00 even offline, mirroring the follow-up alarm design. Armed from every
 * my-tasks fetch (Shell bootstrap + Tasks module open), re-armed after
 * reboot/app update from a prefs cache. The server's minutely tasks_ring_due
 * push is the second path; [FollowupAlarmScheduler]'s "rang" ledger (key
 * "t<id>") dedupes the two.
 */
object TaskAlarmScheduler {

    private const val PREFS = "task_alarms"
    private const val KEY_CACHE = "cache"  // JSON array of {id, title, client, at}
    const val ACTION_TASK_ALARM = "bo.kadlaginvestment.crm.TASK_DUE_ALARM"

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun schedule(context: Context, id: Int, title: String, client: String, at: Long) {
        if (at <= System.currentTimeMillis() + 5_000) return
        val am = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val fire = PendingIntent.getBroadcast(
            context, id,
            Intent(context, FollowupAlarmReceiver::class.java)
                .setAction(ACTION_TASK_ALARM)
                .putExtra("task_id", id)
                .putExtra("task_title", title)
                .putExtra("task_client", client),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        try {
            if (FollowupAlarmScheduler.canScheduleExact(context)) {
                am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, fire)
            } else {
                am.setWindow(AlarmManager.RTC_WAKEUP, at, 10 * 60 * 1000L, fire)
            }
        } catch (_: SecurityException) {
            // Exact-alarm permission revoked; the server push still covers it.
        }
    }

    fun cancel(context: Context, id: Int) {
        val am = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        am.cancel(
            PendingIntent.getBroadcast(
                context, id,
                Intent(context, FollowupAlarmReceiver::class.java).setAction(ACTION_TASK_ALARM),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
        )
    }

    /** Reconcile device alarms with the server's my-tasks rows (needs
     * `due_at_ms`; rows without a due time are skipped). */
    fun syncFromRows(context: Context, rows: JSONArray) {
        val fresh = mutableListOf<JSONObject>()
        for (i in 0 until rows.length()) {
            val t = rows.optJSONObject(i) ?: continue
            val at = if (t.isNull("due_at_ms")) 0L else t.optLong("due_at_ms", 0L)
            val status = t.optString("status")
            if (at <= 0L || status == "completed" || status == "cancelled") continue
            fresh.add(
                JSONObject().put("id", t.optInt("id")).put("title", t.optString("title"))
                    .put("client", t.optString("client")).put("at", at)
            )
        }
        val freshIds = fresh.map { it.optInt("id") }.toSet()
        loadCache(context).forEach { if (it.optInt("id") !in freshIds) cancel(context, it.optInt("id")) }
        fresh.forEach {
            schedule(context, it.optInt("id"), it.optString("title"), it.optString("client"), it.optLong("at"))
        }
        val arr = JSONArray()
        fresh.filter { it.optLong("at") > System.currentTimeMillis() }.forEach { arr.put(it) }
        prefs(context).edit().putString(KEY_CACHE, arr.toString()).apply()
    }

    /** Re-arm from the cache after reboot or app update. */
    fun rescheduleAll(context: Context) {
        loadCache(context).forEach {
            schedule(context, it.optInt("id"), it.optString("title"), it.optString("client"), it.optLong("at"))
        }
    }

    /** Blocking fetch + sync — call from a background thread (Shell bootstrap,
     * Tasks module open) so task alarms stay armed even if the user never
     * scrolls a task list. */
    fun syncBlocking(context: Context) {
        try {
            BackendClient.getJson("/clients/api/app/tasks/?tab=my")?.let { body ->
                JSONObject(body).optJSONArray("tasks")?.let { syncFromRows(context, it) }
            }
        } catch (_: Exception) {
        }
    }

    private fun loadCache(context: Context): List<JSONObject> = try {
        val arr = JSONArray(prefs(context).getString(KEY_CACHE, "[]") ?: "[]")
        (0 until arr.length()).mapNotNull { arr.optJSONObject(it) }
    } catch (_: Exception) {
        emptyList()
    }
}
