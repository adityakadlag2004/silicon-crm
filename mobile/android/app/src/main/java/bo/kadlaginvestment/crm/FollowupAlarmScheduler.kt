package bo.kadlaginvestment.crm

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import org.json.JSONArray
import org.json.JSONObject
import java.util.Calendar

/** One scheduled follow-up reminder, as the alarm pipeline sees it. */
data class FollowupAlarm(
    val id: Int,
    val name: String,   // client name (or phone when unknown)
    val note: String,
    val phone: String,
    val at: Long,       // epoch millis
) {
    fun toJson(): JSONObject = JSONObject()
        .put("id", id).put("name", name).put("note", note)
        .put("phone", phone).put("at", at)

    fun putInto(intent: Intent): Intent = intent
        .putExtra("fu_id", id).putExtra("fu_name", name)
        .putExtra("fu_note", note).putExtra("fu_phone", phone)
        .putExtra("fu_at", at)

    companion object {
        fun fromJson(o: JSONObject) = FollowupAlarm(
            o.optInt("id"), o.optString("name"), o.optString("note"),
            o.optString("phone"), o.optLong("at"),
        )

        fun fromIntent(intent: Intent): FollowupAlarm? {
            val id = intent.getIntExtra("fu_id", 0)
            if (id == 0) return null
            return FollowupAlarm(
                id,
                intent.getStringExtra("fu_name") ?: "",
                intent.getStringExtra("fu_note") ?: "",
                intent.getStringExtra("fu_phone") ?: "",
                intent.getLongExtra("fu_at", 0L),
            )
        }
    }
}

/**
 * On-device exact alarms for call follow-ups, so reminders ring like an alarm
 * clock even when FCM is delayed, the phone is offline, or the OEM has killed
 * background push. Alarms are armed from three places — the post-call popup
 * (creation), every Follow-ups screen load (sync), and the daily digest's
 * self-heal fetch — and re-armed after reboot/app update from a prefs cache.
 *
 * A "rang" ledger dedupes the two reminder paths (local alarm vs. the server's
 * FCM followup_alarm push) so one follow-up never rings twice.
 */
object FollowupAlarmScheduler {

    private const val PREFS = "followup_alarms"
    private const val KEY_CACHE = "cache"       // JSON array of FollowupAlarm
    private const val KEY_RANG = "rang"         // JSON object {id: rangAtMillis}
    private const val DIGEST_REQUEST_CODE = 0   // follow-up alarms use their id (> 0)
    private const val DEDUPE_WINDOW_MS = 10 * 60 * 1000L

    const val ACTION_ALARM = "bo.kadlaginvestment.crm.FOLLOWUP_ALARM"
    const val ACTION_DIGEST = "bo.kadlaginvestment.crm.FOLLOWUP_DIGEST"

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** True when the OS will honor exact alarms (always below Android 12;
     * user-revocable app-op on 12–12L; auto-granted via USE_EXACT_ALARM on 13+). */
    @JvmStatic
    fun canScheduleExact(context: Context): Boolean {
        if (Build.VERSION.SDK_INT < 31) return true
        val am = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        return am.canScheduleExactAlarms()
    }

