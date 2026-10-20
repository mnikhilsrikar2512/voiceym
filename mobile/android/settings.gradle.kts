pluginManagement { repositories { google(); mavenCentral(); gradlePluginPortal() } }
dependencyResolutionManagement { repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS); repositories { google(); mavenCentral() } }
rootProject.name = "VoiceymMobile"
include(":app", ":core:designsystem", ":core:network", ":core:data", ":core:l10n", ":features")
