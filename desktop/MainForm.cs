using System.Diagnostics;
using System.Globalization;
using System.Net.Http;
using System.Text;
using System.Text.Json.Nodes;
using System.Runtime.InteropServices;

namespace ApexDesktop;

/// <summary>A native WinForms pit wall and persistent recording browser.</summary>
internal sealed class ApexForm : Form
{
    private static readonly Color Background = Color.FromArgb(17, 22, 29);
    private static readonly Color Surface = Color.FromArgb(26, 33, 42);
    private static readonly Color Border = Color.FromArgb(48, 59, 72);
    private static readonly Color Ink = Color.FromArgb(230, 237, 242);
    private static readonly Color Muted = Color.FromArgb(158, 173, 185);
    private static readonly Color Lime = Color.FromArgb(185, 239, 111);
    private static readonly Color Warm = Color.FromArgb(255, 184, 115);
    private readonly HttpClient _http;
    private readonly System.Windows.Forms.Timer _pollTimer = new() { Interval = 500 };
    private readonly TabControl _tabs = new() { Dock = DockStyle.Fill, Padding = new Point(20, 9) };
    private TabPage _homePage = null!, _livePage = null!, _historyPage = null!;
    private readonly Label _homeConnection = TextLabel("Waiting for your local engineer", 16, Ink, true);
    private readonly Label _homeCoverage = TextLabel("Your recordings and preferences stay on this computer.", 11, Muted);
    private readonly Label _homeHeading = TextLabel("READY FOR YOUR NEXT SESSION", 24, Ink, true);
    private readonly RichTextBox _homeDebrief = new() { Dock = DockStyle.Fill, ReadOnly = true, BackColor = Surface, ForeColor = Ink, BorderStyle = BorderStyle.None, Font = new Font("Segoe UI", 11), DetectUrls = true };
    private readonly Button _startSession = ActionButton("Start session");
    private readonly Button _endSession = ActionButton("End session & debrief");
    private readonly SessionNavigation _navigation = new();
    private string _debriefKey = "";
    private readonly Label _status = TextLabel("Connecting to your local engineer…", 10, Muted);
    private readonly Label _liveIdentity = TextLabel("Waiting for Assetto Corsa", 19, Ink, true);
    private readonly Label _engineStatus = TextLabel("", 10, Muted);
    private readonly Label _advice = TextLabel("Start a driving session to collect your first clean reference.", 16, Ink);
    private readonly Label _liveLap = TextLabel("—", 26, Ink, true);
    private readonly Label _liveDelta = TextLabel("—", 26, Lime, true);
    private readonly Label _liveFuel = TextLabel("—", 26, Ink, true);
    private readonly Label _liveFuelNote = TextLabel("Learning fuel use", 8, Muted);
    private readonly Label _liveSpeed = TextLabel("—", 26, Ink, true);
    private readonly Label _tyres = TextLabel("Tyres: waiting for telemetry", 11, Muted);
    private readonly Label _liveNote = TextLabel("", 10, Muted);
    private readonly TelemetryChart _liveChart = new() { Dock = DockStyle.Fill, Text = "Driver inputs · completed lap / reference" };
    private readonly DataGridView _liveCorners = Grid();
    private readonly DataGridView _timingGrid = Grid();
    private readonly Label _timingNote = TextLabel("Enable the RaceEngineer in-game app for race timing.", 9, Muted);
    private readonly DataGridView _sessionGrid = Grid();
    private readonly DataGridView _lapGrid = Grid();
    private readonly DataGridView _savedCorners = Grid();
    private readonly TelemetryChart _savedChart = new() { Dock = DockStyle.Fill, Text = "Stored lap analysis" };
    private readonly Label _historyNote = TextLabel("Choose a saved session, then a lap. These views do not replace the live reference.", 10, Muted);
    private readonly Label _savedNote = TextLabel("Select a lap to view its recorded inputs and corner comparison.", 10, Muted);
    private readonly CheckBox _showDeleted = new() { Text = "Show removed laps", AutoSize = true, ForeColor = Ink, Padding = new Padding(5, 7, 5, 0) };
    private readonly ComboBox _references = Combo();
    private readonly Button _analyze = ActionButton("Analyze lap");
    private readonly Button _remove = ActionButton("Remove from analysis");
    private readonly Button _restore = ActionButton("Restore lap");
    private readonly Button _undo = ActionButton("Undo last removal");
    private readonly ComboBox _focus = Combo();
    private readonly ComboBox _engine = Combo();
    private readonly CheckBox _voice = new() { Text = "Spoken engineer feedback", AutoSize = true, ForeColor = Ink };
    private readonly CheckBox _pressureSet = new() { Text = "Set a verified hot-pressure target for this car and compound", AutoSize = true, ForeColor = Ink };
    private readonly NumericUpDown _reserve = NumberInput(0, 100, 2);
    private readonly NumericUpDown _pressure = NumberInput(5, 80, 27);
    private readonly Label _settingsNote = TextLabel("", 10, Muted);
    private readonly Label _lapSummary = TextLabel("Choose a session and a lap on the left.", 16, Ink, true);
    private readonly RichTextBox _cornerDetail = new() { Dock = DockStyle.Fill, ReadOnly = true, BackColor = Surface, ForeColor = Ink, BorderStyle = BorderStyle.None, Font = new Font("Segoe UI", 10), DetectUrls = true };
    private readonly RichTextBox _radioReply = new() { Text = "Ask about fuel, tyres, pace or any detected corner. Answers use your current telemetry.", ReadOnly = true, BackColor = Surface, ForeColor = Ink, BorderStyle = BorderStyle.None, Font = new Font("Segoe UI", 11), DetectUrls = true, ScrollBars = RichTextBoxScrollBars.Vertical };
    private readonly FlowLayoutPanel _radioChoices = new() { Dock = DockStyle.Bottom, Height = 34, WrapContents = false, AutoScroll = true, BackColor = Surface, Visible = false };
    private readonly ListBox _radioLog = new() { Dock = DockStyle.Fill, BackColor = Surface, ForeColor = Ink, BorderStyle = BorderStyle.None, HorizontalScrollbar = true, IntegralHeight = false };
    private readonly TextBox _question = new() { Dock = DockStyle.Fill, PlaceholderText = "Ask: how much fuel do I have?", MaxLength = 300, BackColor = Surface, ForeColor = Ink, BorderStyle = BorderStyle.FixedSingle };
    private readonly CheckBox _pttEnabled = new() { Text = "Enable push-to-talk while driving · English commands", AutoSize = true, ForeColor = Ink };
    private readonly ComboBox _pttKey = Combo();
    private readonly Label _pttNote = TextLabel("Hold F10 to speak · fuel, tyres, pace, next corner…", 9, Muted);
    private readonly System.Windows.Forms.Timer _pttTimer = new() { Interval = 40 };
    private readonly WheelInput _wheel = new();
    private readonly HoldButtonGate _wheelGate = new();
    private PttBinding _pttBinding = new("keyboard");
    private readonly Label _controllerNote = TextLabel("Detecting connected wheels and controllers…", 9, Muted);
    private readonly Label _bindingNote = TextLabel("Keyboard · F10", 10, Ink);
    private bool _learningButton, _loadingPtt;
    private Task _pttCommand = Task.CompletedTask;
    private int _pttSequence;
    private long _nextControllerStatus;
    private bool _pttHeld;
    private int _pttVirtualKey = (int)Keys.F10;
    private const int PttHotkeyId = 0xA943;
    [DllImport("user32.dll", SetLastError = true)] private static extern bool RegisterHotKey(IntPtr window, int id, uint modifiers, uint key);
    [DllImport("user32.dll")] private static extern bool UnregisterHotKey(IntPtr window, int id);
    [DllImport("user32.dll")] private static extern short GetAsyncKeyState(int key);
    private readonly ComboBox _verbosity = Combo();
    private readonly CheckBox _quiet = new() { Text = "Quiet radio · urgent track-limit calls remain active", AutoSize = true, ForeColor = Ink };
    private readonly CheckBox _keepRecording = new() { Text = "Keep recording when I close the window", AutoSize = true, ForeColor = Ink };
    private readonly DataGridView _sourceGrid = Grid();
    private readonly RichTextBox _sourceText = new() { Dock = DockStyle.Fill, ReadOnly = true, BackColor = Surface, ForeColor = Ink, BorderStyle = BorderStyle.None, Font = new Font("Segoe UI", 10), DetectUrls = true };
    private readonly TelemetryChart _sourceChart = new() { Dock = DockStyle.Fill, Text = "Published / imported telemetry · inspection only" };
    private JsonNode? _catalog;
    private JsonNode? _savedAnalysis;
    private string _radioLogKey = "";
    private string _lastReplyKey = "";
    private JsonNode? _state;
    private JsonNode? _historyDetail;
    private string? _selectedSession;
    private string? _selectedLap;
    private string? _undoLap;
    private string? _undoSession;
    private string? _dataDirectory;
    private string _liveCornerKey = "";
    private string _settingsProfileKey = "";
    private bool _polling;
    private bool _historyLoading;
    private bool _settingsLoaded;
    private bool _formClosed;
    private int _sessionGeneration;
    private int _analysisGeneration;
    private readonly Image? _brandImage;
    private readonly Icon? _brandIcon;

    private static Image? LoadBrandImage()
    {
        using var stream = typeof(ApexForm).Assembly.GetManifestResourceStream("Apex.Brand.Symbol.png");
        if (stream is null) return null;
        using var original = Image.FromStream(stream);
        return new Bitmap(original);
    }

    private static Icon? LoadBrandIcon()
    {
        using var stream = typeof(ApexForm).Assembly.GetManifestResourceStream("Apex.Brand.Icon.ico");
        if (stream is null) return null;
        using var original = new Icon(stream);
        return (Icon)original.Clone();
    }

