package bo.kadlaginvestment.crm;

import android.app.Activity;
import android.app.DatePickerDialog;
import android.app.TimePickerDialog;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Calendar;
import java.util.List;
import java.util.Locale;

/**
 * Post-call popup: "When should I remind you to call back?" with a grid of
 * quick timings, an optional note, and an exact date/time picker.
 *
 * The chip grid is server-driven: ShellActivity caches /api/calls/config/
 * popup_choices into SharedPreferences("call_tracking"); the admin edits the
 * set from the app Settings screen. Falls back to the classic grid when no
 * config is cached yet. Choice keys must exist in _FOLLOWUP_CHOICES /
 * FOLLOWUP_CATALOG in clients/views/calls.py.
 */
public class FollowupActivity extends Activity {

    private static final int GOLD = Color.parseColor("#E5B740");
    private static final int GOLD_BG = Color.parseColor("#FDF6E3");
    private static final int INK = Color.parseColor("#1F2937");
    private static final int MUTED = Color.parseColor("#6B7280");
    private static final int CHIPS_PER_ROW = 3;

    /** Fallback when no server config is cached (fresh install, first run). */
    private static final String[][] FALLBACK = {
            {"10m", "10 min"}, {"15m", "15 min"}, {"30m", "30 min"},
            {"1h", "1 hr"}, {"2h", "2 hr"}, {"3h", "3 hr"}, {"4h", "4 hr"},
            {"1d", "1 day"}, {"5d", "5 days"}, {"10d", "10 days"},
            {"1w", "1 week"}, {"2w", "2 weeks"}, {"1mo", "1 month"}, {"2mo", "2 months"},
    };

    private EditText noteInput;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        final String phone = getIntent().getStringExtra("phone");
        long duration = getIntent().getLongExtra("duration", 0);
        boolean connected = getIntent().getBooleanExtra("connected", false);
        boolean incoming = getIntent().getBooleanExtra("incoming", false);
        if (phone == null || phone.isEmpty()) {
            finish();
            return;
        }

        int pad = dp(20);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(pad, pad, pad, dp(10));
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(Color.WHITE);
        bg.setCornerRadius(dp(18));
        root.setBackground(bg);

        TextView title = new TextView(this);
        title.setText("Remind me to call back…");
        title.setTextSize(18);
        title.setTypeface(null, Typeface.BOLD);
        title.setTextColor(INK);
        root.addView(title);

        String status = connected
                ? "talked " + (duration / 60) + "m " + (duration % 60) + "s"
                : (incoming ? "missed call" : "not connected");
        TextView subtitle = new TextView(this);
        subtitle.setText((incoming ? "📞 From " : "📞 To ") + phone + "  ·  " + status);
        subtitle.setTextSize(13);
        subtitle.setTextColor(MUTED);
        subtitle.setPadding(0, dp(4), 0, dp(10));
        root.addView(subtitle);

