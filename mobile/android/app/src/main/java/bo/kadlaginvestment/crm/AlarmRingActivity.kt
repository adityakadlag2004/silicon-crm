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
import bo.kadlaginvestment.crm.ui.KadlagTheme
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Full-screen ringing reminder — launched by the follow-up alarm's
 * full-screen intent (over the lockscreen when the phone is locked, via the
 * heads-up tap otherwise). The looping alarm sound lives on the notification;
 * every way out of this screen silences it.
 */
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

        setContent {
            KadlagTheme {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                    Column(
                        Modifier.fillMaxSize().padding(28.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.Center,
                    ) {
                        Text("⏰", fontSize = 56.sp)
                        Spacer(Modifier.height(8.dp))
                        Text(
                            "Call follow-up",
                            fontSize = 15.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Text(
                            SimpleDateFormat("h:mm a", Locale.US).format(Date(alarm.at)),
                            fontSize = 13.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Spacer(Modifier.height(18.dp))
                        Text(
                            alarm.name,
                            fontSize = 32.sp,
                            fontWeight = FontWeight.Bold,
                            textAlign = TextAlign.Center,
                        )
                        if (alarm.phone.isNotEmpty() && alarm.phone != alarm.name) {
                            Text(
                                alarm.phone,
                                fontSize = 15.sp,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        if (alarm.note.isNotEmpty()) {
                            Spacer(Modifier.height(14.dp))
                            Text(
                                "“${alarm.note}”",
                                fontSize = 17.sp,
                                textAlign = TextAlign.Center,
                            )
                        }
                        Spacer(Modifier.height(36.dp))

                        Button(
                            onClick = {
                                FollowupAlarmNotifier.silence(this@AlarmRingActivity, alarm.id)
                                startActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:${alarm.phone}")))
                                finish()
                            },
                            modifier = Modifier.fillMaxWidth().height(56.dp),
                            colors = ButtonDefaults.buttonColors(
                                containerColor = Color(0xFFE5B740),
                                contentColor = Color.White,
                            ),
                        ) { Text("📞  Call now", fontSize = 18.sp, fontWeight = FontWeight.Bold) }

                        Spacer(Modifier.height(12.dp))
                        OutlinedButton(
                            onClick = {
                                FollowupAlarmScheduler.cancel(this@AlarmRingActivity, alarm.id)
                                Thread {
                                    BackendClient.postJson(
                                        "/clients/api/app/followups/${alarm.id}/action/",
                                        "{\"action\":\"done\"}",
                                    )
                                }.start()
                                silenceAndFinish()
                            },
                            modifier = Modifier.fillMaxWidth().height(50.dp),
                        ) { Text("✓  Mark done", fontSize = 16.sp) }

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
                            modifier = Modifier.fillMaxWidth().height(50.dp),
                        ) { Text("⏰  Snooze ${FollowupAlarmReceiver.SNOOZE_MINUTES} min", fontSize = 16.sp) }

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
