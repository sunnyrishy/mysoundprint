plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.mysoundprint.glasses"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.mysoundprint.glasses"
        minSdk = 24 // the glasses run Android 9 (API 28), so anything at or below 28 installs
        targetSdk = 28
        versionCode = 1
        versionName = "0.1"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    // JVM unit tests only. Android's own org.json is a stub there, so use the real library.
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
