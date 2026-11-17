package com.amurcanov.tgwsproxy

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.nio.charset.StandardCharsets
import java.security.SecureRandom
import java.security.cert.X509Certificate
import java.util.concurrent.Callable
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.SNIHostName
import javax.net.ssl.SSLSocket
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

/** Diagnostic-only TLS handshake probe; never used by the proxy connection. */
object NetworkDiagnostics {
    private const val TAG = "TgWsProxy"
    private const val TCP_MS = 2200
    private const val IO_MS = 3000
    private val httpPorts = listOf(80, 8080, 8880, 2052, 2082, 2086, 2095)
    private val httpsPorts = listOf(443, 2053, 2083, 2087, 2096, 8443)
    private val hosts = listOf("kws2.pclead.co.uk", "kws2.ebally.co.uk", "kws2.nothingthere.co.uk", "kws2.pyatdesyatodin.co.uk", "www.cloudflare.com")
    private val dcs = linkedMapOf("DC1" to "149.154.175.50", "DC2" to "149.154.167.51", "DC3" to "149.154.175.100", "DC4" to "149.154.167.91", "DC5" to "149.154.171.5")
    private val tlsContext: SSLContext by lazy {
        val tm = object : X509TrustManager {
            override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
            override fun checkClientTrusted(c: Array<X509Certificate>, a: String) = Unit
            override fun checkServerTrusted(c: Array<X509Certificate>, a: String) = Unit
        }
        SSLContext.getInstance("TLS").apply { init(null, arrayOf<TrustManager>(tm), SecureRandom()) }
    }
    private fun log(s: String) = Log.i(TAG, "[DIAG] " + s)
    private fun error(e: Exception) = e.javaClass.simpleName + ": " + (e.message ?: "no detail").replace('\n', ' ').take(70)
    private fun elapsed(t: Long) = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - t)

    suspend fun run(context: Context) = withContext(Dispatchers.IO) {
        val pool = Executors.newFixedThreadPool(20)
        try {
            log("========== NETWORK TEST START ==========")
            log("Active network: " + networkType(context))
            log("ICMP ping skipped: mobile carriers often suppress it; TCP/TLS/HTTP are more useful.")
            val dns = linkedMapOf<String, List<String>>()
            hosts.forEach { host ->
                val f = pool.submit(Callable { try { InetAddress.getAllByName(host).map { it.hostAddress.substringBefore('%') }.distinct().take(2) } catch (_: Exception) { emptyList<String>() } })
                val ips = try { f.get(7, TimeUnit.SECONDS) } catch (_: Exception) { emptyList() }
                dns[host] = ips
                log(if (ips.isEmpty()) "DNS " + host + " FAILED/TIMEOUT" else "DNS " + host + " -> " + ips.joinToString(","))
            }
            val targets = hosts.dropLast(1).flatMap { h -> dns[h].orEmpty().map { h to it } }
            val actualTargets = if (targets.isNotEmpty()) targets else listOf(hosts.first() to "104.21.80.254", hosts.first() to "172.67.155.165")
            if (targets.isEmpty()) log("Using previous CF IPs as stale fallback references only")
            val jobs = mutableListOf<Callable<String>>()
            actualTargets.forEach { (h, ip) ->
                jobs += Callable { tcp(h, ip, 80) }
                jobs += Callable { tcp(h, ip, 443) }
                jobs += Callable { http(h, ip, 80) }
                jobs += Callable { tls(h, ip, 443, "CF") }
            }
            val first = actualTargets.first()
            (httpPorts - 80).forEach { p ->
                jobs += Callable { tcp(first.first, first.second, p) }
                jobs += Callable { http(first.first, first.second, p) }
            }
            (httpsPorts - 443).forEach { p ->
                jobs += Callable { tcp(first.first, first.second, p) }
                jobs += Callable { tls(first.first, first.second, p, "CF-alt") }
            }
            dns[hosts.last()].orEmpty().firstOrNull()?.let { ip ->
                jobs += Callable { tls(hosts.last(), ip, 443, "CF-control") }
                jobs += Callable { http(hosts.last(), ip, 443, true) }
            }
            dcs.forEach { (dc, ip) -> jobs += Callable { tcp(dc, ip, 443) } }
            val dc2 = dcs.getValue("DC2")
            jobs += Callable { tls("kws2.web.telegram.org", dc2, 443, "Telegram-DC2") }
            jobs += Callable { tls("sprinthost.ru", dc2, 443, "Telegram-DC2-front-SNI") }
            log("Running " + jobs.size + " TCP/TLS/HTTP checks")
            jobs.map { pool.submit(it) }.forEachIndexed { i, f ->
                val result = try { f.get(12, TimeUnit.SECONDS) } catch (_: Exception) { "Probe " + (i + 1) + " TIMEOUT" }
                log(result)
            }
            log("CF HTTP ports: " + httpPorts.joinToString() + "; HTTPS ports: " + httpsPorts.joinToString())
            log("TCP OPEN=socket connected; TLS OK=handshake completed; HTTP status=server replied")
            log("TLS probe ignores certificate validation for diagnosis only; proxy security is unchanged.")
            log("========== NETWORK TEST END ==========")
        } finally { pool.shutdownNow() }
    }

    private fun networkType(c: Context): String {
        if (Build.VERSION.SDK_INT < 23) return "API<23"
        return try {
            val m = c.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
            val n = m.activeNetwork ?: return "no active network"
            val x = m.getNetworkCapabilities(n) ?: return "no capabilities"
            val names = mutableListOf<String>()
            if (x.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) names += "CELLULAR"
            if (x.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) names += "WIFI"
            if (x.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) names += "VPN"
            names.joinToString("+") + "; validated=" + x.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
        } catch (e: Exception) { "unknown " + error(e) }
    }
    private fun tcp(host: String, ip: String, port: Int): String {
        val t = System.nanoTime()
        return try { Socket().use { it.connect(InetSocketAddress(ip, port), TCP_MS) }; "TCP $host $ip:$port OPEN " + elapsed(t) + "ms" }
        catch (e: Exception) { "TCP $host $ip:$port FAIL " + elapsed(t) + "ms " + error(e) }
    }
    private fun tls(host: String, ip: String, port: Int, label: String): String {
        val t = System.nanoTime(); var s: Socket? = null
        return try {
            val raw = Socket().apply { connect(InetSocketAddress(ip, port), TCP_MS); soTimeout = IO_MS }; s = raw
            val ssl = tlsContext.socketFactory.createSocket(raw, host, port, true) as SSLSocket; s = ssl
            ssl.soTimeout = IO_MS
            ssl.sslParameters = ssl.sslParameters.apply { serverNames = listOf(SNIHostName(host)) }
            ssl.startHandshake()
            "TLS $label $ip:$port SNI=$host OK " + ssl.session.protocol + " " + elapsed(t) + "ms"
        } catch (e: Exception) { "TLS $label $ip:$port SNI=$host FAIL " + elapsed(t) + "ms " + error(e) }
        finally { try { s?.close() } catch (_: Exception) { } }
    }
    private fun http(host: String, ip: String, port: Int, secure: Boolean = false): String {
        val t = System.nanoTime(); var s: Socket? = null
        val scheme = if (secure) "HTTPS" else "HTTP"
        return try {
            val raw = Socket().apply { connect(InetSocketAddress(ip, port), TCP_MS); soTimeout = IO_MS }; s = raw
            val active: Socket = if (secure) (tlsContext.socketFactory.createSocket(raw, host, port, true) as SSLSocket).apply {
                soTimeout = IO_MS
                sslParameters = sslParameters.apply { serverNames = listOf(SNIHostName(host)) }
                startHandshake()
            } else raw
            s = active
            val request = "GET /cdn-cgi/trace HTTP/1.1\r\nHost: " + host + "\r\nConnection: close\r\n\r\n"
            active.getOutputStream().write(request.toByteArray(StandardCharsets.US_ASCII))
            val line = BufferedReader(InputStreamReader(active.getInputStream(), StandardCharsets.US_ASCII)).readLine() ?: "no status"
            scheme + " " + host + ":" + port + " -> " + line + " " + elapsed(t) + "ms"
        } catch (e: Exception) { scheme + " " + host + ":" + port + " FAIL " + elapsed(t) + "ms " + error(e) }
        finally { try { s?.close() } catch (_: Exception) { } }
    }
}