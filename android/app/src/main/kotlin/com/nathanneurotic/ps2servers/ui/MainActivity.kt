package com.nathanneurotic.ps2servers.ui

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.text.Editable
import android.text.InputType
import android.text.TextWatcher
import android.view.View
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.google.android.material.card.MaterialCardView
import com.google.android.material.switchmaterial.SwitchMaterial
import com.nathanneurotic.ps2servers.R
import com.nathanneurotic.ps2servers.config.LauncherConfig
import com.nathanneurotic.ps2servers.net.NetworkAddressCandidate
import com.nathanneurotic.ps2servers.net.NetworkAddressHelper
import com.nathanneurotic.ps2servers.net.OplHintFormatter
import com.nathanneurotic.ps2servers.service.AndroidSafBridge
import com.nathanneurotic.ps2servers.service.Ps2ServerService
import com.nathanneurotic.ps2servers.service.ServerCoordinator
import com.nathanneurotic.ps2servers.service.ServerStatusSnapshot
import java.io.File
import java.util.concurrent.Executors

class MainActivity : AppCompatActivity() {

    private var serverService: Ps2ServerService? = null
    private var serviceBound = false
    private lateinit var localCoordinator: ServerCoordinator
    private val bgExecutor = Executors.newSingleThreadExecutor()

    private lateinit var ipSpinner: Spinner
    private lateinit var customIpInput: EditText
    private lateinit var activeIpBanner: TextView
    private lateinit var rootSummaryText: TextView
    private lateinit var fallbackPathInput: EditText
    private lateinit var serverCardsContainer: LinearLayout
    private lateinit var logTextView: TextView
    private lateinit var logScrollView: ScrollView

    private var detectedCandidates: List<NetworkAddressCandidate> = emptyList()
    private val expandedCards = HashSet<String>()
    private val cardViews = LinkedHashMap<String, CardRefs>()
    private var updatingUi = false

    private data class CardRefs(
        val switchControl: SwitchMaterial,
        val portBadge: TextView,
        val hintText: TextView,
        val redirectBadge: TextView,
        val settingsToggle: Button,
        val settingsBody: LinearLayout
    )

    private val coordinator: ServerCoordinator
        get() = serverService?.coordinator ?: localCoordinator

    private val serviceListener = object : ServerCoordinator.Listener {
        override fun onStateChanged() {
            runOnUiThread { refreshUi() }
        }

        override fun onLogAppended(line: String) {
            runOnUiThread { appendLogToView() }
        }
    }

