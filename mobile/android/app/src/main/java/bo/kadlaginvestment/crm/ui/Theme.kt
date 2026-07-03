package bo.kadlaginvestment.crm.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// Brand palette — matches the web app (static/manifest.json).
val BrandGold = Color(0xFFE5B740)
val BrandGoldDark = Color(0xFFB8912F)
val BrandCream = Color(0xFFFFFEF8)
val BrandInk = Color(0xFF1F2937)
val BrandMuted = Color(0xFF6B7280)
val StatusGreen = Color(0xFF16A34A)
val StatusRed = Color(0xFFDC2626)
val StatusAmber = Color(0xFFD97706)

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
    error = StatusRed,
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
