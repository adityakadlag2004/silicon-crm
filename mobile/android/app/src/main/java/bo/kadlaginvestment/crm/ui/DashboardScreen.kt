package bo.kadlaginvestment.crm.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import bo.kadlaginvestment.crm.net.ApiClient
import kotlinx.coroutines.launch
import org.json.JSONObject

/** Native home screen — data from /clients/api/app/dashboard/. */
@Composable
fun DashboardScreen(
    modifier: Modifier = Modifier,
    onSessionExpired: () -> Unit,
    onOpenWeb: (String) -> Unit,
) {
    // Cache-first: the last dashboard paints instantly (even with no signal)
    // and is replaced when the live one lands. `Session.apply` shares the
    // role/name/unread this payload already carries.
    val loader = rememberLoader(
        "/clients/api/app/dashboard/",
        onSessionExpired = onSessionExpired,
        onLoaded = { Session.apply(it) },
    )

    when {
        loader.data == null && loader.error != null ->
            Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("Could not load dashboard", fontWeight = FontWeight.SemiBold)
                    Text(loader.error ?: "", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = rsp(13))
                    Spacer(Modifier.height(12.dp))
                    Button(onClick = loader.reload) { Text("Retry") }
                }
            }
        loader.data == null -> Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            CircularProgressIndicator()
        }
        else -> RefreshableBox(refreshing = loader.refreshing, onRefresh = loader.reload, modifier = modifier) {
            Dashboard(loader.data!!, Modifier, onOpenWeb, loader.refreshing, loader.error)
        }
    }
}

