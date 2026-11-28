import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

fun readDesktopVersion(): Pair<String, Int> {
    val fallbackVersion = "0.9.7"
    val fallbackCode = 907
    val metaFile = rootProject.file("../launcher/release_metadata.py")
    if (!metaFile.isFile) return fallbackVersion to fallbackCode
    val text = metaFile.readText(Charsets.UTF_8)
    val product = Regex("""PRODUCT_VERSION\s*=\s*["']([^"']+)["']""")
        .find(text)?.groupValues?.getOrNull(1)?.trim() ?: fallbackVersion
    val qualifier = Regex("""VERSION_QUALIFIER\s*=\s*["']([^"']*)["']""")
        .find(text)?.groupValues?.getOrNull(1)?.trim() ?: ""
    val parts = product.split(".")
    val code = if (parts.size >= 3) {
        val major = parts[0].toIntOrNull() ?: 0
        val minor = parts[1].toIntOrNull() ?: 0
        val patch = parts[2].toIntOrNull() ?: 0
        major * 10000 + minor * 100 + patch
    } else {
        fallbackCode
    }
    return (product + qualifier) to code
}

val (appVersionName, appVersionCode) = readDesktopVersion()

android {
    namespace = "com.nathanneurotic.ps2servers"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.nathanneurotic.ps2servers"
        minSdk = 21
        targetSdk = 35
        versionCode = appVersionCode
        versionName = appVersionName
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_1_8
        targetCompatibility = JavaVersion.VERSION_1_8
    }

    kotlinOptions {
        jvmTarget = "1.8"
    }

    lint {
        abortOnError = true
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.12.0")
    implementation("androidx.appcompat:appcompat:1.6.1")
    implementation("com.google.android.material:material:1.11.0")
    implementation("androidx.documentfile:documentfile:1.0.1")
    implementation("androidx.activity:activity-ktx:1.8.2")

    testImplementation("junit:junit:4.13.2")
}
