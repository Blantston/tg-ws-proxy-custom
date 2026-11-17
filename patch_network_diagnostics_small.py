#!/usr/bin/env python3
from pathlib import Path
import sys
root = Path(sys.argv[1])
base = Path(__file__).with_name("NetworkDiagnostics.kt")
dest = root / "app/src/main/java/com/amurcanov/tgwsproxy/NetworkDiagnostics.kt"
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_text(base.read_text(encoding="utf-8"), encoding="utf-8")
ui = root / "app/src/main/java/com/amurcanov/tgwsproxy/ui/LogsTab.kt"
s = ui.read_text(encoding="utf-8")
if "import com.amurcanov.tgwsproxy.NetworkDiagnostics" not in s:
    s = s.replace("import com.amurcanov.tgwsproxy.LogManager\n", "import com.amurcanov.tgwsproxy.LogManager\nimport com.amurcanov.tgwsproxy.NetworkDiagnostics\nimport android.util.Log\n")
if "var diagnosticsRunning by remember" not in s:
    s = s.replace("    val scope = rememberCoroutineScope()\n", "    val scope = rememberCoroutineScope()\n    var diagnosticsRunning by remember { mutableStateOf(false) }\n")
anchor = """            }
        }

        Row(
            modifier = Modifier.fillMaxWidth().padding(bottom = 12.dp),
"""
button = """                TextButton(onClick = {
                    scope.launch {
                        diagnosticsRunning = true
                        Log.i(\"TgWsProxy\", \"[DIAG] User started on-device network diagnostics\")
                        try { NetworkDiagnostics.run(context) }
                        catch (e: Exception) { Log.w(\"TgWsProxy\", \"[DIAG] Diagnostics failed: \" + e.javaClass.simpleName + \": \" + e.message) }
                        finally { diagnosticsRunning = false }
                    }
                }, enabled = !diagnosticsRunning) {
                    Text(if (diagnosticsRunning) \"Тест…\" else \"Тест сети\")
                }
            }
        }

        Row(
            modifier = Modifier.fillMaxWidth().padding(bottom = 12.dp),
"""
if "Тест сети" not in s:
    if s.count(anchor) != 1: raise SystemExit("LogsTab header-action anchor mismatch")
    s = s.replace(anchor, button, 1)
ui.write_text(s, encoding="utf-8")
manifest = root / "app/src/main/AndroidManifest.xml"
s = manifest.read_text(encoding="utf-8")
permission = '    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />\n'
if permission not in s:
    anchor = '    <uses-permission android:name="android.permission.INTERNET" />\n'
    if s.count(anchor) != 1: raise SystemExit("Manifest anchor mismatch")
    s = s.replace(anchor, anchor + permission, 1)
manifest.write_text(s, encoding="utf-8")
print("Added on-device DNS/TCP/TLS/HTTP diagnostics UI")