@Composable
private fun Dashboard(
    d: JSONObject,
    modifier: Modifier,
    onOpenWeb: (String) -> Unit,
    refreshing: Boolean,
    error: String?,
) {
    val isAdmin = d.optString("role") == "admin"
    val today = d.optJSONObject("today") ?: JSONObject()
    val month = d.optJSONObject("month") ?: JSONObject()

    LazyColumn(
        modifier = modifier.fillMaxSize().padding(horizontal = rdp(16)),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            // Pull down to refresh; the strip only appears when a refresh
            // failed and you're looking at the cached copy.
            Column {
                RefreshingBar(refreshing)
                ErrorStrip(error)
            }
        }
        item {
            Row(
                Modifier.fillMaxWidth().padding(top = 8.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column {
                    Text("Hello, ${d.optString("name")}", fontSize = rsp(22), fontWeight = FontWeight.Bold)
                    Text(
                        if (isAdmin) "Firm overview" else "Your performance",
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        fontSize = rsp(13),
                    )
                }
            }
        }

        val profilePercent = d.optInt("profile_percent", 100)
        if (profilePercent < 100) {
            item {
                Card(
                    modifier = Modifier.fillMaxWidth().clickable { onOpenWeb("/clients/me/profile/") },
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.secondaryContainer),
                ) {
                    Column(Modifier.padding(rdp(14))) {
                        Text("Your profile is $profilePercent% complete",
                            fontWeight = FontWeight.SemiBold, fontSize = rsp(15))
                        Text("Add your name and details so the CRM greets you properly →",
                            color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = rsp(13))
                    }
                }
            }
        }

        item {
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                StatCard(
                    title = "Today",
                    value = rupees(today.optDouble("amount", 0.0)),
                    sub = "${today.optInt("sales_count")} sale(s)",
                    modifier = Modifier.weight(1f),
                )
                StatCard(
                    title = "This month",
                    value = rupees(month.optDouble("amount", 0.0)),
                    sub = "${month.optInt("sales_count")} sale(s)",
                    modifier = Modifier.weight(1f),
                )
            }
        }

        item {
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                if (isAdmin) {
                    StatCard(
                        title = "Pending approvals",
                        value = "${d.optInt("pending_approvals")}",
                        sub = "tap to review",
                        accent = if (d.optInt("pending_approvals") > 0) StatusAmber else StatusGreen,
                        modifier = Modifier.weight(1f).clickable { onOpenWeb("/clients/sales/approve/") },
                    )
                } else {
                    StatCard(
                        title = "Points (month)",
                        value = "%.1f".format(month.optDouble("points", 0.0)),
                        sub = "incentive points",
                        modifier = Modifier.weight(1f),
                    )
                }
                StatCard(
                    title = "Follow-ups",
                    value = "${d.optInt("pending_followups")}",
                    sub = "pending calls",
                    accent = if (d.optInt("pending_followups") > 0) StatusAmber else StatusGreen,
                    modifier = Modifier.weight(1f).clickable { onOpenWeb("/clients/calls/followups/") },
                )
            }
        }

        // ── SPANCO pipeline ──
        // The stage chips, then one swipeable page per stage from Approach on.
        // Tasks and call follow-ups are deliberately absent: they own their
        // own tabs, and a third copy is what people learn to swipe past.
        val pipeline = d.optJSONObject("pipeline")
        if (pipeline != null) {
            item { PipelineStages(pipeline, onOpenWeb) }
            item { PipelineBoard(pipeline, onOpenWeb) }
        }

        if (isAdmin) {
            // ── Team calls today ──
            val tc = d.optJSONObject("team_calls_today")
            if (tc != null) {
                item { SectionHeader("Team calls today") }
                item {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        MiniStat("Calls", "${tc.optInt("calls")}", Modifier.weight(1f))
                        MiniStat("Connected", "${tc.optInt("connected")}", Modifier.weight(1f), StatusGreen)
                        MiniStat("Talk", compactMins(tc.optDouble("talk_minutes", 0.0)), Modifier.weight(1f))
                        MiniStat("Serious", "${tc.optInt("serious")}", Modifier.weight(1f), BrandGoldDark)
                    }
                }
            }

            // ── Today's team leaderboard ──
            val lb = d.optJSONArray("leaderboard_today")
            item { SectionHeader("Today's leaders") }
            if (lb == null || lb.length() == 0) {
                item { Text("No sales logged yet today.", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = rsp(13)) }
            } else {
                items((0 until lb.length()).map { lb.getJSONObject(it) to it }) { (e, i) ->
                    Card(
                        shape = RoundedCornerShape(12.dp),
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
                    ) {
                        Row(
                            Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 11.dp),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(
                                    "${i + 1}",
                                    fontSize = rsp(14), fontWeight = FontWeight.Bold,
                                    color = if (i == 0) BrandGoldDark else MaterialTheme.colorScheme.onSurfaceVariant,
                                    modifier = Modifier.padding(end = 12.dp),
                                )
                                Text(e.optString("name"), fontSize = rsp(14), fontWeight = FontWeight.Medium)
                            }
                            Column(horizontalAlignment = Alignment.End) {
                                Text(rupees(e.optDouble("amount", 0.0)), fontWeight = FontWeight.Bold, fontSize = rsp(14))
                                Text("${e.optInt("count")} sale(s)", fontSize = rsp(11), color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                    }
                }
            }

            // ── Month-to-date product-wise ──
            val pm = d.optJSONArray("product_mtd")
            item { SectionHeader("This month by product (till today)") }
            if (pm == null || pm.length() == 0) {
                item { Text("No approved business this month yet.", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = rsp(13)) }
            } else {
                val maxAmt = (0 until pm.length()).maxOf { pm.getJSONObject(it).optDouble("amount", 0.0) }
                items((0 until pm.length()).map { pm.getJSONObject(it) }) { p ->
                    Column(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                            Text(p.optString("name"), fontSize = rsp(13), fontWeight = FontWeight.SemiBold)
                            Text("${rupees(p.optDouble("amount", 0.0))} · ${p.optInt("count")}", fontSize = rsp(13), fontWeight = FontWeight.Bold)
                        }
                        Spacer(Modifier.height(3.dp))
                        ProgressBar(if (maxAmt > 0) (p.optDouble("amount", 0.0) / maxAmt).toFloat() else 0f)
                    }
                }
            }
        } else {
            // ── Gamified employee view ──
            val earnings = d.optJSONObject("earnings")
            if (earnings != null) {
                item { EarningsCard(earnings) }
            }

            // Active campaigns to chase
            val camps = d.optJSONArray("active_campaigns")
            if (camps != null && camps.length() > 0) {
                item { SectionHeader("🔥 Live campaigns — earn extra!") }
                items((0 until camps.length()).map { camps.getJSONObject(it) }) { c ->
                    CampaignCard(c)
                }
            }

            // My business this month by product
            val pm = d.optJSONArray("product_mtd")
            item { SectionHeader("My business this month") }
            if (pm == null || pm.length() == 0) {
                item {
                    Text(
                        "No approved sales yet this month — add one from the Add Sale tab.",
                        color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = rsp(13),
                    )
                }
            } else {
                val maxAmt = (0 until pm.length()).maxOf { pm.getJSONObject(it).optDouble("amount", 0.0) }
                items((0 until pm.length()).map { pm.getJSONObject(it) }) { p ->
                    Column(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                            Text(p.optString("name"), fontSize = rsp(13), fontWeight = FontWeight.SemiBold)
                            Text("${rupees(p.optDouble("amount", 0.0))} · ${p.optInt("count")}", fontSize = rsp(13), fontWeight = FontWeight.Bold)
                        }
                        Spacer(Modifier.height(3.dp))
                        ProgressBar(if (maxAmt > 0) (p.optDouble("amount", 0.0) / maxAmt).toFloat() else 0f)
                    }
                }
            }
        }

        item { Spacer(Modifier.height(16.dp)) }
    }
}