    internal ApexForm()
    {
        // All manually authored bounds and absolute table rows below use the
        // WinForms 96-DPI design baseline. Defer the first layout until every
        // child exists, then let ContainerControl scale the complete tree.
        SuspendLayout();
        Text = "APEX · Race Engineer";
        _brandImage = LoadBrandImage();
        _brandIcon = LoadBrandIcon();
        if (_brandIcon is not null) Icon = _brandIcon;
        BackColor = Background;
        ForeColor = Ink;
        Font = new Font("Segoe UI", 10);
        MinimumSize = new Size(1050, 720);
        Size = new Size(1250, 850);
        StartPosition = FormStartPosition.CenterScreen;
        AutoScaleMode = AutoScaleMode.Dpi;
        _http = new HttpClient(new HttpClientHandler { UseProxy = false, AllowAutoRedirect = false })
        { BaseAddress = Program.Backend.BaseAddress, Timeout = TimeSpan.FromSeconds(12) };

        // Let native preferred sizes reserve the logo and radio controls. Fixed
        // pixel widths clipped both at 150% Windows scaling; only the subtitle
        // is allowed to shrink when the window is narrow.
        var top = new TableLayoutPanel
        {
            Dock = DockStyle.Top, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink,
            MinimumSize = new Size(0, 54), ColumnCount = 3, RowCount = 1,
            Padding = new Padding(22, 10, 22, 8), BackColor = Surface,
            GrowStyle = TableLayoutPanelGrowStyle.FixedSize
        };
        top.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        top.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        top.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        var brand = TextLabel("APEX", 24, Lime, true);
        brand.AutoSize = true; brand.Anchor = AnchorStyles.Left;
        brand.Margin = Padding.Empty;
        var brandLockup = new FlowLayoutPanel
        {
            AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink,
            WrapContents = false, FlowDirection = FlowDirection.LeftToRight,
            Anchor = AnchorStyles.Left, Margin = new Padding(0, 0, 22, 0),
            BackColor = Surface
        };
        if (_brandImage is not null)
            brandLockup.Controls.Add(new PictureBox
            {
                Image = _brandImage, Size = new Size(36, 36), SizeMode = PictureBoxSizeMode.Zoom,
                Margin = new Padding(0, 0, 9, 0), AccessibleName = "APEX logo", TabStop = false
            });
        brandLockup.Controls.Add(brand);
        var subtitle = TextLabel("RACE ENGINEER  /  ASSETTO CORSA ORIGINAL", 8.5f, Muted);
        subtitle.Dock = DockStyle.Fill; subtitle.TextAlign = ContentAlignment.MiddleLeft;
        subtitle.AutoEllipsis = true; subtitle.Margin = new Padding(0, 0, 14, 0);
        var mode = Flow(); mode.AutoSize = true; mode.AutoSizeMode = AutoSizeMode.GrowAndShrink;
        mode.Anchor = AnchorStyles.Right; mode.Margin = Padding.Empty;
        var home = ActionButton("Home"); home.Click += (_, _) => _tabs.SelectedTab = _homePage;
        _endSession.Click += async (_, _) => await GuardAsync(() => SessionActionAsync("end"));
        mode.Controls.AddRange([home, _endSession]); AddAction(mode, "Radio check", "radio");
        top.Controls.Add(brandLockup, 0, 0); top.Controls.Add(subtitle, 1, 0); top.Controls.Add(mode, 2, 0);

        var bottom = new Panel { Dock = DockStyle.Bottom, Height = 35, BackColor = Surface, Padding = new Padding(18, 6, 18, 2) };
        _status.Dock = DockStyle.Fill; bottom.Controls.Add(_status);
        _tabs.Font = new Font("Segoe UI", 10);
        _tabs.DrawMode = TabDrawMode.OwnerDrawFixed;
        _tabs.SizeMode = TabSizeMode.Fixed;
        _tabs.ItemSize = new Size(165, 38);
        _tabs.DrawItem += (_, e) =>
        {
            bool selected = e.Index == _tabs.SelectedIndex;
            using var fill = new SolidBrush(selected ? Surface : Background);
            e.Graphics.FillRectangle(fill, e.Bounds);
            TextRenderer.DrawText(e.Graphics, _tabs.TabPages[e.Index].Text, _tabs.Font, e.Bounds,
                selected ? Lime : Muted, TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);
        };
        _tabs.TabPages.Add(_homePage = BuildHome());
        _tabs.TabPages.Add(_livePage = BuildLive());
        _tabs.TabPages.Add(_historyPage = BuildHistory());
        _tabs.TabPages.Add(BuildSettings());
        Controls.Add(_tabs); Controls.Add(bottom); Controls.Add(top);
        _pollTimer.Tick += async (_, _) => await PollAsync();
        _tabs.SelectedIndexChanged += async (_, _) =>
        {
            if (_tabs.SelectedTab == _historyPage) await GuardAsync(LoadSessionsAsync);
        };
        Shown += async (_, _) =>
        {
            foreach (var grid in new[] { _sessionGrid, _lapGrid, _savedCorners, _liveCorners, _timingGrid, _sourceGrid })
            {
                foreach (DataGridViewColumn column in grid.Columns)
                    if (column.AutoSizeMode == DataGridViewAutoSizeColumnMode.NotSet || column.AutoSizeMode == DataGridViewAutoSizeColumnMode.None) column.Width = LogicalToDeviceUnits(column.Width);
                grid.RowTemplate.Height = LogicalToDeviceUnits(grid.RowTemplate.Height);
                grid.ColumnHeadersHeight = LogicalToDeviceUnits(34);
            }
            _pollTimer.Start();
            _pttTimer.Start();
            await PollAsync();
            await GuardAsync(LoadSessionsAsync);
        };
        _pttTimer.Tick += async (_, _) => await PollPttAsync();
        FormClosed += (_, _) => { _formClosed = true; UnregisterHotKey(Handle, PttHotkeyId); _pttTimer.Stop(); _pollTimer.Stop(); _http.Dispose(); _brandImage?.Dispose(); _brandIcon?.Dispose(); };
        AutoScaleDimensions = new SizeF(96F, 96F);
        ResumeLayout(performLayout: true);
    }

