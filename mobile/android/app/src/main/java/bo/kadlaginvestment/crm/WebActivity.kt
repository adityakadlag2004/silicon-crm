package bo.kadlaginvestment.crm

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient

/**
 * Plain WebView for screens not yet converted to native UI. Shares the
 * session automatically (CookieManager is process-wide). tel:/wa.me links
 * open externally like in the main app.
 */
class WebActivity : Activity() {

    private lateinit var web: WebView

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        web = WebView(this)
        setContentView(web)

        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val url = request.url.toString()
                return if (url.startsWith(BackendClient.BASE_URL)) {
                    false // stay in this WebView
                } else {
                    try {
                        startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
                    } catch (_: Exception) {}
                    true
                }
            }
        }
        web.loadUrl(intent.getStringExtra(EXTRA_URL) ?: BackendClient.BASE_URL)
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (web.canGoBack()) web.goBack() else super.onBackPressed()
    }

    companion object {
        private const val EXTRA_URL = "url"

        fun open(context: Context, path: String) {
            context.startActivity(
                Intent(context, WebActivity::class.java)
                    .putExtra(EXTRA_URL, BackendClient.BASE_URL + path)
            )
        }
    }
}
