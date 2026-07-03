package bo.kadlaginvestment.crm;

import android.Manifest;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

/**
 * Bridge for the web app: request call permissions, store the office-hours
 * config (used by CallTrackerReceiver), and open the overlay-permission page.
 */
@CapacitorPlugin(
        name = "CallTracking",
        permissions = {
                @Permission(alias = "calls", strings = {
                        Manifest.permission.READ_PHONE_STATE,
                        Manifest.permission.READ_CALL_LOG,
                })
        }
)
public class CallTrackingPlugin extends Plugin {

    @PluginMethod
    public void notifyLoggedIn(PluginCall call) {
        // Called by base.html when an authenticated page loads in the
        // Capacitor WebView (i.e. right after login) — hand over to the
        // native shell.
        Intent intent = new Intent(getContext(), ShellActivity.class);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TASK);
        getContext().startActivity(intent);
        call.resolve();
    }

    @PluginMethod
    public void isPushAvailable(PluginCall call) {
        // Without google-services.json compiled in, FirebaseApp never
        // initializes and calling PushNotifications.register() would crash
        // the app natively. The web side gates on this instead. Reflection:
        // FirebaseApp isn't on this module's compile classpath.
        boolean available;
        try {
            Class<?> firebaseApp = Class.forName("com.google.firebase.FirebaseApp");
            Object apps = firebaseApp
                    .getMethod("getApps", Context.class)
                    .invoke(null, getContext());
            available = apps instanceof java.util.List && !((java.util.List<?>) apps).isEmpty();
        } catch (Throwable t) {
            available = false;
        }
        JSObject out = new JSObject();
        out.put("available", available);
        call.resolve(out);
    }

    @PluginMethod
    public void getStatus(PluginCall call) {
        JSObject out = new JSObject();
        out.put("callsGranted", getPermissionState("calls") == com.getcapacitor.PermissionState.GRANTED);
        out.put("overlayGranted", Settings.canDrawOverlays(getContext()));
        call.resolve(out);
    }

    @PluginMethod
    public void requestCallPermissions(PluginCall call) {
        if (getPermissionState("calls") == com.getcapacitor.PermissionState.GRANTED) {
            getStatus(call);
        } else {
            requestPermissionForAlias("calls", call, "callsPermissionCallback");
        }
    }

    @PermissionCallback
    private void callsPermissionCallback(PluginCall call) {
        getStatus(call);
    }

    @PluginMethod
    public void openOverlaySettings(PluginCall call) {
        Intent intent = new Intent(
                Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                Uri.parse("package:" + getContext().getPackageName()));
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        getContext().startActivity(intent);
        call.resolve();
    }

    /** Store work-hours config for the receiver (synced from the CRM at login). */
    @PluginMethod
    public void configure(PluginCall call) {
        SharedPreferences.Editor ed = getContext()
                .getSharedPreferences("call_tracking", Context.MODE_PRIVATE).edit();
        ed.putBoolean("enabled", call.getBoolean("enabled", true));
        ed.putInt("work_start_minutes", call.getInt("work_start_minutes", 600));
        ed.putInt("work_end_minutes", call.getInt("work_end_minutes", 1080));
        ed.apply();
        call.resolve();
    }
}
