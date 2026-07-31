package bo.kadlaginvestment.crm

import android.app.DownloadManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.util.Log
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * Self-hosted updates: checks /api/app/version/ and, when the server has a
 * newer versionCode, downloads /app/latest.apk via DownloadManager and opens
 * the package installer on completion. No Play Store involved.
 *
 * Every step reports back through `onStatus` and falls back to the browser.
 * The silent version of this stranded devices on old builds: a disabled
 * DownloadManager, a failed download, or a missing "install unknown apps"
 * grant all looked identical — a button that did nothing.
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

    /** True once the user has allowed this app to install APKs (Android 8+). */
    fun canInstall(ctx: Context): Boolean =
        Build.VERSION.SDK_INT < 26 || ctx.packageManager.canRequestPackageInstalls()

    /** Send the user to the per-app "Install unknown apps" toggle. */
    fun openInstallPermission(ctx: Context) {
        val i = Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:" + ctx.packageName))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        try {
            ctx.startActivity(i)
        } catch (e: Exception) {
            // Some ROMs hide the per-app screen; the global list is the fallback.
            try {
                ctx.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            } catch (e2: Exception) {
                Log.w(TAG, "no unknown-sources screen: $e2")
            }
        }
    }

    /**
     * Download the APK and open the installer when done.
     * [onStatus] gets a user-readable line on every failure, "" while healthy.
     */
    fun downloadAndInstall(ctx: Context, info: UpdateInfo, onStatus: (String) -> Unit = {}) {
        if (!canInstall(ctx)) {
            onStatus("Allow \"Install unknown apps\" for this app, then come back and tap Retry.")
            openInstallPermission(ctx)
            return
        }
        val dm = ctx.getSystemService(Context.DOWNLOAD_SERVICE) as? DownloadManager
        val downloadId = try {
            dm!!.enqueue(
                // No destination set on purpose: DownloadManager's own cache is
                // the only one getUriForDownloadedFile returns a content:// URI
                // for. An external dir gives a file:// URI the installer rejects.
                DownloadManager.Request(Uri.parse(info.url))
                    .setTitle("Kadlag BO ${info.versionName}")
                    .setDescription("Downloading update…")
                    .setMimeType("application/vnd.android.package-archive")
                    .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            )
        } catch (e: Exception) {
            // DownloadManager is disabled on some OEM ROMs — the browser downloads it instead.
            Log.w(TAG, "enqueue failed: $e")
            openInBrowser(ctx, info, onStatus)
            return
        }

        val receiver = object : BroadcastReceiver() {
            override fun onReceive(c: Context, intent: Intent) {
                if (intent.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1) != downloadId) return
                try {
                    c.unregisterReceiver(this)
                } catch (_: Exception) {}
                val uri = if (downloadSucceeded(dm, downloadId)) dm.getUriForDownloadedFile(downloadId) else null
                if (uri == null) {
                    openInBrowser(ctx, info, onStatus)
                    return
                }
                val install = Intent(Intent.ACTION_VIEW)
                    .setDataAndType(uri, "application/vnd.android.package-archive")
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
                try {
                    c.startActivity(install)
                } catch (e: Exception) {
                    Log.w(TAG, "installer launch failed: $e")
                    openInBrowser(ctx, info, onStatus)
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

    private fun downloadSucceeded(dm: DownloadManager, id: Long): Boolean {
        return try {
            dm.query(DownloadManager.Query().setFilterById(id)).use { c ->
                if (!c.moveToFirst()) return false
                c.getInt(c.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS)) == DownloadManager.STATUS_SUCCESSFUL
            }
        } catch (e: Exception) {
            false
        }
    }

    private fun openInBrowser(ctx: Context, info: UpdateInfo, onStatus: (String) -> Unit) {
        try {
            ctx.startActivity(
                Intent(Intent.ACTION_VIEW, Uri.parse(info.url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
            onStatus("Downloading in your browser — open the file when it finishes, then install.")
        } catch (e: Exception) {
            onStatus("Couldn't start the download. Open ${info.url} in Chrome and install it from there.")
        }
    }
}
