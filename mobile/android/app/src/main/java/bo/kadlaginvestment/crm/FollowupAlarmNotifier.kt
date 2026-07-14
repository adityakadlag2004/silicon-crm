package bo.kadlaginvestment.crm

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.os.Build
import androidx.core.app.NotificationCompat

/**
 * The "this one must not be missed" notification path: a follow-up reminder
 * that looks and sounds like an alarm clock, not a chat ping. The channel
 * plays the device's alarm ringtone on the ALARM audio stream (audible in
 * silent/vibrate mode, allowed through DND by default), the notification is
 * INSISTENT (the sound loops until acted on, capped by a timeout), and a
 * full-screen intent throws [AlarmRingActivity] over the lockscreen.
 */
object FollowupAlarmNotifier {

    const val CHANNEL_ID = "ki_followup_alarms"
    private const val CHANNEL_NAME = "Follow-up alarms"
    private const val RING_TIMEOUT_MS = 3 * 60 * 1000L  // stop ringing after 3 min
    private const val NOTIF_ID_BASE = 0x0F00000          // + followup id
    private const val DIGEST_NOTIF_ID = 0x0D16E57

    fun notifId(followupId: Int) = NOTIF_ID_BASE + followupId

    fun ensureChannel(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (nm.getNotificationChannel(CHANNEL_ID) != null) return
        val channel = NotificationChannel(CHANNEL_ID, CHANNEL_NAME, NotificationManager.IMPORTANCE_HIGH).apply {
            description = "Ringing reminders for scheduled call follow-ups"
            setSound(
                RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM),
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ALARM)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .build(),
            )
            enableVibration(true)
            vibrationPattern = longArrayOf(0, 700, 400, 700, 400, 700)
            enableLights(true)
        }
        nm.createNotificationChannel(channel)
    }

    /** Ring the full alarm for one due follow-up. */
    fun ring(context: Context, alarm: FollowupAlarm) {
        if (FollowupAlarmScheduler.alreadyRang(context, alarm.id)) return
        FollowupAlarmScheduler.markRang(context, alarm.id)
        ensureChannel(context)

        val piFlags = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        val fullScreen = PendingIntent.getActivity(
            context, alarm.id,
            alarm.putInto(Intent(context, AlarmRingActivity::class.java))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            piFlags,
        )
        val done = PendingIntent.getBroadcast(
            context, alarm.id,
            alarm.putInto(
                Intent(context, FollowupAlarmReceiver::class.java)
                    .setAction(FollowupAlarmReceiver.ACTION_DONE)
            ),
            piFlags,
        )
        val snooze = PendingIntent.getBroadcast(
            context, alarm.id,
            alarm.putInto(
                Intent(context, FollowupAlarmReceiver::class.java)
                    .setAction(FollowupAlarmReceiver.ACTION_SNOOZE)
            ),
            piFlags,
        )

        val body = alarm.note.ifEmpty { "Time to call ${alarm.name}" }
        val builder = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(context.applicationInfo.icon)
            .setContentTitle("📞 Follow-up: ${alarm.name}")
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setColor(0xFFE5B740.toInt())
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setOngoing(true)
            .setAutoCancel(false)
            .setContentIntent(fullScreen)
            .setFullScreenIntent(fullScreen, true)
            .addAction(0, "✓ Done", done)
            .addAction(0, "⏰ +15 min", snooze)
            .setTimeoutAfter(RING_TIMEOUT_MS)
            // Pre-O fallback (channels take over on 8.0+): alarm sound + vibration.
            .setSound(RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM))
            .setVibrate(longArrayOf(0, 700, 400, 700, 400, 700))

        val notification = builder.build()
        // Loop the alarm sound until the notification is cancelled or times out.
        notification.flags = notification.flags or Notification.FLAG_INSISTENT

        (context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
            .notify(notifId(alarm.id), notification)
    }

    /** Stop the ringing for one follow-up (any action was taken). */
    fun silence(context: Context, followupId: Int) {
        (context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
            .cancel(notifId(followupId))
    }

    /** Morning digest: one alarm-toned (but not looping) notification listing
     * today's follow-ups, so the day starts with the full picture. */
    fun digest(context: Context, todays: List<FollowupAlarm>) {
        if (todays.isEmpty()) return
        ensureChannel(context)

        val fmt = java.text.SimpleDateFormat("h:mm a", java.util.Locale.US)
        val lines = todays.sortedBy { it.at }.map { fu ->
            "${fmt.format(java.util.Date(fu.at))} — ${fu.name}" +
                if (fu.note.isNotEmpty()) " (${fu.note})" else ""
        }
        val style = NotificationCompat.InboxStyle()
        lines.take(6).forEach { style.addLine(it) }
        if (lines.size > 6) style.setSummaryText("+${lines.size - 6} more")

        val tap = PendingIntent.getActivity(
            context, DIGEST_NOTIF_ID,
            Intent(context, RouterActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
                .putExtra("link", "/clients/calls/followups/"),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val n = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(context.applicationInfo.icon)
            .setContentTitle("📋 ${todays.size} follow-up${if (todays.size == 1) "" else "s"} today")
            .setContentText(lines.first())
            .setStyle(style)
            .setColor(0xFFE5B740.toInt())
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setAutoCancel(true)
            .setContentIntent(tap)
            .setSound(RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM))
            .setVibrate(longArrayOf(0, 700, 400, 700))
            .build()

        (context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
            .notify(DIGEST_NOTIF_ID, n)
    }
}
