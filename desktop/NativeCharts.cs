using System.Drawing.Drawing2D;
using System.Globalization;
using System.Text.Json.Nodes;

namespace ApexDesktop;

internal readonly record struct TraceSample(double X, double? Speed, double? Gas, double? Brake, double? Time);

/// <summary>Pure interpolation/range calculations shared by painting and hover.</summary>
internal static class TelemetryMath
{
    internal static double? Number(JsonNode? node)
        => double.TryParse(node?.ToString(), NumberStyles.Float, CultureInfo.InvariantCulture, out double n)
            && double.IsFinite(n) ? n : null;

    internal static TraceSample[] Read(JsonNode? node)
    {
        if (node is not JsonArray array) return [];
        double? Positive(JsonNode? n) => Number(n) is double v && v >= 0 ? v : null;
        double? Pedal(JsonNode? n) => Number(n) is double v && v >= 0 && v <= 1 ? v : null;
        return array.OfType<JsonObject>()
            .Where(p => Number(p["x"]) is double x && x >= 0 && x <= 1)
            .Select(p => new TraceSample(Number(p["x"])!.Value, Positive(p["speed"]),
                Pedal(p["gas"]), Pedal(p["brake"]), Positive(p["t"])))
            .OrderBy(p => p.X).GroupBy(p => p.X).Select(g => g.Last()).ToArray();
    }

    internal static double? At(TraceSample[] points, double x, Func<TraceSample, double?> value)
    {
        if (!double.IsFinite(x) || points.Length == 0 || x < points[0].X || x > points[^1].X)
            return null;
        int low = 0, high = points.Length - 1;
        while (low <= high)
        {
            int middle = (low + high) / 2;
            if (points[middle].X == x) return value(points[middle]);
            if (points[middle].X < x) low = middle + 1;
            else high = middle - 1;
        }
        if (high < 0 || low >= points.Length) return null;
        double? a = value(points[high]), b = value(points[low]);
        if (a is null || b is null) return null;
        return a.Value + (b.Value - a.Value) * ((x - points[high].X) / (points[low].X - points[high].X));
    }

    internal static bool HasTiming(TraceSample[] points)
    {
        if (points.Length < 2 || points.Any(p => p.Time is null)) return false;
        for (int i = 1; i < points.Length; i++)
            if (points[i].Time < points[i - 1].Time) return false;
        return true;
    }

    internal static double? DeltaAt(TraceSample[] current, TraceSample[] reference, double x)
    {
        if (!HasTiming(current) || !HasTiming(reference)) return null;
        double? a = At(current, x, p => p.Time), b = At(reference, x, p => p.Time);
        return a.HasValue && b.HasValue ? a.Value - b.Value : null;
    }

    internal static (double Start, double End) Range(double start, double end)
    {
        if (!double.IsFinite(start) || !double.IsFinite(end)) return (0, 1);
        if (start > end) (start, end) = (end, start);
        start = Math.Clamp(start, 0, 1); end = Math.Clamp(end, 0, 1);
        if (end - start < 0.005)
        {
            double centre = (start + end) / 2;
            start = Math.Clamp(centre - 0.0025, 0, 0.995);
            end = start + 0.005;
        }
        return (start, end);
    }
}

/// <summary>Native A/B telemetry: hover, drag zoom and corner focus.</summary>
internal sealed class TelemetryChart : Control
{
    private sealed record Corner(double Start, double End, string Label, string Name);
    private TraceSample[] current = [], reference = [];
    private Corner[] corners = [];
    private (double X, double? Value)[] delta = [];
    private bool rolling;
    private double viewStart, viewEnd = 1;
    private double? hoverX;
    private bool dragging;
    private float dragStart, dragEnd;
    private string hoverText = "";
    private readonly ToolTip tip = new() { InitialDelay = 100, ReshowDelay = 30, AutoPopDelay = 8000 };
    private static readonly Color Muted = Color.FromArgb(151, 163, 179);
    private static readonly Color RefColor = Color.FromArgb(165, 175, 195);
    private static readonly Color[] Colors = [Color.FromArgb(116, 205, 245), Color.FromArgb(85, 211, 165),
        Color.FromArgb(245, 135, 120), Color.FromArgb(190, 239, 72)];

