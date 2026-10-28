using System.Runtime.InteropServices;
using System.Reflection;
using System.Text.Json.Nodes;
using ApexDesktop;

static class InputProbe
{
    private static int _checks;
    private static void Check(bool condition, string label)
    { if (!condition) throw new InvalidOperationException(label); _checks++; }
    private static void Main()
    {
        var device = new WheelDevice(0, "Fixture G29", 24, 1133, 49743);
        var learner = new ButtonLearner();
        Check(learner.Observe([new(device, 1)]) is null, "Already-held button cannot be learned");
        Check(learner.Observe([new(device, 0)]) is null, "Release does not bind");
        var binding = learner.Observe([new(device, 1u << 7)]);
        Check(binding is { DeviceId: 0, Button: 7, Kind: "joystick" }, "Rising button binds using zero-based indices");
        Check(binding!.Caption.EndsWith("button 8"), "Displayed button is one-based");
        Check(PttBinding.Parse(binding.Json()) == binding, "Binding metadata round trip");
        Check(binding.Matches(device), "Matching controller accepted");
        Check(!binding.Matches(device with { Product = 7 }), "Different controller in same slot rejected");
        Check(!binding.Matches(device with { Buttons = 7 }), "Unavailable button rejected");
        Check(PttBinding.Parse(JsonNode.Parse("{\"kind\":\"joystick\",\"device_id\":-1,\"button\":32}")).Kind == "keyboard", "Invalid joystick falls back safely");
        Check(PttBinding.Parse(JsonValue.Create(7)).Key == "F10", "Malformed settings safe");
        Check(PttBinding.Parse(JsonNode.Parse("{\"kind\":\"keyboard\",\"key\":\"F24\"}")).Key == "F24", "Expanded key supported");
        Check(PttBinding.Parse(JsonNode.Parse("{\"kind\":\"keyboard\",\"key\":\"Alt+F4\"}")).Key == "F10", "Unknown key rejected");

        var gate = new HoldButtonGate();
        Check(gate.Update(true, true, 0) is null, "Startup held button does not open mic");
        Check(gate.Update(true, false, 40) is null, "Release arms input");
        Check(gate.Update(true, true, 80) is null, "First press awaits debounce");
        Check(gate.Update(true, false, 100) is null, "Short bounce discarded");
        Check(gate.Update(true, true, 140) is null, "New press begins debounce");
        Check(gate.Update(true, true, 180) == true, "Stable hold emits one press");
        Check(gate.Update(true, true, 220) is null, "Hold does not repeat");
        Check(gate.Update(false, false, 260) == false, "Disconnect immediately releases held input");
        Check(gate.Update(true, true, 300) is null, "Reconnect while held cannot restart input");
        gate.Update(true, false, 340); gate.Update(true, true, 380);
        Check(gate.Update(true, true, 420) == true, "Release then press rearms after reconnect");
        Check(gate.Update(true, false, 421) == false, "Release emits stop immediately");

        var nav = new SessionNavigation();
        Check(nav.Observe("home", "") == "home", "Home is default");
        Check(nav.Observe("home", "") is null, "Unchanged state preserves manual browsing");
        Check(nav.Observe("session", "one") == "session", "Session transition opens live");
        Check(nav.Observe("session", "one") is null, "Active polls preserve history/settings browsing");
        Check(nav.Observe("session", "two") == "session", "New session identity opens live");
        Check(nav.Observe("debrief", "") == "home", "Debrief returns home");
        Check(nav.Observe("debrief", "") is null, "Debrief polls preserve manual navigation");

        Check(Marshal.SizeOf(typeof(WheelInput).GetNestedType("JoyInfo", BindingFlags.NonPublic)!) == 52, "WinMM JOYINFOEX layout matches Windows SDK");
        Check(Marshal.SizeOf(typeof(WheelInput).GetNestedType("JoyCaps", BindingFlags.NonPublic)!) == 728, "WinMM JOYCAPSW layout matches Windows SDK");
        var connected = new WheelInput().Discover(force: true);
        Console.WriteLine("READ-ONLY CONTROLLER INVENTORY: " + (connected.Count == 0 ? "none" : string.Join("; ", connected.Select(d => $"slot={d.Id} name={d.Name} buttons={d.Buttons} vendor={d.Vendor} product={d.Product}"))));
        Console.WriteLine($"PASS: {_checks} input/lifecycle assertions. No microphone, audio, device writes or user application starts.");
    }
}
