package bo.kadlaginvestment.crm;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

/**
 * Post-call popup: "When should I remind you to call back?" with a grid of
 * quick timings. Rows: minutes / hours / days / weeks+months. Choice keys
 * must match _FOLLOWUP_CHOICES in clients/views/calls.py.
 */
public class FollowupActivity extends Activity {

    private static final int GOLD = Color.parseColor("#E5B740");
    private static final int GOLD_BG = Color.parseColor("#FDF6E3");
    private static final int INK = Color.parseColor("#1F2937");
    private static final int MUTED = Color.parseColor("#6B7280");

    private static final String[][] ROWS = {
            {"10m|10 min", "15m|15 min", "30m|30 min"},
            {"1h|1 hr", "2h|2 hr", "3h|3 hr", "4h|4 hr"},
            {"1d|1 day", "5d|5 days", "10d|10 days"},
            {"1w|1 week", "2w|2 weeks", "1mo|1 month", "2mo|2 months"},
    };

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
        subtitle.setPadding(0, dp(4), 0, dp(14));
        root.addView(subtitle);

        for (String[] row : ROWS) {
            LinearLayout line = new LinearLayout(this);
            line.setOrientation(LinearLayout.HORIZONTAL);
            LinearLayout.LayoutParams lineLp = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            lineLp.bottomMargin = dp(8);
            for (String cell : row) {
                String[] parts = cell.split("\\|");
                line.addView(chip(parts[1], parts[0], phone), chipParams());
            }
            root.addView(line, lineLp);
        }

        TextView dismiss = new TextView(this);
        dismiss.setText("No follow-up needed");
        dismiss.setTextSize(14);
        dismiss.setTextColor(MUTED);
        dismiss.setGravity(Gravity.CENTER);
        dismiss.setPadding(0, dp(10), 0, dp(10));
        dismiss.setOnClickListener(v -> finish());
        root.addView(dismiss, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        setContentView(root, new ViewGroup.LayoutParams(dp(340), ViewGroup.LayoutParams.WRAP_CONTENT));
        if (getWindow() != null) {
            getWindow().setGravity(Gravity.CENTER);
            getWindow().setBackgroundDrawableResource(android.R.color.transparent);
        }
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
            new Thread(() -> {
                String json = "{\"phone\":\"" + BackendClient.jsonEscape(phone)
                        + "\",\"choice\":\"" + choice + "\"}";
                final int code = BackendClient.postJson("/clients/api/calls/followup/", json);
                runOnUiThread(() -> {
                    Toast.makeText(this,
                            code == 200 ? "✓ Reminder set — " + label
                                    : "Could not save — check internet and retry from the app",
                            Toast.LENGTH_LONG).show();
                    finish();
                });
            }).start();
        });
        return chip;
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
