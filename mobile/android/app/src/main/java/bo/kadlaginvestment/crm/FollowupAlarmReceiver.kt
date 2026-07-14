package bo.kadlaginvestment.crm

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import org.json.JSONObject
import java.util.Calendar

/**
 * Target of every follow-up alarm PendingIntent:
 *  - [FollowupAlarmScheduler.ACTION_ALARM]  — an exact alarm fired: ring.
 *  - [ACTION_DONE] / [ACTION_SNOOZE]        — notification action buttons.
 *  - [FollowupAlarmScheduler.ACTION_DIGEST] — morning digest: fetch today's
 *    list, notify, re-arm alarms from fresh data (daily self-heal), schedule
 *    the next digest.
 */
class FollowupAlarmReceiver : BroadcastReceiver() {

    companion object {
        const val ACTION_DONE = "bo.kadlaginvestment.crm.FOLLOWUP_DONE"
        const val ACTION_SNOOZE = "bo.kadlaginvestment.crm.FOLLOWUP_SNOOZE"
        const val SNOOZE_MINUTES = 15
    }

    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            FollowupAlarmScheduler.ACTION_ALARM -> {
                FollowupAlarm.fromIntent(intent)?.let { FollowupAlarmNotifier.ring(context, it) }
            }

            ACTION_DONE -> {
                val alarm = FollowupAlarm.fromIntent(intent) ?: return
                FollowupAlarmNotifier.silence(context, alarm.id)
                FollowupAlarmScheduler.cancel(context, alarm.id)
                val pending = goAsync()
                Thread {
                    try {
                        BackendClient.postJson(
                            "/clients/api/app/followups/${alarm.id}/action/",
                            "{\"action\":\"done\"}",
                        )
                    } finally {
                        pending.finish()
                    }
                }.start()
            }

            ACTION_SNOOZE -> {
                val alarm = FollowupAlarm.fromIntent(intent) ?: return
                FollowupAlarmNotifier.silence(context, alarm.id)
                FollowupAlarmScheduler.schedule(
                    context,
                    alarm.copy(at = System.currentTimeMillis() + SNOOZE_MINUTES * 60_000L),
                )
            }

            FollowupAlarmScheduler.ACTION_DIGEST -> {
                val pending = goAsync()
                Thread {
                    try {
                        runDigest(context)
                    } finally {
                        pending.finish()
                    }
                }.start()
            }
        }
    }

    private fun runDigest(context: Context) {
        // Fresh data when online; the reboot cache keeps the digest (and the
        // alarms behind it) working offline.
        val body = BackendClient.getJson("/clients/api/app/followups/")
        val todays: List<FollowupAlarm>
        if (body != null) {
            try {
                val pending = JSONObject(body).optJSONArray("pending") ?: org.json.JSONArray()
                FollowupAlarmScheduler.syncFromPending(context, pending)
                val endOfDay = endOfToday()
                todays = (0 until pending.length()).mapNotNull { i ->
                    val f = pending.optJSONObject(i) ?: return@mapNotNull null
                    val at = f.optLong("scheduled_at_ms", 0L)
                    if (at <= 0L || at > endOfDay) return@mapNotNull null
                    FollowupAlarm(
                        f.optInt("id"),
                        f.optString("client").ifEmpty { f.optString("phone") },
                        f.optString("note"), f.optString("phone"), at,
                    )
                }
            } catch (_: Exception) {
                FollowupAlarmScheduler.scheduleDigest(context)
                return
            }
        } else {
            todays = FollowupAlarmScheduler.loadCache(context).filter { it.at <= endOfToday() }
            FollowupAlarmScheduler.scheduleDigest(context)
        }
        FollowupAlarmNotifier.digest(context, todays)
    }

    private fun endOfToday(): Long = Calendar.getInstance().apply {
        set(Calendar.HOUR_OF_DAY, 23)
        set(Calendar.MINUTE, 59)
        set(Calendar.SECOND, 59)
        set(Calendar.MILLISECOND, 999)
    }.timeInMillis
}
