package bo.kadlaginvestment.crm.net

import android.webkit.CookieManager
import bo.kadlaginvestment.crm.BackendClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * HTTP client for the native Compose screens. Reuses the WebView session
 * (CookieManager) so native and web share one login — same approach as
 * [BackendClient], but coroutine-friendly and JSON-typed.
 */
object ApiClient {

    sealed class Result {
        data class Ok(val json: JSONObject) : Result()
        object NotLoggedIn : Result()
        /** [body] is the server's JSON refusal when it sent one — callers that
         * need more than the sentence (e.g. the duplicate-sale check) read it. */
        data class Error(val message: String, val body: JSONObject? = null) : Result()
    }

    /** True when a session cookie is *present*. It may still be rejected by the
     * server (expired, or invalidated by a SECRET_KEY rotation) — callers must
     * treat [Result.NotLoggedIn] as the authority and call [clearSession]. */
    fun hasSession(): Boolean {
        val cookies = CookieManager.getInstance().getCookie(BackendClient.BASE_URL)
        return cookies?.contains("sessionid=") == true
    }

    /** Drop the session + CSRF cookies.
     *
     * Must be called on every path that sends the user back to the login
     * screen. A cookie the server has rejected still makes [hasSession] true,
     * and LoginActivity skips straight to the shell when it sees one — so
     * leaving it in place turns one rejected request into an endless
     * Shell → login → Shell bounce. */
    fun clearSession() {
        val cm = CookieManager.getInstance()
        cm.removeAllCookies(null)
        cm.flush()
    }

    /**
     * Store every Set-Cookie the server returned.
     *
     * Django runs with SESSION_SAVE_EVERY_REQUEST, so *every* response carries
     * a refreshed session cookie — that rolling renewal is what keeps web users
     * signed in indefinitely. Only [login] used to save cookies, so the app's
     * cookie kept the expiry it was born with and died exactly 30 days after
     * sign-in no matter how heavily the app was used.
     */
    private fun storeCookies(conn: HttpURLConnection) {
        val cm = CookieManager.getInstance()
        var got = false
        conn.headerFields.forEach { (name, values) ->
            if (name != null && name.equals("Set-Cookie", ignoreCase = true)) {
                values.forEach { cm.setCookie(BackendClient.BASE_URL, it); got = true }
            }
        }
        if (got) cm.flush()
    }

    /** Native login: posts credentials, then writes the returned session +
     * CSRF cookies into CookieManager so the whole app (native ApiClient and
     * any WebActivity) shares one authenticated session. */
    suspend fun login(username: String, password: String): Result = withContext(Dispatchers.IO) {
        var conn: HttpURLConnection? = null
        try {
            conn = URL(BackendClient.BASE_URL + "/clients/api/app/login/").openConnection() as HttpURLConnection
            conn.requestMethod = "POST"
            conn.connectTimeout = 12000
            conn.readTimeout = 12000
            conn.doOutput = true
            conn.instanceFollowRedirects = false
            conn.setRequestProperty("Content-Type", "application/json")
            conn.setRequestProperty("Accept", "application/json")
            conn.setRequestProperty("Origin", BackendClient.BASE_URL)
            val payload = JSONObject().put("username", username).put("password", password)
            conn.outputStream.use { it.write(payload.toString().toByteArray()) }

            val code = conn.responseCode
            if (code == 200) {
                storeCookies(conn)   // sessionid + csrftoken
                Result.Ok(JSONObject(conn.inputStream.bufferedReader().readText()))
            } else {
                val msg = try {
                    JSONObject(conn.errorStream?.bufferedReader()?.readText() ?: "")
                        .optString("error", "Login failed ($code)")
                } catch (_: Exception) {
                    "Login failed ($code)"
                }
                Result.Error(msg)
            }
        } catch (e: Exception) {
            Result.Error(e.message ?: "Network error")
        } finally {
            conn?.disconnect()
        }
    }

