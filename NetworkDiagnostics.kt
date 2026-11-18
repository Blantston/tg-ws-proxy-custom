package com.amurcanov.tgwsproxy

import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import javax.net.ssl.SNIHostName
import javax.net.ssl.SSLParameters
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory

/** Read-only network diagnostics for DNS, TCP 80/443, TLS and HTTP/80. */
object NetworkDiagnostics {
    private const val TAG = "TgWsProxy"
    private val domains = listOf("www.cloudflare.com","kws2.pclead.co.uk","kws2.ebally.co.uk","kws2.nothingthere.co.uk","kws2.pyatdesyatodin.co.uk","kws2.web.telegram.org","kws2-1.web.telegram.org")

    private fun info(message: String) = Log.i(TAG, "[INFO] NETDIAG " + message)
    private fun warn(message: String) = Log.w(TAG, "[WARN] NETDIAG " + message)

    suspend fun run() = withContext(Dispatchers.IO) {
        info("========== START ==========")
        info("Проверяем DNS, TCP 80/443, TLS и HTTP/80; ICMP ping не используется")
        domains.forEach { probeDomain(it) }
        info("========== FINISH ==========")
    }

    private fun probeDomain(domain: String) {
        info("--- " + domain + " ---")
        val addresses = try {
            val executor = Executors.newSingleThreadExecutor()
            try {
                executor.submit<Array<InetAddress>> { InetAddress.getAllByName(domain) }.get(4, TimeUnit.SECONDS).mapNotNull { it.hostAddress }.distinct()
            } finally { executor.shutdownNow() }
        } catch (e: Exception) {
            warn("DNS " + domain + " FAIL " + e.javaClass.simpleName + ": " + (e.message ?: "timeout"))
            return
        }
        info("DNS " + domain + " -> " + addresses.joinToString(","))
        addresses.take(2).forEach { ip ->
            probeTcp(domain, ip, 443)
            probeTcp(domain, ip, 80)
            probeTls(domain, ip)
        }
        probeHttp80(domain)
    }

    private fun probeTcp(domain: String, ip: String, port: Int) {
        val start = System.nanoTime()
        try {
            Socket().use { it.connect(InetSocketAddress(ip, port), 2500) }
            info("TCP " + domain + " " + ip + ":" + port + " OK " + elapsed(start) + "ms")
        } catch (e: Exception) {
            warn("TCP " + domain + " " + ip + ":" + port + " FAIL " + elapsed(start) + "ms " + e.javaClass.simpleName + ": " + (e.message ?: ""))
        }
    }

    private fun probeTls(domain: String, ip: String) {
        val start = System.nanoTime()
        try {
            val raw = Socket()
            raw.connect(InetSocketAddress(ip, 443), 2500)
            raw.soTimeout = 4000
            val ssl = (SSLSocketFactory.getDefault() as SSLSocketFactory).createSocket(raw, domain, 443, true) as SSLSocket
            ssl.use {
                val params: SSLParameters = it.sslParameters
                params.serverNames = listOf(SNIHostName(domain))
                it.sslParameters = params
                it.startHandshake()
                info("TLS " + domain + " via " + ip + " OK " + it.session.protocol + "/" + it.session.cipherSuite + " " + elapsed(start) + "ms")
            }
        } catch (e: Exception) {
            warn("TLS " + domain + " via " + ip + " FAIL " + elapsed(start) + "ms " + e.javaClass.simpleName + ": " + (e.message ?: ""))
        }
    }

    private fun probeHttp80(domain: String) {
        val start = System.nanoTime()
        try {
            Socket().use { socket ->
                socket.connect(InetSocketAddress(domain, 80), 2500)
                socket.soTimeout = 2500
                val request = "GET /cdn-cgi/trace HTTP/1.1\r\nHost: " + domain + "\r\nConnection: close\r\n\r\n"
                socket.getOutputStream().write(request.toByteArray(Charsets.US_ASCII))
                val status = BufferedReader(InputStreamReader(socket.getInputStream())).readLine() ?: "<empty>"
                info("HTTP80 " + domain + " -> " + status + " " + elapsed(start) + "ms")
            }
        } catch (e: Exception) {
            warn("HTTP80 " + domain + " FAIL " + elapsed(start) + "ms " + e.javaClass.simpleName + ": " + (e.message ?: ""))
        }
    }

    private fun elapsed(start: Long): Long = (System.nanoTime() - start) / 1_000_000
}