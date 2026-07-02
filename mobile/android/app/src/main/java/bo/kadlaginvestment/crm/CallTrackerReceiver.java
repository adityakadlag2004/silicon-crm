package bo.kadlaginvestment.crm;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.os.Build;
import android.provider.CallLog;
import android.provider.Settings;
import android.telephony.TelephonyManager;
import android.util.Log;

import java.util.Calendar;

/**
 * Listens for phone call state changes. When a call ends, reads the call-log
 * entry for it, syncs it to the CRM, and (during office hours) shows the
 * follow-up popup — or a notification when the overlay permission is missing.
 */
public class CallTrackerReceiver extends BroadcastReceiver {

    private static final String TAG = "CallTracker";
    private static final String CHANNEL_ID = "call_followups";

    // Call-session state machine (receiver instances are recreated per broadcast,
    // so state must be static).
    private static boolean callActive = false;
    private static boolean sawRinging = false;
    private static long lastHandledCallDate = 0;

    @Override
    public void onReceive(Context context, Intent intent) {
        if (!TelephonyManager.ACTION_PHONE_STATE_CHANGED.equals(intent.getAction())) return;
        String state = intent.getStringExtra(TelephonyManager.EXTRA_STATE);
        if (state == null) return;

        if (TelephonyManager.EXTRA_STATE_RINGING.equals(state)) {
            sawRinging = true;
            callActive = true;
        } else if (TelephonyManager.EXTRA_STATE_OFFHOOK.equals(state)) {
            callActive = true;
        } else if (TelephonyManager.EXTRA_STATE_IDLE.equals(state) && callActive) {
            callActive = false;
            sawRinging = false;
            final PendingResult pending = goAsync();
            new Thread(() -> {
                try {
                    // Give the OS a moment to write the call-log row.
                    Thread.sleep(2500);
                    handleCallEnded(context.getApplicationContext());
                } catch (Exception e) {
                    Log.w(TAG, "handleCallEnded failed: " + e);
                } finally {
                    pending.finish();
                }
            }).start();
        }
    }

    private void handleCallEnded(Context ctx) {
        if (ctx.checkSelfPermission(Manifest.permission.READ_CALL_LOG) != PackageManager.PERMISSION_GRANTED) {
            return;
        }

        String number = null;
        int type = 0;
        long durationSec = 0;
        long dateMillis = 0;

        Cursor c = ctx.getContentResolver().query(
                CallLog.Calls.CONTENT_URI,
                new String[]{CallLog.Calls.NUMBER, CallLog.Calls.TYPE, CallLog.Calls.DURATION, CallLog.Calls.DATE},
                null, null, CallLog.Calls.DATE + " DESC LIMIT 1");
        if (c != null) {
            if (c.moveToFirst()) {
                number = c.getString(0);
                type = c.getInt(1);
                durationSec = c.getLong(2);
                dateMillis = c.getLong(3);
            }
            c.close();
        }
        if (number == null || number.isEmpty() || dateMillis == 0) return;
        if (dateMillis == lastHandledCallDate) return; // duplicate broadcast
        lastHandledCallDate = dateMillis;

        boolean incoming = type == CallLog.Calls.INCOMING_TYPE
                || type == CallLog.Calls.MISSED_TYPE
                || type == CallLog.Calls.REJECTED_TYPE;
        boolean connected = (type == CallLog.Calls.INCOMING_TYPE)
                || (type == CallLog.Calls.OUTGOING_TYPE && durationSec > 0);

        // 1) Sync the event to the CRM.
        String json = "{\"events\":[{"
                + "\"phone\":\"" + BackendClient.jsonEscape(number) + "\","
                + "\"direction\":\"" + (incoming ? "incoming" : "outgoing") + "\","
                + "\"connected\":" + connected + ","
                + "\"duration_seconds\":" + durationSec + ","
                + "\"started_at\":" + dateMillis
                + "}]}";
        BackendClient.postJson("/clients/api/calls/sync/", json);

        // 2) Follow-up prompt — only during office hours (config synced at login).
        SharedPreferences prefs = ctx.getSharedPreferences("call_tracking", Context.MODE_PRIVATE);
        if (!prefs.getBoolean("enabled", true)) return;
        Calendar now = Calendar.getInstance();
        int minutesNow = now.get(Calendar.HOUR_OF_DAY) * 60 + now.get(Calendar.MINUTE);
        int workStart = prefs.getInt("work_start_minutes", 600);  // 10:00
        int workEnd = prefs.getInt("work_end_minutes", 1080);     // 18:00
        if (minutesNow < workStart || minutesNow >= workEnd) return;

        Intent popup = new Intent(ctx, FollowupActivity.class);
        popup.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_EXCLUDE_FROM_RECENTS);
        popup.putExtra("phone", number);
        popup.putExtra("duration", durationSec);
        popup.putExtra("connected", connected);
        popup.putExtra("incoming", incoming);

        if (Settings.canDrawOverlays(ctx)) {
            try {
                ctx.startActivity(popup);
                return;
            } catch (Exception e) {
                Log.w(TAG, "Popup start failed, falling back to notification: " + e);
            }
        }
        showFollowupNotification(ctx, popup, number);
    }

    private void showFollowupNotification(Context ctx, Intent popup, String number) {
        if (Build.VERSION.SDK_INT >= 33
                && ctx.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            return;
        }
        NotificationManager nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
        PendingIntent pi = PendingIntent.getActivity(
                ctx, (int) (System.currentTimeMillis() & 0xFFFFFF), popup,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder builder;
        if (Build.VERSION.SDK_INT >= 26) {
            nm.createNotificationChannel(new NotificationChannel(
                    CHANNEL_ID, "Call follow-ups", NotificationManager.IMPORTANCE_HIGH));
            builder = new Notification.Builder(ctx, CHANNEL_ID);
        } else {
            builder = new Notification.Builder(ctx).setPriority(Notification.PRIORITY_HIGH);
        }
        Notification n = builder
                .setSmallIcon(R.mipmap.ic_launcher)
                .setContentTitle("Follow up on this call?")
                .setContentText(number + " — tap to schedule a follow-up")
                .setAutoCancel(true)
                .setContentIntent(pi)
                .build();
        nm.notify((int) (System.currentTimeMillis() & 0xFFFFFF), n);
    }
}
