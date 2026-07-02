package bo.kadlaginvestment.crm;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Bundle;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

/**
 * Small dialog shown after a call ends: "Follow up on this call?" with
 * quick scheduling choices. Posts the chosen follow-up to the CRM.
 */
public class FollowupActivity extends Activity {

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
        root.setPadding(pad, pad, pad, pad);
        root.setBackgroundColor(Color.WHITE);

        TextView title = new TextView(this);
        title.setText("Follow up on this call?");
        title.setTextSize(18);
        title.setTypeface(null, Typeface.BOLD);
        title.setTextColor(Color.parseColor("#1f2937"));
        root.addView(title);

        String status = connected
                ? (duration / 60) + "m " + (duration % 60) + "s"
                : (incoming ? "Missed" : "Not connected");
        TextView subtitle = new TextView(this);
        subtitle.setText(phone + "  ·  " + (incoming ? "Incoming" : "Outgoing") + "  ·  " + status);
        subtitle.setTextSize(14);
        subtitle.setTextColor(Color.parseColor("#6b7280"));
        subtitle.setPadding(0, dp(4), 0, dp(14));
        root.addView(subtitle);

        addChoice(root, "In 15 minutes", phone, "15m");
        addChoice(root, "In 1 hour", phone, "1h");
        addChoice(root, "Tomorrow 10 AM", phone, "tomorrow");
        addChoice(root, "Next week", phone, "week");

        Button no = new Button(this);
        no.setText("No follow-up");
        no.setAllCaps(false);
        no.setTextColor(Color.parseColor("#6b7280"));
        no.setBackgroundColor(Color.TRANSPARENT);
        no.setOnClickListener(v -> finish());
        root.addView(no, buttonParams());

        setContentView(root, new ViewGroup.LayoutParams(dp(320), ViewGroup.LayoutParams.WRAP_CONTENT));
        if (getWindow() != null) {
            getWindow().setGravity(Gravity.CENTER);
        }
    }

    private void addChoice(LinearLayout root, String label, String phone, String choice) {
        Button b = new Button(this);
        b.setText(label);
        b.setAllCaps(false);
        b.setTextColor(Color.parseColor("#1f2937"));
        b.setBackgroundColor(Color.parseColor("#FDF6E3"));
        b.setOnClickListener(v -> {
            v.setEnabled(false);
            new Thread(() -> {
                String json = "{\"phone\":\"" + BackendClient.jsonEscape(phone)
                        + "\",\"choice\":\"" + choice + "\"}";
                final int code = BackendClient.postJson("/clients/api/calls/followup/", json);
                runOnUiThread(() -> {
                    Toast.makeText(this,
                            code == 200 ? "Follow-up scheduled ✓" : "Could not save — open the app and retry",
                            Toast.LENGTH_SHORT).show();
                    finish();
                });
            }).start();
        });
        root.addView(b, buttonParams());
    }

    private LinearLayout.LayoutParams buttonParams() {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(8);
        return lp;
    }

    private int dp(int v) {
        return Math.round(getResources().getDisplayMetrics().density * v);
    }
}
