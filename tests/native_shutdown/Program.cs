using System.Diagnostics;
using System.Net;
using System.Reflection;
using System.Text;
using System.Text.Json;
using ApexDesktop;

internal static class ShutdownProbe
{
    private static async Task<int> Main(string[] args)
    {
        if (args.Contains("--fixture-child"))
        {
            Thread.Sleep(Timeout.Infinite);
            return 0;
        }

        await UnownedServiceIsPreserved();
        await KeepRecordingIsPreserved();
        await VerifiedOwnerStopsGracefully();
        await ChangedServicePidDoesNotReceiveStop();
        await HungHealthHasBoundedCleanup();
        FreshAndSavedEnginePreferences();
        Console.WriteLine("PASS: 5 native shutdown scenarios and fresh/saved engine preferences; only test-owned helper processes were started.");
        return 0;
    }

    private static void FreshAndSavedEnginePreferences()
    {
        string folder = Path.Combine(AppContext.BaseDirectory, "apex-preference-probe-" + Guid.NewGuid().ToString("N"));
        string data = Path.Combine(folder, "data"), file = Path.Combine(data, "settings.json");
        var read = typeof(BackendHost).GetMethod("ReadPreferredBackend", BindingFlags.NonPublic | BindingFlags.Static)!;
        string Read() => (string)read.Invoke(null, [folder])!;
        Directory.CreateDirectory(data);
        try
        {
            Check(Read() == "local", "Fresh packages start with the local engineer");
            File.WriteAllText(file, "{\"backend\":\"pro_engineer\"}");
            Check(Read() == "pro_engineer", "An existing Pro Engineer choice is preserved");
            File.WriteAllText(file, "{\"backend\":\"local\"}");
            Check(Read() == "local", "An existing local engineer choice is preserved");
            File.WriteAllText(file, "not-json");
            Check(Read() == "local", "Invalid settings use the simple local startup");
        }
        finally
        {
            if (File.Exists(file)) File.Delete(file);
            Directory.Delete(data);
            Directory.Delete(folder);
        }
    }

    private static Process Child()
    {
        string path = Path.Combine(AppContext.BaseDirectory,
            OperatingSystem.IsWindows() ? "ShutdownProbe.exe" : "ShutdownProbe");
        var start = new ProcessStartInfo(path)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden
        };
        start.ArgumentList.Add("--fixture-child");
        return Process.Start(start) ?? throw new Exception("Fixture child failed to start");
    }

    private static void Finish(Process process)
    {
        if (!process.HasExited)
        {
            process.Kill(entireProcessTree: true);
            process.WaitForExit(1000);
        }
    }

    private static BackendHost Host(Process process, FakeService service, bool owned)
    {
        var host = (BackendHost)Activator.CreateInstance(typeof(BackendHost),
            BindingFlags.Instance | BindingFlags.NonPublic, null,
            [Environment.CurrentDirectory], null)!;
        const BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic;
        var clientField = typeof(BackendHost).GetField("_client", flags)!;
        ((HttpClient)clientField.GetValue(host)!).Dispose();
        clientField.SetValue(host, new HttpClient(service)
        {
            BaseAddress = BackendHost.ServiceAddress,
            Timeout = TimeSpan.FromSeconds(2)
        });
        // The host owns its handle wrapper; the fixture keeps a separate
        // observer so Dispose cannot invalidate cleanup/assertion handles.
        typeof(BackendHost).GetField("_startedProcess", flags)!.SetValue(host, Process.GetProcessById(process.Id));
        typeof(BackendHost).GetField("_owned", flags)!.SetValue(host, owned);
        return host;
    }

    private static void Check(bool condition, string description)
    {
        if (!condition)
            throw new Exception(description);
    }

    private static async Task UnownedServiceIsPreserved()
    {
        using var child = Child();
        try
        {
            var service = new FakeService(child.Id);
            using (var host = Host(child, service, owned: false))
                await host.StopOwnedAsync();
            Check(!child.HasExited, "An unowned process must remain running");
            Check(service.Requests == 0, "An unowned process must receive no shutdown requests");
        }
        finally { Finish(child); }
    }

    private static Task KeepRecordingIsPreserved()
    {
        using var child = Child();
        try
        {
            var service = new FakeService(child.Id);
            using (var host = Host(child, service, owned: true))
                host.KeepRunningOnExit();
            Check(!child.HasExited, "Keep recording must preserve the companion");
            Check(service.Requests == 0, "Keep recording must not send a stop request");
        }
        finally { Finish(child); }
        return Task.CompletedTask;
    }

    private static async Task VerifiedOwnerStopsGracefully()
    {
        using var child = Child();
        try
        {
            var service = new FakeService(child.Id) { Stop = () => Finish(child) };
            using var host = Host(child, service, owned: true);
            Task first = host.StopOwnedAsync();
            Task second = host.StopOwnedAsync();
            Check(ReferenceEquals(first, second), "Concurrent cleanup must share one task");
            await Task.WhenAll(first, second);
            Check(child.HasExited, "Owned child must be reaped");
            Check(service.StopRequests == 1, "Verified owner must receive one stop request");
        }
        finally { Finish(child); }
    }

    private static async Task ChangedServicePidDoesNotReceiveStop()
    {
        using var ownedChild = Child();
        using var unrelatedChild = Child();
        try
        {
            var service = new FakeService(unrelatedChild.Id);
            using var host = Host(ownedChild, service, owned: true);
            await host.StopOwnedAsync();
            Check(ownedChild.HasExited, "The owned stuck child must be cleaned up");
            Check(!unrelatedChild.HasExited, "The process now answering the port must remain running");
            Check(service.StopRequests == 0, "A mismatched PID must never receive a stop command");
        }
        finally { Finish(ownedChild); Finish(unrelatedChild); }
    }

    private static async Task HungHealthHasBoundedCleanup()
    {
        using var child = Child();
        try
        {
            var service = new FakeService(child.Id) { HangHealth = true };
            using var host = Host(child, service, owned: true);
            var elapsed = Stopwatch.StartNew();
            await host.StopOwnedAsync();
            Check(elapsed.ElapsedMilliseconds < 2200, "Cleanup exceeded the two-second budget tolerance");
            Check(child.HasExited, "A hung health request must not leave an owned orphan");
            Check(service.StopRequests == 0, "Unverified identity must not receive HTTP stop");
            Console.WriteLine("Bounded hung-health cleanup: " + elapsed.ElapsedMilliseconds + " ms");
        }
        finally { Finish(child); }
    }

    private sealed class FakeService(int pid) : HttpMessageHandler
    {
        internal int Requests;
        internal int StopRequests;
        internal bool HangHealth;
        internal Action? Stop;

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,
            CancellationToken cancellationToken)
        {
            Requests++;
            if (request.RequestUri!.AbsolutePath == "/api/health")
            {
                if (HangHealth)
                    await Task.Delay(10000, cancellationToken);
                return Json(new { ok = true, app = "RaceEngineer", pid });
            }
            Check(request.RequestUri.AbsolutePath == "/api/action", "Unexpected endpoint");
            StopRequests++;
            Stop?.Invoke();
            return Json(new { ok = true });
        }

        private static HttpResponseMessage Json(object value) => new(HttpStatusCode.OK)
        {
            Content = new StringContent(JsonSerializer.Serialize(value), Encoding.UTF8, "application/json")
        };
    }
}
