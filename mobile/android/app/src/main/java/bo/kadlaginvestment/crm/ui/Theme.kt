package bo.kadlaginvestment.crm.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.ui.graphics.Color

// Brand palette — matches the web app (static/manifest.json).
val BrandGold = Color(0xFFE5B740)
val BrandGoldDark = Color(0xFFB8912F)
val BrandCream = Color(0xFFFFFEF8)
val BrandInk = Color(0xFF1F2937)
val BrandMuted = Color(0xFF6B7280)

// Status colours, light theme. These are tuned against a white surface; on the
// dark surface (#1E2126) the 600-weight amber and green fall to roughly 3.5:1,
// under the 4.5:1 WCAG AA floor for body text. The composables below read the
// theme-aware values instead.
private val StatusGreenLight = Color(0xFF16A34A)
private val StatusRedLight = Color(0xFFDC2626)
private val StatusAmberLight = Color(0xFFD97706)

// Lighter tints for dark surfaces (Tailwind 400-weight): ~7:1 on #1E2126.
private val StatusGreenDark = Color(0xFF4ADE80)
private val StatusRedDark = Color(0xFFF87171)
private val StatusAmberDark = Color(0xFFFBBF24)

/**
 * Status colours that follow the theme.
 *
 * Declared as composable `val`s with getters so every existing `StatusGreen`
 * call site picks up the right variant with no edit — the app refers to these
 * in about 90 places.
 */
val StatusGreen: Color
    @Composable @ReadOnlyComposable
    get() = if (isSystemInDarkTheme()) StatusGreenDark else StatusGreenLight

val StatusRed: Color
    @Composable @ReadOnlyComposable
    get() = if (isSystemInDarkTheme()) StatusRedDark else StatusRedLight

val StatusAmber: Color
    @Composable @ReadOnlyComposable
    get() = if (isSystemInDarkTheme()) StatusAmberDark else StatusAmberLight

private val LightColors = lightColorScheme(
    primary = BrandGold,
    onPrimary = Color.White,
    primaryContainer = Color(0xFFFDF6E3),
    onPrimaryContainer = BrandInk,
    background = BrandCream,
    onBackground = BrandInk,
    surface = Color.White,
    onSurface = BrandInk,
    surfaceVariant = Color(0xFFF5F5F4),
    onSurfaceVariant = BrandMuted,
    secondary = BrandGoldDark,
    onSecondary = Color.White,
    error = StatusRedLight,
)

private val DarkColors = darkColorScheme(
    primary = BrandGold,
    onPrimary = Color(0xFF201A08),
    primaryContainer = Color(0xFF3A2F12),
    onPrimaryContainer = Color(0xFFF5E5BD),
    background = Color(0xFF15171B),
    onBackground = Color(0xFFE7E5E4),
    surface = Color(0xFF1E2126),
    onSurface = Color(0xFFE7E5E4),
    surfaceVariant = Color(0xFF2A2E34),
    onSurfaceVariant = Color(0xFF9CA3AF),
    secondary = BrandGold,
    onSecondary = Color(0xFF201A08),
    error = Color(0xFFF87171),
)

@Composable
fun KadlagTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = if (isSystemInDarkTheme()) DarkColors else LightColors,
        content = content,
    )
}
