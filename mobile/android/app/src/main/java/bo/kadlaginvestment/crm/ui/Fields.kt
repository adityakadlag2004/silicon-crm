package bo.kadlaginvestment.crm.ui

import android.app.DatePickerDialog
import android.app.TimePickerDialog
import android.content.Context
import android.content.Intent
import android.provider.ContactsContract
import android.text.format.DateFormat
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import java.util.Calendar
import java.util.Locale

/**
 * The form controls shared by every screen. Before this file each screen
 * hand-rolled its own read-only dropdown anchor (five copies in Add Sale
 * alone) and typed dates into a free-text "YYYY-MM-DD" box.
 */

/** Read-only field that anchors a DropdownMenu. `menu` is the menu itself. */
@Composable
fun PickerField(
    label: String,
    value: String,
    onOpen: () -> Unit,
    enabled: Boolean = true,
    menu: @Composable () -> Unit,
) {
    Box {
        OutlinedTextField(
            value = value,
            onValueChange = {},
            readOnly = true,
            label = { Text(label) },
            // `enabled = false` on the field (so taps reach us) but the colours
            // are overridden below — a genuinely disabled-looking picker reads
            // as broken.
            modifier = Modifier
                .fillMaxWidth()
                .clickable(enabled = enabled, role = Role.Button, onClick = onOpen),
            enabled = false,
            colors = OutlinedTextFieldDefaults.colors(
                disabledTextColor = MaterialTheme.colorScheme.onSurface,
                disabledBorderColor = MaterialTheme.colorScheme.outline,
                disabledLabelColor = MaterialTheme.colorScheme.onSurfaceVariant,
            ),
        )
        menu()
    }
}

/**
 * Date entry through the platform picker, never the keyboard. `value` is an
 * ISO date ("2026-07-21") or "" — the format the APIs expect; the field shows
 * it in the readable local form.
 */
@Composable
fun DateField(
    label: String,
    value: String,
    modifier: Modifier = Modifier,
    minToday: Boolean = false,
    maxToday: Boolean = false,
    onPick: (String) -> Unit,
) {
    val context = LocalContext.current
    OutlinedTextField(
        value = if (value.isBlank()) "" else fmtDate(value),
        onValueChange = {},
        readOnly = true,
        label = { Text(label) },
        placeholder = { Text("Tap to pick") },
        modifier = modifier
            .fillMaxWidth()
            .clickable(role = Role.Button) {
                pickDate(context, value, minToday, maxToday, onPick)
            },
        enabled = false,
        colors = OutlinedTextFieldDefaults.colors(
            disabledTextColor = MaterialTheme.colorScheme.onSurface,
            disabledBorderColor = MaterialTheme.colorScheme.outline,
            disabledLabelColor = MaterialTheme.colorScheme.onSurfaceVariant,
            disabledPlaceholderColor = MaterialTheme.colorScheme.onSurfaceVariant,
        ),
    )
}

/** Date + time, picked never typed. ISO "yyyy-MM-ddTHH:mm" or "". */
@Composable
fun DateTimeField(label: String, value: String, onPick: (String) -> Unit) {
    val context = LocalContext.current
    OutlinedTextField(
        value = if (value.isBlank()) "" else "${fmtDate(value.take(10))} ${value.takeLast(5)}",
        onValueChange = {},
        readOnly = true,
        label = { Text(label) },
        placeholder = { Text("Tap to pick") },
        enabled = false,
        modifier = Modifier.fillMaxWidth().clickable(
            role = Role.Button,
        ) { pickDateTime(context, value, minNow = true, onPicked = onPick) },
        colors = OutlinedTextFieldDefaults.colors(
            disabledTextColor = MaterialTheme.colorScheme.onSurface,
            disabledBorderColor = MaterialTheme.colorScheme.outline,
            disabledLabelColor = MaterialTheme.colorScheme.onSurfaceVariant,
            disabledPlaceholderColor = MaterialTheme.colorScheme.onSurfaceVariant,
        ),
    )
}

/** Platform date picker seeded with `iso` (or today). Hands back ISO. */
fun pickDate(
    context: Context,
    iso: String = "",
    minToday: Boolean = false,
    maxToday: Boolean = false,
    onPicked: (String) -> Unit,
) {
    val cal = Calendar.getInstance()
    parseIso(iso)?.let { cal.time = it }
    DatePickerDialog(
        context,
        { _, y, mo, d -> onPicked(String.format(Locale.US, "%04d-%02d-%02d", y, mo + 1, d)) },
        cal.get(Calendar.YEAR), cal.get(Calendar.MONTH), cal.get(Calendar.DAY_OF_MONTH),
    ).apply {
        if (minToday) datePicker.minDate = System.currentTimeMillis() - 1000
        if (maxToday) datePicker.maxDate = System.currentTimeMillis()
    }.show()
}