    internal TelemetryChart()
    {
        DoubleBuffered = true;
        SetStyle(ControlStyles.Selectable | ControlStyles.ResizeRedraw, true);
        TabStop = true;
        BackColor = Color.FromArgb(22, 27, 33);
        ForeColor = Color.FromArgb(225, 232, 239);
        Font = new Font("Segoe UI", 9);
        MinimumSize = new Size(300, 260);
        AccessibleRole = AccessibleRole.Chart;
        AccessibleName = "Interactive telemetry: speed, throttle, brake and cumulative time delta";
        AccessibleDescription = "Hover for selected/reference values. Drag to zoom. Double-click, Home or Escape restores the full lap.";
        Cursor = Cursors.Cross;
    }

    public void SetData(JsonNode? traces)
    {
        bool wasRolling = rolling;
        current = TelemetryMath.Read(traces?["current"]);
        reference = TelemetryMath.Read(traces?["reference"]);
        rolling = false;
        if (current.Length < 2 && TelemetryMath.Read(traces?["rolling"]) is var recent && recent.Length >= 2)
        {
            current = recent; reference = []; rolling = true;
        }
        bool timed = !rolling && TelemetryMath.HasTiming(current) && TelemetryMath.HasTiming(reference);
        delta = current.Select(p => (p.X, timed ? TelemetryMath.DeltaAt(current, reference, p.X) : null)).ToArray();
        if (traces is null || wasRolling != rolling)
        {
            viewStart = 0; viewEnd = 1; hoverX = null;
            tip.Hide(this); hoverText = "";
            if (traces is null) corners = [];
        }
        Invalidate();
    }

    public void SetCorners(JsonNode? node)
    {
        if (node is JsonObject) node = node["corners"];
        if (node is not JsonArray array) { corners = []; Invalidate(); return; }
        var list = new List<Corner>();
        foreach (var item in array.OfType<JsonObject>())
        {
            double? start = TelemetryMath.Number(item["section_start_pos"] ?? item["start_pos"]);
            double? end = TelemetryMath.Number(item["section_end_pos"] ?? item["end_pos"]);
            if (start is null || end is null || start < 0 || end > 1 || end <= start) continue;
            string label = item["label"]?.ToString() ?? "C" + (list.Count + 1);
            list.Add(new Corner(start.Value, end.Value, label.Length > 14 ? label[..14] : label,
                item["name"]?.ToString() ?? label));
        }
        corners = list.ToArray();
        Invalidate();
    }

    public void FocusRange(double start, double end)
    {
        (viewStart, viewEnd) = TelemetryMath.Range(start, end);
        hoverX = null; tip.Hide(this); hoverText = ""; Invalidate();
    }

    public void ResetZoom() => FocusRange(0, 1);
    private RectangleF PlotArea => new(140, 76, Math.Max(30, Width - 160), Math.Max(80, Height - 110));
    private float Pixel(double x) => PlotArea.Left + (float)((x - viewStart) / (viewEnd - viewStart)) * PlotArea.Width;
    private double Position(float x) => viewStart + Math.Clamp((x - PlotArea.Left) / PlotArea.Width, 0, 1) * (viewEnd - viewStart);
    private static double? Value(TraceSample p, int row) => row == 0 ? p.Speed : row == 1 ? p.Gas * 100 : p.Brake * 100;

