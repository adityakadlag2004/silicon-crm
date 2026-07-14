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
        Log.i(TAG, "call ended — processing");
        if (ctx.checkSelfPermission(Manifest.permission.READ_CALL_LOG) != PackageManager.PERMISSION_GRANTED) {
            Log.w(TAG, "skip: READ_CALL_LOG not granted");
            return;
        }

        String number = null;
        int type = 0;
        long durationSec = 0;
        long dateMillis = 0;
        String account = null;

        // NOTE: no "LIMIT 1" — Android 14+ call-log provider rejects it
        // (IllegalArgumentException: Invalid token LIMIT). Sort DESC and
        // read only the first row instead.
        Cursor c = ctx.getContentResolver().query(
                CallLog.Calls.CONTENT_URI,
                new String[]{CallLog.Calls.NUMBER, CallLog.Calls.TYPE, CallLog.Calls.DURATION,
                        CallLog.Calls.DATE, CallLog.Calls.PHONE_ACCOUNT_ID},
                null, null, CallLog.Calls.DATE + " DESC");
        if (c != null) {
            if (c.moveToFirst()) {
                number = c.getString(0);
                type = c.getInt(1);
                durationSec = c.getLong(2);
                dateMillis = c.getLong(3);
                account = c.getString(4);
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

        // 1) Sync to the CRM — catch-up style: uploads this call AND any
        // backlog from earlier network gaps (CallSyncManager filters to the
        // office SIM itself). Offline? The marker doesn't advance and the next
        // trigger retries.
        CallSyncManager.syncRecentCalls(ctx);

        // Only prompt a follow-up for calls positively on another SIM
        // (fail-open: unresolvable accounts still get the popup).
        if (!SimHelper.filterFor(ctx).matchesForPopup(account)) {
            Log.i(TAG, "popup skip: call was on a non-office SIM");
            notePopup(ctx, "non-office SIM");
            return;
        }

        // 2) Follow-up prompt — governed by its OWN window (independent of the
        // tracking window, so it can run every day incl. Sunday).
        SharedPreferences prefs = ctx.getSharedPreferences("call_tracking", Context.MODE_PRIVATE);
        if (!prefs.getBoolean("popup_enabled", true)) {
            Log.i(TAG, "popup skip: popup disabled by admin config");
            notePopup(ctx, "disabled in App Settings");
            return;
        }
        Calendar now = Calendar.getInstance();
        int minutesNow = now.get(Calendar.HOUR_OF_DAY) * 60 + now.get(Calendar.MINUTE);
        int popupStart = prefs.getInt("popup_start_minutes", 540);   // 09:00
        int popupEnd = prefs.getInt("popup_end_minutes", 1260);      // 21:00
        if (minutesNow < popupStart || minutesNow >= popupEnd) {
            Log.i(TAG, "popup skip: outside popup hours (" + minutesNow + " not in "
                    + popupStart + "-" + popupEnd + ")");
            notePopup(ctx, "outside popup hours");
            return;
        }
        // Weekday check. Calendar: SUNDAY=1..SATURDAY=7 → our 0=Mon..6=Sun.
        int cd = now.get(Calendar.DAY_OF_WEEK);
        int myDay = (cd == Calendar.SUNDAY) ? 6 : cd - 2;
        String popupDays = prefs.getString("popup_days", "0,1,2,3,4,5,6");
        if (!dayEnabled(popupDays, myDay)) {
            Log.i(TAG, "popup skip: not a popup day (" + myDay + " not in " + popupDays + ")");
            notePopup(ctx, "not a popup day");
            return;
        }

        Intent popup = new Intent(ctx, FollowupActivity.class);
        popup.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_EXCLUDE_FROM_RECENTS);
        popup.putExtra("phone", number);
        popup.putExtra("duration", durationSec);
        popup.putExtra("connected", connected);
        popup.putExtra("incoming", incoming);

        if (Settings.canDrawOverlays(ctx)) {
            try {
                ctx.startActivity(popup);
                Log.i(TAG, "popup shown for " + number);
                notePopup(ctx, "shown");
                return;
            } catch (Exception e) {
                Log.w(TAG, "popup start failed, falling back to notification: " + e);
                notePopup(ctx, "launch blocked - notification shown");
            }
        } else {
            Log.i(TAG, "popup skip: overlay permission missing — using notification");
            notePopup(ctx, "overlay permission off - notification shown");
        }
        showFollowupNotification(ctx, popup, number);
    }

    /** Record why the last popup did / didn't appear; the app reports it in
     * device-status so the admin's Call Analytics roster shows it per device. */
    private static void notePopup(Context ctx, String result) {
        ctx.getSharedPreferences("call_tracking", Context.MODE_PRIVATE).edit()
                .putString("last_popup_result", result)
                .putLong("last_popup_at", System.currentTimeMillis())
                .apply();
    }

    /** True if `day` (0=Mon..6=Sun) appears in a comma-separated day string. */
    private static boolean dayEnabled(String csv, int day) {
        if (csv == null || csv.isEmpty()) return true;
        for (String p : csv.split(",")) {
            try {
                if (Integer.parseInt(p.trim()) == day) return true;
            } catch (NumberFormatException ignored) {}
        }
        return false;
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
