package bo.kadlaginvestment.crm;

import android.Manifest;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;
import android.telecom.PhoneAccountHandle;
import android.telecom.TelecomManager;
import android.telephony.SubscriptionInfo;
import android.telephony.SubscriptionManager;
import android.telephony.TelephonyManager;

import java.util.ArrayList;
import java.util.List;

/**
 * Dual-SIM office/personal split for call tracking.
 *
 * Employees carry two SIMs; only the office SIM's calls should be tracked and
 * prompt a follow-up. On first setup (2+ SIMs) the app asks which SIM is the
 * office one; the choice is stored here and used to filter call-log rows by
 * their PHONE_ACCOUNT_ID. With a single SIM there's nothing to choose — it's
 * auto-selected and just shown in Settings.
 *
 * We store both the subscription id and the SIM slot: the id matches the
 * call-log account on modern devices, and the slot is a stable fallback if the
 * subscription id shifts after a reboot / re-insert.
 */
public final class SimHelper {

    private static final String PREFS = "call_tracking";
    private static final String KEY_SUB = "office_sub_id";
    private static final String KEY_SLOT = "office_sim_slot";
    private static final String KEY_CONFIGURED = "sim_configured";

    private SimHelper() {}

    public static final class SimInfo {
        public final int subId;
        public final int slot;
        public final String label;

        SimInfo(int subId, int slot, String label) {
            this.subId = subId;
            this.slot = slot;
            this.label = label;
        }
    }

    private static SharedPreferences prefs(Context ctx) {
        return ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    /** Active SIMs, or empty if permission missing / none. Needs READ_PHONE_STATE. */
    public static List<SimInfo> getSims(Context ctx) {
        List<SimInfo> out = new ArrayList<>();
        if (ctx.checkSelfPermission(Manifest.permission.READ_PHONE_STATE) != PackageManager.PERMISSION_GRANTED) {
            return out;
        }
        try {
            SubscriptionManager sm = (SubscriptionManager)
                    ctx.getSystemService(Context.TELEPHONY_SUBSCRIPTION_SERVICE);
            if (sm == null) return out;
            List<SubscriptionInfo> subs = sm.getActiveSubscriptionInfoList();
            if (subs == null) return out;
            for (SubscriptionInfo s : subs) {
                CharSequence carrier = s.getCarrierName();
                String name = (carrier != null && carrier.length() > 0)
                        ? carrier.toString() : ("SIM " + (s.getSimSlotIndex() + 1));
                String num = "";
                try { num = s.getNumber(); } catch (Exception ignored) {}
                String label = name + " · Slot " + (s.getSimSlotIndex() + 1)
                        + (num != null && !num.isEmpty() ? " · " + num : "");
                out.add(new SimInfo(s.getSubscriptionId(), s.getSimSlotIndex(), label));
            }
        } catch (Exception ignored) {}
        return out;
    }

    public static boolean isConfigured(Context ctx) {
        return prefs(ctx).getBoolean(KEY_CONFIGURED, false);
    }

    public static int getOfficeSubId(Context ctx) {
        return prefs(ctx).getInt(KEY_SUB, -1);
    }

    public static int getOfficeSlot(Context ctx) {
        return prefs(ctx).getInt(KEY_SLOT, -1);
    }

    public static void saveOfficeSim(Context ctx, int subId, int slot) {
        prefs(ctx).edit()
                .putInt(KEY_SUB, subId)
                .putInt(KEY_SLOT, slot)
                .putBoolean(KEY_CONFIGURED, true)
                .apply();
    }

    /** True when we must ask the user (2+ SIMs and not configured yet). */
    public static boolean needsSetup(Context ctx) {
        return !isConfigured(ctx) && getSims(ctx).size() >= 2;
    }

    /** With a single SIM there's no choice — auto-select it so Settings can show it. */
    public static void ensureSingleSimConfigured(Context ctx) {
        if (isConfigured(ctx)) return;
        List<SimInfo> sims = getSims(ctx);
        if (sims.size() == 1) {
            saveOfficeSim(ctx, sims.get(0).subId, sims.get(0).slot);
        }
    }

    /** A cached filter — resolve the SIM list once, then match many call rows. */
    public static SimFilter filterFor(Context ctx) {
        return new SimFilter(ctx);
    }

    public static final class SimFilter {
        private final boolean configured;
        private final int officeSub;
        private final int officeSlot;
        private final List<SimInfo> sims;
        private final Context ctx;

        SimFilter(Context ctx) {
            this.ctx = ctx;
            this.configured = isConfigured(ctx);
            this.officeSub = getOfficeSubId(ctx);
            this.officeSlot = getOfficeSlot(ctx);
            this.sims = getSims(ctx);
        }

        /** Whether a call on `phoneAccountId` belongs to the office SIM.
         * Strict: unknown accounts on dual-SIM devices are excluded — used for
         * call SYNC, where counting personal calls would corrupt analytics. */
        public boolean matches(String phoneAccountId) {
            if (!configured) return true;           // track all until the user chooses
            int sub = resolveSub(phoneAccountId);
            if (sub == -1) return sims.size() <= 1; // unknown account → only track if single SIM
            if (sub == officeSub) return true;
            // Fallback: match by stable slot if the subscription id drifted.
            for (SimInfo s : sims) {
                if (s.subId == sub) return s.slot == officeSlot;
            }
            return false;
        }

        /** Popup gate: only skip when the call is POSITIVELY on a non-office
         * SIM. Unknown / unresolvable accounts fail OPEN — some OEMs write
         * call-log PHONE_ACCOUNT_IDs we can't map to a subscription, and a
         * silently missed follow-up prompt is worse than an occasional popup
         * for a personal call. (Analytics stay strict via {@link #matches}.) */
        public boolean matchesForPopup(String phoneAccountId) {
            if (!configured) return true;
            int sub = resolveSub(phoneAccountId);
            if (sub == -1 || sub == officeSub) return true;
            for (SimInfo s : sims) {
                if (s.subId == sub) return s.slot == officeSlot;
            }
            return true; // resolved to a SIM we can't map → benefit of the doubt
        }

        private int resolveSub(String acc) {
            if (acc == null || acc.isEmpty()) return -1;
            // Common case: PHONE_ACCOUNT_ID is the subscription id as a string.
            for (SimInfo s : sims) {
                if (String.valueOf(s.subId).equals(acc)) return s.subId;
            }
            try {
                int v = Integer.parseInt(acc.trim());
                for (SimInfo s : sims) if (s.subId == v) return v;
            } catch (NumberFormatException ignored) {}
            // Telecom account id → subscription id (API 30+).
            if (Build.VERSION.SDK_INT >= 30) {
                try {
                    TelecomManager tm = (TelecomManager) ctx.getSystemService(Context.TELECOM_SERVICE);
                    TelephonyManager tel = (TelephonyManager) ctx.getSystemService(Context.TELEPHONY_SERVICE);
                    if (tm != null && tel != null) {
                        for (PhoneAccountHandle h : tm.getCallCapablePhoneAccounts()) {
                            if (acc.equals(h.getId())) {
                                int sid = tel.getSubscriptionId(h);
                                if (sid != SubscriptionManager.INVALID_SUBSCRIPTION_ID) return sid;
                            }
                        }
                    }
                } catch (Exception ignored) {}
            }
            return -1;
        }
    }
}
