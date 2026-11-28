package com.nathanneurotic.ps2servers.service

import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.wifi.WifiManager
import android.os.Binder
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import com.nathanneurotic.ps2servers.R
import com.nathanneurotic.ps2servers.config.LauncherConfig
import com.nathanneurotic.ps2servers.net.NetworkAddressHelper
import com.nathanneurotic.ps2servers.ui.MainActivity

/**
 * Foreground service hosting active PS2 game servers so Android does not
 * throttle sockets or suspend the process while loading games over Wi-Fi
 * or USB-Ethernet tethering.
 */
class Ps2ServerService : Service() {
    inner class LocalBinder : Binder() {
        val service: Ps2ServerService
            get() = this@Ps2ServerService
    }

    private val binder = LocalBinder()
    lateinit var coordinator: ServerCoordinator
        private set

    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLock: WifiManager.WifiLock? = null
    private var multicastLock: WifiManager.MulticastLock? = null
    private var foregroundStarted = false

    private val coordinatorListener = object : ServerCoordinator.Listener {
        override fun onStateChanged() {
            syncLocksAndNotification()
        }

        override fun onLogAppended(line: String) = Unit
    }

    override fun onCreate() {
        super.onCreate()
        coordinator = ServerCoordinator(
            configFile = ServerCoordinator.defaultConfigFile(applicationContext),
            safBridge = AndroidSafBridge(applicationContext)
        )
        coordinator.addListener(coordinatorListener)
        ensureNotificationChannel()
    }

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP_ALL -> {
                coordinator.stopAll()
                teardownForegroundAndLocks()
                stopSelf()
                return START_NOT_STICKY
            }
            ACTION_RESTORE -> {
                promoteForeground()
                coordinator.restoreLastActive()
                syncLocksAndNotification()
                return START_STICKY
            }
            else -> {
                if (coordinator.isAnyBackgroundWorkActive()) {
                    promoteForeground()
                    syncLocksAndNotification()
                }
                return START_STICKY
            }
        }
    }

    fun startServer(key: String): ServerStatusSnapshot {
        promoteForeground()
        val status = coordinator.startServer(key)
        syncLocksAndNotification()
        return status
    }

    fun stopServer(key: String) {
        coordinator.stopServer(key)
        syncLocksAndNotification()
        if (!coordinator.isAnyBackgroundWorkActive()) {
            teardownForegroundAndLocks()
            stopSelf()
        }
    }

    fun startMobileNat(lanIface: String, cellIface: String, dnsOverride: String = ""): com.nathanneurotic.ps2servers.net.NatShareStatus {
        promoteForeground()
        val status = coordinator.startMobileNat(lanIface, cellIface, dnsOverride)
        syncLocksAndNotification()
        if (!coordinator.isAnyBackgroundWorkActive()) {
            teardownForegroundAndLocks()
            stopSelf()
        }
        return status
    }

    fun stopMobileNat() {
        coordinator.stopMobileNat()
        syncLocksAndNotification()
        if (!coordinator.isAnyBackgroundWorkActive()) {
            teardownForegroundAndLocks()
            stopSelf()
        }
    }

    fun stopAll() {
        coordinator.stopAll()
        teardownForegroundAndLocks()
        stopSelf()
    }

    override fun onDestroy() {
        coordinator.removeListener(coordinatorListener)
        coordinator.stopAll()
        teardownForegroundAndLocks()
        super.onDestroy()
    }

    private fun promoteForeground() {
        val notification = buildNotification()
        val fgsType = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
        } else {
            0
        }
        ServiceCompat.startForeground(this, NOTIFICATION_ID, notification, fgsType)
        foregroundStarted = true
    }

    private fun syncLocksAndNotification() {
        val anyActive = coordinator.isAnyBackgroundWorkActive()
        if (anyActive) {
            acquireWakeAndWifiLocks()
            updateMulticastLock(coordinator.needsMulticastLock())
            if (!foregroundStarted) {
                promoteForeground()
            } else {
                val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
                nm.notify(NOTIFICATION_ID, buildNotification())
            }
        } else {
            updateMulticastLock(false)
            releaseWakeAndWifiLocks()
        }
    }

    @SuppressLint("WakelockTimeout")
    private fun acquireWakeAndWifiLocks() {
        if (wakeLock?.isHeld != true) {
            val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "ps2servers:ServerWakeLock").apply {
                setReferenceCounted(false)
                acquire()
            }
        }
        if (wifiLock?.isHeld != true) {
            val wm = applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
            if (wm != null) {
                val mode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    WifiManager.WIFI_MODE_FULL_LOW_LATENCY
                } else {
                    @Suppress("DEPRECATION")
                    WifiManager.WIFI_MODE_FULL_HIGH_PERF
                }
                wifiLock = wm.createWifiLock(mode, "ps2servers:ServerWifiLock").apply {
                    setReferenceCounted(false)
                    acquire()
                }
            }
        }
    }

    private fun updateMulticastLock(needed: Boolean) {
        if (needed) {
            if (multicastLock?.isHeld != true) {
                val wm = applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
                if (wm != null) {
                    multicastLock = wm.createMulticastLock("ps2servers:MulticastLock").apply {
                        setReferenceCounted(false)
                        acquire()
                    }
                }
            }
        } else {
            if (multicastLock?.isHeld == true) {
                try {
                    multicastLock?.release()
                } catch (_: Exception) {
                }
            }
        }
    }

    private fun releaseWakeAndWifiLocks() {
        if (wakeLock?.isHeld == true) {
            try {
                wakeLock?.release()
            } catch (_: Exception) {
            }
        }
        if (wifiLock?.isHeld == true) {
            try {
                wifiLock?.release()
            } catch (_: Exception) {
            }
        }
    }

    private fun teardownForegroundAndLocks() {
        updateMulticastLock(false)
        releaseWakeAndWifiLocks()
        if (foregroundStarted) {
            ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
            foregroundStarted = false
        }
    }

    private fun ensureNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            val existing = nm.getNotificationChannel(CHANNEL_ID)
            if (existing == null) {
                val channel = NotificationChannel(
                    CHANNEL_ID,
                    getString(R.string.notification_channel_name),
                    NotificationManager.IMPORTANCE_LOW
                ).apply {
                    description = getString(R.string.notification_channel_desc)
                    setShowBadge(false)
                }
                nm.createNotificationChannel(channel)
            }
        }
    }

    private fun buildNotification(): Notification {
        val active = coordinator.statuses().filter { it.running }
        val cfg = coordinator.snapshotConfig()
        val chosenIp = LauncherConfig.asString(cfg["ip"]).trim()
        val displayIp = chosenIp.ifEmpty {
            NetworkAddressHelper.detectLanAddresses().firstOrNull()?.ipv4 ?: "0.0.0.0"
        }

        val nat = coordinator.natStatus()
        val parts = ArrayList<String>()
        if (active.isNotEmpty()) {
            parts.add(
                active.joinToString(", ") { s ->
                    val portLabel = if (s.redirect445Active) "445->${s.boundPort}" else "${s.boundPort ?: "?"}"
                    "${s.displayName}:$portLabel"
                }
            )
        }
        if (nat.active) {
            parts.add("NAT ${nat.lanIface}->${nat.cellularIface}")
        }
        val summary = if (parts.isEmpty()) {
            "Ready on $displayIp (no servers active)"
        } else {
            "IP $displayIp · ${parts.joinToString(" · ")}"
        }

        val openIntent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val immutableFlag = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            PendingIntent.FLAG_IMMUTABLE
        } else {
            0
        }
        val openPending = PendingIntent.getActivity(
            this,
            100,
            openIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or immutableFlag
        )
        val stopAllIntent = Intent(this, Ps2ServerService::class.java).apply {
            action = ACTION_STOP_ALL
        }
        val stopPending = PendingIntent.getService(
            this,
            101,
            stopAllIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or immutableFlag
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_upload_done)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(summary)
            .setStyle(NotificationCompat.BigTextStyle().bigText(summary))
            .setOngoing(active.isNotEmpty() || nat.active)
            .setOnlyAlertOnce(true)
            .setContentIntent(openPending)
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, getString(R.string.stop_all), stopPending)
            .build()
    }

    companion object {
        const val CHANNEL_ID = "ps2_servers_foreground"
        const val NOTIFICATION_ID = 33901
        const val ACTION_STOP_ALL = "com.nathanneurotic.ps2servers.action.STOP_ALL"
        const val ACTION_RESTORE = "com.nathanneurotic.ps2servers.action.RESTORE"
    }
}
