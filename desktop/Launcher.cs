using System.Diagnostics;
using System.Net.Http.Json;
using System.Text.Json;

namespace ApexDesktop;

/// <summary>
/// Starts only the packaged Python companion. Existing healthy companions are
/// reused without taking ownership; shutdown never targets another process.
/// </summary>
internal sealed class BackendHost : IDisposable
{
    internal static readonly Uri ServiceAddress = new("http://127.0.0.1:8777/");
    private readonly HttpClient _client;
    private readonly object _shutdownGate = new();
    private Task? _shutdownTask;
    private Process? _startedProcess;
    private bool _owned;
    private bool _leaveRunning;
    private bool _disposed;
    private bool _usedPushToTalk;

    internal string PackageRoot { get; }
    internal Uri BaseAddress => ServiceAddress;
    internal int BackendPid { get; private set; }
    internal bool OwnsBackend => _owned && IsStartedProcessRunning();

    private BackendHost(string packageRoot)
    {
        PackageRoot = Path.GetFullPath(packageRoot);
        _client = new HttpClient(new HttpClientHandler
        {
            UseProxy = false,
            AllowAutoRedirect = false
        })
        {
            BaseAddress = ServiceAddress,
            Timeout = TimeSpan.FromSeconds(2)
        };
    }

    internal static async Task<BackendHost> ConnectAsync(string packageRoot)
    {
        var host = new BackendHost(packageRoot);
        try
        {
            int? existing = await host.GetHealthPidAsync().ConfigureAwait(false);
            if (existing.HasValue)
            {
                host.BackendPid = existing.Value;
                return host;
            }

            string script = Path.Combine(host.PackageRoot, "app.py");
            if (!File.Exists(script))
                throw new InvalidOperationException(
                    "The companion files are missing. Extract the complete APEX package and open Apex.exe from that folder.");

            string python = await FindPythonAsync().ConfigureAwait(false);
            string preferredBackend = ReadPreferredBackend(host.PackageRoot);
            var start = new ProcessStartInfo
            {
                FileName = python,
                WorkingDirectory = host.PackageRoot,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden
            };
            start.ArgumentList.Add(script);
            start.ArgumentList.Add("--no-browser");
            start.ArgumentList.Add("--engine");
            start.ArgumentList.Add(preferredBackend);
            if (preferredBackend == "pro_engineer")
                start.ArgumentList.Add("--start-engine");
            start.Environment["PYTHONUTF8"] = "1";
            host._startedProcess = Process.Start(start)
                ?? throw new InvalidOperationException("Windows could not start the local engineer.");

            var startup = Stopwatch.StartNew();
            while (startup.Elapsed < TimeSpan.FromSeconds(20))
            {
                if (host._startedProcess.HasExited)
                    throw new InvalidOperationException(
                        "The local engineer stopped during startup. Check data\\companion.log in the APEX folder.");

                int? healthPid = await host.GetHealthPidAsync().ConfigureAwait(false);
                if (healthPid.HasValue)
                {
                    host.BackendPid = healthPid.Value;
                    host._owned = healthPid.Value == host._startedProcess.Id;
                    if (!host._owned)
                    {
                        // Another desktop/CLI launch won the port between the
                        // first health check and our start. Keep that service.
                        host.TerminateStartedProcess();
                    }
                    return host;
                }
                await Task.Delay(250).ConfigureAwait(false);
            }
            throw new InvalidOperationException(
                "The local engineer did not become ready. Check data\\companion.log in the APEX folder.");
        }
        catch
        {
            host.TerminateStartedProcess();
            host.Dispose();
            throw;
        }
    }

    internal void KeepRunningOnExit() => _leaveRunning = true;
    internal void StopOnExit() => _leaveRunning = false;
    internal void MarkPushToTalkUsed() => _usedPushToTalk = true;