    private val serviceConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, service: IBinder?) {
            val binder = service as? Ps2ServerService.LocalBinder ?: return
            localCoordinator.removeListener(serviceListener)
            serverService = binder.service
            serviceBound = true
            binder.service.coordinator.addListener(serviceListener)
            refreshUi()
            appendLogToView()
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            serverService = null
            serviceBound = false
        }
    }

    private val safTreePicker = registerForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri == null) return@registerForActivityResult
        AndroidSafBridge.persistTreePermission(this, uri, wantWrite = true)
        coordinator.updateTopLevel("android_saf_tree_uri", uri.toString())
        coordinator.appendLog("Selected SAF Games Folder: $uri")
        refreshUi()
    }

    private val udpbdImagePicker = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri == null) return@registerForActivityResult
        AndroidSafBridge.persistDocumentPermission(this, uri, wantWrite = true)
        coordinator.updateTopLevel("android_udpbd_image_uri", uri.toString())
        coordinator.updateTopLevel("android_udpbd_mode", "image_file")
        coordinator.appendLog("Selected UDPBD raw image: $uri")
        rebuildServerCards()
        refreshUi()
    }

    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (!granted) {
            Toast.makeText(
                this,
                "Notification permission keeps servers visible when backgrounded.",
                Toast.LENGTH_LONG
            ).show()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        localCoordinator = ServerCoordinator(
            configFile = ServerCoordinator.defaultConfigFile(applicationContext),
            safBridge = AndroidSafBridge(applicationContext)
        )
        localCoordinator.addListener(serviceListener)

        ipSpinner = findViewById(R.id.spinnerIp)
        customIpInput = findViewById(R.id.editCustomIp)
        activeIpBanner = findViewById(R.id.textActiveIpBanner)
        rootSummaryText = findViewById(R.id.textRootSummary)
        fallbackPathInput = findViewById(R.id.editFallbackPath)
        serverCardsContainer = findViewById(R.id.containerServers)
        logTextView = findViewById(R.id.textLogs)
        logScrollView = findViewById(R.id.scrollLogs)

        bindHeaderControls()
        rebuildServerCards()
        refreshNetworkChoices()
        refreshUi()
        appendLogToView()

        val bindIntent = Intent(this, Ps2ServerService::class.java)
        bindService(bindIntent, serviceConnection, Context.BIND_AUTO_CREATE)
    }

    override fun onDestroy() {
        localCoordinator.removeListener(serviceListener)
        serverService?.coordinator?.removeListener(serviceListener)
        if (serviceBound) {
            unbindService(serviceConnection)
            serviceBound = false
        }
        bgExecutor.shutdownNow()
        super.onDestroy()
    }

    private fun bindHeaderControls() {
        findViewById<Button>(R.id.btnRefreshIp).setOnClickListener {
            refreshNetworkChoices()
            refreshUi()
        }
        customIpInput.addTextChangedListener(SimpleAfterTextWatcher { text ->
            if (!updatingUi) {
                val customIp = text.trim()
                if (ipSpinner.selectedItemPosition == detectedCandidates.size) {
                    coordinator.updateTopLevel("ip_custom", true)
                    coordinator.updateTopLevel("ip", customIp)
                }
                updateIpBannerAndHints()
            }
        })

        findViewById<Button>(R.id.btnPickSafFolder).setOnClickListener {
            safTreePicker.launch(null)
        }
        findViewById<Button>(R.id.btnClearSafFolder).setOnClickListener {
            coordinator.updateTopLevel("android_saf_tree_uri", "")
            refreshUi()
        }
        findViewById<Button>(R.id.btnUseAppFolder).setOnClickListener {
            val base = getExternalFilesDir(null) ?: filesDir
            val gamesDir = File(base, "PS2Games")
            gamesDir.mkdirs()
            fallbackPathInput.setText(gamesDir.absolutePath)
            coordinator.updateTopLevel("android_fallback_path", gamesDir.absolutePath)
            refreshUi()
        }
        findViewById<Button>(R.id.btnCheckFolder).setOnClickListener {
            val path = fallbackPathInput.text.toString().trim()
            coordinator.updateTopLevel("android_fallback_path", path)
            val status = coordinator.describeActiveRoot()
            coordinator.appendLog("Folder check -> $status")
            Toast.makeText(this, status, Toast.LENGTH_SHORT).show()
            refreshUi()
        }
        fallbackPathInput.addTextChangedListener(SimpleAfterTextWatcher { text ->
            if (!updatingUi) {
                coordinator.updateTopLevel("android_fallback_path", text.trim())
            }
        })

        findViewById<Button>(R.id.btnStopAll).setOnClickListener {
            bgExecutor.execute {
                val svc = serverService
                if (svc != null) svc.stopAll() else coordinator.stopAll()
            }
        }
        findViewById<Button>(R.id.btnResetDefaults).setOnClickListener {
            AlertDialog.Builder(this)
                .setTitle("Reset Settings")
                .setMessage("Stop active servers and restore desktop-aligned defaults?")
                .setPositiveButton("Reset") { _, _ ->
                    bgExecutor.execute {
                        coordinator.resetConfig()
                        runOnUiThread {
                            rebuildServerCards()
                            refreshNetworkChoices()
                            refreshUi()
                        }
                    }
                }
                .setNegativeButton("Cancel", null)
                .show()
        }
        findViewById<Button>(R.id.btnClearLogs).setOnClickListener {
            coordinator.clearLogs()
            appendLogToView()
        }
    }

    private fun refreshNetworkChoices() {
        detectedCandidates = NetworkAddressHelper.detectLanAddresses()
        val labels = detectedCandidates.map { it.summary() }.toMutableList()
        labels.add("Custom IP…")
        val adapter = ArrayAdapter(this, android.R.layout.simple_spinner_item, labels)
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)

        val cfg = coordinator.snapshotConfig()
        val savedIp = LauncherConfig.asString(cfg["ip"]).trim()
        val isCustomIp = LauncherConfig.asBool(cfg["ip_custom"], false)

        updatingUi = true
        ipSpinner.adapter = adapter
        val idx = detectedCandidates.indexOfFirst { it.ipv4 == savedIp }
        if (!isCustomIp && idx >= 0) {
            ipSpinner.setSelection(idx)
            customIpInput.setText("")
            customIpInput.visibility = View.GONE
        } else if (isCustomIp || (savedIp.isNotEmpty() && idx < 0)) {
            ipSpinner.setSelection(detectedCandidates.size)
            customIpInput.setText(savedIp)
            customIpInput.visibility = View.VISIBLE
        } else if (detectedCandidates.isNotEmpty()) {
            ipSpinner.setSelection(0)
            customIpInput.setText("")
            customIpInput.visibility = View.GONE
            coordinator.updateTopLevel("ip_custom", false)
            coordinator.updateTopLevel("ip", detectedCandidates[0].ipv4)
        } else {
            ipSpinner.setSelection(0)
            customIpInput.setText(savedIp)
            customIpInput.visibility = View.VISIBLE
        }
        updatingUi = false

        ipSpinner.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                if (updatingUi) return
                if (position < detectedCandidates.size) {
                    customIpInput.visibility = View.GONE
                    coordinator.updateTopLevel("ip_custom", false)
                    coordinator.updateTopLevel("ip", detectedCandidates[position].ipv4)
                } else {
                    customIpInput.visibility = View.VISIBLE
                    coordinator.updateTopLevel("ip_custom", true)
                    coordinator.updateTopLevel("ip", customIpInput.text.toString().trim())
                }
                updateIpBannerAndHints()
            }

            override fun onNothingSelected(parent: AdapterView<*>?) = Unit
        }
    }

    private fun currentDisplayIp(cfg: Map<String, Any?>): String {
        val chosen = LauncherConfig.asString(cfg["ip"]).trim()
        if (chosen.isNotEmpty()) return chosen
        return detectedCandidates.firstOrNull()?.ipv4 ?: "0.0.0.0"
    }

    private fun rebuildServerCards() {
        serverCardsContainer.removeAllViews()
        cardViews.clear()
        val cfg = coordinator.snapshotConfig()

        for (serverKey in LauncherConfig.SERVER_KEYS) {
            val card = MaterialCardView(this).apply {
                radius = dp(10).toFloat()
                cardElevation = dp(2).toFloat()
                useCompatPadding = true
                val lp = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT
                )
                lp.bottomMargin = dp(8)
                layoutParams = lp
            }
            val inner = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(14), dp(12), dp(14), dp(12))
            }

            // Header row: Name + Port badge + Switch
            val headerRow = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = android.view.Gravity.CENTER_VERTICAL
            }
            val titleCol = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            }
            val titleText = TextView(this).apply {
                text = ServerCoordinator.displayNameFor(serverKey)
                textSize = 17f
                setTypeface(null, android.graphics.Typeface.BOLD)
            }
            val portBadge = TextView(this).apply {
                textSize = 13f
                setTextColor(ContextCompat.getColor(context, android.R.color.darker_gray))
            }
            titleCol.addView(titleText)
            titleCol.addView(portBadge)

            val switchControl = SwitchMaterial(this)
            switchControl.setOnCheckedChangeListener { _, isChecked ->
                if (!updatingUi) {
                    toggleServer(serverKey, isChecked)
                }
            }

            headerRow.addView(titleCol)
            headerRow.addView(switchControl)
            inner.addView(headerRow)

            val redirectBadge = TextView(this).apply {
                textSize = 12f
                visibility = View.GONE
            }
            inner.addView(redirectBadge)

            val hintText = TextView(this).apply {
                textSize = 12f
                setPadding(0, dp(4), 0, dp(4))
            }
            inner.addView(hintText)

            val settingsBody = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                visibility = if (expandedCards.contains(serverKey)) View.VISIBLE else View.GONE
                setPadding(0, dp(6), 0, 0)
            }
            val settingsToggle = Button(this, null, com.google.android.material.R.attr.borderlessButtonStyle).apply {
                text = if (expandedCards.contains(serverKey)) "Hide Settings" else "Settings"
                setOnClickListener {
                    if (expandedCards.contains(serverKey)) {
                        expandedCards.remove(serverKey)
                        settingsBody.visibility = View.GONE
                        text = "Settings"
                    } else {
                        expandedCards.add(serverKey)
                        settingsBody.visibility = View.VISIBLE
                        text = "Hide Settings"
                    }
                }
            }
            inner.addView(settingsToggle)

            populateSettingsControls(serverKey, cfg, settingsBody)
            inner.addView(settingsBody)

            card.addView(inner)
            serverCardsContainer.addView(card)
            cardViews[serverKey] = CardRefs(
                switchControl = switchControl,
                portBadge = portBadge,
                hintText = hintText,
                redirectBadge = redirectBadge,
                settingsToggle = settingsToggle,
                settingsBody = settingsBody
            )
        }
    }

    private fun populateSettingsControls(
        serverKey: String,
        cfg: Map<String, Any?>,
        container: LinearLayout
    ) {
        val settings = cfg[serverKey] as? Map<*, *> ?: emptyMap<Any?, Any?>()

        fun addIntField(label: String, settingKey: String, defaultVal: Int) {
            val row = labeledEditRow(
                label = label,
                initial = LauncherConfig.asInt(settings[settingKey], defaultVal).toString(),
                numeric = true
            ) { text ->
                val parsed = text.toIntOrNull()
                if (parsed != null && parsed in 0..65535) {
                    coordinator.updateServerSetting(serverKey, settingKey, parsed)
                    updateIpBannerAndHints()
                }
            }
            container.addView(row)
        }

        fun addTextField(label: String, settingKey: String, defaultVal: String = "") {
            val row = labeledEditRow(
                label = label,
                initial = LauncherConfig.asString(settings[settingKey], defaultVal),
                numeric = false
            ) { text ->
                coordinator.updateServerSetting(serverKey, settingKey, text)
                updateIpBannerAndHints()
            }
            container.addView(row)
        }

        fun addCheckBox(label: String, settingKey: String, defaultVal: Boolean) {
            val cb = CheckBox(this).apply {
                text = label
                isChecked = LauncherConfig.asBool(settings[settingKey], defaultVal)
                setOnCheckedChangeListener { _, checked ->
                    if (!updatingUi) {
                        coordinator.updateServerSetting(serverKey, settingKey, checked)
                        updateIpBannerAndHints()
                    }
                }
            }
            container.addView(cb)
        }

        when (serverKey) {
            "udpfs" -> {
                addIntField("Control Port", "port", 62966)
                addIntField("Data Port (0=auto)", "data_port", 0)
                container.addView(
                    addSpinnerRow(
                        label = "Protocol Mode",
                        choices = LauncherConfig.PROTOCOL_MODE_CHOICES.map { it.first to it.first },
                        currentValue = LauncherConfig.asString(settings["protocol_mode"], "Auto (recommended)")
                    ) { chosen ->
                        coordinator.updateServerSetting(serverKey, "protocol_mode", chosen)
                    }
                )
                addCheckBox("Enable CSO/ZSO Compression", "enable_compression", true)
                addCheckBox("Read-Only Share", "read_only", false)
                addCheckBox("Verbose Logging", "verbose", false)
                addTextField("Bind IP Override", "bind", "")
            }
            "smbv1" -> {
                addTextField("Share Name", "share_name", "games")
                addIntField("Port (unrooted >= 1024)", "port", 1025)
                addTextField("Username", "username", "guest")
                addTextField("Password", "password", "")
                addCheckBox("Redirect TCP 445 -> Port (requires root)", "port_445_redirect", false)
                addCheckBox("Read-Only Share", "read_only", false)
                addCheckBox("Enable CSO/ZSO Compression", "enable_compression", false)
                addCheckBox("Verbose Logging", "verbose", false)
            }
            "smbv2", "smbv3" -> {
                addTextField("Share Name", "share_name", "games")
                addIntField("Port (unrooted >= 1024)", "port", 1445)
                addTextField("Username", "username", "ps2")
                addTextField("Password", "password", "")
                addCheckBox("Open Share (allow anonymous)", "open_share", false)
                addCheckBox("Redirect TCP 445 -> Port (requires root)", "port_445_redirect", false)
                addCheckBox("Read-Only Share", "read_only", false)
                addCheckBox("Verbose Logging", "verbose", false)
            }
            "http" -> {
                addIntField("Port", "port", 1100)
                container.addView(
                    addSpinnerRow(
                        label = "Catalog Profile",
                        choices = LauncherConfig.HTTP_CATALOG_PROFILE_CHOICES.map { it.first to it.first },
                        currentValue = LauncherConfig.asString(
                            settings["catalog_profile"],
                            LauncherConfig.HTTP_CATALOG_PROFILE_CHOICES.first().first
                        )
                    ) { chosen ->
                        coordinator.updateServerSetting(serverKey, "catalog_profile", chosen)
                    }
                )
                addCheckBox("Enable CSO/ZSO Compression", "enable_compression", true)
                addCheckBox("Recursive Directory Scan", "recursive", false)
                addCheckBox("Verbose Logging", "verbose", false)
            }
            "nfs" -> {
                addIntField("Port", "port", 2049)
                addCheckBox("Allow Writes", "allow_writes", false)
                addCheckBox("Verbose Logging", "verbose", true)
                addTextField("Bind IP Override", "bind", "")
            }
            "udpbd" -> {
                val modeChoices = listOf(
                    "virtual_exfat" to "Virtual exFAT from Games Folder (no image needed)",
                    "image_file" to "Raw exFAT Disk Image (.iso / .img)"
                )
                container.addView(
                    addSpinnerRow(
                        label = "Source Mode",
                        choices = modeChoices,
                        currentValue = LauncherConfig.asString(cfg["android_udpbd_mode"], "virtual_exfat")
                    ) { chosen ->
                        coordinator.updateTopLevel("android_udpbd_mode", chosen)
                    }
                )
                val pickImgBtn = Button(this).apply {
                    text = "Pick Raw Image (SAF)"
                    setOnClickListener {
                        udpbdImagePicker.launch(arrayOf("*/*"))
                    }
                }
                container.addView(pickImgBtn)
                val currentUri = LauncherConfig.asString(cfg["android_udpbd_image_uri"]).trim()
                if (currentUri.isNotEmpty()) {
                    val uriLabel = TextView(this).apply {
                        text = "SAF Image URI: $currentUri"
                        textSize = 11f
                    }
                    container.addView(uriLabel)
                }
                addTextField("Fallback Raw Image Path", "image_file", "")
                addCheckBox("Read-Only", "read_only", false)
                addCheckBox("Verbose Logging", "verbose", true)
            }
        }
    }

    private fun labeledEditRow(
        label: String,
        initial: String,
        numeric: Boolean,
        onCommit: (String) -> Unit
    ): View {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = android.view.Gravity.CENTER_VERTICAL
        }
        val tv = TextView(this).apply {
            text = label
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        }
        val et = EditText(this).apply {
            setText(initial)
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            if (numeric) inputType = InputType.TYPE_CLASS_NUMBER
            addTextChangedListener(SimpleAfterTextWatcher { text ->
                if (!updatingUi) onCommit(text)
            })
        }
        row.addView(tv)
        row.addView(et)
        return row
    }

    private fun addSpinnerRow(
        label: String,
        choices: List<Pair<String, String>>,
        currentValue: String,
        onSelect: (String) -> Unit
    ): View {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, dp(4), 0, dp(4))
        }
        val tv = TextView(this).apply { text = label }
        val spinner = Spinner(this)
        val labels = choices.map { it.second }
        val adapter = ArrayAdapter(this, android.R.layout.simple_spinner_item, labels)
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
        spinner.adapter = adapter
        val idx = choices.indexOfFirst { it.first == currentValue }.coerceAtLeast(0)
        spinner.setSelection(idx)
        spinner.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                if (!updatingUi) {
                    onSelect(choices[position].first)
                }
            }

            override fun onNothingSelected(parent: AdapterView<*>?) = Unit
        }
        row.addView(tv)
        row.addView(spinner)
        return row
    }

    private fun toggleServer(serverKey: String, turnOn: Boolean) {
        if (turnOn) {
            ensureNotificationPermission()
            val intent = Intent(this, Ps2ServerService::class.java)
            ContextCompat.startForegroundService(this, intent)
        }
        bgExecutor.execute {
            try {
                val svc = serverService
                if (turnOn) {
                    if (svc != null) svc.startServer(serverKey) else coordinator.startServer(serverKey)
                } else {
                    if (svc != null) svc.stopServer(serverKey) else coordinator.stopServer(serverKey)
                }
            } catch (e: Exception) {
                val msg = "${ServerCoordinator.displayNameFor(serverKey)}: ${e.message ?: e::class.java.simpleName}"
                coordinator.appendLog("Start error -> $msg")
                runOnUiThread {
                    Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
                    refreshUi()
                }
            }
        }
    }

    private fun ensureNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val granted = ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.POST_NOTIFICATIONS
            ) == PackageManager.PERMISSION_GRANTED
            if (!granted) {
                notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
    }

    private fun refreshUi() {
        val cfg = coordinator.snapshotConfig()
        val statuses = coordinator.statuses().associateBy { it.key }

        updatingUi = true
        val savedFallback = LauncherConfig.asString(cfg["android_fallback_path"])
        if (fallbackPathInput.text.toString() != savedFallback) {
            fallbackPathInput.setText(savedFallback)
        }
        rootSummaryText.text = coordinator.describeActiveRoot()

        updateCardStatuses(cfg, statuses)
        updatingUi = false
    }

    private fun updateIpBannerAndHints() {
        val cfg = coordinator.snapshotConfig()
        val statuses = coordinator.statuses().associateBy { it.key }
        updatingUi = true
        updateCardStatuses(cfg, statuses)
        updatingUi = false
    }

    private fun updateCardStatuses(
        cfg: Map<String, Any?>,
        statuses: Map<String, ServerStatusSnapshot>
    ) {
        val displayIp = currentDisplayIp(cfg)
        val running = statuses.values.filter { it.running }
        activeIpBanner.text = if (running.isEmpty()) {
            "Device IP: $displayIp · Stopped"
        } else {
            val list = running.joinToString("  |  ") { s ->
                val p = if (s.redirect445Active) "445->${s.boundPort}" else "${s.boundPort ?: "?"}"
                "${s.displayName}:$p"
            }
            "Device IP: $displayIp · Active: $list"
        }

        for (key in LauncherConfig.SERVER_KEYS) {
            val refs = cardViews[key] ?: continue
            val snap = statuses[key]
            val settings = cfg[key] as? Map<*, *> ?: emptyMap<Any?, Any?>()
            @Suppress("UNCHECKED_CAST")
            val typedSettings = settings as Map<String, Any?>

            refs.switchControl.isChecked = snap?.running == true
            val defaultPort = when (key) {
                "udpfs" -> 62966
                "smbv1" -> 1025
                "smbv2", "smbv3" -> 1445
                "http" -> 1100
                "nfs" -> 2049
                "udpbd" -> 48573
                else -> 0
            }
            val shownPort = snap?.boundPort ?: LauncherConfig.asInt(typedSettings["port"], defaultPort)
            val proto = if (key == "udpfs" || key == "udpbd") "UDP" else "TCP"
            refs.portBadge.text = if (snap?.running == true) {
                "Running on $proto $shownPort"
            } else {
                "Stopped ($proto $shownPort)"
            }
            if (snap?.redirectMessage != null && snap.running) {
                refs.redirectBadge.visibility = View.VISIBLE
                refs.redirectBadge.text = snap.redirectMessage
            } else {
                refs.redirectBadge.visibility = View.GONE
            }
            refs.hintText.text = OplHintFormatter.formatHint(
                serverKey = key,
                ip = displayIp,
                settings = typedSettings,
                boundPort = snap?.boundPort
            )
        }
    }

    private fun appendLogToView() {
        val lines = coordinator.logsSnapshot()
        logTextView.text = if (lines.isEmpty()) "No server activity yet." else lines.joinToString("\n")
        logScrollView.post { logScrollView.fullScroll(View.FOCUS_DOWN) }
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    private class SimpleAfterTextWatcher(private val onAfter: (String) -> Unit) : TextWatcher {
        override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
        override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = Unit
        override fun afterTextChanged(s: Editable?) {
            onAfter(s?.toString().orEmpty())
        }
    }
}
