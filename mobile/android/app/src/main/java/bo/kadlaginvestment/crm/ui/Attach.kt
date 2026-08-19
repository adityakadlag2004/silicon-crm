package bo.kadlaginvestment.crm.ui

import android.content.Context
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.core.content.FileProvider
import bo.kadlaginvestment.crm.BackendClient
import java.io.File

const val MAX_ATTACHMENT_BYTES = 25L * 1024 * 1024

/**
 * Attaching a file from the phone — camera first, picker second.
 *
 * A policy document, a hospital bill and a discharge summary are photographed
 * where the client is, not filed from a desk, so the camera has to be one tap
 * and not a detour through the gallery app. `TakePicture` writes into the
 * app's cache through the FileProvider the manifest already declares. No
 * CAMERA permission is declared on purpose: without one in the manifest the
 * system hands the capture to the camera app and asks for nothing; declaring
 * it would make Android demand a runtime grant this flow never needs.
 */
class Attacher(val pickFile: () -> Unit, val takePhoto: () -> Unit)

@Composable
fun rememberAttacher(onPicked: (Uri) -> Unit): Attacher {
    val context = androidx.compose.ui.platform.LocalContext.current
    // The capture target has to outlive the launch — TakePicture reports only
    // success, not where it wrote.
    var pendingPhoto by remember { mutableStateOf<Uri?>(null) }

    val pick = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) onPicked(uri)
    }
    val camera = rememberLauncherForActivityResult(ActivityResultContracts.TakePicture()) { ok ->
        val uri = pendingPhoto
        pendingPhoto = null
        if (ok && uri != null) onPicked(uri)
    }

    return remember {
        Attacher(
            pickFile = { pick.launch("*/*") },
            takePhoto = {
                val file = File(context.cacheDir, "ki_${System.currentTimeMillis()}.jpg")
                val uri = FileProvider.getUriForFile(
                    context, "${context.packageName}.fileprovider", file)
                pendingPhoto = uri
                camera.launch(uri)
            },
        )
    }
}

/**
 * Upload one content Uri as multipart. Blocking — call off the main thread.
 * Returns null when it worked, otherwise the reason to show the user.
 *
 * A silent catch used to make a failed upload look exactly like a successful
 * one, so the failure is returned rather than swallowed.
 */
fun uploadUri(context: Context, uri: Uri, path: String, field: String = "document"): String? {
    return try {
        val cr = context.contentResolver
        var name = "attachment"
        var size = -1L
        cr.query(uri, null, null, null, null)?.use { cur ->
            val i = cur.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME)
            val s = cur.getColumnIndex(android.provider.OpenableColumns.SIZE)
            if (cur.moveToFirst()) {
                if (i >= 0) name = cur.getString(i) ?: name
                if (s >= 0 && !cur.isNull(s)) size = cur.getLong(s)
            }
        }
        // Guard before spending the user's data on a doomed upload.
        if (size > MAX_ATTACHMENT_BYTES) {
            "File is too large (max ${MAX_ATTACHMENT_BYTES / (1024 * 1024)} MB)"
        } else {
            cr.openInputStream(uri)?.use { stream ->
                val body = BackendClient.postMultipart(
                    path, field, name, cr.getType(uri) ?: "", stream)
                if (body != null) null else "Upload failed — check your connection"
            } ?: "Could not read that file"
        }
    } catch (e: Exception) {
        e.message ?: "Upload failed"
    }
}