    private async Task ReleaseInputAsync()
    {
        using var budget = new CancellationTokenSource(400);
        try
        {
            if (await GetHealthPidAsync(budget.Token, 180).ConfigureAwait(false) == BackendPid)
            {
                using var response = await _client.PostAsJsonAsync("api/ptt", new { action = "stop" }, budget.Token).ConfigureAwait(false);
            }
        }
        catch (Exception) { }
    }

    internal Task StopOwnedAsync()
    {
        lock (_shutdownGate)
            return _shutdownTask ??= StopOwnedCoreAsync();
    }

    private async Task StopOwnedCoreAsync()
    {
        var elapsed = Stopwatch.StartNew();
        if (!OwnsBackend)
            return;
        using var gracefulBudget = new CancellationTokenSource(TimeSpan.FromMilliseconds(1650));
        // Recheck identity immediately before sending a shutdown request.
        // A different service that later reused the port is left alone.
        int? healthPid = null;
        try { healthPid = await GetHealthPidAsync(gracefulBudget.Token, 450).ConfigureAwait(false); }
        catch (InvalidOperationException) { }
        if (healthPid == _startedProcess!.Id)
        {
            try
            {
                using var requestBudget = CancellationTokenSource.CreateLinkedTokenSource(gracefulBudget.Token);
                requestBudget.CancelAfter(400);
                using var response = await _client.PostAsJsonAsync("api/action",
                    new { action = "stop" }, requestBudget.Token).ConfigureAwait(false);
            }
            catch (HttpRequestException) { }
            catch (TaskCanceledException) { }
        }

        try
        {
            await _startedProcess!.WaitForExitAsync(gracefulBudget.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            TerminateStartedProcess(Math.Max(0, 1900 - (int)elapsed.ElapsedMilliseconds));
        }
        _owned = false;
    }

    private async Task<int?> GetHealthPidAsync(CancellationToken cancellationToken = default, int timeoutMs = 900)
    {
        try
        {
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeout.CancelAfter(timeoutMs);
            using var response = await _client.GetAsync("api/health",
                HttpCompletionOption.ResponseHeadersRead, timeout.Token).ConfigureAwait(false);
            if (!response.IsSuccessStatusCode || response.Content.Headers.ContentLength > 8192)
                throw new InvalidOperationException("Another service is using APEX's local port 8777.");
            await using var body = await response.Content.ReadAsStreamAsync(timeout.Token).ConfigureAwait(false);
            using var buffer = new MemoryStream();
            var bytes = new byte[1024];
            int read;
            while ((read = await body.ReadAsync(bytes, timeout.Token).ConfigureAwait(false)) > 0)
            {
                if (buffer.Length + read > 8192)
                    throw new InvalidOperationException("Another service is using APEX's local port 8777.");
                buffer.Write(bytes, 0, read);
            }
            using var document = JsonDocument.Parse(buffer.ToArray());
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object
                || !root.TryGetProperty("ok", out var ok) || ok.ValueKind != JsonValueKind.True
                || !root.TryGetProperty("app", out var app) || app.ValueKind != JsonValueKind.String
                || app.GetString() != "RaceEngineer"
                || !root.TryGetProperty("pid", out var pid) || pid.ValueKind != JsonValueKind.Number
                || !pid.TryGetInt32(out int processId)
                || processId <= 0)
                throw new InvalidOperationException("Another service is using APEX's local port 8777.");
            return processId;
        }
        catch (HttpRequestException) { return null; }
        catch (OperationCanceledException) { return null; }
        catch (JsonException)
        {
            throw new InvalidOperationException("Another service is using APEX's local port 8777.");
        }
    }

