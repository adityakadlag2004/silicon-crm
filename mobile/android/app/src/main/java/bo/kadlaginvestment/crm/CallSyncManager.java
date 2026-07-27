package bo.kadlaginvestment.crm;

import android.Manifest;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.provider.CallLog;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * Offline-safe call syncing. Keeps a "last successfully synced call" marker in
 * SharedPreferences and uploads every call-log entry after it (with a small
 * overlap — the server dedupes on employee+phone+started_at, so re-sends are
 * harmless). If the network is down the marker doesn't advance and the next
 * trigger (next call end, or app open) retries automatically.
 */
public final class CallSyncManager {

    private static final String TAG = "CallSync";
    private static final String PREFS = "call_tracking";
    private static final String KEY_LAST_SYNC = "last_synced_call_date";
    private static final long OVERLAP_MS = 15L * 60 * 1000;        // resend window
    private static final long MAX_LOOKBACK_MS = 72L * 60 * 60 * 1000; // catch-up cap
    private static final int CHUNK = 50;

    private CallSyncManager() {}

    /** Sync all unsynced calls. Blocking — call from a background thread. */
    public static synchronized void syncRecentCalls(Context ctx) {
        if (ctx.checkSelfPermission(Manifest.permission.READ_CALL_LOG)
                != PackageManager.PERMISSION_GRANTED) {
            return;
        }
        SharedPreferences prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        if (!prefs.getBoolean("enabled", true)) return;

        long last = prefs.getLong(KEY_LAST_SYNC, 0);
        long now = System.currentTimeMillis();
        if (last == 0) {
            // First run: don't upload pre-install history — start tracking now.
            prefs.edit().putLong(KEY_LAST_SYNC, now - OVERLAP_MS).apply();
            return;
        }
        long since = Math.max(last - OVERLAP_MS, now - MAX_LOOKBACK_MS);

        // Only track the office SIM's calls (dual-SIM employees).
        SimHelper.SimFilter simFilter = SimHelper.filterFor(ctx);

        List<JSONObject> events = new ArrayList<>();
        long newestDate = last;
        Cursor c = ctx.getContentResolver().query(
                CallLog.Calls.CONTENT_URI,
                new String[]{CallLog.Calls.NUMBER, CallLog.Calls.TYPE,
                        CallLog.Calls.DURATION, CallLog.Calls.DATE,
                        CallLog.Calls.PHONE_ACCOUNT_ID},
                CallLog.Calls.DATE + " > ?",
                new String[]{String.valueOf(since)},
                CallLog.Calls.DATE + " ASC");
        if (c != null) {
            try {
                while (c.moveToNext() && events.size() < 400) {
                    String number = c.getString(0);
                    int type = c.getInt(1);
                    long duration = c.getLong(2);
                    long date = c.getLong(3);
                    String account = c.getString(4);
                    if (number == null || number.isEmpty()) continue;
                    if (!simFilter.matches(account)) continue;   // skip personal SIM
                    boolean incoming;
                    boolean connected;
                    switch (type) {
                        case CallLog.Calls.INCOMING_TYPE:
                            // An incoming row with zero duration rang but was
                            // never picked up (or the network dropped it).
                            // Counting it as "connected" inflated every
                            // employee's connect rate.
                            incoming = true; connected = duration > 0; break;
                        case CallLog.Calls.MISSED_TYPE:
                        case CallLog.Calls.REJECTED_TYPE:
                            incoming = true; connected = false; break;
                        case CallLog.Calls.OUTGOING_TYPE:
                            incoming = false; connected = duration > 0; break;
                        default:
                            continue; // voicemail / blocked etc.
                    }
                    JSONObject ev = new JSONObject();
                    ev.put("phone", number);
                    ev.put("direction", incoming ? "incoming" : "outgoing");
                    ev.put("connected", connected);
                    ev.put("duration_seconds", duration);
                    ev.put("started_at", date);
                    events.add(ev);
                    if (date > newestDate) newestDate = date;
                }
            } catch (Exception e) {
                Log.w(TAG, "call log read failed: " + e);
            } finally {
                c.close();
            }
        }
        if (events.isEmpty()) return;

        // Upload in chunks; stop at the first failure so the marker only
        // advances past what the server has definitely received.
        long confirmed = last;
        for (int i = 0; i < events.size(); i += CHUNK) {
            JSONArray batch = new JSONArray();
            long batchMax = confirmed;
            for (int j = i; j < Math.min(i + CHUNK, events.size()); j++) {
                batch.put(events.get(j));
                long d = events.get(j).optLong("started_at");
                if (d > batchMax) batchMax = d;
            }
            int code = BackendClient.postJson(
                    "/clients/api/calls/sync/",
                    "{\"events\":" + batch.toString() + "}");
            if (code != 200) {
                Log.w(TAG, "sync chunk failed (" + code + ") — will retry later");
                break;
            }
            confirmed = batchMax;
        }
        if (confirmed > last) {
            prefs.edit().putLong(KEY_LAST_SYNC, confirmed).apply();
            Log.i(TAG, "synced calls up to " + confirmed);
        }
    }
}
