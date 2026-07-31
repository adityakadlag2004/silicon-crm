package bo.kadlaginvestment.crm.ui

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import bo.kadlaginvestment.crm.net.ApiClient
import org.json.JSONObject

/**
 * Who is signed in, cached for the session.
 *
 * Four separate screens (Menu, Tasks, Reports, Dashboard) each fetched the
 * whole `/api/app/dashboard/` — ten aggregate queries for admins — purely to
 * read one `role` string. They now read it from here, and only the real
 * dashboard hits the heavy endpoint.
 */
object Session {
    var role by mutableStateOf<String?>(null)
        private set
    var name by mutableStateOf("")
        private set

    val isAdmin: Boolean get() = role == "admin"
    val isManagerPlus: Boolean get() = role == "admin" || role == "manager"

    /** Fetch once per app launch. Returns false when the session is dead. */
    suspend fun load(force: Boolean = false): Boolean {
        if (role != null && !force) return true
        return when (val r = ApiClient.get("/clients/api/app/me/")) {
            is ApiClient.Result.Ok -> {
                apply(r.json)
                true
            }
            is ApiClient.Result.NotLoggedIn -> false
            // Degrade to the non-admin menu rather than blocking the screen.
            is ApiClient.Result.Error -> {
                if (role == null) role = "employee"
                true
            }
        }
    }

    fun apply(json: JSONObject) {
        role = json.optString("role").ifBlank { "employee" }
        name = json.optString("name")
        NotificationBadge.set(json.optInt("unread_notifications", NotificationBadge.unread))
        FollowupBadge.set(json.optInt("overdue_followups", FollowupBadge.overdue))
    }

    fun clear() {
        role = null
        name = ""
        NotificationBadge.set(0)
        FollowupBadge.set(0)
    }
}

/**
 * Follow-ups that are due right now. On the Calls tab, so a call you should
 * already have made is visible without opening the screen. Refreshed by
 * `/api/app/me/` on launch and by the Calls screen itself.
 */
object FollowupBadge {
    var overdue by mutableIntStateOf(0)
        private set

    fun set(n: Int) { overdue = n.coerceAtLeast(0) }
}

/**
 * Unread notification count. The API had always returned it; nothing in the
 * app displayed it, so people had no reason to open the Notifications screen.
 */
object NotificationBadge {
    var unread by mutableIntStateOf(0)
        private set

    fun set(n: Int) { unread = n.coerceAtLeast(0) }

    /** Re-read the count after reading/clearing notifications. */
    suspend fun refresh() {
        when (val r = ApiClient.get("/clients/api/app/me/")) {
            is ApiClient.Result.Ok -> set(r.json.optInt("unread_notifications", 0))
            else -> {}
        }
    }
}