    private static string ReadPreferredBackend(string packageRoot)
    {
        const string fallback = "local";
        try
        {
            string settingsPath = Path.Combine(packageRoot, "data", "settings.json");
            using var stream = new FileStream(settingsPath, FileMode.Open, FileAccess.Read,
                FileShare.ReadWrite | FileShare.Delete);
            if (stream.Length > 65536)
                return fallback;
            using var settings = JsonDocument.Parse(stream);
            if (settings.RootElement.ValueKind == JsonValueKind.Object
                && settings.RootElement.TryGetProperty("backend", out var backend)
                && backend.ValueKind == JsonValueKind.String)
            {
                string? selected = backend.GetString();
                if (selected is "local" or "pro_engineer")
                    return selected;
            }
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
        catch (JsonException) { }
        return fallback;
    }

    private static async Task<string> FindPythonAsync()
    {
        string? windowed = FindOnPath("pythonw.exe");
        if (windowed is not null)
            return windowed;

        string? python = FindOnPath("python.exe");
        if (python is not null)
            return python;

        // Ask the installed launcher for an interpreter path, then start that
        // interpreter directly so its PID reliably proves backend ownership.
        string? launcher = FindOnPath("py.exe");
        if (launcher is not null)
        {
            var query = new ProcessStartInfo
            {
                FileName = launcher,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            };
            query.ArgumentList.Add("-3");
            query.ArgumentList.Add("-c");
            query.ArgumentList.Add("import sys; sys.exit(2) if sys.version_info < (3, 10) else print(sys.executable)");
            using var process = Process.Start(query);
            if (process is not null)
            {
                using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
                try
                {
                    Task<string> output = process.StandardOutput.ReadToEndAsync(timeout.Token);
                    Task<string> error = process.StandardError.ReadToEndAsync(timeout.Token);
                    await process.WaitForExitAsync(timeout.Token).ConfigureAwait(false);
                    string resolved = (await output.ConfigureAwait(false)).Trim();
                    await error.ConfigureAwait(false);
                    if (process.ExitCode == 0 && Path.IsPathFullyQualified(resolved) && File.Exists(resolved))
                    {
                        string sibling = Path.Combine(Path.GetDirectoryName(resolved)!, "pythonw.exe");
                        return File.Exists(sibling) ? sibling : resolved;
                    }
                }
                catch (OperationCanceledException)
                {
                    try { if (!process.HasExited) process.Kill(entireProcessTree: true); }
                    catch (InvalidOperationException) { }
                }
            }
        }
        throw new InvalidOperationException("Install Python 3.10 or newer, then reopen APEX.");
    }

    private static string? FindOnPath(string executable)
    {
        foreach (string directory in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator))
        {
            if (string.IsNullOrWhiteSpace(directory)
                || directory.Contains(@"\Microsoft\WindowsApps", StringComparison.OrdinalIgnoreCase))
                continue;
            try
            {
                string candidate = Path.Combine(directory.Trim().Trim('"'), executable);
                if (File.Exists(candidate))
                    return Path.GetFullPath(candidate);
            }
            catch (ArgumentException) { }
        }
        return null;
    }

    private bool IsStartedProcessRunning()
    {
        try { return _startedProcess is not null && !_startedProcess.HasExited; }
        catch (InvalidOperationException) { return false; }
    }

    private void TerminateStartedProcess(int waitMilliseconds = 150)
    {
        try
        {
            if (IsStartedProcessRunning())
            {
                _startedProcess!.Kill(entireProcessTree: true);
                _startedProcess.WaitForExit(waitMilliseconds);
            }
        }
        catch (InvalidOperationException) { }
        catch (System.ComponentModel.Win32Exception) { }
    }

    public void Dispose()
    {
        if (_disposed)
            return;
        if (_usedPushToTalk && (!_owned || _leaveRunning)) ReleaseInputAsync().GetAwaiter().GetResult();
        if (_owned && !_leaveRunning)
        {
            try { StopOwnedAsync().GetAwaiter().GetResult(); }
            catch (Exception) { TerminateStartedProcess(); }
        }
        _startedProcess?.Dispose();
        _client.Dispose();
        _disposed = true;
    }
}