    protected override void OnPaint(PaintEventArgs e)
    {
        base.OnPaint(e);
        Graphics g = e.Graphics;
        g.SmoothingMode = SmoothingMode.AntiAlias;
        using var fg = new SolidBrush(ForeColor);
        using var muted = new SolidBrush(Muted);
        using var refBrush = new SolidBrush(RefColor);
        using var grid = new Pen(Color.FromArgb(48, 58, 70));
        using var title = new Font(Font, FontStyle.Bold);
        using var small = new Font(Font.FontFamily, Math.Max(7, Font.Size - 1));
        g.DrawString(string.IsNullOrEmpty(Text) ? "TELEMETRY" : Text, title, fg, 12, 8);
        g.DrawString("A  selected", Font, fg, 12, 31);
        g.DrawString("B  reference · dashed", Font, refBrush, 110, 31);
        if (Width > 560) g.DrawString("Drag to zoom · double-click to reset", small, muted, Width - 255, 12);

        RectangleF area = PlotArea;
        float rowHeight = area.Height / 4;
        double speedMax = Math.Max(50, Math.Ceiling(current.Concat(reference).Where(p => p.X >= viewStart && p.X <= viewEnd)
            .Select(p => p.Speed ?? 0).DefaultIfEmpty().Max() / 25) * 25);
        double deltaMax = Math.Max(0.1, Math.Ceiling(delta.Where(p => p.X >= viewStart && p.X <= viewEnd)
            .Select(p => Math.Abs(p.Value ?? 0)).DefaultIfEmpty().Max() * 10) / 10);
        string[] labels = ["SPEED\nkm/h", "THROTTLE\n%", "BRAKE\n%", "TIME DELTA\ns · A minus B"];
        for (int row = 0; row < 4; row++)
        {
            var plot = new RectangleF(area.Left, area.Top + row * rowHeight, area.Width, rowHeight - 14);
            double min = row == 3 ? -deltaMax : 0, max = row == 0 ? speedMax : row == 3 ? deltaMax : 100;
            float Y(double v) => plot.Bottom - (float)((v - min) / (max - min)) * plot.Height;
            using var channel = new SolidBrush(Colors[row]);
            g.DrawString(labels[row], small, channel, 7, plot.Top + 2);
            foreach (var corner in corners.Where(c => !rolling && c.End >= viewStart && c.Start <= viewEnd))
            {
                float a = Math.Clamp(Pixel(corner.Start), plot.Left, plot.Right);
                float b = Math.Clamp(Pixel(corner.End), plot.Left, plot.Right);
                using var band = new SolidBrush(Color.FromArgb(15, 125, 160, 205));
                g.FillRectangle(band, a, plot.Top, b - a, plot.Height);
                if (row == 0 && b - a > 20) g.DrawString(corner.Label, small, muted, a + 2, plot.Top - 17);
            }
            using var tickFormat = new StringFormat { Alignment = StringAlignment.Far, LineAlignment = StringAlignment.Center };
            foreach (double value in new[] { min, (min + max) / 2, max })
            {
                float y = Y(value);
                g.DrawLine(grid, plot.Left, y, plot.Right, y);
                string tick = row == 3 ? value.ToString("+0.0;-0.0;0", CultureInfo.InvariantCulture) : value.ToString("0", CultureInfo.InvariantCulture);
                g.DrawString(tick, small, muted, new RectangleF(94, y - 8, 38, 16), tickFormat);
            }
            for (int tick = 0; tick <= 4; tick++)
            {
                float x = plot.Left + tick * plot.Width / 4;
                g.DrawLine(grid, x, plot.Top, x, plot.Bottom);
            }
            var state = g.Save();
            g.SetClip(plot);
            void Draw(IEnumerable<(double X, double? V)> points, Color color, bool dashed)
            {
                using var pen = new Pen(color, dashed ? 1.35f : 1.9f);
                if (dashed) pen.DashStyle = DashStyle.Dash;
                PointF? previous = null;
                foreach (var point in points)
                {
                    if (point.V is not double value) { previous = null; continue; }
                    var p = new PointF(Pixel(point.X), Y(value));
                    if (previous is PointF before) g.DrawLine(pen, before, p);
                    previous = p;
                }
            }
            if (row == 3)
            {
                g.DrawLine(grid, plot.Left, Y(0), plot.Right, Y(0));
                Draw(delta, Colors[row], false);
            }
            else
            {
                int channelRow = row;
                Draw(reference.Select(p => (p.X, Value(p, channelRow))), RefColor, true);
                Draw(current.Select(p => (p.X, Value(p, channelRow))), Colors[row], false);
            }
            if (hoverX is double hover && hover >= viewStart && hover <= viewEnd)
            {
                using var cross = new Pen(Color.FromArgb(145, 215, 225, 235)) { DashStyle = DashStyle.Dot };
                g.DrawLine(cross, Pixel(hover), plot.Top, Pixel(hover), plot.Bottom);
                double? a = row == 3 ? TelemetryMath.DeltaAt(current, reference, hover) : TelemetryMath.At(current, hover, p => Value(p, row));
                if (a is double v) g.FillEllipse(channel, Pixel(hover) - 3, Y(v) - 3, 6, 6);
                if (row < 3 && TelemetryMath.At(reference, hover, p => Value(p, row)) is double b)
                    g.FillEllipse(refBrush, Pixel(hover) - 2.5f, Y(b) - 2.5f, 5, 5);
            }
            if (dragging)
            {
                using var selection = new SolidBrush(Color.FromArgb(45, 190, 239, 72));
                g.FillRectangle(selection, Math.Min(dragStart, dragEnd), plot.Top, Math.Abs(dragEnd - dragStart), plot.Height);
            }
            g.Restore(state);
            if (row == 3 && !delta.Any(p => p.Value.HasValue))
                g.DrawString("Comparable lap-time samples unavailable", small, muted, plot.Left + 10, plot.Top + 4);
        }
        using var axisFormat = new StringFormat { Alignment = StringAlignment.Center };
        for (int tick = 0; tick <= 4; tick++)
        {
            double x = viewStart + tick * (viewEnd - viewStart) / 4;
            g.DrawString((x * 100).ToString("0.#", CultureInfo.InvariantCulture) + "%", small, muted,
                new RectangleF(Pixel(x) - 25, Height - 30, 50, 16), axisFormat);
        }
        if (Width > 470) g.DrawString(rolling ? "Recent input window" : "Track distance", small, muted, 8, Height - 27);
        if (current.Length < 2 && reference.Length < 2)
            g.DrawString("Select a recorded lap or drive to see inputs.", title, fg, area.Left + 8, area.Top + 14);
        if (viewStart > 0 || viewEnd < 1)
            g.DrawString("ZOOM", small, muted, Width - 50, Height - 13);
    }

