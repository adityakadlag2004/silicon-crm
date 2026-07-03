package bo.kadlaginvestment.crm

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import bo.kadlaginvestment.crm.net.ApiClient

/**
 * Launcher with no UI: signed-in users go straight to the native shell,
 * everyone else to the WebView login (Capacitor MainActivity). After a
 * successful web login, CallTrackingPlugin.notifyLoggedIn() brings the
 * user back to the shell.
 */
class RouterActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val target = if (ApiClient.hasSession()) ShellActivity::class.java else MainActivity::class.java
        startActivity(Intent(this, target).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK))
        finish()
    }
}
