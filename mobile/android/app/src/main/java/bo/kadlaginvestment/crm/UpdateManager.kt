package bo.kadlaginvestment.crm

import android.app.DownloadManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.Uri
import android.os.Build
import android.util.Log
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * Self-hosted updates: checks /api/app/version/ and, when the server has a
 * newer versionCode, downloads /app/latest.apk via DownloadManager and opens
 * the package installer on completion. No Play Store involved.
 */
object UpdateManager {

    private const val TAG = "UpdateManager"

    data class UpdateInfo(val versionCode: Long, val versionName: String, val notes: String, val url: String)

    /** Blocking; call from a background thread. Returns null when up to date. */
    fun checkForUpdate(ctx: Context): UpdateInfo? {
        val current = try {
            val pi = ctx.packageManager.getPackageInfo(ctx.packageName, 0)
            if (Build.VERSION.SDK_INT >= 28) pi.longVersionCode else @Suppress("DEPRECATION") pi.versionCode.toLong()
        } catch (e: Exception) {
            return null
        }
        var conn: HttpURLConnection? = null
        return try {
            conn = URL(BackendClient.BASE_URL + "/clients/api/app/version/").openConnection() as HttpURLConnection
            conn.connectTimeout = 8000
            conn.readTimeout = 8000
            if (conn.responseCode != 200) return null
            val json = JSONObject(conn.inputStream.bufferedReader().readText())
            if (!json.optBoolean("available")) return null
            val remote = json.optLong("version_code", 0)
            if (remote <= current) return null
            UpdateInfo(
                versionCode = remote,
                versionName = json.optString("version_name"),
                notes = json.optString("notes"),
                url = json.optString("url"),
            )
        } catch (e: Exception) {
            Log.w(TAG, "update check failed: $e")
            null
        } finally {
            conn?.disconnect()
        }
    }

    /** Download the APK and open the installer when done. */
    fun downloadAndInstall(ctx: Context, info: UpdateInfo) {
        val dm = ctx.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
        val request = DownloadManager.Request(Uri.parse(info.url))
            .setTitle("Kadlag BO ${info.versionName}")
            .setDescription("Downloading update…")
            .setMimeType("application/vnd.android.package-archive")
            .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
        val downloadId = dm.enqueue(request)

        val receiver = object : BroadcastReceiver() {
            override fun onReceive(c: Context, intent: Intent) {
                if (intent.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1) != downloadId) return
                try {
                    c.unregisterReceiver(this)
                } catch (_: Exception) {}
                val uri = dm.getUriForDownloadedFile(downloadId) ?: return
                val install = Intent(Intent.ACTION_VIEW)
                    .setDataAndType(uri, "application/vnd.android.package-archive")
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
                try {
                    c.startActivity(install)
                } catch (e: Exception) {
                    Log.w(TAG, "installer launch failed: $e")
                }
            }
        }
        if (Build.VERSION.SDK_INT >= 33) {
            ctx.registerReceiver(
                receiver,
                IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE),
                Context.RECEIVER_EXPORTED,
            )
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            ctx.registerReceiver(receiver, IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE))
        }
    }
}
