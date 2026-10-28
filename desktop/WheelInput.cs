using System.Runtime.InteropServices;
using System.Text.Json.Nodes;
using Microsoft.Win32;

namespace ApexDesktop;

internal sealed record WheelDevice(int Id, string Name, int Buttons, int Vendor, int Product);
internal readonly record struct WheelSample(WheelDevice Device, uint Buttons);

/// <summary>Read-only WinMM input. No capture, force feedback or device writes.</summary>
internal sealed class WheelInput
{
    private readonly Dictionary<int, WheelDevice> _devices = [];
    private long _nextDiscovery;

    [StructLayout(LayoutKind.Sequential)]
    private struct JoyInfo
    {
        public uint Size, Flags, X, Y, Z, R, U, V, Buttons, ButtonNumber, Pov, Reserved1, Reserved2;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct JoyCaps
    {
        public ushort Vendor, Product;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string Name;
        public uint Xmin, Xmax, Ymin, Ymax, Zmin, Zmax, Buttons, PeriodMin, PeriodMax;
        public uint Rmin, Rmax, Umin, Umax, Vmin, Vmax, Caps, MaxAxes, Axes, MaxButtons;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string RegKey;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)] public string Oem;
    }

    [DllImport("winmm.dll")] private static extern uint joyGetNumDevs();
    [DllImport("winmm.dll")] private static extern uint joyGetPosEx(uint id, ref JoyInfo info);
    [DllImport("winmm.dll", EntryPoint = "joyGetDevCapsW", CharSet = CharSet.Unicode)]
    private static extern uint joyGetDevCaps(nuint id, out JoyCaps caps, uint size);

    internal IReadOnlyList<WheelDevice> Discover(bool force = false)
    {
        if (!OperatingSystem.IsWindows()) return [];
        if (!force && Environment.TickCount64 < _nextDiscovery) return _devices.Values.OrderBy(d => d.Id).ToArray();
        _nextDiscovery = Environment.TickCount64 + 2000;
        _devices.Clear();
        // The WinMM API exposes at most sixteen slots and thirty-two buttons.
        for (uint id = 0; id < Math.Min(joyGetNumDevs(), 16); id++)
        {
            var info = new JoyInfo { Size = (uint)Marshal.SizeOf<JoyInfo>(), Flags = 0x80 };
            if (joyGetPosEx(id, ref info) != 0 || joyGetDevCaps(id, out var caps, (uint)Marshal.SizeOf<JoyCaps>()) != 0) continue;
            string fallback = string.IsNullOrWhiteSpace(caps.Name) ? "Controller " + (id + 1) : caps.Name.Trim();
            _devices[(int)id] = new WheelDevice((int)id, FriendlyName(caps.Vendor, caps.Product, fallback), (int)Math.Min(caps.Buttons, 32), caps.Vendor, caps.Product);
        }
        return _devices.Values.OrderBy(d => d.Id).ToArray();
    }

    internal WheelSample? Read(int id)
    {
        if (id < 0 || id > 15 || !OperatingSystem.IsWindows()) return null;
        Discover();
        if (!_devices.TryGetValue(id, out var device)) return null;
        var info = new JoyInfo { Size = (uint)Marshal.SizeOf<JoyInfo>(), Flags = 0x80 };
        return joyGetPosEx((uint)id, ref info) == 0 ? new WheelSample(device, info.Buttons) : null;
    }

    internal WheelSample[] ReadAll() => Discover().Select(d => Read(d.Id)).OfType<WheelSample>().ToArray();

    private static string FriendlyName(ushort vendor, ushort product, string fallback)
    {
        if (!OperatingSystem.IsWindows()) return fallback;
        // WinMM often returns only "Microsoft PC-joystick driver". The OEM name
        // is Windows' registered product name, not a hard-coded device guess.
        string path = @"SYSTEM\CurrentControlSet\Control\MediaProperties\PrivateProperties\Joystick\OEM\VID_" + vendor.ToString("X4") + "&PID_" + product.ToString("X4");
        try
        {
            using var user = Registry.CurrentUser.OpenSubKey(path, writable: false);
            if (user?.GetValue("OEMName") is string name && !string.IsNullOrWhiteSpace(name)) return name[..Math.Min(name.Length, 180)].Trim();
            using var machine = Registry.LocalMachine.OpenSubKey(path, writable: false);
            if (machine?.GetValue("OEMName") is string systemName && !string.IsNullOrWhiteSpace(systemName)) return systemName[..Math.Min(systemName.Length, 180)].Trim();
        }
        catch (Exception error) when (error is UnauthorizedAccessException or System.Security.SecurityException or IOException) { }
        return fallback;
    }
}