/** Stage-by-stage standing, each chip a way into that slice of the list. */
@Composable
private fun PipelineStages(p: JSONObject, onOpenWeb: (String) -> Unit) {
    val stages = p.optJSONArray("stages") ?: return
    Card(
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.fillMaxWidth().padding(rdp(14))) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("Pipeline · ${p.optInt("live")} live", fontSize = rsp(15), fontWeight = FontWeight.Bold)
                TextButton(onClick = { onOpenWeb("/clients/leads/") }) {
                    Text("All leads →", fontSize = rsp(13))
                }
            }
            Row(
                Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                horizontalArrangement = Arrangement.spacedBy(rdp(8)),
            ) {
                for (i in 0 until stages.length()) {
                    val st = stages.getJSONObject(i)
                    StageChip(st) { onOpenWeb("/clients/leads/?stage=${st.optString("stage")}") }
                }
            }
        }
    }
}

@Composable
private fun StageChip(st: JSONObject, onClick: () -> Unit) {
    // Approach/Negotiation/Conclusion is the live band — the deals that are
    // actually in play, so they carry the brand colour and the rest stay quiet.
    val hot = st.optBoolean("hot")
    val count = st.optInt("count")
    val tint = if (hot && count > 0) BrandGoldDark else MaterialTheme.colorScheme.onSurfaceVariant
    Column(
        Modifier
            .clickable(onClick = onClick)
            .background(
                if (hot && count > 0) BrandGold.copy(alpha = 0.14f)
                else MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f),
                RoundedCornerShape(12.dp),
            )
            .padding(horizontal = rdp(12), vertical = rdp(8)),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("$count", fontSize = rsp(17), fontWeight = FontWeight.Bold, color = tint)
        Text(
            st.optString("label").substringBefore(" /"),
            fontSize = rsp(11), color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

/**
 * The pipeline, one swipeable page per stage.
 *
 * Approach onward only: Suspect and Prospect are the top of the funnel and
 * belong on the list screen, not on a dashboard answering "what do I do
 * today?". Each card says on itself whether the lead is being chased, so
 * chased and unchased leads are visible in the same place instead of the
 * chased ones being invisible behind a "needs you" list.
 */
@Composable
private fun PipelineBoard(pipeline: JSONObject, onOpenWeb: (String) -> Unit) {
    val board = pipeline.optJSONArray("board") ?: return
    if (board.length() == 0) return
    val pages = (0 until board.length()).map { board.getJSONObject(it) }
    // Saveable: rotating the phone must not throw the user back to Approach.
    val pagerState = androidx.compose.foundation.pager.rememberPagerState(
        pageCount = { pages.size })
    val scope = rememberCoroutineScope()

    Column(Modifier.fillMaxWidth()) {
        // The chips are the page indicator and the jump-to control; swiping
        // moves them, tapping moves the pager. One row, both directions.
        Row(
            Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(vertical = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(rdp(6)),
        ) {
            pages.forEachIndexed { index, page ->
                val unchased = page.optInt("unchased")
                Chip(
                    "${page.optString("label").substringBefore("/").trim()} ${page.optInt("count")}" +
                        if (unchased > 0) "  ·  $unchased ⚠" else "",
                    pagerState.currentPage == index,
                ) { scope.launch { pagerState.animateScrollToPage(index) } }
            }
        }

        androidx.compose.foundation.pager.HorizontalPager(
            state = pagerState,
            modifier = Modifier.fillMaxWidth(),
            pageSpacing = rdp(8),
            verticalAlignment = Alignment.Top,
        ) { index ->
            val page = pages[index]
            val leads = page.optJSONArray("leads")
            Column(
                Modifier.fillMaxWidth().heightIn(min = rdp(150)),
                verticalArrangement = Arrangement.spacedBy(rdp(8)),
            ) {
                if ((leads?.length() ?: 0) == 0) {
                    Text(
                        "Nothing at ${page.optString("label")} right now.",
                        fontSize = rsp(13), color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                for (i in 0 until (leads?.length() ?: 0)) {
                    val l = leads!!.getJSONObject(i)
                    BoardLeadCard(l) { onOpenWeb("/clients/leads/${l.optInt("id")}/") }
                }
                if (page.optBoolean("has_more")) {
                    TextButton(onClick = {
                        onOpenWeb("/clients/leads/?stage=${page.optString("stage")}")
                    }) {
                        Text("See all ${page.optInt("count")} →", fontSize = rsp(13))
                    }
                }
            }
        }
        Text(
            "Swipe for the next stage",
            fontSize = rsp(10), color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 4.dp),
        )
    }
}

/** One lead, wearing its follow-up. Tapping opens it. */
@Composable
private fun BoardLeadCard(l: JSONObject, onClick: () -> Unit) {
    val chased = l.optInt("followups") > 0
    Card(
        modifier = Modifier.fillMaxWidth().clickable(onClick = onClick),
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = rdp(14), vertical = rdp(11)),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text(l.optString("name"), fontSize = rsp(14), fontWeight = FontWeight.SemiBold)
                val owner = l.optString("owner")
                Text(
                    "${l.optInt("days_in_stage")}d here" +
                        if (owner.isNotBlank()) " · $owner" else "",
                    fontSize = rsp(11), color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Column(horizontalAlignment = Alignment.End, verticalArrangement = Arrangement.spacedBy(3.dp)) {
                if (chased) {
                    // The date is the answer to "is anyone on this?", so it is
                    // the thing shown, not a tick.
                    ReasonTag("📅 ${fmtDate(l.optString("next_followup"))}", StatusGreen)
                } else {
                    ReasonTag("No follow-up", StatusRed)
                }
                if (l.optBoolean("stalled")) ReasonTag("Stalled", StatusAmber)
            }
        }
    }
}

@Composable
private fun ReasonTag(text: String, color: Color) {
    Box(
        Modifier
            .background(color.copy(alpha = 0.14f), RoundedCornerShape(8.dp))
            .padding(horizontal = rdp(8), vertical = rdp(2))
    ) {
        Text(text, fontSize = rsp(10), color = color, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
private fun SectionHeader(text: String) {
    Text(text, fontSize = rsp(16), fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 8.dp))
}

@Composable
private fun EarningsCard(e: JSONObject) {
    val salary = e.optDouble("salary", 0.0)
    val earned = e.optDouble("earned", 0.0)
    val justified = e.optBoolean("justified")
    val hasSalary = !e.isNull("percent") && salary > 0
    val percent = if (hasSalary) e.optDouble("percent", 0.0) else 0.0
    val fraction = (earned / salary).coerceIn(0.0, 1.0).toFloat()

    Card(
        shape = RoundedCornerShape(18.dp),
        colors = CardDefaults.cardColors(
            containerColor = if (justified) StatusGreen.copy(alpha = 0.12f)
            else MaterialTheme.colorScheme.primaryContainer
        ),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(
                if (justified) "🎉 Salary justified!" else "💪 Your earning progress",
                fontSize = rsp(16), fontWeight = FontWeight.Bold,
                color = if (justified) StatusGreen else BrandGoldDark,
            )

            if (!hasSalary) {
                Text("You've earned ${rupees(earned)} in incentive points this month.", fontSize = rsp(14))
            } else {
                Text(
                    "${rupees(earned)} earned  ·  ${rupees(salary)} salary",
                    fontSize = rsp(13), color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                // Progress bar
                androidx.compose.foundation.layout.Box(
                    Modifier.fillMaxWidth().height(14.dp)
                        .background(MaterialTheme.colorScheme.surface, RoundedCornerShape(8.dp))
                ) {
                    androidx.compose.foundation.layout.Box(
                        Modifier.fillMaxWidth(fraction).height(14.dp)
                            .background(if (justified) StatusGreen else BrandGold, RoundedCornerShape(8.dp))
                    )
                }
                Text(
                    "${"%.0f".format(percent)}% of salary",
                    fontSize = rsp(11), fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (justified) {
                    Text(
                        "You're ${rupees(e.optDouble("surplus", 0.0))} above your salary — that's your bonus zone. Keep selling! 🚀",
                        fontSize = rsp(13), fontWeight = FontWeight.Medium, color = StatusGreen,
                    )
                } else {
                    Text(
                        "Just ${rupees(e.optDouble("remaining", 0.0))} more to justify your salary this month.",
                        fontSize = rsp(13), fontWeight = FontWeight.Medium,
                    )
                }
            }
        }
    }
}

@Composable
private fun CampaignCard(c: JSONObject) {
    Card(
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.fillMaxWidth().padding(14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text(c.optString("name"), fontSize = rsp(15), fontWeight = FontWeight.Bold, color = BrandGoldDark)
                Text("ends ${c.optString("ends")}", fontSize = rsp(11), color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            val products = c.optJSONArray("products")
            for (i in 0 until (products?.length() ?: 0)) {
                val p = products!!.getJSONObject(i)
                val benefit = if (p.optString("benefit_type") == "unit") {
                    "${"%.1f".format(p.optDouble("points_per_unit", 0.0))} pts per ${rupees(p.optDouble("unit_amount", 0.0))}"
                } else {
                    "target payout — hit the slab for a bonus"
                }
                Text("• ${p.optString("product")}: $benefit", fontSize = rsp(13))
            }
        }
    }
}

@Composable
private fun MiniStat(
    title: String,
    value: String,
    modifier: Modifier = Modifier,
    accent: androidx.compose.ui.graphics.Color = androidx.compose.ui.graphics.Color.Unspecified,
) {
    Card(
        modifier = modifier,
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.padding(vertical = 12.dp, horizontal = 8.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Text(
                value, fontSize = rsp(16), fontWeight = FontWeight.Bold,
                color = if (accent == androidx.compose.ui.graphics.Color.Unspecified) MaterialTheme.colorScheme.onSurface else accent,
            )
            Text(title, fontSize = rsp(10), color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun ProgressBar(fraction: Float) {
    val track = MaterialTheme.colorScheme.surfaceVariant
    val fill = MaterialTheme.colorScheme.primary
    androidx.compose.foundation.layout.Box(
        Modifier.fillMaxWidth().height(7.dp).background(track, RoundedCornerShape(4.dp))
    ) {
        androidx.compose.foundation.layout.Box(
            Modifier.fillMaxWidth(fraction.coerceIn(0f, 1f)).height(7.dp).background(fill, RoundedCornerShape(4.dp))
        )
    }
}

private fun compactMins(totalMinutes: Double): String {
    val totalSec = (totalMinutes * 60).toInt()
    val h = totalSec / 3600
    val m = (totalSec % 3600) / 60
    return if (h > 0) "${h}h${m}m" else "${m}m"
}

@Composable
private fun StatCard(
    title: String,
    value: String,
    sub: String,
    modifier: Modifier = Modifier,
    accent: androidx.compose.ui.graphics.Color = MaterialTheme.colorScheme.onSurface,
) {
    Card(
        modifier = modifier,
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.padding(16.dp)) {
            Text(title, fontSize = rsp(12), color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(value, fontSize = rsp(20), fontWeight = FontWeight.Bold, color = accent)
            Text(sub, fontSize = rsp(11), color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}
