package bo.kadlaginvestment.crm;

import android.util.Log;
import android.webkit.CookieManager;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/**
 * Minimal HTTP client for the native side (call receiver, follow-up popup).
 * Reuses the WebView's session cookies so requests act as the logged-in user,
 * and sends the CSRF token + Origin header so Django's CSRF checks pass.
 */
public final class BackendClient {

    public static final String BASE_URL = "https://bo.kadlaginvestment.com";
    private static final String TAG = "BackendClient";

    private BackendClient() {}

    /** POST a JSON body to BASE_URL+path with the WebView session. Returns HTTP status or -1. */
    public static int postJson(String path, String json) {
        HttpURLConnection conn = null;
        try {
            String cookies = CookieManager.getInstance().getCookie(BASE_URL);
            if (cookies == null || !cookies.contains("sessionid=")) {
                Log.w(TAG, "No session cookie; user not logged in — skipping " + path);
                return -1;
            }
            String csrf = extractCookie(cookies, "csrftoken");

            URL url = new URL(BASE_URL + path);
            conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("POST");
            conn.setConnectTimeout(8000);
            conn.setReadTimeout(8000);
            conn.setDoOutput(true);
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setRequestProperty("Cookie", cookies);
            conn.setRequestProperty("Origin", BASE_URL);
            conn.setRequestProperty("Referer", BASE_URL + "/");
            if (csrf != null) {
                conn.setRequestProperty("X-CSRFToken", csrf);
            }

            OutputStream os = conn.getOutputStream();
            os.write(json.getBytes(StandardCharsets.UTF_8));
            os.flush();
            os.close();

            int code = conn.getResponseCode();
            if (code >= 400) {
                Log.w(TAG, "POST " + path + " -> " + code);
            }
            return code;
        } catch (Exception e) {
            Log.w(TAG, "POST " + path + " failed: " + e);
            return -1;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    private static String extractCookie(String cookies, String name) {
        for (String part : cookies.split(";")) {
            String trimmed = part.trim();
            if (trimmed.startsWith(name + "=")) {
                return trimmed.substring(name.length() + 1);
            }
        }
        return null;
    }

    /** Escape a string for embedding in a JSON literal. */
    public static String jsonEscape(String s) {
        if (s == null) return "";
        return s.replace("\\", "\\\\").replace("\"", "\\\"");
    }
}
