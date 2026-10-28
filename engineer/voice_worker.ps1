param([string]$OutputPath, [switch]$Persistent)
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Speech
$taskSpeech = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    if ($OutputPath) { $taskSpeech.SetOutputToWaveFile($OutputPath) }
    else { $taskSpeech.SetOutputToDefaultAudioDevice() }
    $taskSpeech.Rate = 1
    $taskSpeech.Volume = 90
    if (-not $Persistent) {
        $taskText = [Console]::In.ReadToEnd()
        if ($taskText.Length -gt 0) { $taskSpeech.Speak($taskText) }
        return
    }

    # CLR event callbacks run independently of the PowerShell input loop, so a
    # blocking ReadLine cannot delay completion reports or interrupt commands.
    Add-Type -ReferencedAssemblies System.Speech -TypeDefinition @'
using System;
using System.Collections.Concurrent;
using System.Speech.Synthesis;
public static class ApexSpeechPipe {
    private static readonly ConcurrentDictionary<Prompt, int> Ids = new ConcurrentDictionary<Prompt, int>();
    private static readonly object Output = new object();
    private static void Report(string kind, int id) {
        lock (Output) { Console.Out.WriteLine("{\"event\":\"" + kind + "\",\"id\":" + id + "}"); Console.Out.Flush(); }
    }
    public static void Attach(SpeechSynthesizer speaker) {
        speaker.SpeakStarted += delegate(object sender, SpeakStartedEventArgs e) {
            int id; if (Ids.TryGetValue(e.Prompt, out id)) Report("started", id);
        };
        speaker.SpeakCompleted += delegate(object sender, SpeakCompletedEventArgs e) {
            int id; if (Ids.TryRemove(e.Prompt, out id)) Report(e.Cancelled ? "cancelled" : e.Error != null ? "error" : "finished", id);
        };
    }
    public static void Speak(SpeechSynthesizer speaker, int id, string text) {
        Prompt prompt = new Prompt(text); Ids[prompt] = id; speaker.SpeakAsync(prompt);
    }
}
'@
    [ApexSpeechPipe]::Attach($taskSpeech)
    [Console]::Out.WriteLine('{"event":"ready"}')
    [Console]::Out.Flush()
    while (($taskLine = [Console]::In.ReadLine()) -ne $null) {
        if ($taskLine.Length -gt 8192) { continue }
        $taskCommand = $taskLine | ConvertFrom-Json
        switch ($taskCommand.action) {
            'clear' { $taskSpeech.SpeakAsyncCancelAll() }
            'close' { return }
            'speak' {
                $taskText = [string]$taskCommand.text
                if ($taskText.Length -gt 420) { $taskText = $taskText.Substring(0, 420) }
                if ($taskCommand.interrupt) { $taskSpeech.SpeakAsyncCancelAll() }
                if ($taskText.Length -gt 0) { [ApexSpeechPipe]::Speak($taskSpeech, [int]$taskCommand.id, $taskText) }
            }
        }
    }
} finally {
    $taskSpeech.SpeakAsyncCancelAll()
    $taskSpeech.Dispose()
}
