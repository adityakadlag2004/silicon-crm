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

    // Only calls that ended within this window are processed, so a stray IDLE
    // broadcast (boot, radio events) can't pop up an old historical call.
    private static final long RECENT_CALL_WINDOW_MS = 10 * 60 * 1000;

    // How long to wait for the OS to write the call-log row before giving up.
    // goAsync() gives a receiver ~10s of wall clock, so stay well inside it.
    private static final long ROW_WAIT_MAX_MS = 8000;
    private static final long ROW_POLL_MS = 400;

    @Override
    public void onReceive(Context context, Intent intent) {
        if (!TelephonyManager.ACTION_PHONE_STATE_CHANGED.equals(intent.getAction())) return;
        String state = intent.getStringExtra(TelephonyManager.EXTRA_STATE);

        // Process on IDLE only, with NO in-memory "was there a call?" state:
        // Android routinely kills this process during a call (statics reset),
        // which used to make the IDLE event look call-less and silently eat
        // the popup. The call-log row itself + a persisted last-handled marker
        // (in handleCallEnded) provide the dedup instead.
        if (!TelephonyManager.EXTRA_STATE_IDLE.equals(state)) return;

        final PendingResult pending = goAsync();
        new Thread(() -> {
            try {
                // Wait for the OS to write the call-log row — but POLL for it
                // rather than sleeping a fixed 2.5s. On slower/OEM-heavy
                // devices the row lands later than that; the old code then read
                // the PREVIOUS call, failed its own "already handled" check and
                // returned, so that call never got a popup and never would.
                Context app = context.getApplicationContext();
                long deadline = System.currentTimeMillis() + ROW_WAIT_MAX_MS;
                long lastHandled = app.getSharedPreferences("call_tracking", Context.MODE_PRIVATE)
                        .getLong("last_handled_call_date", 0);
                while (System.currentTimeMillis() < deadline) {
                    Thread.sleep(ROW_POLL_MS);
                    if (newestCallDate(app) > lastHandled) break;
                }
                handleCallEnded(app);
            } catch (Exception e) {
                Log.w(TAG, "handleCallEnded failed: " + e);
            } finally {
                pending.finish();
            }
        }).start();
    }

    /** Timestamp of the newest call-log row, or 0 when unreadable. */
    private static long newestCallDate(Context ctx) {
        if (ctx.checkSelfPermission(Manifest.permission.READ_CALL_LOG) != PackageManager.PERMISSION_GRANTED) {
            return 0;
        }
        try (Cursor c = ctx.getContentResolver().query(
                CallLog.Calls.CONTENT_URI, new String[]{CallLog.Calls.DATE},
                null, null, CallLog.Calls.DATE + " DESC")) {
            if (c != null && c.moveToFirst()) return c.getLong(0);
        } catch (Exception e) {
            Log.w(TAG, "call log peek failed: " + e);
        }
        return 0;
    }

    private void handleCallEnded(Context ctx) {
        if (ctx.checkSelfPermission(Manifest.permission.READ_CALL_LOG) != PackageManager.PERMISSION_GRANTED) {
            Log.w(TAG, "skip: READ_CALL_LOG not granted");
            notePopup(ctx, "call log permission off");
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

        // Dedup + staleness gate, persisted so it survives process death.
        SharedPreferences track = ctx.getSharedPreferences("call_tracking", Context.MODE_PRIVATE);
        synchronized (CallTrackerReceiver.class) {
            if (dateMillis <= track.getLong("last_handled_call_date", 0)) return; // already handled / no new call
            long endedAt = dateMillis + durationSec * 1000;
            if (endedAt < System.currentTimeMillis() - RECENT_CALL_WINDOW_MS) return; // old history
            track.edit()
                    .putLong("last_handled_call_date", dateMillis)
                    .putLong("last_call_handled_at", System.currentTimeMillis())
                    .apply();
        }
        Log.i(TAG, "call ended — processing " + number);

        boolean incoming = type == CallLog.Calls.INCOMING_TYPE
                || type == CallLog.Calls.MISSED_TYPE
                || type == CallLog.Calls.REJECTED_TYPE;
        // Zero-duration means nobody talked, whichever way the call went.
        boolean connected = (type == CallLog.Calls.INCOMING_TYPE
                || type == CallLog.Calls.OUTGOING_TYPE) && durationSec > 0;

        // 1) Sync to the CRM — catch-up style: uploads this call AND any
        // backlog from earlier network gaps (CallSyncManager filters to the
        // office SIM itself). Offline? The marker doesn't advance and the next
        // trigger retries.
        CallSyncManager.syncRecentCalls(ctx);
        // End of a call is the moment the phone most reliably has signal — a
        // good time to flush follow-ups written while it didn't.
        bo.kadlaginvestment.crm.net.Outbox.drain(ctx);

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
                .setSmallIcon(R.drawable.ic_stat_ki)
                .setContentTitle("Follow up on this call?")
                .setContentText(number + " — tap to schedule a follow-up")
                .setAutoCancel(true)
                .setContentIntent(pi)
                .build();
        nm.notify((int) (System.currentTimeMillis() & 0xFFFFFF), n);
    }
}
