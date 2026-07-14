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
        String body = postJsonForBody(path, json);
        return body != null ? 200 : -1;
    }

    /** Like {@link #postJson} but returns the 200 response body, or null on any failure. */
    public static String postJsonForBody(String path, String json) {
        HttpURLConnection conn = null;
        try {
            String cookies = CookieManager.getInstance().getCookie(BASE_URL);
            if (cookies == null || !cookies.contains("sessionid=")) {
                Log.w(TAG, "No session cookie; user not logged in — skipping " + path);
                return null;
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
            if (code != 200) {
                Log.w(TAG, "POST " + path + " -> " + code);
                return null;
            }
            return readBody(conn);
        } catch (Exception e) {
            Log.w(TAG, "POST " + path + " failed: " + e);
            return null;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    /** GET BASE_URL+path with the WebView session. Returns the 200 response
     * body, or null when logged out / non-200 / offline. */
    public static String getJson(String path) {
        HttpURLConnection conn = null;
        try {
            String cookies = CookieManager.getInstance().getCookie(BASE_URL);
            if (cookies == null || !cookies.contains("sessionid=")) {
                return null;
            }
            URL url = new URL(BASE_URL + path);
            conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(8000);
            conn.setReadTimeout(8000);
            conn.setRequestProperty("Accept", "application/json");
            conn.setRequestProperty("Cookie", cookies);
            conn.setInstanceFollowRedirects(false); // login redirect ≠ JSON
            if (conn.getResponseCode() != 200) {
                return null;
            }
            return readBody(conn);
        } catch (Exception e) {
            Log.w(TAG, "GET " + path + " failed: " + e);
            return null;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    private static String readBody(HttpURLConnection conn) throws java.io.IOException {
        java.io.InputStream in = conn.getInputStream();
        java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        int n;
        while ((n = in.read(buf)) != -1) {
            out.write(buf, 0, n);
        }
        in.close();
        return out.toString("UTF-8");
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