    suspend fun get(path: String): Result = withContext(Dispatchers.IO) {
        val cookies = CookieManager.getInstance().getCookie(BackendClient.BASE_URL)
        if (cookies == null || !cookies.contains("sessionid=")) {
            return@withContext Result.NotLoggedIn
        }
        var conn: HttpURLConnection? = null
        try {
            conn = URL(BackendClient.BASE_URL + path).openConnection() as HttpURLConnection
            conn.requestMethod = "GET"
            conn.connectTimeout = 10000
            conn.readTimeout = 10000
            conn.instanceFollowRedirects = false // a redirect to /login means session died
            conn.setRequestProperty("Cookie", cookies)
            conn.setRequestProperty("Accept", "application/json")

            when (val code = conn.responseCode) {
                200 -> {
                    storeCookies(conn)   // rolling session renewal
                    Result.Ok(JSONObject(conn.inputStream.bufferedReader().readText()))
                }
                301, 302, 401, 403 -> Result.NotLoggedIn
                else -> Result.Error("Server error ($code)")
            }
        } catch (e: Exception) {
            Result.Error(e.message ?: "Network error")
        } finally {
            conn?.disconnect()
        }
    }

    /**
     * POST JSON.
     *
     * `offlineQueue` parks the request in [Outbox] when the network is down,
     * instead of losing it. Only pass it for endpoints that are safe to replay
     * (follow-up and task actions) — never for creating a sale or a client,
     * where a replay could double-book real business.
     *
     * A queued write returns Ok with `"queued": true` so callers can say
     * "saved, will sync" rather than claiming it reached the server.
     */
    suspend fun post(
        path: String,
        body: JSONObject,
        offlineQueue: android.content.Context? = null,
    ): Result = withContext(Dispatchers.IO) {
        val cookies = CookieManager.getInstance().getCookie(BackendClient.BASE_URL)
        if (cookies == null || !cookies.contains("sessionid=")) {
            return@withContext Result.NotLoggedIn
        }
        val csrf = cookies.split(";").map { it.trim() }
            .firstOrNull { it.startsWith("csrftoken=") }?.substringAfter("=")
        var conn: HttpURLConnection? = null
        try {
            conn = URL(BackendClient.BASE_URL + path).openConnection() as HttpURLConnection
            conn.requestMethod = "POST"
            conn.connectTimeout = 10000
            conn.readTimeout = 10000
            conn.doOutput = true
            conn.instanceFollowRedirects = false
            conn.setRequestProperty("Content-Type", "application/json")
            conn.setRequestProperty("Accept", "application/json")
            conn.setRequestProperty("Cookie", cookies)
            conn.setRequestProperty("Origin", BackendClient.BASE_URL)
            conn.setRequestProperty("Referer", BackendClient.BASE_URL + "/")
            if (csrf != null) conn.setRequestProperty("X-CSRFToken", csrf)
            conn.outputStream.use { it.write(body.toString().toByteArray()) }

            when (val code = conn.responseCode) {
                200 -> {
                    storeCookies(conn)   // rolling session renewal
                    Result.Ok(JSONObject(conn.inputStream.bufferedReader().readText()))
                }
                301, 302, 401 -> Result.NotLoggedIn
                else -> {
                    // Keep the parsed body: a refusal can carry more than a
                    // sentence (the duplicate-sale check answers with the sale
                    // it matched, and the screen offers to add it anyway).
                    val payload = try {
                        JSONObject(conn.errorStream?.bufferedReader()?.readText() ?: "")
                    } catch (_: Exception) {
                        null
                    }
                    val msg = payload?.optString("error", "Server error ($code)") ?: ""
                    // A 403 with no JSON error is Django's CSRF/permission wall,
                    // not something the user can act on — treat it as a dead
                    // session (GET already did) so the app re-authenticates
                    // instead of showing "Server error (403)" forever. A 403
                    // that DID carry an error message is a real permission
                    // refusal ("Only the assignee can acknowledge") — show it.
                    if (code == 403 && msg.isEmpty()) Result.NotLoggedIn
                    else Result.Error(msg.ifEmpty { "Server error ($code)" }, payload)
                }
            }
        } catch (e: Exception) {
            // Network-level failure (no signal, DNS, timeout) — the server never
            // saw this. Park it if the caller opted in.
            if (offlineQueue != null) {
                Outbox.enqueue(offlineQueue, path, body.toString())
                Result.Ok(JSONObject().put("ok", true).put("queued", true))
            } else {
                Result.Error(e.message ?: "Network error")
            }
        } finally {
            conn?.disconnect()
        }
    }
}
