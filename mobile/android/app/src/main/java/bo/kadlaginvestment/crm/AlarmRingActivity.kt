package bo.kadlaginvestment.crm

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
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
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import bo.kadlaginvestment.crm.ui.KadlagTheme
import bo.kadlaginvestment.crm.ui.rsp
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Full-screen ringing reminder — launched by the follow-up alarm's
 * full-screen intent (over the lockscreen when the phone is locked, via the
 * heads-up tap otherwise). The looping alarm sound lives on the notification;
 * every way out of this screen silences it.
 */
/** Mirrors CallFollowUp.OUTCOME_CHOICES — the ones worth a tap while a phone
 * is ringing in your hand. "Spoke" is first because it is the common one. */
private val OUTCOMES = listOf(
    "spoke" to "Spoke",
    "no_answer" to "No answer",
    "call_back" to "Call back later",
    "not_interested" to "Not interested",
    "converted" to "Converted",
)

class AlarmRingActivity : ComponentActivity() {

    private var followupId = 0

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val alarm = FollowupAlarm.fromIntent(intent) ?: run { finish(); return }
        followupId = alarm.id

        if (Build.VERSION.SDK_INT >= 27) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
                    WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON
            )
        }
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        fun silenceAndFinish() {
            FollowupAlarmNotifier.silence(this, alarm.id)
            finish()
        }

        /** Close the follow-up, recording what the call produced. Ringing was
         * the most-used Done path in the app and it recorded nothing. */
        fun closeWithOutcome(outcome: String) {
            FollowupAlarmScheduler.cancel(this, alarm.id)
            val body = if (outcome.isEmpty()) "{\"action\":\"done\"}"
            else "{\"action\":\"done\",\"outcome\":\"$outcome\"}"
            Thread {
                BackendClient.postJson("/clients/api/app/followups/${alarm.id}/action/", body)
            }.start()
            silenceAndFinish()
        }

        setContent {
            KadlagTheme {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                    Column(
                        Modifier.fillMaxSize().padding(28.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.Center,
                    ) {
                        Text("⏰", fontSize = rsp(56))
                        Spacer(Modifier.height(8.dp))
                        Text(
                            "Call follow-up",
                            fontSize = rsp(15),
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Text(
                            SimpleDateFormat("h:mm a", Locale.US).format(Date(alarm.at)),
                            fontSize = rsp(13),
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Spacer(Modifier.height(18.dp))
                        Text(
                            alarm.name,
                            fontSize = rsp(32),
                            fontWeight = FontWeight.Bold,
                            textAlign = TextAlign.Center,
                        )
                        if (alarm.phone.isNotEmpty() && alarm.phone != alarm.name) {
                            Text(
                                alarm.phone,
                                fontSize = rsp(15),
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        if (alarm.note.isNotEmpty()) {
                            Spacer(Modifier.height(14.dp))
                            Text(
                                "“${alarm.note}”",
                                fontSize = rsp(17),
                                textAlign = TextAlign.Center,
                            )
                        }
                        Spacer(Modifier.heightIn(min = 36.dp))

                        Button(
                            onClick = {
                                FollowupAlarmNotifier.silence(this@AlarmRingActivity, alarm.id)
                                startActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:${alarm.phone}")))
                                finish()
                            },
                            modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp),
                            colors = ButtonDefaults.buttonColors(
                                containerColor = Color(0xFFE5B740),
                                contentColor = Color.White,
                            ),
                        ) { Text("📞  Call now", fontSize = rsp(18), fontWeight = FontWeight.Bold) }

                        // Closing from here is the most-used Done path there
                        // is — it used to record nothing, so the outcome
                        // report never saw the calls that rang.
                        Spacer(Modifier.height(12.dp))
                        var closing by remember { mutableStateOf(false) }
                        if (!closing) {
                            OutlinedButton(
                                onClick = { closing = true },
                                modifier = Modifier.fillMaxWidth().heightIn(min = 50.dp),
                            ) { Text("✓  Mark done", fontSize = rsp(16)) }
                        } else {
                            Text(
                                "How did it go?",
                                fontSize = rsp(14),
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            Spacer(Modifier.height(6.dp))
                            OUTCOMES.forEach { (key, label) ->
                                OutlinedButton(
                                    onClick = { closeWithOutcome(key) },
                                    modifier = Modifier.fillMaxWidth().heightIn(min = 46.dp),
                                ) { Text(label, fontSize = rsp(15)) }
                                Spacer(Modifier.height(6.dp))
                            }
                            TextButton(onClick = { closeWithOutcome("") }) {
                                Text("Skip — just mark done", fontSize = rsp(14))
                            }
                        }

                        Spacer(Modifier.height(12.dp))
                        OutlinedButton(
                            onClick = {
                                FollowupAlarmScheduler.schedule(
                                    this@AlarmRingActivity,
                                    alarm.copy(
                                        at = System.currentTimeMillis() +
                                            FollowupAlarmReceiver.SNOOZE_MINUTES * 60_000L
                                    ),
                                )
                                silenceAndFinish()
                            },
                            modifier = Modifier.fillMaxWidth().heightIn(min = 50.dp),
                        ) { Text("⏰  Snooze ${FollowupAlarmReceiver.SNOOZE_MINUTES} min", fontSize = rsp(16)) }

                        Spacer(Modifier.height(8.dp))
                        TextButton(onClick = { silenceAndFinish() }) {
                            Text("Dismiss", color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
        }
    }

    /** Back gesture / home — anything that hides the screen stops the ringing;
     * the user has seen the reminder and the follow-up stays pending. */
    override fun onStop() {
        super.onStop()
        if (followupId != 0) FollowupAlarmNotifier.silence(this, followupId)
    }
}