        // Optional note, saved with whichever timing is tapped.
        noteInput = new EditText(this);
        noteInput.setHint("Note (optional) — e.g. discuss SIP top-up");
        noteInput.setTextSize(13);
        noteInput.setTextColor(INK);
        noteInput.setHintTextColor(MUTED);
        noteInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES);
        noteInput.setSingleLine(true);
        noteInput.setPadding(dp(12), dp(9), dp(12), dp(9));
        GradientDrawable noteBg = new GradientDrawable();
        noteBg.setColor(Color.parseColor("#F9FAFB"));
        noteBg.setCornerRadius(dp(10));
        noteBg.setStroke(dp(1), Color.parseColor("#E5E7EB"));
        noteInput.setBackground(noteBg);
        LinearLayout.LayoutParams noteLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        noteLp.bottomMargin = dp(12);
        root.addView(noteInput, noteLp);

        // Server-configured quick chips, chunked into rows.
        List<String[]> chips = loadChips();
        for (int i = 0; i < chips.size(); i += CHIPS_PER_ROW) {
            LinearLayout line = new LinearLayout(this);
            line.setOrientation(LinearLayout.HORIZONTAL);
            LinearLayout.LayoutParams lineLp = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            lineLp.bottomMargin = dp(8);
            for (int j = i; j < Math.min(i + CHIPS_PER_ROW, chips.size()); j++) {
                String[] c = chips.get(j);
                line.addView(chip(c[1], c[0], phone), chipParams());
            }
            root.addView(line, lineLp);
        }

        // Exact date & time picker.
        TextView custom = pill("📅  Pick date & time…", GOLD_BG, INK);
        custom.setOnClickListener(v -> pickCustom(phone));
        LinearLayout.LayoutParams customLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        customLp.topMargin = dp(2);
        customLp.bottomMargin = dp(8);
        root.addView(custom, customLp);

        TextView ignore = pill("✕  Ignore — no follow-up", Color.parseColor("#F3F4F6"), MUTED);
        ignore.setOnClickListener(v -> finish());
        LinearLayout.LayoutParams ignoreLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        ignoreLp.bottomMargin = dp(6);
        root.addView(ignore, ignoreLp);

        setContentView(root, new ViewGroup.LayoutParams(dp(340), ViewGroup.LayoutParams.WRAP_CONTENT));
        if (getWindow() != null) {
            getWindow().setGravity(Gravity.CENTER);
            getWindow().setBackgroundDrawableResource(android.R.color.transparent);
        }
    }

    /** Chip list from the cached server config; classic grid as fallback. */
    private List<String[]> loadChips() {
        List<String[]> out = new ArrayList<>();
        try {
            SharedPreferences prefs = getSharedPreferences("call_tracking", MODE_PRIVATE);
            JSONArray arr = new JSONArray(prefs.getString("popup_choices", "[]"));
            for (int i = 0; i < arr.length(); i++) {
                JSONObject o = arr.getJSONObject(i);
                String key = o.optString("key");
                String label = o.optString("label");
                if (!key.isEmpty() && !label.isEmpty()) out.add(new String[]{key, label});
            }
        } catch (Exception ignored) {
        }
        if (out.isEmpty()) {
            for (String[] c : FALLBACK) out.add(c);
        }
        return out;
    }

    private void pickCustom(String phone) {
        Calendar now = Calendar.getInstance();
        DatePickerDialog dateDlg = new DatePickerDialog(this, (dv, y, mo, d) -> {
            TimePickerDialog timeDlg = new TimePickerDialog(this, (tv, h, min) -> {
                String iso = String.format(Locale.US, "%04d-%02d-%02dT%02d:%02d", y, mo + 1, d, h, min);
                String label = String.format(Locale.US, "%02d/%02d %02d:%02d", d, mo + 1, h, min);
                send("{\"phone\":\"" + BackendClient.jsonEscape(phone)
                        + "\",\"custom_at\":\"" + iso + "\"" + notePart() + "}", label);
            }, now.get(Calendar.HOUR_OF_DAY), now.get(Calendar.MINUTE), false);
            timeDlg.show();
        }, now.get(Calendar.YEAR), now.get(Calendar.MONTH), now.get(Calendar.DAY_OF_MONTH));
        dateDlg.getDatePicker().setMinDate(System.currentTimeMillis() - 1000);
        dateDlg.show();
    }

    private String notePart() {
        String note = noteInput != null ? noteInput.getText().toString().trim() : "";
        if (note.isEmpty()) return "";
        if (note.length() > 255) note = note.substring(0, 255);
        return ",\"note\":\"" + BackendClient.jsonEscape(note) + "\"";
    }

    private void send(String json, String label) {
        // Test mode ("▶ Test the follow-up popup" in Office SIM settings):
        // prove the popup renders and responds — never save anything.
        if (getIntent().getBooleanExtra("test", false)) {
            Toast.makeText(this, "✓ Popup works — this was a test, nothing saved", Toast.LENGTH_LONG).show();
            finish();
            return;
        }
        new Thread(() -> {
            final String body = BackendClient.postJsonForBody("/clients/api/calls/followup/", json);
            // Arm the on-device alarm right away so the reminder rings at the
            // scheduled time even if the phone is offline or FCM is delayed.
            if (body != null) {
                try {
                    JSONObject r = new JSONObject(body);
                    // The server deleted older pending follow-ups for this same
                    // number — drop their on-device alarms so only the new one rings.
                    JSONArray superseded = r.optJSONArray("superseded_ids");
                    if (superseded != null) {
                        for (int i = 0; i < superseded.length(); i++) {
                            int oldId = superseded.optInt(i, 0);
                            if (oldId > 0) FollowupAlarmScheduler.cancel(this, oldId);
                        }
                    }
                    long at = r.optLong("scheduled_at_ms", 0);
                    int id = r.optInt("id", 0);
                    if (id > 0 && at > 0) {
                        String phone = getIntent().getStringExtra("phone");
                        String name = r.optString("client");
                        if (name.isEmpty()) name = phone;
                        FollowupAlarmScheduler.schedule(this, new FollowupAlarm(
                                id, name, r.optString("note"), phone, at));
                    }
                } catch (Exception ignored) {
                }
            }
            runOnUiThread(() -> {
                Toast.makeText(this,
                        body != null ? "✓ Reminder set — " + label
                                : "Could not save — check internet and retry from the app",
                        Toast.LENGTH_LONG).show();
                finish();
            });
        }).start();
    }

    private TextView chip(String label, String choice, String phone) {
        TextView chip = new TextView(this);
        chip.setText(label);
        chip.setTextSize(13);
        chip.setTypeface(null, Typeface.BOLD);
        chip.setTextColor(INK);
        chip.setGravity(Gravity.CENTER);
        chip.setPadding(0, dp(11), 0, dp(11));
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(GOLD_BG);
        bg.setCornerRadius(dp(10));
        bg.setStroke(dp(1), Color.parseColor("#EAD9A6"));
        chip.setBackground(bg);

        chip.setOnClickListener(v -> {
            v.setEnabled(false);
            GradientDrawable active = new GradientDrawable();
            active.setColor(GOLD);
            active.setCornerRadius(dp(10));
            chip.setBackground(active);
            chip.setTextColor(Color.WHITE);
            send("{\"phone\":\"" + BackendClient.jsonEscape(phone)
                    + "\",\"choice\":\"" + choice + "\"" + notePart() + "}", label);
        });
        return chip;
    }

    private TextView pill(String text, int bgColor, int textColor) {
        TextView t = new TextView(this);
        t.setText(text);
        t.setTextSize(14);
        t.setTypeface(null, Typeface.BOLD);
        t.setTextColor(textColor);
        t.setGravity(Gravity.CENTER);
        t.setPadding(0, dp(12), 0, dp(12));
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(bgColor);
        bg.setCornerRadius(dp(10));
        t.setBackground(bg);
        return t;
    }

    private LinearLayout.LayoutParams chipParams() {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        lp.setMarginEnd(dp(6));
        return lp;
    }

    private int dp(int v) {
        return Math.round(getResources().getDisplayMetrics().density * v);
    }
}
