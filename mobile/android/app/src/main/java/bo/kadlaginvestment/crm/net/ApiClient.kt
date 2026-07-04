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
        data class Error(val message: String) : Result()
    }

    fun hasSession(): Boolean {
        val cookies = CookieManager.getInstance().getCookie(BackendClient.BASE_URL)
        return cookies?.contains("sessionid=") == true
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
                // Store every Set-Cookie the server returned (sessionid, csrftoken).
                val cm = CookieManager.getInstance()
                conn.headerFields.forEach { (name, values) ->
                    if (name != null && name.equals("Set-Cookie", ignoreCase = true)) {
                        values.forEach { cm.setCookie(BackendClient.BASE_URL, it) }
                    }
                }
                cm.flush()
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
                200 -> Result.Ok(JSONObject(conn.inputStream.bufferedReader().readText()))
                301, 302, 401, 403 -> Result.NotLoggedIn
                else -> Result.Error("Server error ($code)")
            }
        } catch (e: Exception) {
            Result.Error(e.message ?: "Network error")
        } finally {
            conn?.disconnect()
        }
    }

    suspend fun post(path: String, body: JSONObject): Result = withContext(Dispatchers.IO) {
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
                200 -> Result.Ok(JSONObject(conn.inputStream.bufferedReader().readText()))
                301, 302, 401 -> Result.NotLoggedIn
                else -> {
                    val msg = try {
                        JSONObject(conn.errorStream?.bufferedReader()?.readText() ?: "")
                            .optString("error", "Server error ($code)")
                    } catch (_: Exception) {
                        "Server error ($code)"
                    }
                    Result.Error(msg)
                }
            }
        } catch (e: Exception) {
            Result.Error(e.message ?: "Network error")
        } finally {
            conn?.disconnect()
        }
    }
}
