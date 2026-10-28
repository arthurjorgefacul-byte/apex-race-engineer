namespace ApexDesktop;

internal static class Program
{
    internal static BackendHost Backend { get; private set; } = null!;

    [STAThread]
    private static void Main()
    {
        using var instance = new Mutex(true, @"Local\APEX_RaceEngineer_Desktop", out bool firstInstance);
        if (!firstInstance)
        {
            MessageBox.Show("APEX is already running. Switch to its open window.", "APEX",
                MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }

        ApplicationConfiguration.Initialize();
        try
        {
            Backend = BackendHost.ConnectAsync(AppContext.BaseDirectory).GetAwaiter().GetResult();
            using var form = new ApexForm();
            // Closing the form is never gated on network/process cleanup.
            // The explicit keep-recording setting can call KeepRunningOnExit;
            // otherwise the owned companion is stopped after the window is gone.
            Application.Run(form);
        }
        catch (Exception exception)
        {
            MessageBox.Show("APEX could not start.\n\n" + exception.Message,
                "APEX", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
        finally
        {
            // Application.Run has returned: the native window is already gone.
            // Owned cleanup is bounded independently of visible UI shutdown.
            Backend?.Dispose();
            instance.ReleaseMutex();
        }
    }
}
