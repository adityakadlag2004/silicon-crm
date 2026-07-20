package bo.kadlaginvestment.crm

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import bo.kadlaginvestment.crm.net.ApiClient
import bo.kadlaginvestment.crm.ui.BrandGoldDark
import bo.kadlaginvestment.crm.ui.KadlagTheme
import bo.kadlaginvestment.crm.ui.StatusRed
import bo.kadlaginvestment.crm.ui.rsp
import kotlinx.coroutines.launch

/** Native login screen. On success the session cookies are stored (shared
 * with the whole app) and control passes to the native shell. */
class LoginActivity : ComponentActivity() {

    companion object {
        private const val EXTRA_SESSION_EXPIRED = "session_expired"

        /** Intent for "the server rejected our session" — clears the dead
         * cookie and forces the login form even if a cookie is still around. */
        fun expiredIntent(ctx: android.content.Context): Intent =
            Intent(ctx, LoginActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
                .putExtra(EXTRA_SESSION_EXPIRED, true)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val expired = intent?.getBooleanExtra(EXTRA_SESSION_EXPIRED, false) == true
        if (expired) {
            // Belt and braces: the caller clears the cookie too, but if a stale
            // one survives here we must NOT bounce back to the shell — that is
            // the loop.
            ApiClient.clearSession()
        }

        // Already signed in (e.g. relaunched) — skip straight to the shell.
        // Never taken on the expired path, however stale cookies look.
        if (!expired && ApiClient.hasSession()) {
            goShell()
            return
        }

        setContent {
            KadlagTheme {
                LoginForm(sessionExpired = expired, onLoggedIn = { goShell() })
            }
        }
    }

    private fun goShell() {
        startActivity(
            Intent(this, ShellActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
        )
        finish()
    }
}

@androidx.compose.runtime.Composable
private fun LoginForm(sessionExpired: Boolean = false, onLoggedIn: () -> Unit) {
    val scope = rememberCoroutineScope()
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var loading by remember { mutableStateOf(false) }
    var error by remember {
        mutableStateOf<String?>(
            if (sessionExpired) "Your session ended. Please sign in again." else null
        )
    }

    fun submit() {
        if (username.isBlank() || password.isBlank()) return
        loading = true
        error = null
        scope.launch {
            when (val r = ApiClient.login(username.trim(), password)) {
                is ApiClient.Result.Ok -> onLoggedIn()
                is ApiClient.Result.NotLoggedIn -> error = "Invalid username or password."
                is ApiClient.Result.Error -> error = r.message
            }
            loading = false
        }
    }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 28.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Spacer(Modifier.heightIn(min = 56.dp))
        androidx.compose.foundation.Image(
            painter = androidx.compose.ui.res.painterResource(id = bo.kadlaginvestment.crm.R.drawable.kilogo),
            contentDescription = "Kadlag Investment",
            modifier = Modifier.fillMaxWidth(0.72f).heightIn(min = 120.dp),
            contentScale = androidx.compose.ui.layout.ContentScale.Fit,
        )
        Text(
            "Back Office",
            fontSize = rsp(15),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.heightIn(min = 36.dp))

        OutlinedTextField(
            value = username,
            onValueChange = { username = it },
            label = { Text("Username") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(12.dp))
        OutlinedTextField(
            value = password,
            onValueChange = { password = it },
            label = { Text("Password") },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done),
            modifier = Modifier.fillMaxWidth(),
        )

        error?.let {
            Spacer(Modifier.height(10.dp))
            Text(it, color = StatusRed, fontSize = rsp(13), fontWeight = FontWeight.SemiBold, textAlign = TextAlign.Center)
        }

        Spacer(Modifier.height(20.dp))
        Button(
            onClick = { submit() },
            enabled = !loading && username.isNotBlank() && password.isNotBlank(),
            modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
        ) { Text(if (loading) "Signing in…" else "Sign in", fontSize = rsp(16)) }

        Spacer(Modifier.heightIn(min = 40.dp))
    }
}