/**
 * Date then time, chained. Hands back a local ISO timestamp
 * ("2026-07-21T15:30") — what the backend's `custom_at` expects.
 *
 * `is24HourFormat` honours the device setting; every picker in the app used to
 * hardcode 12-hour regardless of what the user had chosen.
 */
fun pickDateTime(
    context: Context,
    startIso: String = "",
    minNow: Boolean = false,
    onPicked: (String) -> Unit,
) {
    val cal = Calendar.getInstance()
    parseIso(startIso.take(10))?.let { cal.time = it }
    val startHour = startIso.substring(11.coerceAtMost(startIso.length))
        .take(5).takeIf { it.length == 5 }
    val h0 = startHour?.substringBefore(":")?.toIntOrNull() ?: cal.get(Calendar.HOUR_OF_DAY)
    val m0 = startHour?.substringAfter(":")?.toIntOrNull() ?: cal.get(Calendar.MINUTE)
    DatePickerDialog(
        context,
        { _, y, mo, d ->
            TimePickerDialog(
                context,
                { _, h, mi ->
                    onPicked(String.format(Locale.US, "%04d-%02d-%02dT%02d:%02d", y, mo + 1, d, h, mi))
                },
                h0, m0, DateFormat.is24HourFormat(context),
            ).show()
        },
        cal.get(Calendar.YEAR), cal.get(Calendar.MONTH), cal.get(Calendar.DAY_OF_MONTH),
    ).apply {
        if (minNow) datePicker.minDate = System.currentTimeMillis() - 1000
    }.show()
}

/** Time-only picker, seeded from "HH:mm". Hands back "HH:mm". */
fun pickTime(context: Context, hhmm: String, onPicked: (String) -> Unit) {
    val h = hhmm.substringBefore(":").toIntOrNull() ?: 9
    val m = hhmm.substringAfter(":", "0").toIntOrNull() ?: 0
    TimePickerDialog(
        context,
        { _, hh, mm -> onPicked(String.format(Locale.US, "%02d:%02d", hh, mm)) },
        h, m, DateFormat.is24HourFormat(context),
    ).show()
}

/**
 * Money input filter: digits and at most one decimal point. The old
 * `filter { it.isDigit() || it == '.' }` happily produced "1.2.3", which the
 * server then rejected after a round trip.
 */
fun moneyInput(raw: String): String {
    val sb = StringBuilder()
    var dot = false
    for (ch in raw) {
        when {
            ch.isDigit() -> sb.append(ch)
            ch == '.' && !dot -> { dot = true; sb.append(ch) }
        }
    }
    return sb.toString()
}

private fun parseIso(iso: String): java.util.Date? = try {
    if (iso.length >= 10) java.text.SimpleDateFormat("yyyy-MM-dd", Locale.US).parse(iso.take(10)) else null
} catch (_: Exception) {
    null
}

/**
 * Pick a contact's phone number off the device.
 *
 * ACTION_PICK on the *Phone* table, not `PickContact()`: the user picks one
 * **number**, so a contact with three of them resolves itself, and the row
 * comes back readable without a READ_CONTACTS grant — the picker hands over
 * access to just that row. Typing the number by hand stays available; this is
 * a shortcut, never the only way in.
 *
 * `label` is the caller's, so a screen can show the picked name on the button
 * and drop it again when the number is edited by hand.
 */
@Composable
fun ContactPickButton(
    label: String,
    modifier: Modifier = Modifier,
    onPicked: (name: String, phone: String) -> Unit,
) {
    val context = LocalContext.current
    val launcher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        val uri = result.data?.data ?: return@rememberLauncherForActivityResult
        try {
            context.contentResolver.query(
                uri,
                arrayOf(
                    ContactsContract.CommonDataKinds.Phone.NUMBER,
                    ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
                ),
                null, null, null,
            )?.use { c ->
                if (c.moveToFirst()) {
                    onPicked(
                        c.getString(1).orEmpty(),
                        c.getString(0).orEmpty().filterNot { it.isWhitespace() },
                    )
                }
            }
        } catch (_: Exception) {
            // provider hiccup → the number can still be typed in by hand
        }
    }
    OutlinedButton(
        onClick = {
            launcher.launch(
                Intent(Intent.ACTION_PICK, ContactsContract.CommonDataKinds.Phone.CONTENT_URI)
            )
        },
        modifier = modifier,
    ) { Text(label) }
}
