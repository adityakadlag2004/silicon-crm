package bo.kadlaginvestment.crm

import android.content.Context
import android.os.Build
import org.json.JSONObject
import java.io.PrintWriter
import java.io.StringWriter

/**
 * Field crashes, visible.
 *
 * This is a self-hosted APK with no Play Console and no Crashlytics, so a crash
 * on an employee's phone produced exactly one signal: "it closed". This catches
 * the uncaught exception, writes it to disk (the process is about to die, so a
 * network call here would not finish), and posts it on the next launch.
 *
 * ponytail: deliberately not Crashlytics/Sentry — one file, no dependency, no
 * third party holding client-adjacent data. Swap it in if crash volume ever
 * justifies grouping and alerting.
 */
object CrashReporter {

    private const val PREFS = "crash"
    private const val KEY = "pending"
    private const val MAX_TRACE_CHARS = 6000

    /** Install once, from the first Activity that starts. */
    fun install(context: Context) {
        val app = context.applicationContext
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        if (previous is Handler) return  // already installed
        Thread.setDefaultUncaughtExceptionHandler(Handler(app, previous))
    }

    private class Handler(
        private val app: Context,
        private val previous: Thread.UncaughtExceptionHandler?,
    ) : Thread.UncaughtExceptionHandler {
        override fun uncaughtException(t: Thread, e: Throwable) {
            try {
                val sw = StringWriter()
                e.printStackTrace(PrintWriter(sw))
                val version = try {
                    app.packageManager.getPackageInfo(app.packageName, 0).versionName ?: ""
                } catch (_: Exception) {
                    ""
                }
                val report = JSONObject()
                    .put("message", e.toString().take(500))
                    .put("stack", sw.toString().take(MAX_TRACE_CHARS))
                    .put("thread", t.name)
                    .put("app_version", version)
                    .put("android", Build.VERSION.SDK_INT)
                    .put("device", "${Build.MANUFACTURER} ${Build.MODEL}")
                    .put("at", System.currentTimeMillis())
                app.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                    .edit().putString(KEY, report.toString()).commit()  // commit: we're dying
            } catch (_: Throwable) {
                // Never let the reporter mask the real crash.
            }
            previous?.uncaughtException(t, e)
        }
    }

    /** Post and clear any stored crash. Blocking — call off the main thread. */
    fun flush(context: Context) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val stored = prefs.getString(KEY, null) ?: return
        try {
            if (BackendClient.postJson("/clients/api/app/crash/", stored) == 200) {
                prefs.edit().remove(KEY).apply()
            }
        } catch (_: Exception) {
            // Keep it for the next launch.
        }
    }
}
