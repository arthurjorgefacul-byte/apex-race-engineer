# CLR callbacks keep release/cancel independent of microphone noise.
param([string]$AudioFile = '')
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
try {
    Add-Type -AssemblyName System.Speech
    Add-Type -ReferencedAssemblies System.Speech -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Speech.Recognition;
using System.Threading;
public static class ApexPtt {
    private static readonly object Gate = new object(), Output = new object();
    private static readonly ManualResetEvent Stopped = new ManualResetEvent(true);
    private static SpeechRecognitionEngine Engine;
    private static Timer Timeout;
    private static bool Active, Running, Closing;
    private static readonly Dictionary<string,string> Phrases = new Dictionary<string,string> {
        {"fuel","fuel"},{"fuel status","fuel"},{"how much fuel","fuel"},
        {"tyres","tyres"},{"tires","tyres"},{"tyre status","tyres"},
        {"pace","pace"},{"my pace","pace"},{"next corner","next_corner"},
        {"corner losses","corners"},{"last lap","last_lap"},{"race strategy","strategy"},
        {"strategy","strategy"},{"energy","energy"},{"battery","energy"},
        {"track limits","track_limits"},{"car status","status"},{"status","status"},
        {"quiet","quiet"},{"quiet radio","quiet"},{"more updates","more_updates"},
        {"fewer updates","less_updates"},{"less updates","less_updates"},
        {"my fastest lap","personal_best"},{"my best lap","personal_best"},{"personal best","personal_best"},
        {"what is my fastest lap","personal_best"},{"what is my best lap","personal_best"},
        {"race fastest lap","race_best"},{"fastest lap of the race","race_best"},{"fastest lap","race_best"},
        {"who has the fastest lap","race_best"},{"session fastest lap","race_best"},
        {"other drivers fastest laps","others_best"},{"other drivers best laps","others_best"},
        {"others fastest laps","others_best"},{"opponent best laps","others_best"},
        {"gap ahead","gap_ahead"},{"gap to the car ahead","gap_ahead"},{"how far ahead","gap_ahead"},
        {"gap behind","gap_behind"},{"gap to the car behind","gap_behind"},{"how far behind","gap_behind"},
        {"gap to leader","gap_leader"},{"gap to the leader","gap_leader"},{"leader gap","gap_leader"},
        {"change compound","change_compound"},{"change tyre compound","change_compound"},
        {"different compound","change_compound"},{"change tyres","change_compound"},{"change tires","change_compound"},
        {"which compound","change_compound"},{"which tyres","change_compound"},{"box for tyres","change_compound"},
        {"soft","compound_soft"},{"soft tyres","compound_soft"},{"soft tires","compound_soft"},
        {"fit soft tyres","compound_soft"},{"change to soft","compound_soft"},{"box for softs","compound_soft"},
        {"medium","compound_medium"},{"medium tyres","compound_medium"},{"medium tires","compound_medium"},
        {"fit medium tyres","compound_medium"},{"change to medium","compound_medium"},{"box for mediums","compound_medium"},
        {"hard","compound_hard"},{"hard tyres","compound_hard"},{"hard tires","compound_hard"},
        {"fit hard tyres","compound_hard"},{"change to hard","compound_hard"},{"box for hards","compound_hard"},
        {"intermediate","compound_intermediate"},{"intermediate tyres","compound_intermediate"},
        {"intermediates","compound_intermediate"},{"change to intermediates","compound_intermediate"},
        {"wet","compound_wet"},{"wet tyres","compound_wet"},{"wet tires","compound_wet"},
        {"wets","compound_wet"},{"change to wets","compound_wet"},
        {"feedback","feedback"},{"driver feedback","feedback"},{"give me feedback","feedback"},
        {"where am I losing time","feedback"},{"how can I improve","feedback"},{"what should I improve","feedback"},
        {"corner one","corner_1"},{"corner two","corner_2"},{"corner three","corner_3"},
        {"corner four","corner_4"},{"corner five","corner_5"},{"corner six","corner_6"},
        {"corner seven","corner_7"},{"corner eight","corner_8"},{"corner nine","corner_9"},{"corner ten","corner_10"}
    };
    private static string Escape(string s) { return (s ?? "").Replace("\\","\\\\").Replace("\"","\\\"").Replace("\r"," ").Replace("\n"," "); }
    private static void Emit(string json) { lock(Output) { Console.Out.WriteLine(json); Console.Out.Flush(); } }
    public static void Init() {
        RecognizerInfo selected = null;
        foreach(var info in SpeechRecognitionEngine.InstalledRecognizers()) if(info.Culture.Name == "en-US") { selected=info; break; }
        if(selected == null) throw new InvalidOperationException("Install the Windows English (United States) speech recognizer to use push-to-talk.");
        Engine = new SpeechRecognitionEngine(selected);
        string[] words = new string[Phrases.Count]; Phrases.Keys.CopyTo(words,0);
        var builder = new GrammarBuilder(new Choices(words)); builder.Culture=selected.Culture;
        Engine.LoadGrammar(new Grammar(builder));
        Engine.BabbleTimeout=TimeSpan.FromSeconds(3);
        Engine.InitialSilenceTimeout=TimeSpan.FromSeconds(3);
        Engine.EndSilenceTimeout=TimeSpan.FromMilliseconds(200);
        Engine.SpeechRecognized += delegate(object sender, SpeechRecognizedEventArgs e) {
            string topic;
            lock(Gate) {
                if(!Active || Closing || e.Result.Confidence < 0.55 || !Phrases.TryGetValue(e.Result.Text,out topic)) return;
                Active=false;
            }
            Emit("{\"event\":\"recognized\",\"text\":\""+Escape(e.Result.Text)+"\",\"topic\":\""+topic+"\",\"confidence\":"+e.Result.Confidence.ToString(System.Globalization.CultureInfo.InvariantCulture)+"}");
            Stop();
        };
        Engine.RecognizeCompleted += delegate(object sender, RecognizeCompletedEventArgs e) {
            lock(Gate) {
                Running=Active=false;
                if(Timeout!=null) Timeout.Change(System.Threading.Timeout.Infinite,System.Threading.Timeout.Infinite);
                try { Engine.SetInputToNull(); } catch(InvalidOperationException) { }
                Stopped.Set();
            }
            Emit("{\"event\":\"stopped\"}");
        };
        Timeout=new Timer(delegate(object state) { Stop(); },null,System.Threading.Timeout.Infinite,System.Threading.Timeout.Infinite);
        Emit("{\"event\":\"ready\",\"language\":\"en-US\"}");
    }
    public static void Start(string wave) {
        Stop();
        if(!Stopped.WaitOne(400)) throw new InvalidOperationException("The previous microphone request is still stopping. Try again.");
        lock(Gate) {
            if(Closing) return;
            if(string.IsNullOrEmpty(wave)) Engine.SetInputToDefaultAudioDevice(); else Engine.SetInputToWaveFile(wave);
            Active=Running=true; Stopped.Reset();
            Engine.RecognizeAsync(RecognizeMode.Multiple);
            Timeout.Change(12000,System.Threading.Timeout.Infinite);
        }
        Emit("{\"event\":\"listening\"}");
    }
    public static void Stop() {
        lock(Gate) { Active=false; if(Running) Engine.RecognizeAsyncCancel(); }
    }
    public static void Close() {
        lock(Gate) { Closing=true; }
        if(Engine!=null) { Stop(); Stopped.WaitOne(250); }
        if(Timeout!=null) Timeout.Dispose();
        if(Engine!=null) Engine.Dispose();
    }
}
'@
    [ApexPtt]::Init()
    while (($line = [Console]::In.ReadLine()) -ne $null) {
        if ($line.Length -gt 4096) { continue }
        try {
            $request = $line | ConvertFrom-Json
            if ($request.action -eq 'close') { break }
            if ($request.action -eq 'start') { [ApexPtt]::Start($AudioFile) }
            elseif ($request.action -eq 'stop') { [ApexPtt]::Stop() }
        } catch { [Console]::Out.WriteLine((@{event='error';message=$_.Exception.Message} | ConvertTo-Json -Compress)); [Console]::Out.Flush() }
    }
} catch { [Console]::Out.WriteLine((@{event='error';message=$_.Exception.Message} | ConvertTo-Json -Compress)); [Console]::Out.Flush() }
finally { if ('ApexPtt' -as [type]) { [ApexPtt]::Close() } }
