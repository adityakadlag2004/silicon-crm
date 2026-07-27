package bo.kadlaginvestment.crm

import android.annotation.SuppressLint
import android.app.Activity
import android.app.DownloadManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.Gravity
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import android.widget.ProgressBar
import android.widget.Toast

/**
 * WebView for screens not yet converted to native UI. Shares the session
 * automatically (CookieManager is process-wide).
 *
 * It used to be a bare WebView, which quietly broke three things the remaining
 * web screens need: file uploads (no chooser → the button did nothing),
 * downloads (report exports did nothing), and any feedback at all while a slow
 * page loaded or failed.
 */
class WebActivity : Activity() {

    private lateinit var web: WebView
    private lateinit var progress: ProgressBar
    private var filePicker: ValueCallback<Array<Uri>>? = null

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val root = FrameLayout(this)
        web = WebView(this)
        root.addView(
            web,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT,
            ),
        )
        progress = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            max = 100
            visibility = ViewGroup.VISIBLE
        }
        root.addView(
            progress,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.TOP,
            ),
        )
        setContentView(root)

        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            // Pages here are the desktop CRM; let them size to the phone.
            useWideViewPort = true
            loadWithOverviewMode = true
            builtInZoomControls = true
            displayZoomControls = false
            // Follow the device dark-mode setting where the page supports it.
            @Suppress("DEPRECATION")
            cacheMode = WebSettings.LOAD_DEFAULT
        }
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, true)

        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val url = request.url.toString()
                return if (url.startsWith(BackendClient.BASE_URL)) {
                    false // stay in this WebView
                } else {
                    try {
                        startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
                    } catch (_: Exception) {
                        Toast.makeText(this@WebActivity, "Nothing can open that link", Toast.LENGTH_SHORT).show()
                    }
                    true
                }
            }

            override fun onReceivedError(
                view: WebView,
                request: WebResourceRequest,
                error: android.webkit.WebResourceError,
            ) {
                // Only the main document matters; a failed image is not a
                // reason to replace the page.
                if (!request.isForMainFrame) return
                progress.visibility = ViewGroup.GONE
                view.loadData(ERROR_PAGE, "text/html", "UTF-8")
            }
        }

        web.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView, newProgress: Int) {
                progress.progress = newProgress
                progress.visibility = if (newProgress >= 100) ViewGroup.GONE else ViewGroup.VISIBLE
            }

            /** Without this, every <input type="file"> on a web screen — KYC
             * documents, policy uploads — silently did nothing. */
            override fun onShowFileChooser(
                view: WebView,
                callback: ValueCallback<Array<Uri>>,
                params: FileChooserParams,
            ): Boolean {
                filePicker?.onReceiveValue(null)   // a previous chooser was abandoned
                filePicker = callback
                return try {
                    startActivityForResult(params.createIntent(), REQ_FILE)
                    true
                } catch (_: Exception) {
                    filePicker = null
                    Toast.makeText(this@WebActivity, "No file picker available", Toast.LENGTH_SHORT).show()
                    false
                }
            }
        }

        // Report/statement exports: hand the URL to DownloadManager with the
        // session cookie attached, so the file actually lands in Downloads.
        web.setDownloadListener { url, userAgent, contentDisposition, mimeType, _ ->
            try {
                val name = android.webkit.URLUtil.guessFileName(url, contentDisposition, mimeType)
                val req = DownloadManager.Request(Uri.parse(url))
                    .addRequestHeader("Cookie", CookieManager.getInstance().getCookie(url) ?: "")
                    .addRequestHeader("User-Agent", userAgent)
                    .setMimeType(mimeType)
                    .setTitle(name)
                    .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                    .setDestinationInExternalPublicDir(android.os.Environment.DIRECTORY_DOWNLOADS, name)
                (getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager).enqueue(req)
                Toast.makeText(this, "Downloading $name…", Toast.LENGTH_SHORT).show()
            } catch (_: Exception) {
                Toast.makeText(this, "Could not start the download", Toast.LENGTH_SHORT).show()
            }
        }

        web.loadUrl(intent.getStringExtra(EXTRA_URL) ?: BackendClient.BASE_URL)
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        @Suppress("DEPRECATION")
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQ_FILE) return
        // The callback MUST be answered either way, or the page's file input
        // stays wedged and no further picker will open.
        filePicker?.onReceiveValue(
            if (resultCode == RESULT_OK) WebChromeClient.FileChooserParams.parseResult(resultCode, data)
            else null
        )
        filePicker = null
    }

    override fun onDestroy() {
        filePicker?.onReceiveValue(null)
        filePicker = null
        super.onDestroy()
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (web.canGoBack()) web.goBack() else @Suppress("DEPRECATION") super.onBackPressed()
    }

    companion object {
        private const val EXTRA_URL = "url"
        private const val REQ_FILE = 4001

        private const val ERROR_PAGE =
            "<html><body style='font-family:sans-serif;padding:40px;text-align:center;color:#6B7280'>" +
                "<h3 style='color:#1F2937'>Couldn't load this page</h3>" +
                "<p>Check your internet connection, then pull down or reopen the screen.</p>" +
                "</body></html>"

        fun open(context: Context, path: String) {
            context.startActivity(
                Intent(context, WebActivity::class.java)
                    .putExtra(EXTRA_URL, BackendClient.BASE_URL + path)
            )
        }
    }
}