    /** Arm (or re-arm) the exact alarm for one follow-up. Safe to call again
     * for the same id — the PendingIntent is replaced, not duplicated. */
    @JvmStatic
    fun schedule(context: Context, alarm: FollowupAlarm) {
        if (alarm.at <= System.currentTimeMillis() + 5_000) return
        val am = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager

        val fire = PendingIntent.getBroadcast(
            context, alarm.id,
            alarm.putInto(Intent(context, FollowupAlarmReceiver::class.java).setAction(ACTION_ALARM)),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        // Tapping the status-bar alarm icon opens the Follow-ups tab.
        val show = PendingIntent.getActivity(
            context, alarm.id,
            Intent(context, RouterActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                .putExtra("link", "/clients/calls/followups/"),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        try {
            if (canScheduleExact(context)) {
                // setAlarmClock: fires exactly, even in Doze — the OS treats it
                // like a real alarm-clock alarm (icon in the status bar).
                am.setAlarmClock(AlarmManager.AlarmClockInfo(alarm.at, show), fire)
            } else {
                am.setWindow(AlarmManager.RTC_WAKEUP, alarm.at, 10 * 60 * 1000L, fire)
            }
            upsertCache(context, alarm)
        } catch (_: SecurityException) {
            // Exact-alarm permission revoked mid-flight; the FCM push still covers it.
        }
    }

    @JvmStatic
    fun cancel(context: Context, id: Int) {
        val am = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val pi = PendingIntent.getBroadcast(
            context, id,
            Intent(context, FollowupAlarmReceiver::class.java).setAction(ACTION_ALARM),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        am.cancel(pi)
        saveCache(context, loadCache(context).filterNot { it.id == id })
    }

    /** Reconcile device alarms with the server's pending list (the JSON array
     * from /clients/api/app/followups/): cancel alarms for follow-ups that were
     * completed elsewhere, arm every future one, refresh the reboot cache. */
    fun syncFromPending(context: Context, pending: JSONArray) {
        val fresh = mutableListOf<FollowupAlarm>()
        for (i in 0 until pending.length()) {
            val f = pending.optJSONObject(i) ?: continue
            val at = f.optLong("scheduled_at_ms", 0L)
            if (at <= 0L) continue
            val name = f.optString("client").ifEmpty { f.optString("phone") }
            fresh.add(FollowupAlarm(f.optInt("id"), name, f.optString("note"), f.optString("phone"), at))
        }
        val freshIds = fresh.map { it.id }.toSet()
        loadCache(context).filterNot { it.id in freshIds }.forEach { cancel(context, it.id) }
        fresh.forEach { schedule(context, it) }
        saveCache(context, fresh.filter { it.at > System.currentTimeMillis() })
        scheduleDigest(context)
    }

    /** Re-arm everything from the prefs cache — after reboot or app update. */
    fun rescheduleAll(context: Context) {
        loadCache(context).forEach { schedule(context, it) }
        scheduleDigest(context)
    }

    // ── Daily digest ─────────────────────────────────────────────────────────

    /** Arm the next morning-digest alarm ("you have N follow-ups today") at the
     * admin's popup-window start (cached by ShellActivity; default 9:00), on
     * the admin's popup days (default every day). Replaces any prior one. */
    fun scheduleDigest(context: Context) {
        val cfg = context.getSharedPreferences("call_tracking", Context.MODE_PRIVATE)
        // A 24×7 popup window starts at 00:00 — nobody wants the digest at
        // midnight, so anything before 6:00 falls back to 9:00.
        val startMinutes = cfg.getInt("popup_start_minutes", 540)
            .let { if (it < 360) 540 else it }
        val days = (cfg.getString("popup_days", null) ?: "0,1,2,3,4,5,6")
            .split(",").mapNotNull { it.trim().toIntOrNull() }.toSet()
            .ifEmpty { setOf(0, 1, 2, 3, 4, 5, 6) }

        val cal = Calendar.getInstance().apply {
            set(Calendar.HOUR_OF_DAY, startMinutes / 60)
            set(Calendar.MINUTE, startMinutes % 60)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }
        for (i in 0..7) {
            val mondayIndexed = (cal.get(Calendar.DAY_OF_WEEK) + 5) % 7  // Calendar: SUN=1 → 6
            if (cal.timeInMillis > System.currentTimeMillis() && mondayIndexed in days) break
            cal.add(Calendar.DAY_OF_MONTH, 1)
        }

        val am = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val fire = PendingIntent.getBroadcast(
            context, DIGEST_REQUEST_CODE,
            Intent(context, FollowupAlarmReceiver::class.java).setAction(ACTION_DIGEST),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        try {
            if (canScheduleExact(context)) {
                am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, cal.timeInMillis, fire)
            } else {
                am.setWindow(AlarmManager.RTC_WAKEUP, cal.timeInMillis, 15 * 60 * 1000L, fire)
            }
        } catch (_: SecurityException) {
        }
    }

    // ── Ring dedupe (local alarm vs. server FCM push) ────────────────────────
    // String keys so task alarms ("t42") share the ledger with follow-ups ("42").

    /** True when this follow-up already rang recently on either path. */
    fun alreadyRang(context: Context, id: Int): Boolean = alreadyRangKey(context, id.toString())

    fun markRang(context: Context, id: Int) = markRangKey(context, id.toString())

    fun alreadyRangKey(context: Context, key: String): Boolean {
        val rang = try { JSONObject(prefs(context).getString(KEY_RANG, "{}") ?: "{}") } catch (_: Exception) { JSONObject() }
        return System.currentTimeMillis() - rang.optLong(key, 0L) < DEDUPE_WINDOW_MS
    }

    fun markRangKey(context: Context, key: String) {
        val p = prefs(context)
        val rang = try { JSONObject(p.getString(KEY_RANG, "{}") ?: "{}") } catch (_: Exception) { JSONObject() }
        rang.put(key, System.currentTimeMillis())
        // Prune entries older than a day so the ledger can't grow unbounded.
        val cutoff = System.currentTimeMillis() - 24 * 60 * 60 * 1000L
        rang.keys().asSequence().toList()
            .filter { rang.optLong(it, 0L) < cutoff }
            .forEach { rang.remove(it) }
        p.edit().putString(KEY_RANG, rang.toString()).apply()
    }

    // ── Cache (source for reboot/app-update re-arming) ───────────────────────

    fun loadCache(context: Context): List<FollowupAlarm> = try {
        val arr = JSONArray(prefs(context).getString(KEY_CACHE, "[]") ?: "[]")
        (0 until arr.length()).mapNotNull { arr.optJSONObject(it)?.let(FollowupAlarm::fromJson) }
    } catch (_: Exception) {
        emptyList()
    }

    private fun saveCache(context: Context, alarms: List<FollowupAlarm>) {
        val arr = JSONArray()
        alarms.forEach { arr.put(it.toJson()) }
        prefs(context).edit().putString(KEY_CACHE, arr.toString()).apply()
    }

    private fun upsertCache(context: Context, alarm: FollowupAlarm) {
        saveCache(context, loadCache(context).filterNot { it.id == alarm.id } + alarm)
    }
}