    private void UpdateHover(float mouseX, float mouseY)
    {
        hoverX = Position(mouseX);
        double x = hoverX.Value;
        string F(double? value, string format = "0.0") => value?.ToString(format, CultureInfo.InvariantCulture) ?? "--";
        string Time(double? seconds) => seconds is double s ? TimeSpan.FromSeconds(s).ToString(@"m\:ss\.fff", CultureInfo.InvariantCulture) : "--";
        double? A(Func<TraceSample, double?> value) => TelemetryMath.At(current, x, value);
        double? B(Func<TraceSample, double?> value) => TelemetryMath.At(reference, x, value);
        string text = (rolling ? "Window " : "Lap ") + (x * 100).ToString("0.00", CultureInfo.InvariantCulture) + "%\n"
            + "Time   A " + Time(A(p => p.Time)) + "   B " + Time(B(p => p.Time)) + "\n"
            + "Speed (km/h)   A " + F(A(p => p.Speed)) + "   B " + F(B(p => p.Speed)) + "\n"
            + "Throttle (%)   A " + F(A(p => p.Gas * 100), "0") + "   B " + F(B(p => p.Gas * 100), "0") + "\n"
            + "Brake (%)   A " + F(A(p => p.Brake * 100), "0") + "   B " + F(B(p => p.Brake * 100), "0") + "\n"
            + "Cumulative delta (s)   " + F(TelemetryMath.DeltaAt(current, reference, x), "+0.000;-0.000;0.000");
        var corner = corners.FirstOrDefault(c => x >= c.Start && x <= c.End);
        if (corner is not null && !rolling) text = corner.Name + "\n" + text;
        if (text != hoverText)
        {
            hoverText = text;
            AccessibleDescription = text;
            tip.Show(text, this, new System.Drawing.Point((int)Math.Min(mouseX + 18, Width - 200),
                (int)Math.Min(mouseY + 18, Height - 100)), 8000);
        }
        Invalidate();
    }

    protected override void OnMouseMove(MouseEventArgs e)
    {
        base.OnMouseMove(e);
        if (dragging) { dragEnd = Math.Clamp(e.X, PlotArea.Left, PlotArea.Right); Invalidate(); }
        else if (PlotArea.Contains(e.Location)) UpdateHover(e.X, e.Y);
        else { hoverX = null; tip.Hide(this); hoverText = ""; Invalidate(); }
    }

    protected override void OnMouseDown(MouseEventArgs e)
    {
        base.OnMouseDown(e);
        if (e.Button == MouseButtons.Left && PlotArea.Contains(e.Location))
        {
            Focus(); dragging = true; dragStart = dragEnd = e.X; Capture = true; tip.Hide(this);
        }
    }

    protected override void OnMouseUp(MouseEventArgs e)
    {
        base.OnMouseUp(e);
        if (!dragging) return;
        dragging = false; Capture = false;
        if (Math.Abs(dragEnd - dragStart) >= 8) FocusRange(Position(dragStart), Position(dragEnd));
        else UpdateHover(e.X, e.Y);
        Invalidate();
    }

    protected override void OnMouseDoubleClick(MouseEventArgs e) { base.OnMouseDoubleClick(e); ResetZoom(); }
    protected override void OnMouseLeave(EventArgs e)
    {
        base.OnMouseLeave(e);
        if (!dragging) { hoverX = null; hoverText = ""; tip.Hide(this); Invalidate(); }
    }
    protected override void OnKeyDown(KeyEventArgs e)
    {
        base.OnKeyDown(e);
        if (e.KeyCode is Keys.Home or Keys.Escape) { ResetZoom(); e.Handled = true; }
    }
    protected override void Dispose(bool disposing) { if (disposing) tip.Dispose(); base.Dispose(disposing); }
}
