package bo.kadlaginvestment.crm

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * AlarmManager alarms don't survive a reboot (or an app update), so re-arm
 * every cached follow-up alarm plus the daily digest when either happens.
 */
class FollowupBootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            Intent.ACTION_BOOT_COMPLETED, Intent.ACTION_MY_PACKAGE_REPLACED -> {
                FollowupAlarmScheduler.rescheduleAll(context)
                TaskAlarmScheduler.rescheduleAll(context)
            }
        }
    }
}
