# R8 rules for the release build (minifyEnabled true).
#
# Everything here is either (a) reached only by reflection/the framework, so R8
# cannot see the reference, or (b) needed to read a crash report.

# ── Readable crash reports ──
# Stack traces are posted to /api/app/crash/ and read by a human, so keep the
# line numbers. Source file names are hidden (they add nothing once obfuscated).
-keepattributes SourceFile,LineNumberTable
-renamesourcefileattribute SourceFile

# ── Framework entry points (referenced from AndroidManifest.xml only) ──
-keep class bo.kadlaginvestment.crm.RouterActivity { *; }
-keep class bo.kadlaginvestment.crm.LoginActivity { *; }
-keep class bo.kadlaginvestment.crm.ShellActivity { *; }
-keep class bo.kadlaginvestment.crm.TasksActivity { *; }
-keep class bo.kadlaginvestment.crm.WebActivity { *; }
-keep class bo.kadlaginvestment.crm.MainActivity { *; }
-keep class bo.kadlaginvestment.crm.FollowupActivity { *; }
-keep class bo.kadlaginvestment.crm.AlarmRingActivity { *; }
-keep class bo.kadlaginvestment.crm.CallTrackerReceiver { *; }
-keep class bo.kadlaginvestment.crm.FollowupAlarmReceiver { *; }
-keep class bo.kadlaginvestment.crm.FollowupBootReceiver { *; }
-keep class bo.kadlaginvestment.crm.KadlagMessagingService { *; }

# Capacitor + its plugins are wired up by reflection from plugin metadata.
-keep class com.getcapacitor.** { *; }
-keep class com.capacitorjs.** { *; }
-keep @com.getcapacitor.annotation.CapacitorPlugin class * { *; }
-keepclassmembers class * extends com.getcapacitor.Plugin {
    @com.getcapacitor.PluginMethod public <methods>;
}
-keep class bo.kadlaginvestment.crm.CallTrackingPlugin { *; }

# The WebView JS bridge calls into this by name.
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}

# Firebase Messaging resolves the service and its callbacks reflectively.
-keep class com.google.firebase.** { *; }
-dontwarn com.google.firebase.**

# org.json is part of the platform; nothing to shrink, but keep it quiet.
-dontwarn org.json.**