internal sealed record PttBinding(string Kind, string Key = "F10", int DeviceId = -1, int Button = -1,
    string DeviceName = "", int? Vendor = null, int? Product = null)
{
    internal static string[] KeyboardKeys => [.. Enumerable.Range(1, 24).Select(n => "F" + n),
        "Insert", "Home", "End", "PageUp", "PageDown", "Space", .. Enumerable.Range('A', 26).Select(n => ((char)n).ToString()),
        .. Enumerable.Range(0, 10).Select(n => n.ToString())];

    internal static PttBinding Parse(JsonNode? node, string fallback = "F10")
    {
        if (node is not JsonObject) node = null;
        string Text(string key) => node?[key]?.ToString() ?? "";
        int? Integer(string key) => int.TryParse(Text(key), out int number) ? number : null;
        if (Text("kind") == "joystick" && Integer("device_id") is int device && device >= 0 && device <= 15
            && Integer("button") is int button && button >= 0 && button <= 31)
            return new("joystick", "F10", device, button, Text("device_name"), Integer("vendor"), Integer("product"));
        string key = Text("kind") == "keyboard" ? Text("key") : fallback;
        return new("keyboard", KeyboardKeys.Contains(key) ? key : "F10");
    }

    internal JsonObject Json() => Kind == "joystick"
        ? new JsonObject { ["kind"] = "joystick", ["device_id"] = DeviceId, ["button"] = Button,
            ["device_name"] = DeviceName, ["vendor"] = Vendor, ["product"] = Product }
        : new JsonObject { ["kind"] = "keyboard", ["key"] = Key };

    internal bool Matches(WheelDevice device) => DeviceId == device.Id && Button < device.Buttons
        && (!Vendor.HasValue || Vendor == device.Vendor) && (!Product.HasValue || Product == device.Product)
        && (DeviceName.Length == 0 || DeviceName == device.Name);
    internal string Caption => Kind == "joystick" ? (DeviceName.Length > 0 ? DeviceName : "Controller " + (DeviceId + 1)) + " · button " + (Button + 1) : Key;
}

/// <summary>Requires release before arming; debounces presses and releases on disconnect.</summary>
internal sealed class HoldButtonGate
{
    private bool _armed, _held, _candidate;
    private long _candidateSince;
    internal bool Held => _held;
    internal void Reset() { _armed = _held = _candidate = false; _candidateSince = 0; }
    internal bool? Update(bool connected, bool down, long now)
    {
        if (!connected)
        {
            bool release = _held; Reset(); return release ? false : null;
        }
        if (!_armed) { if (!down) _armed = true; return null; }
        if (!down)
        {
            _candidate = false;
            if (_held) { _held = false; return false; }
            return null;
        }
        if (_held) return null;
        if (!_candidate) { _candidate = true; _candidateSince = now; return null; }
        if (now - _candidateSince < 35) return null;
        _held = true; return true;
    }
}

internal sealed class ButtonLearner
{
    private readonly Dictionary<int, uint> _previous = [];
    internal PttBinding? Observe(IEnumerable<WheelSample> samples)
    {
        var present = new HashSet<int>();
        foreach (var sample in samples)
        {
            int id = sample.Device.Id; present.Add(id);
            uint rising = _previous.TryGetValue(id, out uint previous) ? sample.Buttons & ~previous : 0;
            _previous[id] = sample.Buttons;
            for (int button = 0; button < Math.Min(sample.Device.Buttons, 32); button++)
                if ((rising & (1u << button)) != 0)
                    return new PttBinding("joystick", "F10", id, button, sample.Device.Name, sample.Device.Vendor, sample.Device.Product);
        }
        foreach (int missing in _previous.Keys.Where(id => !present.Contains(id)).ToArray()) _previous.Remove(missing);
        return null;
    }
}

/// <summary>Navigation decisions occur on lifecycle transitions, never on every poll.</summary>
internal sealed class SessionNavigation
{
    private string? _identity;
    internal string? Observe(string phase, string sessionId)
    {
        phase = phase is "session" or "debrief" ? phase : "home";
        string identity = phase + ":" + sessionId;
        if (identity == _identity) return null;
        _identity = identity;
        return phase == "session" ? "session" : "home";
    }
}
