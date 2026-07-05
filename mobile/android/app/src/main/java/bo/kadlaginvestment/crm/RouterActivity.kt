package bo.kadlaginvestment.crm

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import bo.kadlaginvestment.crm.net.ApiClient

/**
 * Launcher with no UI: signed-in users go straight to the native shell,
 * everyone else to the native login screen. A push-notification tap launches
 * this activity with the notification's `data` payload in the extras — we
 * forward the "link" so the shell can open the right screen.
 */
class RouterActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val target = if (ApiClient.hasSession()) ShellActivity::class.java else LoginActivity::class.java
        val next = Intent(this, target)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
        // Forward the deep-link from a push tap (FCM puts data payload in extras).
        intent?.getStringExtra("link")?.let { if (it.isNotBlank()) next.putExtra("route", it) }
        startActivity(next)
        finish()
    }
}
