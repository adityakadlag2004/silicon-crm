package bo.kadlaginvestment.crm

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import org.json.JSONObject

/**
 * Receives FCM pushes for task events (assigned, comment, status change, due,
 * overdue, reminders, …). Shows a heads-up notification whose tap deep-links to
 * the right screen via [RouterActivity] (which forwards the "link" extra to the
 * shell / TasksActivity). Also re-registers the device token when it rotates.
 *
 * Background/killed `notification` messages are drawn by the system tray and
 * routed on tap through RouterActivity's launch intent extras; this service
 * covers the foreground case and gives us one consistent channel.
 */
class KadlagMessagingService : FirebaseMessagingService() {

    companion object {
        const val CHANNEL_ID = "ki_notifications"
        const val CHANNEL_NAME = "Alerts"

        /** Create the high-importance channel. Safe to call repeatedly. */
        fun ensureChannel(context: Context) {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
            val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            if (nm.getNotificationChannel(CHANNEL_ID) == null) {
                nm.createNotificationChannel(
                    NotificationChannel(CHANNEL_ID, CHANNEL_NAME, NotificationManager.IMPORTANCE_HIGH)
                        .apply { description = "Task and business notifications" }
                )
            }
        }
    }

    override fun onNewToken(token: String) {
        // Push the fresh token to the server so notifications keep flowing.
        Thread {
            try {
                val body = JSONObject().put("token", token).put("platform", "android")
                BackendClient.postJson("/clients/api/push/register/", body.toString())
            } catch (_: Exception) {}
        }.start()
    }

    override fun onMessageReceived(message: RemoteMessage) {
        // Follow-up reminders arrive as data-only messages (so this runs even
        // when the app is backgrounded) and ring like an alarm clock instead
        // of showing a plain tray notification. The locally-scheduled alarm
        // usually fires first — FollowupAlarmNotifier dedupes by id.
        if (message.data["kind"] == "followup_alarm") {
            val id = message.data["followup_id"]?.toIntOrNull() ?: 0
            if (id > 0) {
                FollowupAlarmNotifier.ring(
                    this,
                    FollowupAlarm(
                        id,
                        (message.data["client"] ?: "").ifEmpty { message.data["phone"] ?: "" },
                        message.data["note"] ?: "",
                        message.data["phone"] ?: "",
                        System.currentTimeMillis(),
                    ),
                )
            }
            return
        }

        // Task assignments / comments / due-time alerts ring like an alarm too
        // (data-only push). Due-time pushes carry task_id — dedupe against the
        // locally-scheduled due alarm so one deadline never rings twice.
        if (message.data["kind"] == "task_alarm") {
            val taskId = message.data["task_id"]?.toIntOrNull() ?: 0
            if (taskId > 0) {
                if (FollowupAlarmScheduler.alreadyRangKey(this, "t$taskId")) return
                FollowupAlarmScheduler.markRangKey(this, "t$taskId")
            }
            FollowupAlarmNotifier.ringTask(
                this,
                message.data["title"] ?: "Task update",
                message.data["body"] ?: "",
                message.data["link"] ?: "/clients/tasks/",
            )
            return
        }

        val title = message.notification?.title ?: message.data["title"] ?: "Kadlag Investment"
        val bodyText = message.notification?.body ?: message.data["body"] ?: ""
        val link = message.data["link"].orEmpty()

        ensureChannel(this)

        val tapIntent = Intent(this, RouterActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
            if (link.isNotBlank()) putExtra("link", link)
        }
        val flags = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        val pending = PendingIntent.getActivity(this, link.hashCode(), tapIntent, flags)

        val notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(applicationInfo.icon)
            .setContentTitle(title)
            .setContentText(bodyText)
            .setStyle(NotificationCompat.BigTextStyle().bigText(bodyText))
            .setColor(0xFFE5B740.toInt())
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setContentIntent(pending)
            .build()

        val id = if (link.isNotBlank()) link.hashCode() else System.currentTimeMillis().toInt()
        (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).notify(id, notification)
    }
}