    private TabPage BuildHome()
    {
        var page = Page("Home");
        var root = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(28, 22, 28, 20), ColumnCount = 1, RowCount = 5 };
        root.RowStyles.Add(new RowStyle(SizeType.Absolute, 56));
        root.RowStyles.Add(new RowStyle(SizeType.Absolute, 95));
        root.RowStyles.Add(new RowStyle(SizeType.Absolute, 82));
        root.RowStyles.Add(new RowStyle(SizeType.Absolute, 52));
        root.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        _homeHeading.Dock = DockStyle.Fill; root.Controls.Add(_homeHeading, 0, 0);
        var connection = new Panel { Dock = DockStyle.Fill, BackColor = Surface, Padding = new Padding(16, 10, 16, 8) };
        _homeConnection.Dock = DockStyle.Fill; connection.Controls.Add(_homeConnection); root.Controls.Add(connection, 0, 1);
        _homeCoverage.Dock = DockStyle.Fill; _homeCoverage.Padding = new Padding(0, 8, 0, 0); root.Controls.Add(_homeCoverage, 0, 2);
        var actions = Flow(); actions.Dock = DockStyle.Fill;
        _startSession.BackColor = Lime; _startSession.ForeColor = Background;
        _startSession.Click += async (_, _) => await GuardAsync(() => SessionActionAsync("start"));
        var browse = ActionButton("Browse sessions"); browse.Click += (_, _) => _tabs.SelectedTab = _historyPage;
        var live = ActionButton("Open live engineer"); live.Click += (_, _) => _tabs.SelectedTab = _livePage;
        actions.Controls.AddRange([_startSession, browse, live]); root.Controls.Add(actions, 0, 3);
        _homeDebrief.Padding = new Padding(16); _homeDebrief.AccessibleName = "Last session debrief and next session plan";
        _homeDebrief.Text = "YOUR ENGINEER IS READY\n\nStart a session here, then drive in Assetto Corsa. APEX will open the live engineer when telemetry arrives.\n\nFinish the session to review what went well, the main priorities and a plan for your next drive. Saved sessions remain available under Lap analysis.";
        root.Controls.Add(_homeDebrief, 0, 4); page.Controls.Add(root); return page;
    }

    private async Task SessionActionAsync(string action)
    {
        _startSession.Enabled = false;
        try { await PostAsync("api/session", new JsonObject { ["action"] = action }); await PollAsync(); }
        finally { if (!_formClosed) { if (_state is not null) RenderHome(_state); else _startSession.Enabled = true; } }
    }

    private TabPage BuildLive()
    {
        var page = Page("Live engineer");
        var root = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(12), ColumnCount = 2, RowCount = 1 };
        root.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 69)); root.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 31));
        var left = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 5 };
        foreach (int height in new[] { 58, 104, 74, 32 }) left.RowStyles.Add(new RowStyle(SizeType.Absolute, height));
        _liveChart.MinimumSize = new Size(300, 175);
        left.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        var identity = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 2, ColumnCount = 1 };
        identity.RowStyles.Add(new RowStyle(SizeType.Percent, 58)); identity.RowStyles.Add(new RowStyle(SizeType.Percent, 42));
        _liveIdentity.Font = new Font("Segoe UI", 16, FontStyle.Bold); _liveIdentity.AutoEllipsis = true;
        _liveIdentity.Dock = _engineStatus.Dock = DockStyle.Fill;
        identity.Controls.Add(_liveIdentity); identity.Controls.Add(_engineStatus); left.Controls.Add(identity, 0, 0);
        var metrics = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 4, RowCount = 1 };
        foreach (var _ in Enumerable.Range(0, 4)) metrics.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 25));
        foreach (var label in new[] { _liveLap, _liveDelta, _liveFuel, _liveSpeed }) label.Font = new Font("Segoe UI", 20, FontStyle.Bold);
        metrics.Controls.Add(Metric("CURRENT LAP", _liveLap), 0, 0);
        metrics.Controls.Add(Metric("LIVE DELTA", _liveDelta), 1, 0);
        metrics.Controls.Add(Metric("FUEL / RANGE", _liveFuel, _liveFuelNote), 2, 0);
        metrics.Controls.Add(Metric("SPEED / GEAR", _liveSpeed), 3, 0); left.Controls.Add(metrics, 0, 1);
        var advice = new Panel { Dock = DockStyle.Fill, BackColor = Surface, Padding = new Padding(12), Margin = new Padding(4) };
        _advice.Font = new Font("Segoe UI", 12); _advice.Dock = DockStyle.Fill; advice.Controls.Add(_advice); left.Controls.Add(advice, 0, 2);
        _tyres.Font = new Font("Segoe UI", 9); _tyres.Dock = DockStyle.Fill; left.Controls.Add(_tyres, 0, 3);
        _liveChart.Text = "Your last lap / clean reference · hover to inspect";
        var tabs = new TabControl { Dock = DockStyle.Fill };
        var inputs = Page("Driver inputs"); inputs.Controls.Add(_liveChart); tabs.TabPages.Add(inputs);
        var corners = Page("All detected corners"); CornerColumns(_liveCorners); _liveNote.Dock = DockStyle.Bottom; _liveNote.Height = 30;
        corners.Controls.Add(_liveCorners); corners.Controls.Add(_liveNote);
        _liveCorners.SelectionChanged += (_, _) => { if (_liveCorners.SelectedRows.Count > 0 && _liveCorners.SelectedRows[0].Tag is JsonNode c) _liveChart.FocusRange(Num(c["section_start_pos"]) ?? 0, Num(c["section_end_pos"]) ?? 1); };
        var timing = Page("Race timing");
        AddColumn(_timingGrid, "Order", "ORDER", 58); AddColumn(_timingGrid, "Driver", "DRIVER", 200);
        AddColumn(_timingGrid, "Lap", "LAP", 50); AddColumn(_timingGrid, "Last", "LAST LAP", 100);
        AddColumn(_timingGrid, "Best", "BEST LAP", 100); AddColumn(_timingGrid, "Compound", "COMPOUND", 105);
        AddColumn(_timingGrid, "Gap", "GAP FROM YOU", 120);
        AddColumn(_timingGrid, "Current", "CURRENT LAP", 110); AddColumn(_timingGrid, "Speed", "km/h", 70);
        _timingGrid.Columns["Driver"]!.AutoSizeMode = DataGridViewAutoSizeColumnMode.Fill;
        _timingNote.Dock = DockStyle.Bottom; _timingNote.Height = 30; timing.Controls.Add(_timingGrid); timing.Controls.Add(_timingNote);
        tabs.TabPages.Add(corners); tabs.TabPages.Add(timing); left.Controls.Add(tabs, 0, 4);
        root.Controls.Add(left, 0, 0);
        var radio = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 6, Padding = new Padding(10, 0, 0, 0) };
        foreach (int height in new[] { 35, 216, 38, 110, 28 }) radio.RowStyles.Add(new RowStyle(SizeType.Absolute, height));
        radio.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        var title = TextLabel("ASK YOUR ENGINEER", 12, Lime, true); title.Dock = DockStyle.Fill; radio.Controls.Add(title, 0, 0);
        var commands = new TabControl { Dock = DockStyle.Fill, Font = new Font("Segoe UI", 9) };
        void Group(string label, (string Topic, string Name)[] items)
        {
            var group = Page(label); int rows = (items.Length + 1) / 2;
            var grid = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = rows, Padding = new Padding(4) };
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50)); grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50));
            for (int row = 0; row < rows; row++) grid.RowStyles.Add(new RowStyle(SizeType.Percent, 100f / rows));
            for (int i = 0; i < items.Length; i++)
            {
                string topic = items[i].Topic; var button = ActionButton(items[i].Name);
                button.Dock = DockStyle.Fill; button.Padding = Padding.Empty; button.Font = new Font("Segoe UI", 9); button.AutoSize = false;
                button.AccessibleName = "Ask engineer: " + items[i].Name;
                button.Click += async (_, _) => await GuardAsync(() => AskAsync(topic)); grid.Controls.Add(button, i % 2, i / 2);
            }
            group.Controls.Add(grid); commands.TabPages.Add(group);
        }
        Group("Pace", [("pace", "Current pace"), ("personal_best", "My best lap"), ("race_best", "Session best"), ("others_best", "Other drivers"), ("last_lap", "Last lap"), ("feedback", "Driver feedback")]);
        Group("Race", [("gap_ahead", "Gap ahead"), ("gap_behind", "Gap behind"), ("gap_leader", "Gap to leader"), ("strategy", "Race strategy"), ("track_limits", "Track limits"), ("status", "Car status")]);
        Group("Car", [("fuel", "Fuel"), ("tyres", "Tyres"), ("change_compound", "Change compound"), ("energy", "Hybrid energy")]);
        Group("Coach", [("next_corner", "Next corner"), ("corners", "Corner losses"), ("quiet", "Quiet radio"), ("more_updates", "More updates")]);
        radio.Controls.Add(commands, 0, 1);
        var ask = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2 }; ask.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100)); ask.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        var send = ActionButton("Ask"); send.Padding = new Padding(6, 2, 6, 2); send.Click += async (_, _) => await GuardAsync(() => AskAsync(_question.Text));
        _question.AccessibleName = "Engineer question"; _question.Margin = new Padding(0, 5, 5, 0);
        _question.KeyDown += async (_, e) => { if (e.KeyCode == Keys.Enter) { e.SuppressKeyPress = true; await GuardAsync(() => AskAsync(_question.Text)); } };
        ask.Controls.Add(_question, 0, 0); ask.Controls.Add(send, 1, 0); radio.Controls.Add(ask, 0, 2);
        var replyPanel = new Panel { Dock = DockStyle.Fill, BackColor = Surface };
        _radioReply.Dock = DockStyle.Fill; _radioReply.Padding = new Padding(8); _radioReply.BackColor = Surface;
        replyPanel.Controls.Add(_radioReply); replyPanel.Controls.Add(_radioChoices); radio.Controls.Add(replyPanel, 0, 3);
        _pttNote.Dock = DockStyle.Fill; _pttNote.AccessibleName = "Push-to-talk status"; radio.Controls.Add(_pttNote, 0, 4);
        _radioLog.AccessibleName = "Engineer radio history"; _radioLog.SelectedIndexChanged += (_, _) => { if (_radioLog.SelectedItem is RadioLine item) _radioReply.Text = item.FullText; };
        radio.Controls.Add(_radioLog, 0, 5); root.Controls.Add(radio, 1, 0);
        page.Controls.Add(root); return page;
    }

    private TabPage BuildHistory()
    {
        var page = Page("Lap analysis");
        var root = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(12), ColumnCount = 2, RowCount = 1 };
        root.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 310)); root.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        var picker = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 7, Padding = new Padding(0, 0, 12, 0) };
        picker.RowStyles.Add(new RowStyle(SizeType.Absolute, 32)); picker.RowStyles.Add(new RowStyle(SizeType.Percent, 38));
        picker.RowStyles.Add(new RowStyle(SizeType.Absolute, 32)); picker.RowStyles.Add(new RowStyle(SizeType.Percent, 62));
        picker.RowStyles.Add(new RowStyle(SizeType.Absolute, 34)); picker.RowStyles.Add(new RowStyle(SizeType.Absolute, 76)); picker.RowStyles.Add(new RowStyle(SizeType.Absolute, 44));
        var title = TextLabel("1  CHOOSE A SESSION", 10, Lime, true); title.Dock = DockStyle.Fill; picker.Controls.Add(title, 0, 0);
        AddColumn(_sessionGrid, "When", "RECORDED", 90); AddColumn(_sessionGrid, "Source", "SOURCE", 60);
        AddColumn(_sessionGrid, "Car", "CAR / TRACK", 190); AddColumn(_sessionGrid, "Track", "TRACK", 180);
        AddColumn(_sessionGrid, "Laps", "LAPS", 50); AddColumn(_sessionGrid, "Best", "BEST", 80);
        foreach (string name in new[] { "Source", "Track", "Laps", "Best" }) _sessionGrid.Columns[name]!.Visible = false;
        _sessionGrid.Columns["Car"]!.AutoSizeMode = DataGridViewAutoSizeColumnMode.Fill;
        _sessionGrid.DefaultCellStyle.WrapMode = DataGridViewTriState.True; _sessionGrid.Columns["Car"]!.DefaultCellStyle.WrapMode = DataGridViewTriState.True; _sessionGrid.RowTemplate.Height = 58;
        _sessionGrid.SelectionChanged += async (_, _) => { if (!_historyLoading && _sessionGrid.SelectedRows.Count > 0 && _sessionGrid.SelectedRows[0].Tag is string id && id != _selectedSession) await GuardAsync(() => LoadSessionAsync(id)); };
        picker.Controls.Add(_sessionGrid, 0, 1);
        var lapTitle = TextLabel("2  CHOOSE A LAP", 10, Lime, true); lapTitle.Dock = DockStyle.Fill; picker.Controls.Add(lapTitle, 0, 2);
        AddColumn(_lapGrid, "Lap", "LAP", 44); AddColumn(_lapGrid, "Time", "TIME", 82); AddColumn(_lapGrid, "Delta", "DELTA", 90);
        AddColumn(_lapGrid, "Fuel", "FUEL", 80); AddColumn(_lapGrid, "Validity", "STATUS", 150);
        _lapGrid.Columns["Delta"]!.Visible = _lapGrid.Columns["Fuel"]!.Visible = false;
        _lapGrid.Columns["Validity"]!.AutoSizeMode = DataGridViewAutoSizeColumnMode.Fill;
        _lapGrid.SelectionChanged += async (_, _) => { if (!_historyLoading && _lapGrid.SelectedRows.Count > 0 && _lapGrid.SelectedRows[0].Tag is string id) { _selectedLap = id; FillReferences(); UpdateLapActions(); await GuardAsync(AnalyzeSelectedAsync); } };
        picker.Controls.Add(_lapGrid, 0, 3); picker.Controls.Add(_showDeleted, 0, 4);
        var edits = Flow(); edits.Dock = DockStyle.Fill; edits.WrapContents = true;
        _remove.Text = "Remove lap"; _restore.Text = "Restore"; _undo.Text = "Undo removal";
        edits.Controls.AddRange([_remove, _restore, _undo]); picker.Controls.Add(edits, 0, 5);
        _remove.Click += async (_, _) => await GuardAsync(() => EditLapAsync(false)); _restore.Click += async (_, _) => await GuardAsync(() => EditLapAsync(true)); _undo.Click += async (_, _) => await GuardAsync(UndoAsync);
        _showDeleted.CheckedChanged += async (_, _) => await GuardAsync(LoadSessionsAsync);
        var tools = Flow(); tools.Dock = DockStyle.Fill;
        var refresh = ActionButton("Refresh"); refresh.Click += async (_, _) => await GuardAsync(LoadSessionsAsync);
        var export = ActionButton("Export…"); export.Click += async (_, _) => await GuardAsync(ExportSessionAsync);
        var folder = ActionButton("Files"); folder.Click += (_, _) => OpenDataFolder(); tools.Controls.AddRange([refresh, export, folder]); picker.Controls.Add(tools, 0, 6);
        root.Controls.Add(picker, 0, 0);
        var detail = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 6 };
        detail.RowStyles.Add(new RowStyle(SizeType.Absolute, 78)); detail.RowStyles.Add(new RowStyle(SizeType.Absolute, 42));
        detail.RowStyles.Add(new RowStyle(SizeType.Percent, 100)); detail.RowStyles.Add(new RowStyle(SizeType.Absolute, 28));
        detail.RowStyles.Add(new RowStyle(SizeType.Absolute, 130)); detail.RowStyles.Add(new RowStyle(SizeType.Absolute, 90));
        _savedChart.MinimumSize = new Size(300, 200);
        _lapSummary.Dock = DockStyle.Fill; detail.Controls.Add(_lapSummary, 0, 0);
        var compare = Flow(); compare.Dock = DockStyle.Fill;
        _references.Width = 285; _references.Margin = new Padding(0, 6, 7, 0); _analyze.Text = "Compare";
        var fullLap = ActionButton("Full lap"); fullLap.Click += (_, _) => { _savedChart.ResetZoom(); _cornerDetail.Text = "Select any detected corner below to zoom in. Drag across the graph to inspect a smaller section. Double-click to show the full lap."; };
        compare.Controls.AddRange([_references, _analyze, fullLap]); detail.Controls.Add(compare, 0, 1);
        _analyze.Click += async (_, _) => await GuardAsync(AnalyzeSelectedAsync);
        _references.SelectedIndexChanged += async (_, _) => { if (!_historyLoading) await GuardAsync(AnalyzeSelectedAsync); };
        _savedChart.Text = "Selected lap (A) / reference (B) · hover values · drag to zoom"; detail.Controls.Add(_savedChart, 0, 2);
        _savedNote.Dock = DockStyle.Fill; _savedNote.AutoEllipsis = true; detail.Controls.Add(_savedNote, 0, 3);
        CornerColumns(_savedCorners); _savedCorners.SelectionChanged += (_, _) => FocusSelectedCorner();
        _savedCorners.CellClick += (_, e) => { if (e.RowIndex >= 0) FocusSelectedCorner(); };
        detail.Controls.Add(_savedCorners, 0, 4);
        _cornerDetail.Padding = new Padding(10); _cornerDetail.LinkClicked += (_, e) => OpenSource(e.LinkText); detail.Controls.Add(_cornerDetail, 0, 5);
        root.Controls.Add(detail, 1, 0); UpdateLapActions(); page.Controls.Add(root); return page;
    }

    private TabPage BuildSources()
    {
        var page = Page("Reference library");
        var layout = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(16), ColumnCount = 2, RowCount = 3 };
        layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 42)); layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 58));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 60)); layout.RowStyles.Add(new RowStyle(SizeType.Percent, 48)); layout.RowStyles.Add(new RowStyle(SizeType.Percent, 52));
        _sourceChart.MinimumSize = new Size(300, 180);
        var tools = Flow(); tools.Dock = DockStyle.Fill;
        var title = TextLabel("REAL DATA & COACHING SOURCES", 14, Lime, true); title.Width = 365; title.Height = 40;
        var import = ActionButton("Import reference CSV…"); import.Click += async (_, _) => await GuardAsync(ImportReferenceAsync);
        tools.Controls.AddRange([title, import]); layout.Controls.Add(tools, 0, 0); layout.SetColumnSpan(tools, 2);
        AddColumn(_sourceGrid, "Type", "SOURCE", 95); AddColumn(_sourceGrid, "Name", "REFERENCE / METHOD", 270); AddColumn(_sourceGrid, "Time", "LAP TIME", 90);
        _sourceGrid.Columns["Name"]!.AutoSizeMode = DataGridViewAutoSizeColumnMode.Fill;
        _sourceGrid.SelectionChanged += async (_, _) => await GuardAsync(ShowSourceAsync);
        layout.Controls.Add(_sourceGrid, 0, 1); layout.SetRowSpan(_sourceGrid, 2);
        layout.Controls.Add(_sourceChart, 1, 1); _sourceText.LinkClicked += (_, e) => OpenSource(e.LinkText); layout.Controls.Add(_sourceText, 1, 2);
        page.Controls.Add(layout); return page;
    }

    private async Task AskAsync(string topic, string? compound = null)
    {
        if (string.IsNullOrWhiteSpace(topic)) return;
        var body = new JsonObject { ["topic"] = topic };
        if (compound is not null) body["compound"] = compound;
        using var content = new StringContent(body.ToJsonString(), Encoding.UTF8, "application/json");
        using var response = await _http.PostAsync("api/radio", content);
        if (!response.IsSuccessStatusCode) throw new InvalidOperationException(await ErrorTextAsync(response));
        var answer = JsonNode.Parse(await response.Content.ReadAsStringAsync());
        _radioReply.Text = "Reply at " + DateTime.Now.ToShortTimeString() + "\n" + Str(answer?["text"]); _lastReplyKey = Str(answer?["id"]); _settingsLoaded = false;
        RenderRadioChoices(answer);
        _status.Text = "Engineer replied · " + SpeechReplyNote(answer);
    }

    private static string SpeechReplyNote(JsonNode? reply) => Str(reply?["speech_status"]) switch
    {
        "started" or "speaking" => "speaking answer",
        "queued" => "spoken answer queued",
        "muted" => "voice is switched off in Settings",
        "unavailable" or "failed" => "speech unavailable · answer is shown here",
        _ => "answer shown below"
    };

    private void RenderRadioChoices(JsonNode? answer)
    {
        foreach (Control control in _radioChoices.Controls.Cast<Control>().ToArray()) control.Dispose();
        _radioChoices.Controls.Clear();
        foreach (var choice in answer?["choices"] as JsonArray ?? [])
        {
            string topic = Str(choice?["topic"], "change_compound"), compound = Str(choice?["compound"]);
            if (compound.Length == 0) continue;
            var button = ActionButton(Str(choice?["label"], compound)); button.Padding = new Padding(6, 1, 6, 1); button.Font = new Font("Segoe UI", 9);
            button.Click += async (_, _) => await GuardAsync(() => AskAsync(topic, compound)); _radioChoices.Controls.Add(button);
        }
        _radioChoices.Visible = _radioChoices.Controls.Count > 0;
    }

    protected override void WndProc(ref Message message)
    {
        if (message.Msg == 0x0312 && message.WParam.ToInt32() == PttHotkeyId && !_pttHeld && !_learningButton && _pttBinding.Kind == "keyboard")
        {
            _ = GuardAsync(BeginPttAsync);
        }
        base.WndProc(ref message);
    }

    private void RegisterPtt()
    {
        if (!IsHandleCreated || _formClosed || _loadingPtt) return;
        UnregisterHotKey(Handle, PttHotkeyId);
        if (_pttHeld) _ = EndPttAsync();
        _wheelGate.Reset();
        _bindingNote.Text = (_pttBinding.Kind == "joystick" ? "Wheel · " : "Keyboard · ") + _pttBinding.Caption;
        if (_learningButton) return;
        string key = _pttBinding.Key;
        _pttVirtualKey = key.Length == 1 && char.IsDigit(key[0]) ? (int)Keys.D0 + key[0] - '0' : Enum.TryParse<Keys>(key, out var parsed) ? (int)parsed : (int)Keys.F10;
        if (_pttEnabled.Checked && _pttBinding.Kind == "keyboard" && !RegisterHotKey(Handle, PttHotkeyId, 0x4000, (uint)_pttVirtualKey))
            _pttNote.Text = key + " is in use. Choose another push-to-talk key in Settings.";
        else _pttNote.Text = _pttEnabled.Checked ? "Hold " + _pttBinding.Caption + " to speak · English commands" : "Push-to-talk disabled · use the quick menu";
    }

    private async Task PollPttAsync()
    {
        if (_formClosed || _learningButton) return;
        long now = Environment.TickCount64;
        if (now >= _nextControllerStatus)
        {
            _nextControllerStatus = now + 2000;
            var devices = _wheel.Discover().Where(d => d.Buttons > 0).ToArray();
            _controllerNote.Text = devices.Length == 0 ? "No wheel or controller detected by Windows. Keyboard push-to-talk remains available."
                : "Detected: " + string.Join(" · ", devices.Select(d => d.Name + " (" + d.Buttons + " buttons)"));
        }
        if (!_pttEnabled.Checked) { if (_pttHeld) await EndPttAsync(); return; }
        if (_pttBinding.Kind == "keyboard")
        {
            if (_pttHeld && (GetAsyncKeyState(_pttVirtualKey) & 0x8000) == 0) await EndPttAsync();
            return;
        }
        var sample = _wheel.Read(_pttBinding.DeviceId);
        bool connected = sample.HasValue && _pttBinding.Matches(sample.Value.Device);
        bool down = connected && (sample!.Value.Buttons & (1u << _pttBinding.Button)) != 0;
        bool? edge = _wheelGate.Update(connected, down, now);
        if (edge == true) await GuardAsync(BeginPttAsync);
        else if (edge == false || !connected && _pttHeld) await EndPttAsync();
        if (!connected) _pttNote.Text = "Bound wheel disconnected or changed · reconnect it, or bind another button in Settings";
        else if (_pttNote.Text.StartsWith("Bound wheel disconnected")) _pttNote.Text = "Hold " + _pttBinding.Caption + " to speak · English commands";
    }

    private async Task QueuePttAsync(string action)
    {
        Task previous = _pttCommand;
        int sequence = _pttSequence;
        _pttCommand = Send();
        async Task Send()
        {
            try { await previous; } catch { /* A release must still follow a failed start. */ }
            if (_formClosed || action == "start" && (sequence != _pttSequence || !_pttHeld || _learningButton || !_pttEnabled.Checked)) return;
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(2));
            await PostAsync("api/ptt", new JsonObject { ["action"] = action }, timeout.Token);
        }
        await _pttCommand;
    }

    private async Task BeginPttAsync()
    {
        if (_pttHeld || !_pttEnabled.Checked || _learningButton) return;
        Program.Backend.MarkPushToTalkUsed();
        _pttHeld = true; _pttSequence++; _pttNote.Text = "Starting microphone… keep holding your button";
        try { await QueuePttAsync("start"); }
        catch { _pttHeld = false; _pttSequence++; _ = GuardAsync(() => QueuePttAsync("stop")); throw; }
    }

    private async Task EndPttAsync()
    {
        if (!_pttHeld) return;
        _pttHeld = false; _pttSequence++;
        await GuardAsync(() => QueuePttAsync("stop"));
        if (!_formClosed && !_learningButton) _pttNote.Text = "Hold " + _pttBinding.Caption + " to speak · English commands";
    }

    private async Task LearnWheelButtonAsync()
    {
        // End recognition before entering learn mode. The dialog has no PTT or microphone path.
        _learningButton = true; UnregisterHotKey(Handle, PttHotkeyId);
        try
        {
            await EndPttAsync();
            await QueuePttAsync("stop");
            using var dialog = new Form { Text = "Bind a wheel button", Size = new Size(560, 280), StartPosition = FormStartPosition.CenterParent, FormBorderStyle = FormBorderStyle.FixedDialog, MaximizeBox = false, MinimizeBox = false, BackColor = Background, ForeColor = Ink, Font = new Font("Segoe UI", 10), AutoScaleMode = AutoScaleMode.Dpi, AutoScaleDimensions = new SizeF(96, 96) };
            var text = TextLabel("Release your wheel buttons, then press the button you want to hold for radio.\n\nMicrophone is off while learning.", 12, Ink);
            text.Dock = DockStyle.Fill; text.Padding = new Padding(20);
            var detected = TextLabel("Detecting controllers…", 10, Muted); detected.Dock = DockStyle.Bottom; detected.Height = 65; detected.Padding = new Padding(20, 0, 20, 0);
            var cancel = ActionButton("Cancel"); cancel.Dock = DockStyle.Bottom; cancel.DialogResult = DialogResult.Cancel; dialog.CancelButton = cancel;
            dialog.Controls.Add(text); dialog.Controls.Add(detected); dialog.Controls.Add(cancel);
            var learner = new ButtonLearner(); PttBinding? learned = null;
            using var timer = new System.Windows.Forms.Timer { Interval = 40 };
            timer.Tick += (_, _) =>
            {
                var samples = _wheel.ReadAll().Where(s => s.Device.Buttons > 0).ToArray();
                detected.Text = samples.Length == 0 ? "No compatible controller detected. Connect your wheel and try again." : "Detected: " + string.Join(" · ", samples.Select(s => s.Device.Name));
                learned = learner.Observe(samples);
                if (learned is not null) { timer.Stop(); dialog.DialogResult = DialogResult.OK; dialog.Close(); }
            };
            dialog.Shown += (_, _) => timer.Start();
            if (dialog.ShowDialog(this) == DialogResult.OK && learned is not null)
            {
                _pttBinding = learned;
                await PostAsync("api/settings", new JsonObject { ["ptt_binding"] = learned.Json() });
                _settingsNote.Text = "Radio button saved: " + learned.Caption + ". Release it, then hold it to speak.";
            }
        }
        finally { _learningButton = false; RegisterPtt(); }
    }

    private void FocusSelectedCorner()
    {
        if (_savedCorners.SelectedRows.Count == 0 || _savedCorners.SelectedRows[0].Tag is not JsonNode c) return;
        _savedChart.FocusRange(Num(c["section_start_pos"]) ?? 0, Num(c["section_end_pos"]) ?? 1);
        var text = new StringBuilder();
        var priority = (_savedAnalysis?["feedback"]?["priorities"] as JsonArray ?? [])
            .FirstOrDefault(p => Num(p?["corner"]) == Num(c["number"]));
        if (priority is not null)
        {
            text.AppendLine(Str(priority["instruction"]));
            text.AppendLine(Str(priority["experiment"]));
            text.Append("Phase: ").Append(Str(priority["phase"]).Replace('_', ' ')).Append(" · ").AppendLine(Bool(priority["repeated"]) ? "Repeated evidence" : "One comparison · confirm on another clean lap");
            foreach (var evidence in priority["evidence"] as JsonArray ?? []) text.Append("• ").AppendLine(Str(evidence));
            foreach (var caveat in priority["caveats"] as JsonArray ?? []) text.AppendLine(Str(caveat));
            text.AppendLine();
        }
        text.Append("Detected corner ").Append(Str(c["number"])).Append(" + exit straight: ").Append(Delta(Num(c["time_loss_s"]))).AppendLine(" against B.");
        text.Append("Entry A/B ").Append(Fixed(Num(c["entry_speed_kmh"]), 0)).Append('/').Append(Fixed(Num(c["reference_entry_speed_kmh"]), 0));
        text.Append(" · minimum ").Append(Fixed(Num(c["minimum_speed_kmh"]), 0)).Append('/').Append(Fixed(Num(c["reference_minimum_speed_kmh"]), 0));
        text.Append(" · exit ").Append(Fixed(Num(c["exit_speed_kmh"]), 0)).Append('/').Append(Fixed(Num(c["reference_exit_speed_kmh"]), 0)).AppendLine(" km/h.");
        if (Num(c["full_throttle_delta_m"]) is double throttle) text.Append("Full throttle ").Append(Math.Abs(throttle).ToString("0")).Append(throttle > 0 ? " m later" : " m earlier").AppendLine(" than B.");
        foreach (var guidance in (c["guidance"] as JsonArray ?? []).Where(g => Str(g?["id"]) != "distance-before-verdict").Take(1))
        {
            text.AppendLine(Str(guidance?["experiment"]));
            text.Append("Testable suggestion · ").AppendLine(Str(guidance?["limitations"]));
            foreach (var source in guidance?["sources"] as JsonArray ?? []) text.Append(Str(source?["publisher"])).Append(" · ").AppendLine(Str(source?["url"]));
        }
        text.Append("Section labels are detected from telemetry; they are not official track turn numbers.");
        _cornerDetail.Text = text.ToString();
    }

    private async Task LoadSourcesAsync()
    {
        _catalog = await GetAsync("api/references");
        _sourceGrid.Rows.Clear();
        foreach (var r in _catalog?["references"] as JsonArray ?? [])
        {
            if (r is null) continue;
            int row = _sourceGrid.Rows.Add("Human lap", Pretty(Str(r["metadata"]?["car_id"])) + " · " + Pretty(Str(r["metadata"]?["track_id"])), Lap(Num(r["lap_time_ms"])));
            _sourceGrid.Rows[row].Tag = new JsonObject { ["type"] = "reference", ["payload"] = r.DeepClone() };
        }
        foreach (var r in _catalog?["benchmarks"] as JsonArray ?? [])
        {
            if (r is null) continue;
            int row = _sourceGrid.Rows.Add("Real record", Str(r["driver"]) + " · " + Str(r["year"]), Lap(Num(r["lap_time_ms"])));
            _sourceGrid.Rows[row].Tag = new JsonObject { ["type"] = "record", ["payload"] = r.DeepClone() };
        }
        foreach (var r in _catalog?["knowledge"]?["principles"] as JsonArray ?? [])
        {
            if (r is null) continue;
            int row = _sourceGrid.Rows.Add("Coaching", Str(r["title"]), "");
            _sourceGrid.Rows[row].Tag = new JsonObject { ["type"] = "principle", ["payload"] = r.DeepClone() };
        }
        if (_sourceGrid.Rows.Count > 0) { _sourceGrid.CurrentCell = _sourceGrid.Rows[0].Cells[0]; _sourceGrid.Rows[0].Selected = true; }
        await ShowSourceAsync();
    }

    private async Task ShowSourceAsync()
    {
        if (_sourceGrid.SelectedRows.Count == 0 || _sourceGrid.SelectedRows[0].Tag is not JsonNode tag) return;
        var r = tag["payload"]; string type = Str(tag["type"]); var text = new StringBuilder();
        _sourceChart.SetData(null);
        if (type == "reference")
        {
            var full = await GetAsync("api/references/" + Uri.EscapeDataString(Str(r?["id"])));
            if (_sourceGrid.SelectedRows.Count == 0 || !ReferenceEquals(_sourceGrid.SelectedRows[0].Tag, tag)) return;
            _sourceChart.SetData(new JsonObject { ["current"] = full?["trace"]?.DeepClone() });
            var m = r?["metadata"];
            text.AppendLine("PUBLISHED / IMPORTED TELEMETRY · INSPECTION");
            text.AppendLine(Pretty(Str(m?["car_id"])) + " · " + Pretty(Str(m?["track_id"])) + " · " + Str(m?["layout"]));
            text.AppendLine("Lap " + Lap(Num(r?["lap_time_ms"])) + " · " + Str(m?["recorded_at"]) + " · " + Str(m?["tyre_compound"]));
            text.AppendLine(Str(m?["attribution"])); text.AppendLine("License: " + Str(m?["license"])); text.AppendLine(Str(m?["source_url"]));
            text.AppendLine(); text.AppendLine(Bool(r?["eligibility"]?["compatible"]) ? "Metadata screening passed. External targets are not activated automatically." : "Context only. Your own comparable clean laps remain the live reference.");
            text.AppendLine(string.Join("\n", (r?["eligibility"]?["reasons"] as JsonArray ?? []).Select(n => "• " + Str(n))));
            text.AppendLine(); text.AppendLine(Str(full?["quality"]?["note"]));
        }
        else if (type == "record")
        {
            text.AppendLine("REAL-WORLD LAP RECORD · CONTEXT ONLY"); text.AppendLine(Str(r?["label"]));
            text.AppendLine(Str(r?["driver"]) + " · " + Str(r?["car"]) + " · " + Lap(Num(r?["lap_time_ms"])));
            text.AppendLine(Str(r?["note"])); text.AppendLine(Str(r?["source_url"]));
            text.AppendLine(); text.AppendLine("This record does not provide speed, brake or throttle telemetry. It cannot explain your AC corner losses.");
        }
        else
        {
            text.AppendLine(Str(r?["title"])); text.AppendLine(); text.AppendLine(Str(r?["mechanism"]));
            text.AppendLine("CHECK: " + Str(r?["check"])); text.AppendLine("TRY: " + Str(r?["experiment"])); text.AppendLine("LIMIT: " + Str(r?["limitations"]));
            var ids = (r?["source_ids"] as JsonArray ?? []).Select(n => Str(n)).ToHashSet();
            foreach (var source in _catalog?["knowledge"]?["sources"] as JsonArray ?? [])
                if (ids.Contains(Str(source?["id"]))) { text.AppendLine(); text.AppendLine(Str(source?["publisher"]) + " · " + Str(source?["title"])); text.AppendLine(Str(source?["url"])); }
        }
        _sourceText.Text = text.ToString();
    }

    private async Task ImportReferenceAsync()
    {
        using var file = new OpenFileDialog { Title = "Choose a one-lap telemetry CSV", Filter = "Telemetry CSV (*.csv)|*.csv", CheckFileExists = true };
        if (file.ShowDialog(this) != DialogResult.OK) return;
        using var dialog = new Form { Text = "Describe this reference lap", StartPosition = FormStartPosition.CenterParent, Size = new Size(650, 610), MinimumSize = new Size(650, 610), BackColor = Background, ForeColor = Ink, Font = Font, AutoScaleMode = AutoScaleMode.Dpi };
        var layout = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(18), ColumnCount = 2, RowCount = 11 };
        layout.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 155)); layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        var intro = TextLabel("Import one complete lap. Use normalized_pos, lap_time_ms, speed_kmh, gas and brake columns. MoTeC exports need explicit channel units. Unknown conditions keep this lap for inspection.", 10, Muted); intro.Dock = DockStyle.Fill;
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 80)); layout.Controls.Add(intro, 0, 0); layout.SetColumnSpan(intro, 2);
        string[] fields = ["Car ID", "Track ID", "Layout ID", "Tyre compound", "Driver / attribution", "Source URL", "Recorded date"];
        string[] defaults = [Str(_state?["telemetry"]?["car_id"]), Str(_state?["telemetry"]?["track_id"]), Str(_state?["telemetry"]?["layout"]), Str(_state?["telemetry"]?["tyre_compound"]), "My own driving", "local://user-export", DateTime.Today.ToString("yyyy-MM-dd")];
        var inputs = new TextBox[fields.Length];
        for (int i = 0; i < fields.Length; i++) { layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 38)); var label = TextLabel(fields[i], 10, Ink); label.Dock = DockStyle.Fill; layout.Controls.Add(label, 0, i+1); inputs[i] = new TextBox { Text = defaults[i], Dock = DockStyle.Fill, BackColor = Surface, ForeColor = Ink, MaxLength = 1000 }; layout.Controls.Add(inputs[i], 1, i+1); }
        var own = new CheckBox { Text = "I own this export or have permission to use it", ForeColor = Ink, Dock = DockStyle.Fill }; layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 40)); layout.Controls.Add(own, 0, 8); layout.SetColumnSpan(own, 2);
        var note = TextLabel("Original AC · local import · never changes your recordings or live reference. Full version, setup and condition verification is required before any external target can be trusted.", 9, Muted); note.Dock = DockStyle.Fill; layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100)); layout.Controls.Add(note, 0, 9); layout.SetColumnSpan(note, 2);
        var buttons = Flow(); buttons.Dock = DockStyle.Fill; layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 45));
        var import = ActionButton("Import for inspection"); import.DialogResult = DialogResult.OK; import.Enabled = false; own.CheckedChanged += (_, _) => import.Enabled = own.Checked;
        var cancel = ActionButton("Cancel"); cancel.DialogResult = DialogResult.Cancel; buttons.Controls.AddRange([import, cancel]); layout.Controls.Add(buttons, 0, 10); layout.SetColumnSpan(buttons, 2); dialog.Controls.Add(layout); dialog.AcceptButton = import; dialog.CancelButton = cancel;
        if (dialog.ShowDialog(this) != DialogResult.OK) return;
        var metadata = new JsonObject { ["kind"] = "simulator", ["game"] = "assetto_corsa", ["car_id"] = inputs[0].Text.Trim(), ["track_id"] = inputs[1].Text.Trim(), ["layout"] = inputs[2].Text.Trim(), ["tyre_compound"] = inputs[3].Text.Trim(), ["attribution"] = inputs[4].Text.Trim(), ["source_url"] = inputs[5].Text.Trim(), ["recorded_at"] = inputs[6].Text.Trim(), ["license"] = "LicenseRef-UserExport", ["rights_confirmed"] = true, ["conditions"] = new JsonObject(), ["verification"] = new JsonObject() };
        await PostAsync("api/references/import", new JsonObject { ["path"] = file.FileName, ["metadata"] = metadata });
        await LoadSourcesAsync(); _status.Text = "Reference imported for inspection. Source and condition information is saved with the lap.";
    }

    private static void OpenSource(string? url)
    {
        if (Uri.TryCreate(url, UriKind.Absolute, out var target) && target.Scheme is "https" or "http") Process.Start(new ProcessStartInfo(target.AbsoluteUri) { UseShellExecute = true });
    }

    private TabPage BuildSettings()
    {
        var page = Page("Settings");
        var scroll = new Panel { Dock = DockStyle.Fill, AutoScroll = true, Padding = new Padding(28), BackColor = Background };
        var content = new FlowLayoutPanel { FlowDirection = FlowDirection.TopDown, WrapContents = false, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, Width = 900, BackColor = Background };
        var title = TextLabel("YOUR ENGINEER, YOUR WAY", 20, Ink, true);
        title.Size = new Size(880, 42); content.Controls.Add(title);
        content.Controls.Add(Spacer(12));
        _engine.Items.AddRange(["AC Pro Engineer · free native engine", "APEX live engineer"]); _engine.Width = 350;
        content.Controls.Add(SettingRow("Analysis engine", _engine));
        var engineButtons = Flow(); engineButtons.Width = 880; engineButtons.Height = 45;
        var applyEngine = ActionButton("Use selected engine"); applyEngine.Click += async (_, _) => await RunActionAsync(_engine.SelectedIndex == 0 ? "engine_pro" : "engine_local");
        var start = ActionButton("Start Pro Engineer"); start.Click += async (_, _) => await RunActionAsync("engine_start");
        engineButtons.Controls.AddRange([applyEngine, start]); content.Controls.Add(engineButtons);
        var nativeNote = TextLabel("Pro Engineer opens its own controls for a live session. Start AC, then press Enter on Start in that window.\nIts tyre targets and rule settings are managed there; the preferences below do not rewrite its rules.\nThe unmodified native app may check for updates and install/update its own CSP overlay.", 10, Muted);
        nativeNote.Size = new Size(870, 78); content.Controls.Add(nativeNote); content.Controls.Add(Spacer(15));
        _focus.Items.AddRange(["Balanced", "Stability", "Rotation"]); _focus.Width = 230;
        content.Controls.Add(SettingRow("APEX handling preference", _focus));
        content.Controls.Add(SettingRow("Fuel reserve · litres", _reserve));
        content.Controls.Add(_pressureSet); _pressureSet.Margin = new Padding(0, 13, 0, 9);
        content.Controls.Add(SettingRow("Target hot pressure · psi", _pressure));
        _pressureSet.CheckedChanged += (_, _) => _pressure.Enabled = _pressureSet.Checked && Bool(_state?["connected"]);
        _verbosity.Items.AddRange(["Fewer updates", "Balanced", "More updates"]); _verbosity.Width = 230;
        content.Controls.Add(SettingRow("Radio frequency", _verbosity));
        _pttKey.Items.AddRange(PttBinding.KeyboardKeys); _pttKey.Width = 120;
        content.Controls.Add(_pttEnabled); _pttEnabled.Margin = new Padding(0, 8, 0, 8);
        content.Controls.Add(SettingRow("Hold to speak · global key", _pttKey));
        _pttEnabled.CheckedChanged += (_, _) => RegisterPtt();
        _pttKey.SelectedIndexChanged += (_, _) => { if (!_loadingPtt) { _pttBinding = new PttBinding("keyboard", _pttKey.SelectedItem?.ToString() ?? "F10"); RegisterPtt(); } };
        var bindingTools = Flow(); bindingTools.Width = 880; bindingTools.Height = 44;
        var learn = ActionButton("Bind wheel button…"); learn.Click += async (_, _) => await GuardAsync(LearnWheelButtonAsync);
        _bindingNote.Size = new Size(500, 36); _bindingNote.Padding = new Padding(5, 5, 0, 0);
        bindingTools.Controls.AddRange([learn, _bindingNote]); content.Controls.Add(bindingTools);
        _controllerNote.Size = new Size(870, 48); content.Controls.Add(_controllerNote);
        var keyNote = TextLabel("Choose an unused button or key. APEX reserves the keyboard key while radio input is enabled.\nWheel learning does not open the microphone. Button numbers shown here start at 1.", 9, Muted);
        keyNote.Size = new Size(870, 48); content.Controls.Add(keyNote);
        content.Controls.Add(_quiet); _quiet.Margin = new Padding(0, 10, 0, 10);
        content.Controls.Add(_keepRecording); _keepRecording.Margin = new Padding(0, 5, 0, 10);
        _keepRecording.CheckedChanged += (_, _) => { if (_keepRecording.Checked) Program.Backend.KeepRunningOnExit(); else Program.Backend.StopOnExit(); };
        content.Controls.Add(_voice); _voice.Margin = new Padding(0, 13, 0, 12);
        var save = ActionButton("Save preferences"); save.BackColor = Lime; save.ForeColor = Background;
        save.Click += async (_, _) => await GuardAsync(SavePreferencesAsync); content.Controls.Add(save);
        _settingsNote.Size = new Size(870, 75); _settingsNote.Margin = new Padding(0, 15, 0, 12); content.Controls.Add(_settingsNote);
        var notes = TextLabel("RECORDINGS STAY ON YOUR COMPUTER\nEvery recorded session is available under Lap analysis after restarting APEX. Full exports contain the original recorded samples.\nRemoving a lap excludes it from APEX references, corner comparisons and fuel estimates; use Show removed laps to restore it.\nUnknown tyre targets and unavailable hybrid channels remain unset. This version does not automatically alter your garage setup.", 10, Muted);
        notes.Size = new Size(880, 120); content.Controls.Add(notes);
        var modes = Flow(); modes.Width = 880; modes.Height = 44;
        AddAction(modes, "Use live AC telemetry", "live"); AddAction(modes, "Explore demo", "demo"); content.Controls.Add(modes);
        scroll.Controls.Add(content); page.Controls.Add(scroll); return page;
    }

    private async Task PollAsync()
    {
        if (_polling || _formClosed) return;
        _polling = true;
        _pollTimer.Interval = WindowState == FormWindowState.Minimized ? 2000 : 500;
        try
        {
            var state = await GetAsync("api/state");
            if (!_formClosed) RenderLive(state);
        }
        catch (Exception error)
        {
            if (_formClosed) return;
            _status.Text = "Companion unavailable · " + error.Message;
            _status.ForeColor = Warm;
            _engineStatus.Text = "Live values are paused until the local companion reconnects.";
            _liveLap.Text = _liveDelta.Text = _liveFuel.Text = _liveSpeed.Text = "—";
            _liveFuelNote.Text = "No live fuel estimate";
            _tyres.Text = "No live telemetry · previous values are no longer current";
            _advice.Text = "The local companion is unavailable. Recorded session browsing resumes when it reconnects.";
            _homeConnection.Text = "Local engineer unavailable · reconnecting";
            if (_tabs.SelectedTab == _livePage)
            {
                _liveChart.SetData(null);
                RenderTiming(null);
            }
        }
        finally { _polling = false; }
    }

    private void RenderLive(JsonNode? state)
    {
        _state = state;
        RenderHome(state);
        var t = state?["telemetry"]; var a = state?["analysis"];
        bool connected = Bool(state?["connected"]), demo = Str(state?["mode"]) == "demo";
        bool active = connected && Str(t?["status"]) == "live";
        _dataDirectory = Str(state?["data_dir"], _dataDirectory ?? "");
        bool recording = Str(state?["lifecycle"]?["phase"]) == "session";
        _status.Text = (demo ? "DEMO · synthetic data" : active ? recording ? "LIVE · recording locally" : "AC connected · start a session to record" : connected ? "SESSION " + Str(t?["status"]).ToUpperInvariant() : "Waiting for Assetto Corsa") + "   |   " + DateTime.Now.ToShortTimeString();
        _status.ForeColor = active ? Lime : Muted;
        _liveIdentity.Text = connected ? Pretty(Str(t?["car_id"])) + "  /  " + Pretty(Str(t?["track_id"])) + (Str(t?["layout"]) is string layout && layout.Length > 0 ? " · " + Pretty(layout) : "") : "Ready when you are.";
        var engine = state?["engine"]; bool pro = Str(engine?["backend"]) == "pro_engineer";
        _engineStatus.Text = pro ? connected ? "AC Pro Engineer · receiving native telemetry and computed advice" : Bool(engine?["runtime"]?["running"]) ? "Pro Engineer is open. Start AC, then press Enter on Start in its window." : "Start Pro Engineer in Settings to connect the free native engine." : "APEX live engineer · telemetry, live calls and local recordings";
        if (Str(state?["error"]) is string error && error.Length > 0) _engineStatus.Text = error;
        _liveLap.Text = connected ? Lap(Num(t?["lap_time_ms"])) : "—";
        double? delta = Num(a?["coaching"]?["current_delta_s"]);
        _liveDelta.Text = active ? Delta(delta) : "—";
        _liveDelta.ForeColor = delta > 0 ? Warm : Lime;
        double? range = Num(a?["fuel"]?["laps_remaining"]);
        _liveFuel.Text = connected ? Fixed(Num(t?["fuel_l"])) + " L" : "—";
        _liveFuelNote.Text = connected && range.HasValue ? Fixed(range) + " laps above your reserve" : "Learning fuel use";
        _liveSpeed.Text = connected ? Fixed(Num(t?["speed_kmh"]), 0) + " km/h  " + Str(t?["gear"], "N") : "—";
        var radio = state?["radio"];
        var ptt = state?["push_to_talk"];
        if (Bool(ptt?["listening"])) _pttNote.Text = "LISTENING · say feedback, my best lap, gap ahead…";
        else if (Bool(ptt?["starting"])) _pttNote.Text = "Starting microphone… keep holding your button";
        else if (!string.IsNullOrEmpty(Str(ptt?["error"]))) _pttNote.Text = Str(ptt?["error"]);
        var messages = radio?["history"] as JsonArray;
        var reply = radio?["reply"];
        string replyKey = Str(reply?["id"]) + ":" + Str(reply?["speech_status"]);
        if (replyKey != _lastReplyKey) { _lastReplyKey = replyKey; _radioReply.Text = reply is null ? "Ask about fuel, tyres, pace or any detected corner." : "Reply at " + When(Num(reply["timestamp"])) + " · " + SpeechReplyNote(reply) + "\n" + Str(reply["text"]); RenderRadioChoices(reply); }
        string radioKey = messages?.LastOrDefault()?["id"]?.ToString() ?? "";
        if (radioKey != _radioLogKey) { _radioLogKey = radioKey; _radioLog.BeginUpdate(); _radioLog.Items.Clear(); foreach (var message in (messages ?? []).Reverse()) if (message is not null) _radioLog.Items.Add(new RadioLine(Str(message["text"]), Str(message["kind"]))); _radioLog.EndUpdate(); }
        var latest = messages?.LastOrDefault(m => (Num(m?["expires_at"]) ?? (Num(m?["timestamp"]) ?? 0) + 20) > DateTimeOffset.UtcNow.ToUnixTimeSeconds());
        _advice.Text = !connected ? Str(state?["telemetry_error"], "Start a driving session. Your first complete clean lap establishes a reference.") : !active ? "Session " + Str(t?["status"]) + ". Coaching is paused." : latest is not null ? Str(latest["text"]) : Str(a?["feedback"]?["instruction"], "Engineer active · ask for fuel, tyres, pace or the next corner.");
        string pressure = t?["tyre_pressure_psi"] is JsonArray tyres ? string.Join(" / ", tyres.Select(p => Fixed(Num(p)))) + " psi" : "unavailable";
        string charge = Bool(t?["capabilities"]?["energy"]) && Num(t?["kers_charge"]) is double energy ? Fixed(energy * 100, 0) + "%" : "unavailable";
        _tyres.Text = "TYRES FL / FR / RL / RR   " + pressure + "     |     HYBRID   " + charge + "     |     " + Str(t?["tyre_compound"]);
        if (_tabs.SelectedTab == _livePage)
            _liveChart.SetData(connected ? a?["traces"] : null);
        bool available = Bool(a?["coaching"]?["comparison_available"]);
        string key = available + ":" + (a?["corners"]?.ToJsonString() ?? "");
        if (key != _liveCornerKey)
        {
            _liveCornerKey = key;
            RenderCornerRows(_liveCorners, available ? a?["corners"] as JsonArray : null);
            _liveChart.SetCorners(available ? a?["corners"] : null);
        }
        _liveNote.Text = available ? "Last completed comparison · detected corner labels · exit straights included" : "Two comparable clean laps are needed for corner differences. Your first observed partial lap is excluded.";
        if (pro && demo && !available)
            _liveNote.Text = "The native demo omits track position. Use a real lap or the APEX local demo to explore corner comparisons.";
        if (_tabs.SelectedTab == _livePage) RenderTiming(state?["timing"]);
        string profileKey = Str(state?["mode"]) + ":" + Str(t?["car_id"]) + ":" + Str(t?["tyre_compound"]);
        if (profileKey != _settingsProfileKey)
        {
            _settingsProfileKey = profileKey;
            _settingsLoaded = false;
        }
        if (!_settingsLoaded)
        {
            var settings = state?["settings"];
            _voice.Checked = Bool(settings?["voice"]);
            _quiet.Checked = Bool(settings?["quiet"]);
            _loadingPtt = true;
            try
            {
                _pttBinding = PttBinding.Parse(settings?["ptt_binding"], Str(settings?["ptt_key"], "F10"));
                _pttKey.SelectedItem = _pttBinding.Kind == "keyboard" ? _pttBinding.Key : Str(settings?["ptt_key"], "F10");
                _pttEnabled.Checked = settings?["ptt_enabled"] is null || Bool(settings?["ptt_enabled"]);
            }
            finally { _loadingPtt = false; }
            RegisterPtt();
            _verbosity.SelectedIndex = Str(settings?["verbosity"]) switch { "less" => 0, "more" => 2, _ => 1 };
            _focus.SelectedIndex = Str(settings?["focus"]) switch { "stability" => 1, "rotation" => 2, _ => 0 };
            _engine.SelectedIndex = pro ? 0 : 1;
            _reserve.Value = ClampDecimal(Num(settings?["fuel_reserve_l"]), 0, 100, 2);
            _pressureSet.Checked = Num(settings?["target_pressure_psi"]).HasValue;
            _pressure.Value = ClampDecimal(Num(settings?["target_pressure_psi"]), 5, 80, 27);
            _settingsLoaded = true;
        }
        _pressureSet.Enabled = connected;
        _pressure.Enabled = connected && _pressureSet.Checked;
    }

    private void RenderHome(JsonNode? state)
    {
        var lifecycle = state?["lifecycle"]; var t = state?["telemetry"];
        string phase = Str(lifecycle?["phase"], "home"), sessionId = Str(lifecycle?["active_session_id"]);
        bool connected = Bool(state?["connected"]), armed = Bool(lifecycle?["armed"]), active = phase == "session";
        string? navigate = _navigation.Observe(phase, sessionId);
        if (navigate is not null) _tabs.SelectedTab = navigate == "session" ? _livePage : _homePage;
        _homeHeading.Text = phase == "debrief" ? "YOUR SESSION DEBRIEF" : active ? "YOUR SESSION IS UNDERWAY" : "READY FOR YOUR NEXT SESSION";
        string identity = Pretty(Str(t?["car_id"])) + "  /  " + Pretty(Str(t?["track_id"]));
        _homeConnection.Text = connected ? identity + "\n" + (Str(state?["mode"]) == "demo" ? "DEMO · synthetic telemetry" : "Assetto Corsa connected · " + Str(t?["status"], "waiting"))
            : armed ? "Session armed · start driving in Assetto Corsa\nAPEX will open your live engineer when telemetry arrives." : "Waiting for Assetto Corsa\nStart a session, then drive your chosen car and track.";
        _startSession.Text = active ? "Session in progress" : armed ? "Waiting for Assetto Corsa" : "Start session";
        _startSession.Enabled = !active && !armed;
        _endSession.Visible = active || armed;
        _endSession.Text = armed && !active ? "Cancel session start" : "End session & debrief";
        var feedback = state?["analysis"]?["feedback"]; var foundation = feedback?["foundation"] ?? state?["foundation"];
        var coverage = foundation?["coverage"];
        if (coverage is JsonObject)
        {
            string category = Str(foundation?["category"]?["name"], "car category unverified"), track = Str(foundation?["track"]?["name"], "track unverified");
            string examples = Fixed(Num(coverage["exact_car_human_traces"]), 0), categoryExamples = Fixed(Num(coverage["category_human_traces"]), 0);
            _homeCoverage.Text = "Coaching base: " + category + " · " + track + " · " + (Bool(coverage["track_profile"]) ? "track profile available" : "track mapping unverified") + ".\nResearch coverage · exact car: " + examples + " · category: " + categoryExamples + ". Your clean laps determine measured comparisons.";
        }
        else _homeCoverage.Text = "Coaching uses your comparable clean laps. Drive clean laps to build evidence for this car and track.";
        var debrief = lifecycle?["last_debrief"];
        if (debrief is null) return;
        string key = debrief.ToJsonString(); if (key == _debriefKey) return; _debriefKey = key;
        var text = new StringBuilder(); text.AppendLine("LAST SESSION"); text.AppendLine(Str(debrief["summary"], "Your recorded session is ready to review."));
        void Section(string heading, JsonNode? entries, string[] fields)
        {
            if (entries is not JsonArray array || array.Count == 0) return;
            text.AppendLine(); text.AppendLine(heading);
            foreach (var entry in array)
            {
                if (entry is JsonObject)
                {
                    var parts = fields.Select(f => Str(entry[f])).Where(v => v.Length > 0).Distinct();
                    text.AppendLine("• " + string.Join(" · ", parts));
                    if (entry["evidence"] is JsonArray evidence && evidence.Count > 0) text.AppendLine("  " + string.Join(" ", evidence.Select(n => Str(n))));
                }
                else if (entry is not null) text.AppendLine("• " + Str(entry));
            }
        }
        Section("WHAT WENT WELL", debrief["strengths"], ["text", "title", "action"]);
        Section("NEXT PRIORITIES", debrief["priorities"], ["title", "label", "instruction", "action", "experiment"]);
        Section("PLAN FOR YOUR NEXT SESSION", debrief["next_session_plan"], ["title", "action"]);
        if (debrief["metrics"] is JsonObject metrics && metrics.Count > 0)
        {
            text.AppendLine(); text.AppendLine("SESSION NOTES");
            string[] names = ["session_laps", "clean_laps", "best_lap_ms", "consistency_s", "fuel_used_l", "duration_s"];
            foreach (string name in names)
            {
                if (metrics[name] is null) continue;
                string formatted = name == "best_lap_ms" ? Lap(Num(metrics[name])) : Fixed(Num(metrics[name]), name.EndsWith("laps") ? 0 : 1);
                string label = name switch { "session_laps" => "Recorded laps", "clean_laps" => "Clean laps", "best_lap_ms" => "Best lap", "consistency_s" => "Consistency (s)", "fuel_used_l" => "Fuel used (L)", "duration_s" => "Elapsed time (s)", _ => name };
                text.AppendLine(label + ": " + formatted);
            }
        }
        _homeDebrief.Text = text.ToString();
    }

    private static string DescribeValue(JsonNode? value)
    {
        if (value is JsonArray array) return string.Join(" · ", array.Select(DescribeValue).Where(s => s.Length > 0));
        if (value is JsonObject obj) return string.Join(" · ", obj.Select(p => p.Key.Replace('_', ' ') + ": " + DescribeValue(p.Value)));
        return Str(value);
    }

    private async Task LoadSessionsAsync()
    {
        var result = await GetAsync("api/sessions" + (_showDeleted.Checked ? "?include_deleted=1" : ""));
        _dataDirectory = Str(result?["data_dir"], _dataDirectory ?? "");
        var sessions = result?["sessions"] as JsonArray ?? [];
        string? previous = _selectedSession;
        _historyLoading = true;
        try
        {
            _sessionGrid.Rows.Clear();
            foreach (var session in sessions)
            {
                if (session is null) continue;
                string id = Str(session["id"]);
                int count = (int)(Num(session["lap_count"]) ?? 0);
                string lapLabel = count > 0 ? count.ToString(CultureInfo.InvariantCulture) : Bool(session["active"]) ? "0 · in progress" : "0 · no full laps";
                int row = _sessionGrid.Rows.Add(When(Num(session["started"])), Str(session["source"]), Pretty(Str(session["car_id"])), Pretty(Str(session["track_id"])) + " · " + Pretty(Str(session["layout"])), lapLabel, Lap(Num(session["best_lap_ms"])));
                _sessionGrid.Rows[row].Tag = id;
                _sessionGrid.Rows[row].Cells["Car"].Value = Pretty(Str(session["car_id"])) + "\n" + Pretty(Str(session["track_id"])) + " · " + Str(session["lap_count"]) + " laps";
                _sessionGrid.Rows[row].Cells["Car"].ToolTipText = Pretty(Str(session["layout"])) + " · " + Str(session["source"]) + " · best " + Lap(Num(session["best_lap_ms"]));
                _sessionGrid.Rows[row].Cells["Laps"].Tag = count;
                if (id == previous) _sessionGrid.Rows[row].Selected = true;
            }
            if (_sessionGrid.Rows.Count == 0)
            {
                _savedNote.Text = "No saved sessions yet. Press Start session on Home, then drive or explore Demo.";
                _lapGrid.Rows.Clear(); _savedChart.SetData(null); _selectedSession = null; _selectedLap = null;
            }
            _sessionGrid.ClearSelection();
            var rows = _sessionGrid.Rows.Cast<DataGridViewRow>().ToArray();
            // Preserve a user's explicit selection, including an empty active
            // session. On first visit, show actual saved lap data before a
            // newly created zero-lap recording session.
            var selectedRow = rows.FirstOrDefault(r => (string?)r.Tag == previous)
                ?? rows.FirstOrDefault(r => r.Cells["Laps"].Tag is int count && count > 0)
                ?? rows.FirstOrDefault();
            if (selectedRow is not null)
            {
                _sessionGrid.CurrentCell = selectedRow.Cells[0];
                selectedRow.Selected = true;
            }
        }
        finally { _historyLoading = false; }
        if (_sessionGrid.SelectedRows.Count > 0 && _sessionGrid.SelectedRows[0].Tag is string selected)
            await LoadSessionAsync(selected);
        _status.Text = sessions.Count + " saved sessions · full history loaded from this computer";
    }

    private async Task LoadSessionAsync(string id)
    {
        int generation = ++_sessionGeneration;
        var detail = await GetAsync("api/sessions/" + Uri.EscapeDataString(id) + (_showDeleted.Checked ? "?include_deleted=1" : ""));
        if (generation != _sessionGeneration || _formClosed) return;
        string? previousLap = id == _selectedSession ? _selectedLap : null;
        _selectedSession = id; _historyDetail = detail;
        _historyLoading = true;
        try
        {
            _lapGrid.Rows.Clear();
            foreach (var lap in detail?["laps"] as JsonArray ?? [])
            {
                if (lap is null) continue;
                bool deleted = IsDeleted(lap);
                string status = deleted ? "Removed · restore to analyze" : Bool(lap["clean"]) ? "Observed clean" : Str(lap["reason"], "Excluded");
                int row = _lapGrid.Rows.Add(Str(lap["lap_number"]), Lap(Num(lap["lap_time_ms"])), deleted ? "—" : Delta(Num(lap["delta_s"])), Fixed(Num(lap["fuel_used_l"]), 2) + " L", status);
                _lapGrid.Rows[row].Tag = Str(lap["id"]);
                _lapGrid.Rows[row].Cells["Validity"].Value = deleted ? "Removed" : Bool(lap["clean"]) ? "Clean" : "Excluded";
                foreach (DataGridViewCell cell in _lapGrid.Rows[row].Cells) cell.ToolTipText = status + " · fuel " + Fixed(Num(lap["fuel_used_l"]), 2) + " L";
                if (deleted) _lapGrid.Rows[row].DefaultCellStyle.ForeColor = Muted;
                else if (!Bool(lap["clean"])) _lapGrid.Rows[row].DefaultCellStyle.ForeColor = Warm;
            }
            _lapGrid.ClearSelection();
            var select = _lapGrid.Rows.Cast<DataGridViewRow>().FirstOrDefault(r => (string?)r.Tag == previousLap) ?? _lapGrid.Rows.Cast<DataGridViewRow>().LastOrDefault();
            _selectedLap = select?.Tag as string;
            if (select is not null)
            {
                _lapGrid.CurrentCell = select.Cells[0];
                select.Selected = true;
            }
            FillReferences(); UpdateLapActions();
        }
        finally { _historyLoading = false; }
        if (_selectedLap is not null) await AnalyzeSelectedAsync();
        else { _savedChart.SetData(null); _savedCorners.Rows.Clear(); _savedNote.Text = "No visible laps in this session. Enable Show removed laps to restore excluded records."; }
    }

    private JsonNode? SelectedLap() => (_historyDetail?["laps"] as JsonArray)?.FirstOrDefault(lap => Str(lap?["id"]) == _selectedLap);

    private void FillReferences()
    {
        bool previousLoading = _historyLoading; _historyLoading = true;
        try
        {
            _references.Items.Clear(); _references.Items.Add(new ReferenceChoice(null, "Reference: automatic clean best"));
            foreach (var lap in _historyDetail?["laps"] as JsonArray ?? [])
                if (lap is not null && Bool(lap["clean"]) && !IsDeleted(lap) && Str(lap["id"]) != _selectedLap)
                    _references.Items.Add(new ReferenceChoice(Str(lap["id"]), "Reference lap " + Str(lap["lap_number"]) + " · " + Lap(Num(lap["lap_time_ms"]))));
            _references.SelectedIndex = 0;
        }
        finally { _historyLoading = previousLoading; }
    }

    private void UpdateLapActions()
    {
        var lap = SelectedLap(); bool selected = lap is not null, deleted = selected && IsDeleted(lap);
        _analyze.Enabled = selected && !deleted; _references.Enabled = selected && !deleted;
        _remove.Enabled = selected && !deleted; _restore.Enabled = selected && deleted; _undo.Enabled = _undoLap is not null;
    }

    private async Task AnalyzeSelectedAsync()
    {
        string? session = _selectedSession, lap = _selectedLap;
        if (session is null || lap is null) return;
        if (IsDeleted(SelectedLap()))
        {
            _savedChart.SetData(null); _savedCorners.Rows.Clear(); _savedNote.Text = "This lap was removed from analysis. Restore it to inspect its data."; return;
        }
        int generation = ++_analysisGeneration;
        _savedNote.Text = "Loading recorded lap " + Str(SelectedLap()?["lap_number"]) + "…";
        _cornerDetail.Clear();
        _savedChart.SetData(null); _savedCorners.Rows.Clear();
        string path = "api/analyze?session_id=" + Uri.EscapeDataString(session) + "&lap_id=" + Uri.EscapeDataString(lap);
        if (_references.SelectedItem is ReferenceChoice choice && choice.Id is not null) path += "&reference_id=" + Uri.EscapeDataString(choice.Id);
        var result = await GetAsync(path);
        if (generation != _analysisGeneration || session != _selectedSession || lap != _selectedLap || _formClosed) return;
        _savedAnalysis = result;
        _savedChart.ResetZoom(); _savedChart.SetData(result?["traces"]); _savedChart.SetCorners(result?["corners"]);
        var losses = (result?["corners"] as JsonArray ?? []).Where(c => Num(c?["time_loss_s"]) > .08).OrderByDescending(c => Num(c?["time_loss_s"])).Take(3).ToArray();
        string biggest = losses.Length > 0 ? "Improve: " + string.Join("  ·  ", losses.Select(c => "C" + Str(c?["number"]) + " " + Delta(Num(c?["time_loss_s"])))) : "No detected section lost more than 0.08 s.";
        string instruction = Str(result?["feedback"]?["instruction"]);
        _lapSummary.Text = "Lap " + Str(result?["lap"]?["lap_number"]) + "   " + Lap(Num(result?["lap"]?["lap_time_ms"])) + "   " + Delta(Num(result?["coaching"]?["delta_s"])) + "\n" + (instruction.Length > 0 ? instruction : Bool(result?["comparison_available"]) ? biggest : "Comparison unavailable · inspect the lap trace and status below");
        RenderCornerRows(_savedCorners, result?["corners"] as JsonArray);
        string reference = result?["reference"] is JsonNode r ? "reference lap " + Str(r["lap_number"]) + " · " + Delta(Num(result?["coaching"]?["delta_s"])) : "no other comparable clean reference";
        bool comparisonAvailable = Bool(result?["comparison_available"]);
        _savedNote.Text = "Saved lap " + Str(result?["lap"]?["lap_number"]) + " · " + reference + (comparisonAvailable ? ". Select a corner below to inspect it." : ". Recorded inputs are shown; condition differences are below.");
        if (!comparisonAvailable && result?["notes"] is JsonArray notes)
            _cornerDetail.Text = "WHY THIS LAP HAS NO COMPARISON\n" + string.Join("\n", notes.Select(n => Str(n)).Where(n => !n.StartsWith("Stored analysis;", StringComparison.Ordinal))) + "\nSelect another clean lap to inspect a comparison within its recorded conditions.";
    }

    private async Task EditLapAsync(bool restore)
    {
        string? session = _selectedSession, lap = _selectedLap;
        if (session is null || lap is null) return;
        await PostAsync(restore ? "api/laps/restore" : "api/laps/delete", new JsonObject { ["session_id"] = session, ["lap_id"] = lap });
        if (!restore) { _undoLap = lap; _undoSession = session; }
        else if (_undoLap == lap) { _undoLap = null; _undoSession = null; }
        await LoadSessionsAsync();
        _status.Text = restore ? "Lap restored. APEX references and fuel estimates recalculated." : "Lap removed from APEX analysis. Original recordings are retained. Undo is available.";
        UpdateLapActions();
    }

    private async Task UndoAsync()
    {
        if (_undoLap is null || _undoSession is null) return;
        await PostAsync("api/laps/restore", new JsonObject { ["session_id"] = _undoSession, ["lap_id"] = _undoLap });
        _selectedSession = _undoSession; _selectedLap = _undoLap; _undoLap = null; _undoSession = null;
        await LoadSessionsAsync(); UpdateLapActions(); _status.Text = "Lap restored to APEX analysis.";
    }

    private async Task ExportSessionAsync()
    {
        if (_selectedSession is null) { _status.Text = "Choose a saved session to export."; return; }
        using var dialog = new SaveFileDialog { Title = "Export full recorded session", Filter = "JSON recording (*.json)|*.json", FileName = "APEX-session-" + DateTime.Now.ToString("yyyyMMdd-HHmm") + ".json", AddExtension = true };
        if (dialog.ShowDialog(this) != DialogResult.OK) return;
        string path = "api/sessions/" + Uri.EscapeDataString(_selectedSession) + "/export" + (_showDeleted.Checked ? "?include_deleted=1" : "");
        using var response = await _http.GetAsync(path, HttpCompletionOption.ResponseHeadersRead);
        if (!response.IsSuccessStatusCode) throw new InvalidOperationException(await ErrorTextAsync(response));
        await using var output = new FileStream(dialog.FileName, FileMode.Create, FileAccess.Write, FileShare.None, 65536, true);
        await response.Content.CopyToAsync(output); _status.Text = "Full session exported, including original recorded samples.";
    }

    private async Task SavePreferencesAsync()
    {
        var body = new JsonObject { ["voice"] = _voice.Checked, ["focus"] = _focus.SelectedIndex switch { 1 => "stability", 2 => "rotation", _ => "balanced" }, ["fuel_reserve_l"] = (double)_reserve.Value, ["verbosity"] = _verbosity.SelectedIndex switch { 0 => "less", 2 => "more", _ => "balanced" }, ["quiet"] = _quiet.Checked, ["ptt_enabled"] = _pttEnabled.Checked, ["ptt_key"] = _pttKey.SelectedItem?.ToString() ?? "F10", ["ptt_binding"] = _pttBinding.Json() };
        if (Bool(_state?["connected"])) body["target_pressure_psi"] = _pressureSet.Checked ? JsonValue.Create((double)_pressure.Value) : null;
        await PostAsync("api/settings", body);
        _settingsLoaded = false; _settingsNote.Text = "Preferences saved on this computer. Targets apply to the current car and tyre compound.\nNative Pro Engineer rule thresholds remain in its own settings.";
        await PollAsync();
    }

    private async Task RunActionAsync(string action)
    {
        await GuardAsync(async () =>
        {
            await PostAsync("api/action", new JsonObject { ["action"] = action });
            _settingsLoaded = false; await PollAsync();
            _status.Text = action == "radio" ? "Radio check sent to your default audio output." : "Engineer controls updated.";
        });
    }

    private void OpenDataFolder()
    {
        try
        {
            string folder = !string.IsNullOrWhiteSpace(_dataDirectory) ? _dataDirectory : Path.Combine(Program.Backend.PackageRoot, "data");
            if (!Directory.Exists(folder)) { _status.Text = "No recordings folder exists yet."; return; }
            Process.Start(new ProcessStartInfo { FileName = folder, UseShellExecute = true });
        }
        catch (Exception error) { ShowError(error.Message); }
    }

    private async Task<JsonNode?> GetAsync(string path)
    {
        using var response = await _http.GetAsync(path);
        string raw = await response.Content.ReadAsStringAsync();
        if (!response.IsSuccessStatusCode) throw new InvalidOperationException(ParseError(raw, (int)response.StatusCode));
        return JsonNode.Parse(raw);
    }

    private async Task PostAsync(string path, JsonObject body, CancellationToken cancellation = default)
    {
        using var content = new StringContent(body.ToJsonString(), Encoding.UTF8, "application/json");
        using var response = await _http.PostAsync(path, content, cancellation);
        if (!response.IsSuccessStatusCode) throw new InvalidOperationException(await ErrorTextAsync(response));
    }

    private static async Task<string> ErrorTextAsync(HttpResponseMessage response) => ParseError(await response.Content.ReadAsStringAsync(), (int)response.StatusCode);
    private static string ParseError(string raw, int status)
    {
        try { return Str(JsonNode.Parse(raw)?["error"], "The local engineer could not complete this action (" + status + ")."); }
        catch { return "The local engineer could not complete this action (" + status + ")."; }
    }

    private async Task GuardAsync(Func<Task> action)
    {
        try { await action(); }
        catch (Exception error) { if (!_formClosed) ShowError(error.Message); }
    }

    private void ShowError(string message) { _status.Text = message; _status.ForeColor = Warm; _savedNote.Text = message; }
    private void AddAction(FlowLayoutPanel panel, string text, string action) { var button = ActionButton(text); button.Click += async (_, _) => await RunActionAsync(action); panel.Controls.Add(button); }
    private static Panel Metric(string title, Label value, Label? note = null)
    {
        var panel = new Panel { Dock = DockStyle.Fill, Padding = new Padding(13, 8, 8, 4), Margin = new Padding(4), BackColor = Surface };
        var rows = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = note is null ? 2 : 3, Margin = Padding.Empty, Padding = Padding.Empty };
        rows.RowStyles.Add(new RowStyle(SizeType.Absolute, 22));
        rows.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        var label = TextLabel(title, 8, Muted, true); label.Dock = DockStyle.Fill;
        value.Dock = DockStyle.Fill; value.Margin = Padding.Empty;
        rows.Controls.Add(label, 0, 0); rows.Controls.Add(value, 0, 1);
        if (note is not null)
        {
            rows.RowStyles.Add(new RowStyle(SizeType.Absolute, 18));
            note.Dock = DockStyle.Fill; note.Margin = Padding.Empty;
            rows.Controls.Add(note, 0, 2);
        }
        panel.Controls.Add(rows);
        return panel;
    }

    private static void CornerColumns(DataGridView grid)
    {
        AddColumn(grid, "Corner", "SECTION", 83); AddColumn(grid, "Delta", "TIME Δ", 82);
        AddColumn(grid, "Entry", "ENTRY km/h", 94); AddColumn(grid, "Minimum", "MIN km/h", 91);
        AddColumn(grid, "Exit", "EXIT km/h", 91); AddColumn(grid, "Throttle", "THROTTLE Δ", 111);
        foreach (DataGridViewColumn column in grid.Columns)
            if (column.Name != "Throttle") column.AutoSizeMode = DataGridViewAutoSizeColumnMode.DisplayedCells;
        grid.Columns["Throttle"]!.AutoSizeMode = DataGridViewAutoSizeColumnMode.Fill;
    }

    private void RenderTiming(JsonNode? timing)
    {
        var cars = Bool(timing?["available"]) ? timing?["cars"] as JsonArray : null;
        if (cars is null || cars.Count == 0)
        {
            if (_timingGrid.Rows.Count > 0) _timingGrid.Rows.Clear();
            _timingNote.Text = Str(timing?["note"], "Enable the RaceEngineer in-game app in an AC live session for race timing.");
            return;
        }
        while (_timingGrid.Rows.Count < cars.Count) _timingGrid.Rows.Add();
        while (_timingGrid.Rows.Count > cars.Count) _timingGrid.Rows.RemoveAt(_timingGrid.Rows.Count - 1);
        double? playerId = Num(timing?["player_id"]);
        for (int i = 0; i < cars.Count; i++)
        {
            var car = cars[i]; if (car is null) continue;
            bool player = Num(car["id"]) == playerId;
            string order = Num(car["race_position"]) is double position && position > 0 ? ((int)position).ToString() : "~" + (i + 1);
            string[] values = [order, Str(car["name"], "Car " + Str(car["id"])) + (player ? " · YOU" : ""), Num(car["lap_count"]) is double lap ? ((int)lap + 1).ToString() : "—", Lap(Num(car["last_lap_ms"])), Lap(Num(car["best_lap_ms"])), Str(car["tyre_compound"], "—"), player ? "YOU" : Delta(Num(car["gap_to_player_s"])), Lap(Num(car["lap_time_ms"])), Fixed(Num(car["speed_kmh"]), 0)];
            for (int column = 0; column < values.Length; column++)
                if (!Equals(_timingGrid.Rows[i].Cells[column].Value, values[column]))
                    _timingGrid.Rows[i].Cells[column].Value = values[column];
            _timingGrid.Rows[i].DefaultCellStyle.ForeColor = player ? Lime : Ink;
        }
        _timingNote.Text = "~ Progress order · gap + ahead / − behind · gaps are estimates. " + Str(timing?["note"]);
    }

    private static void RenderCornerRows(DataGridView grid, JsonArray? corners)
    {
        grid.Rows.Clear();
        foreach (var c in corners ?? [])
        {
            if (c is null) continue;
            int row = grid.Rows.Add("Detected C" + Str(c["number"]), Delta(Num(c["time_loss_s"])), Fixed(Num(c["entry_speed_kmh"]), 0), Fixed(Num(c["minimum_speed_kmh"]), 0), Fixed(Num(c["exit_speed_kmh"]), 0), Num(c["full_throttle_delta_m"]) is double throttle ? (throttle >= 0 ? "+" : "−") + Math.Abs(throttle).ToString("0", CultureInfo.InvariantCulture) + " m" : "—");
            grid.Rows[row].Tag = c;
            grid.Rows[row].Cells[1].Style.ForeColor = Num(c["time_loss_s"]) > 0 ? Warm : Lime;
        }
    }

    private static DataGridView Grid() => new()
    {
        Dock = DockStyle.Fill, BackgroundColor = Surface, ForeColor = Ink, BorderStyle = BorderStyle.None,
        AllowUserToAddRows = false, AllowUserToDeleteRows = false, AllowUserToResizeRows = false,
        ReadOnly = true, RowHeadersVisible = false, MultiSelect = false, SelectionMode = DataGridViewSelectionMode.FullRowSelect,
        EnableHeadersVisualStyles = false, AutoSizeRowsMode = DataGridViewAutoSizeRowsMode.None,
        RowTemplate = { Height = 30 }, GridColor = Border, ColumnHeadersHeight = 34,
        ColumnHeadersDefaultCellStyle = new DataGridViewCellStyle { BackColor = Background, ForeColor = Muted, Font = new Font("Segoe UI", 8, FontStyle.Bold), SelectionBackColor = Background },
        DefaultCellStyle = new DataGridViewCellStyle { BackColor = Surface, ForeColor = Ink, SelectionBackColor = Color.FromArgb(55, 77, 58), SelectionForeColor = Ink, Font = new Font("Segoe UI", 9), Padding = new Padding(5, 0, 3, 0) },
        AlternatingRowsDefaultCellStyle = new DataGridViewCellStyle { BackColor = Color.FromArgb(23, 30, 38) },
    };

    private static void AddColumn(DataGridView grid, string name, string header, int width) => grid.Columns.Add(new DataGridViewTextBoxColumn { Name = name, HeaderText = header, Width = width, SortMode = DataGridViewColumnSortMode.NotSortable });
    private static TabPage Page(string text) => new(text) { BackColor = Background, ForeColor = Ink, Padding = Padding.Empty };
    private static Label TextLabel(string text, float size, Color color, bool bold = false) => new() { Text = text, AutoSize = false, ForeColor = color, Font = new Font("Segoe UI", size, bold ? FontStyle.Bold : FontStyle.Regular), BackColor = Color.Transparent, UseMnemonic = false };
    private static FlowLayoutPanel Flow() => new() { FlowDirection = FlowDirection.LeftToRight, WrapContents = false, AutoSize = false, Height = 40, BackColor = Color.Transparent };
    private static Button ActionButton(string text) => new() { Text = text, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, FlatStyle = FlatStyle.Flat, ForeColor = Ink, BackColor = Surface, Padding = new Padding(10, 5, 10, 5), Margin = new Padding(0, 2, 7, 2), FlatAppearance = { BorderColor = Border, BorderSize = 1 }, UseVisualStyleBackColor = false };
    private static ComboBox Combo() => new() { DropDownStyle = ComboBoxStyle.DropDownList, FlatStyle = FlatStyle.Flat, BackColor = Surface, ForeColor = Ink, Width = 230 };
    private static NumericUpDown NumberInput(decimal min, decimal max, decimal initial) => new() { Minimum = min, Maximum = max, Value = initial, DecimalPlaces = 1, Increment = 0.5m, Width = 130, BackColor = Surface, ForeColor = Ink, BorderStyle = BorderStyle.FixedSingle };
    private static Control Spacer(int height) => new Panel { Size = new Size(10, height) };
    private static FlowLayoutPanel SettingRow(string label, Control control)
    {
        var row = Flow(); row.Width = 880; row.Height = 43;
        var caption = TextLabel(label, 10, Ink); caption.Width = 255; caption.Height = 33; caption.TextAlign = ContentAlignment.MiddleLeft;
        control.Margin = new Padding(0, 5, 0, 0); row.Controls.Add(caption); row.Controls.Add(control); return row;
    }

    private static string Str(JsonNode? node, string fallback = "") => node is null ? fallback : node is JsonValue v && v.TryGetValue<string>(out var text) ? text : node.ToString();
    private static double? Num(JsonNode? node) => node is JsonValue value && value.TryGetValue<double>(out var number) && double.IsFinite(number) ? number : null;
    private static bool Bool(JsonNode? node) => node is JsonValue value && value.TryGetValue<bool>(out var yes) && yes;
    private static bool IsDeleted(JsonNode? node) => Bool(node?["deleted"]) || node?["deleted_at"] is not null;
    private static string Pretty(string value) => value.Replace("ks_", "").Replace('_', ' ');
    private static string Fixed(double? value, int decimals = 1) => value?.ToString("F" + decimals, CultureInfo.InvariantCulture) ?? "—";
    private static string Delta(double? value) => value.HasValue ? (value.Value >= 0 ? "+" : "−") + Math.Abs(value.Value).ToString("0.000", CultureInfo.InvariantCulture) + " s" : "—";
    private static string Lap(double? ms) => ms is > 0 ? ((int)(ms.Value / 60000)).ToString(CultureInfo.InvariantCulture) + ":" + ((ms.Value % 60000) / 1000).ToString("00.000", CultureInfo.InvariantCulture) : "—";
    private static string When(double? timestamp) { try { return timestamp.HasValue ? DateTimeOffset.FromUnixTimeMilliseconds((long)(timestamp * 1000)).ToLocalTime().ToString("dd MMM HH:mm") : "—"; } catch { return "—"; } }
    private static decimal ClampDecimal(double? value, decimal min, decimal max, decimal fallback) => value.HasValue ? Math.Clamp((decimal)value.Value, min, max) : fallback;
    private sealed record RadioLine(string FullText, string Kind) { public override string ToString() => Kind.Replace('_', ' ') + " · " + FullText; }
    private sealed record ReferenceChoice(string? Id, string Label) { public override string ToString() => Label; }
}
